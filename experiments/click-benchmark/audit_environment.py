"""Final tool-container smoke test and asset checksum record, no Agent calls."""
import hashlib
from pathlib import Path
import subprocess
import sys
import uuid
import bench


def main():
    report = bench.read(bench.ROOT/'reports/validation.json')
    tasks = bench.read(bench.PRIVATE/'manifest.json')['tasks']
    assert report['passed'] is True and len(report['tasks']) == len(tasks)
    first_task = tasks[0]
    run = 'ready-'+uuid.uuid4().hex[:8]
    with bench.locked(run):
        prepared = bench.prepare(run,first_task)
        workspace = bench.checked_workspace(run)
        lock = bench.read(bench.ROOT/'environment.lock.json')
        probe = '''from pathlib import Path
import subprocess
assert not Path('/grader.py').exists()
assert not Path('/private').exists()
assert not Path('/var/run/docker.sock').exists()
subprocess.run(['git','status','--porcelain'],check=True)
assert subprocess.check_output(['git','rev-list','--count','HEAD']).strip() == b'1'
subprocess.run(['rg','--version'],check=True)
Path('/workspace/agent-smoke.txt').write_text('temporary tool edit')
print('agent isolation and write smoke passed')
'''
        result = bench.command(bench.docker_args(workspace,False)+[lock['agent_image_id'],'python','-c',probe])
        bench.restore(run,first_task)
        assert bench.fingerprint(workspace) == prepared['broken_sha256']
        assert not (workspace/'agent-smoke.txt').exists()
        grade = bench.grade(run)
        assert not grade['passed'] and not grade['infrastructure_error']
        tests = subprocess.run([sys.executable,'-m','unittest','discover','-s',str(bench.ROOT/'tests'),'-v'],capture_output=True,text=True)
        assert tests.returncode == 0, tests.stderr
        checksums = {}
        for p in sorted(bench.PRIVATE.rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts:
                checksums[p.relative_to(bench.ROOT).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
        bench.write(bench.ROOT/'reports/environment-audit.json',{'passed':True,'ready_run':run,'environment':lock,'agent_probe_output':result.stdout.decode(),'agent_write_then_reset':True,'ready_workspace_broken':True,'controller_tests':tests.stderr,'asset_sha256':checksums})
    print('Ready run: '+run)


if __name__ == '__main__':
    main()
