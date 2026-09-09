"""Host-only reusable benchmark controller. Standard library + Git + Docker."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import uuid

ENGINE_ROOT = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('BENCHMARK_SUITE_ROOT', ENGINE_ROOT)).resolve()
RUNTIME = ROOT / 'runtime'
PRIVATE = ROOT / 'private'
SUITE = None
GRADER_TIMEOUT_SECONDS = 120
GRADER_ATTEMPTS = 2


def suite_config():
    global SUITE
    if SUITE is None:
        path = PRIVATE/'suite.json'
        if not path.is_file():
            raise RuntimeError(f'Missing benchmark suite configuration: {path}')
        SUITE = json.loads(path.read_text(encoding='utf-8'))
    return SUITE


def configured_path(value):
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT/path).resolve()


SOURCE = configured_path(suite_config()['repository']['source_path'])


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value, sharing_timeout=30.0):
    """Atomically publish JSON without letting Windows readers abort a run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f'.{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp'
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    deadline = time.monotonic() + sharing_timeout
    try:
        while True:
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.1)
    finally:
        if temporary.exists():
            temporary.unlink()


def command(args, **kwargs):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)


def task(tid):
    if tid not in read(PRIVATE/'manifest.json')['tasks']:
        raise ValueError('Unknown task')
    return read(PRIVATE/'tasks'/tid/'task.json')


def run_dir(run):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', run):
        raise ValueError('Invalid run ID')
    path = RUNTIME / 'runs' / run
    # Refuse reparse points in every managed ancestor, including Windows junctions.
    for p in [RUNTIME, RUNTIME/'runs', path]:
        if p.exists() and (p.is_symlink() or bool(getattr(p.lstat(), 'st_file_attributes', 0) & 0x400)):
            raise ValueError('Managed path must not be a link or junction')
    if path.resolve().parent != (RUNTIME/'runs').resolve():
        raise ValueError('Run escaped managed root')
    return path


@contextmanager
def locked(run):
    rd = run_dir(run)
    rd.mkdir(parents=True, exist_ok=True)
    lock = rd/'controller.lock'
    def owner_is_alive():
        try:
            payload = json.loads(lock.read_text(encoding='utf-8'))
            pid = int(payload['pid'])
        except Exception:
            return True
        if pid <= 0:
            return True
        if os.name == 'nt':
            # os.kill(pid, 0) terminates processes on Windows. Query a handle instead.
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x00100000, False, pid)
            if not handle:
                return ctypes.get_last_error() != 87
            try:
                return kernel.WaitForSingleObject(handle, 0) != 0
            finally:
                kernel.CloseHandle(handle)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except (PermissionError, OSError):
            return True
        return True
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if owner_is_alive():
            raise
        lock.unlink()
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, json.dumps({'pid':os.getpid(),'created_at':time.time()}).encode('utf-8'))
    try:
        yield rd
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def checked_workspace(run):
    rd = run_dir(run)
    marker = rd/'owner.json'
    owner = suite_config()['workspace_owner']
    if not marker.is_file() or read(marker) != {'owner':owner,'run':run}:
        raise ValueError('Missing or invalid workspace ownership marker')
    workspace = rd/'workspace'
    if workspace.resolve() != rd.resolve()/'workspace' or workspace.is_symlink():
        raise ValueError('Workspace escaped its exact owned path')
    if workspace.exists() and getattr(workspace.lstat(),'st_file_attributes',0) & 0x400:
        raise ValueError('Workspace must not be a reparse point')
    return workspace


def fingerprint(workspace):
    digest = hashlib.sha256()
    for p in sorted(workspace.rglob('*')):
        if '.git' in p.relative_to(workspace).parts:
            continue
        if p.is_symlink() or (getattr(p.lstat(),'st_file_attributes',0) & 0x400):
            raise ValueError('Links are not supported in submissions')
        relative = p.relative_to(workspace).as_posix()
        digest.update(('D' if p.is_dir() else 'F').encode()+relative.encode()+b'\0')
        if p.is_file():
            digest.update(p.read_bytes())
    return digest.hexdigest()


def snapshot_workspace(workspace, expected_sha256=None):
    """Copy a submission into a shallow suite-owned path and verify it."""
    workspace = Path(workspace)
    before = expected_sha256 or fingerprint(workspace)
    snapshot_root = RUNTIME/'s'
    snapshot_root.mkdir(parents=True, exist_ok=True)
    if snapshot_root.is_symlink() or (getattr(snapshot_root.lstat(),'st_file_attributes',0) & 0x400):
        raise ValueError('Snapshot root must not be a link or junction')
    snapshot = snapshot_root/uuid.uuid4().hex
    if snapshot.resolve().parent != snapshot_root.resolve():
        raise ValueError('Snapshot escaped managed root')
    shutil.copytree(workspace, snapshot, ignore=shutil.ignore_patterns('.git'))
    if fingerprint(snapshot) != before:
        raise RuntimeError('Submission changed during snapshot')
    return {'path':snapshot,'sha256':before}


def restore(run, tid):
    definition = task(tid)
    rd = run_dir(run)
    rd.mkdir(parents=True, exist_ok=True)
    marker = rd/'owner.json'
    if not marker.exists():
        if (rd/'workspace').exists():
            raise ValueError('Refusing to adopt existing workspace')
        write(marker, {'owner':suite_config()['workspace_owner'],'run':run})
    workspace = checked_workspace(run)
    # Destructive deletion is restricted to this exact owned leaf; never SOURCE.
    if workspace.exists():
        def writable_retry(function, filename, error):
            target = Path(filename)
            if not target.resolve().is_relative_to(workspace.resolve()) or target.is_symlink():
                raise ValueError('Refusing permission change outside owned workspace')
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            function(filename)
        shutil.rmtree(workspace, onerror=writable_retry)
    workspace.mkdir()
    archive = command(['git','-C',str(SOURCE),'archive',definition['base_commit']]).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            target = (workspace/member.name).resolve()
            if not target.is_relative_to(workspace.resolve()) or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe base archive member')
        tar.extractall(workspace)
    # Establish a repository boundary before invoking Git.  Suite workspaces live
    # below the platform repository, so applying first can otherwise inherit the
    # parent repository context instead of the isolated submission context.
    command(['git','init','-q'], cwd=workspace)
    command(['git','apply','--check',str(PRIVATE/'tasks'/tid/'damage.patch')], cwd=workspace)
    command(['git','apply',str(PRIVATE/'tasks'/tid/'damage.patch')], cwd=workspace)
    # No upstream history or gold revision is exposed. Agent gets a broken snapshot.
    command(['git','-c','core.autocrlf=false','add','.'], cwd=workspace)
    command(['git','-c','user.name=Benchmark','-c','user.email=benchmark@localhost','-c','core.hooksPath=/dev/null','commit','-qm','Initial workspace'], cwd=workspace)
    digest = fingerprint(workspace)
    state_path = rd/'task-state.json'
    state = read(state_path) if state_path.exists() else {}
    expected = state.get('broken_sha256') if state.get('task_id') == tid else None
    if expected and expected != digest:
        raise RuntimeError('Broken state changed unexpectedly')
    write(state_path, {'task_id':tid,'base_commit':definition['base_commit'],'broken_sha256':digest,'workspace_state':'BROKEN'})
    return digest


def prepare(run, tid):
    rd = run_dir(run)
    if (rd/'run-state.json').exists() and read(rd/'run-state.json')['status'] == 'ACTIVE':
        raise ValueError('Finish active task before preparing another; reset preserves turn state')
    digest = restore(run, tid)
    definition = task(tid)
    state = {'run_id':run,'task_id':tid,'status':'ACTIVE','turns_completed':0,'max_user_turns':definition['max_user_turns'],'next_message':definition['initial_query'],'history':[]}
    if (rd/'run-state.json').exists():
        old = read(rd/'run-state.json')
        write(rd/'completed'/f'{uuid.uuid4().hex}.json',old)
    write(rd/'run-state.json',state)
    return {'task_id':tid,'workspace':str(checked_workspace(run)),'initial_query':definition['initial_query'],'broken_sha256':digest}


def image_id():
    return read(ROOT/'environment.lock.json')['image_id']


def image_tag(kind):
    return suite_config()['images'][kind]


def suite_slug():
    return re.sub(r'[^a-z0-9-]+','-',suite_config()['benchmark_id'].lower()).strip('-')[:24]


def docker_args(workspace, readonly=True):
    pythonpath = suite_config()['validation']['pythonpath']
    return ['docker','run','--rm','--network','none','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','128','--memory','512m','--cpus','1','--read-only','--tmpfs','/tmp:rw,nosuid,exec,size=128m','-e',f'PYTHONPATH={pythonpath}','--mount',f'type=bind,src={workspace},dst=/workspace'+(',readonly' if readonly else '')]


def grade(run):
    rd = run_dir(run)
    ts = read(rd/'task-state.json')
    tid = ts['task_id']
    definition = task(tid)
    workspace = checked_workspace(run)
    before = fingerprint(workspace)
    evaluation = rd/'evaluations'/uuid.uuid4().hex
    # Graders and conversation probes share the same shallow snapshot mechanism.
    # Deep repositories such as FastAPI otherwise exceed the Windows path limit.
    snapshot = snapshot_workspace(workspace, before)['path']
    def check(state):
        timeout_seconds = GRADER_TIMEOUT_SECONDS
        timeout_logs = []
        for attempt in range(1, GRADER_ATTEMPTS + 1):
            name = suite_slug()+'-grade-'+uuid.uuid4().hex
            args = docker_args(snapshot)+['--name',name,'--mount',f'type=bind,src={PRIVATE / "grader.py"},dst=/grader.py,readonly',image_id(),'python','-I','/grader.py',tid,state]
            try:
                result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout_seconds)
                output = result.stdout + result.stderr
                check_result = {
                    'passed':result.returncode == 0 and '"passed": true' in result.stdout,
                    'exit_code':result.returncode,
                    'infrastructure_error':result.returncode in (125,126,127),
                    'attempts':attempt,
                    'log':output,
                }
                if check_result['infrastructure_error'] and attempt < GRADER_ATTEMPTS:
                    timeout_logs.append(f"Grader infrastructure exit {result.returncode}, attempt {attempt}/{GRADER_ATTEMPTS}")
                    time.sleep(2)
                    continue
                if timeout_logs:
                    check_result['log'] = '\n'.join(timeout_logs+[check_result['log']])
                return check_result
            except subprocess.TimeoutExpired:
                try:
                    cleanup = subprocess.run(['docker','rm','-f',name], capture_output=True, timeout=30)
                except subprocess.TimeoutExpired:
                    return {'passed':False,'exit_code':124,'infrastructure_error':True,
                            'attempts':attempt,'log':'Grader cleanup timed out; submission remains ungraded'}
                if cleanup.returncode:
                    return {'passed':False,'exit_code':124,'infrastructure_error':True,
                            'attempts':attempt,'log':'Grader cleanup failed; submission remains ungraded'}
                timeout_logs.append(f'Grader timeout ({timeout_seconds}s), attempt {attempt}/{GRADER_ATTEMPTS}')
                if attempt < GRADER_ATTEMPTS:
                    time.sleep(2)
        return {
            'passed':False,
            'exit_code':124,
            'infrastructure_error':True,
            'attempts':GRADER_ATTEMPTS,
            'log':'\n'.join(timeout_logs),
        }
    with ThreadPoolExecutor(max_workers=4) as pool:
        states = dict(zip(definition['grader_priority'],pool.map(check,definition['grader_priority'])))
    result = {'task_id':tid,'submission_sha256':before,'passed':all(s['passed'] for s in states.values()),'infrastructure_error':any(s['infrastructure_error'] for s in states.values()),'states':states,'evaluation_id':evaluation.name}
    write(rd/'task-state.json',{**ts,'workspace_state':'BROKEN' if before == ts['broken_sha256'] else 'MODIFIED','last_submission_sha256':before})
    write(evaluation/'result.json',result)
    write(rd/'latest-grade.json',result)
    return result


def transition(state, result, definition):
    if state['status'] != 'ACTIVE':
        raise ValueError('Terminal task cannot accept another turn')
    if result['task_id'] != state['task_id']:
        raise ValueError('Grader task mismatch')
    if result.get('infrastructure_error'):
        raise RuntimeError('Infrastructure error: no turn consumed; retry after repair')
    priorities = definition['grader_priority']
    if set(result['states']) != set(priorities):
        raise ValueError('Incomplete grader result')
    if any(type(result['states'][s].get('passed')) is not bool for s in priorities):
        raise ValueError('Grader pass values must be booleans')
    passed = all(result['states'][s]['passed'] is True for s in priorities)
    turns = state['turns_completed']+1
    status = 'PASS' if passed else 'FAIL' if turns >= definition['max_user_turns'] else 'ACTIVE'
    failed_state = next((s for s in priorities if not result['states'][s]['passed']),None)
    message = definition['feedback_by_state'][failed_state] if status == 'ACTIVE' else None
    return {**state,'turns_completed':turns,'status':status,'next_message':message,'history':state['history']+[{'turn':turns,'passed':passed,'failed_state':failed_state,'evaluation_id':result.get('evaluation_id'),'feedback':message}]}


def end_turn(run, reset_terminal=True):
    rd = run_dir(run)
    state = read(rd/'run-state.json')
    if state['status'] != 'ACTIVE':
        raise ValueError('Task already terminal')
    result = grade(run)
    updated = transition(state,result,task(state['task_id']))
    write(rd/'run-state.json',updated)
    if updated['status'] != 'ACTIVE' and reset_terminal:
        restore(run,state['task_id'])
    return {'status':updated['status'],'turns_completed':updated['turns_completed'],'next_message':updated['next_message']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action',choices=['prepare','reset','grader','state','end-turn','agent-command','lock-environment','lock-agent-environment'])
    parser.add_argument('--run',default='click-local')
    parser.add_argument('--task')
    args = parser.parse_args()
    if args.action == 'lock-agent-environment':
        result = read(ROOT/'environment.lock.json')
        tag = image_tag('agent')
        result['agent_image_id'] = command(['docker','image','inspect',tag,'--format','{{.Id}}']).stdout.decode().strip()
        result['agent_os_packages'] = command(['docker','run','--rm',tag,'dpkg-query','--show','git','ripgrep','less']).stdout.decode().splitlines()
        cli_tag = image_tag('claude_cli')
        result['claude_cli_image_id'] = command(['docker','image','inspect',cli_tag,'--format','{{.Id}}']).stdout.decode().strip()
        result['claude_cli_version'] = command(['docker','run','--rm',cli_tag,'claude','--version']).stdout.decode().strip()
        write(ROOT/'environment.lock.json',result)
    elif args.action == 'lock-environment':
        tag = image_tag('grader')
        result = {'image_id':command(['docker','image','inspect',tag,'--format','{{.Id}}']).stdout.decode().strip(), 'python':command(['docker','run','--rm',tag,'python','--version']).stdout.decode().strip(),'packages':command(['docker','run','--rm',tag,'pip','freeze']).stdout.decode().splitlines()}
        write(ROOT/'environment.lock.json',result)
    else:
        with locked(args.run) as rd:
            if args.action == 'prepare':
                result = prepare(args.run,args.task)
            elif args.action == 'reset':
                tid = read(rd/'task-state.json')['task_id']
                if args.task and args.task != tid:
                    raise ValueError('Reset cannot switch task; use prepare after terminal')
                result = {'broken_sha256':restore(args.run,tid)}
            elif args.action == 'grader':
                result = grade(args.run)
            elif args.action == 'end-turn':
                result = end_turn(args.run)
            elif args.action == 'state':
                ts = read(rd/'task-state.json')
                digest = fingerprint(checked_workspace(args.run))
                result = {'task_state':{**ts,'workspace_state':'BROKEN' if digest == ts['broken_sha256'] else 'MODIFIED'},'run_state':read(rd/'run-state.json'),'current_sha256':digest}
            else:
                result = {'argv':docker_args(checked_workspace(args.run),False)+['-it',read(ROOT/'environment.lock.json')['agent_image_id'],'bash'],'warning':'Agent must execute inside this container, not host shell. Only workspace is mounted. Stop Agent before end-turn/reset.'}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if args.action == 'grader':
        return 2 if result['infrastructure_error'] else 0 if result['passed'] else 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
