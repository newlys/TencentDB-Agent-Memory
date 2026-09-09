"""Read-only suite contract validation. Does not call an Agent or an LLM."""
import argparse
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile


def fail(message):
    raise ValueError(message)


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    root = args.suite.resolve()
    private = root/'private'
    suite = load(private/'suite.json')
    manifest = load(private/'manifest.json')
    protocol = load(private/'protocol.json')
    source_raw = Path(suite['repository']['source_path'])
    source = source_raw.resolve() if source_raw.is_absolute() else (root/source_raw).resolve()
    required_suite = {'schema_version','benchmark_id','workspace_owner','repository','images','validation','agent','extensions'}
    missing = required_suite-set(suite)
    if missing: fail(f'suite.json missing: {sorted(missing)}')
    for section, fields in {
        'repository': {'source_path','base_commit'},
        'images': {'grader','agent','no_skill_proxy','claude_cli'},
        'validation': {'agent_visible_probe_file','pythonpath','upstream_command'},
        'agent': {'model','language'},
        'extensions': {'ours'},
    }.items():
        absent = fields-set(suite[section])
        if absent: fail(f'suite.json {section} missing: {sorted(absent)}')
    base = suite['repository']['base_commit']
    if manifest['base_commit'] != base: fail('manifest and suite base_commit differ')
    resolved = subprocess.run(['git','-C',str(source),'rev-parse',f'{base}^{{commit}}'],capture_output=True,text=True)
    if resolved.returncode or resolved.stdout.strip() != base: fail('base_commit is absent or not a full immutable commit')
    tasks = manifest['tasks']
    if not tasks or len(tasks) != len(set(tasks)): fail('task list must be non-empty and unique')
    event_ids = [e['event_id'] for e in manifest['events']]
    if len(event_ids) != len(set(event_ids)): fail('event_id values must be unique')
    if [e['order'] for e in manifest['events']] != sorted(e['order'] for e in manifest['events']):
        fail('events must be in ascending order')
    task_events = [e['event_id'] for e in manifest['events'] if e['event_type'] in ('sop','non-sop')]
    if task_events != tasks: fail('task events must exactly match manifest task order')
    if protocol['language']['user_visible_messages'] != suite['agent']['language']:
        fail('protocol and suite language differ')
    required_task = {'task_id','base_commit','initial_query','max_user_turns','grader_priority','feedback_by_state','success_condition'}
    with tempfile.TemporaryDirectory(prefix='benchmark-import-check-') as temp:
        checkout = Path(temp)/'base'
        checkout.mkdir()
        archive = subprocess.run(['git','-C',str(source),'archive',base],check=True,capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            for member in bundle.getmembers():
                target = (checkout/member.name).resolve()
                if not target.is_relative_to(checkout.resolve()) or not (member.isfile() or member.isdir()):
                    fail('unsafe repository archive member')
            bundle.extractall(checkout)
        for task_id in tasks:
            directory = private/'tasks'/task_id
            definition = load(directory/'task.json')
            absent = required_task-set(definition)
            if absent: fail(f'{task_id}: task.json missing {sorted(absent)}')
            if definition['task_id'] != task_id or definition['base_commit'] != base:
                fail(f'{task_id}: identity/base mismatch')
            if not isinstance(definition['max_user_turns'],int) or definition['max_user_turns'] < 1:
                fail(f'{task_id}: invalid max_user_turns')
            priority = definition['grader_priority']
            if not priority or len(priority) != len(set(priority)):
                fail(f'{task_id}: invalid grader_priority')
            if set(definition['feedback_by_state']) != set(priority):
                fail(f'{task_id}: feedback states must exactly equal grader_priority')
            for name in ('damage.patch','gold.patch'):
                patch = directory/name
                if not patch.is_file() or patch.stat().st_size == 0: fail(f'{task_id}: missing {name}')
            subprocess.run(['git','apply','--check',str(directory/'damage.patch')],cwd=checkout,check=True,capture_output=True)
            subprocess.run(['git','apply',str(directory/'damage.patch')],cwd=checkout,check=True,capture_output=True)
            subprocess.run(['git','apply','--check',str(directory/'gold.patch')],cwd=checkout,check=True,capture_output=True)
            subprocess.run(['git','apply','--check','--reverse',str(directory/'damage.patch')],cwd=checkout,check=True,capture_output=True)
            subprocess.run(['git','apply','--reverse',str(directory/'damage.patch')],cwd=checkout,check=True,capture_output=True)
    grader = private/'grader.py'
    if not grader.is_file(): fail('private/grader.py is missing')
    extension_status = {
        name: value.get('status')
        for name, value in suite.get('extensions', {}).items()
        if isinstance(value, dict)
    }
    connected = [name for name, status in extension_status.items() if status == 'connected']
    result = {
        'passed': True,
        'suite': suite['benchmark_id'],
        'suite_root': str(root),
        'source': str(source),
        'base_commit': base,
        'tasks': len(tasks),
        'events': len(manifest['events']),
        'runnable_variants': ['no-skill','baseline',*connected],
        'reserved_variants': {
            name: status for name, status in extension_status.items()
            if status != 'connected'
        },
        'checks': ['schema','immutable_commit','event_order','task_contract','damage_patch','gold_patch','grader_presence','language_policy'],
        'paid_calls': False,
    }
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'passed':False,'error':str(error)},ensure_ascii=False,indent=2),file=sys.stderr)
        raise
