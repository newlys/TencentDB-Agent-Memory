"""No Agent or Skill calls. Reproducibility checks and simulated control flow."""
import itertools
import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid
import bench


def validation_progress(report, phase, task_id=None, total=None):
    report['progress'] = {
        'phase':phase,
        'task_id':task_id,
        'completed_tasks':len(report.get('tasks', {})),
        'total_tasks':total,
        'updated_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    bench.write(bench.ROOT/'reports/validation.json', report)


def simulate(tid):
    definition = bench.task(tid)
    priority = definition['grader_priority']
    initial = {'task_id':tid,'status':'ACTIVE','turns_completed':0,'history':[]}
    count = 0
    for bits in itertools.product((False,True),repeat=len(priority)):
        result = {'task_id':tid,'states':{s:{'passed':b} for s,b in zip(priority,bits)}}
        updated = bench.transition(initial,result,definition)
        assert updated['status'] == ('PASS' if all(bits) else 'ACTIVE')
        expected = next((s for s,b in zip(priority,bits) if not b),None)
        assert updated['next_message'] == (definition['feedback_by_state'][expected] if expected else None)
        at_limit = {**initial,'turns_completed':definition['max_user_turns']-1}
        assert bench.transition(at_limit,result,definition)['status'] == ('PASS' if all(bits) else 'FAIL')
        count += 2
    for terminal in ('PASS','FAIL'):
        try:
            bench.transition({**initial,'status':terminal},result,definition)
            raise AssertionError('Terminal accepted another turn')
        except ValueError:
            pass
    try:
        bench.transition(initial,{**result,'infrastructure_error':True},definition)
        raise AssertionError('Infrastructure failure consumed turn')
    except RuntimeError:
        assert initial['turns_completed'] == 0
    return {'matrix_cases':count,'terminal_rejection':True,'infra_no_turn':True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--finish-existing',action='store_true',help='Resume final environment checks after every task check has passed.')
    args = parser.parse_args()
    manifest_tasks = bench.read(bench.PRIVATE/'manifest.json')['tasks']
    if args.finish_existing:
        report = bench.read(bench.ROOT/'reports/validation.json')
        assert set(report['tasks']).issubset(set(manifest_tasks))
        assert all(t['gold'] == 'PASS' and t['broken_sha256'] == t['reset_sha256'] for t in report['tasks'].values())
        run = report['run']
        tasks_to_validate = [tid for tid in manifest_tasks if tid not in report['tasks']]
    else:
        run = 'verify-'+uuid.uuid4().hex[:8]
        report = {'run':run,'base_commit':bench.read(bench.PRIVATE/'manifest.json')['base_commit'],'environment':bench.read(bench.ROOT/'environment.lock.json'),'tasks':{},'formal_agent_experiments':False}
        tasks_to_validate = manifest_tasks
    start = time.time()
    for tid in tasks_to_validate:
        validation_progress(report, 'VALIDATING_TASK', tid, len(manifest_tasks))
        print('Validating '+tid,flush=True)
        with bench.locked(run):
            prepared = bench.prepare(run,tid)
            workspace = bench.checked_workspace(run)
            broken = bench.grade(run)
            assert not broken['infrastructure_error'] and not broken['passed'], broken
            assert broken['states']['primary']['passed'] is False, broken
            # A real no-op Agent turn must select pre-defined feedback.
            state = bench.end_turn(run)
            if bench.task(tid)['max_user_turns'] == 1:
                assert state['status'] == 'FAIL' and state['turns_completed'] == 1
                # A one-turn task is terminal after the no-op probe. Re-prepare
                # the same deterministic Broken State before testing Gold.
                prepared_again = bench.prepare(run,tid)
                assert prepared_again['broken_sha256'] == prepared['broken_sha256']
                workspace = bench.checked_workspace(run)
            else:
                assert state['status'] == 'ACTIVE' and state['turns_completed'] == 1
            bench.command(['git','apply',str(bench.PRIVATE/'tasks'/tid/'gold.patch')],cwd=workspace)
            gold = bench.grade(run)
            assert gold['passed'], gold
            gold_fingerprint = bench.fingerprint(workspace)
            # Terminal completion automatically resets but preserves result/history.
            final = bench.end_turn(run)
            expected_turns = 1 if bench.task(tid)['max_user_turns'] == 1 else 2
            assert final['status'] == 'PASS' and final['turns_completed'] == expected_turns
            assert bench.fingerprint(workspace) == prepared['broken_sha256']
            # Deliberately leave tracked, untracked and ignored Agent modifications.
            visible_probe = bench.suite_config()['validation']['agent_visible_probe_file']
            with (workspace/visible_probe).open('a',encoding='utf-8') as f:
                f.write('\n# simulated abandoned edit\n')
            (workspace/'agent-leftover.txt').write_text('leftover',encoding='utf-8')
            (workspace/'__pycache__').mkdir(exist_ok=True)
            (workspace/'__pycache__/leftover.pyc').write_bytes(b'leftover')
            bench.restore(run,tid)
            assert bench.fingerprint(workspace) == prepared['broken_sha256']
            assert not (workspace/'agent-leftover.txt').exists()
            reset = bench.grade(run)
            assert not reset['passed'] and not reset['infrastructure_error']
            assert {s:v['passed'] for s,v in broken['states'].items()} == {s:v['passed'] for s,v in reset['states'].items()}
            report['tasks'][tid] = {'broken':'FAIL','gold':'PASS','reset':'FAIL','broken_sha256':prepared['broken_sha256'],'reset_sha256':bench.fingerprint(workspace),'gold_sha256':gold_fingerprint,'broken_states':{s:v['passed'] for s,v in broken['states'].items()},'gold_states':{s:v['passed'] for s,v in gold['states'].items()},'evaluation_ids':[broken['evaluation_id'],gold['evaluation_id'],reset['evaluation_id']],'no_residue':True,'real_controller_noop_then_gold':final,'state_machine':simulate(tid)}
            bench.write(bench.ROOT/'reports/validation.json',report)
        print(tid+' Broken FAIL -> Gold PASS -> Reset FAIL',flush=True)
    # Gold is the original source for all tasks; run complete upstream suite once.
    assert len({r['gold_sha256'] for r in report['tasks'].values()}) == 1
    validation_progress(report, 'UPSTREAM_SUITE', None, len(manifest_tasks))
    with bench.locked(run):
        workspace = bench.checked_workspace(run)
        last_task = bench.read(bench.PRIVATE/'manifest.json')['tasks'][-1]
        bench.command(['git','apply',str(bench.PRIVATE/'tasks'/last_task/'gold.patch')],cwd=workspace)
        upstream = bench.suite_config()['validation']['upstream_command']
        result = subprocess.run(bench.docker_args(workspace)+[bench.read(bench.ROOT/'environment.lock.json')['agent_image_id'],*upstream],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
        (bench.ROOT/'reports/upstream-tests.txt').write_text(result.stdout+result.stderr,encoding='utf-8')
        report['upstream_suite'] = {'exit_code':result.returncode,'log':'upstream-tests.txt','image_id':bench.read(bench.ROOT/'environment.lock.json')['agent_image_id']}
        bench.restore(run,last_task)
    assert result.returncode == 0, result.stdout[-4000:]+result.stderr
    # Check Agent-only mount has neither evaluator nor original clone/history.
    visible_probe = bench.suite_config()['validation']['agent_visible_probe_file']
    probe = f'from pathlib import Path; assert not Path("/grader.py").exists(); assert not Path("/private").exists(); assert not Path("/var/run/docker.sock").exists(); assert Path("/workspace/{visible_probe}").exists(); print("isolated")'
    isolated = bench.command(bench.docker_args(workspace)+[bench.image_id(),'python','-c',probe]).stdout.decode()
    assert 'isolated' in isolated
    report['agent_mount_isolation'] = True
    report['task_switching'] = f'All {len(report["tasks"])} tasks initialized sequentially in the same run workspace; each old snapshot removed.'
    for invalid in ('../outside','D:\\','..','a/b'):
        try:
            bench.run_dir(invalid)
            raise AssertionError('Unsafe ID accepted')
        except ValueError:
            pass
    report['unsafe_run_ids_rejected'] = True
    report['seconds'] = round(time.time()-start,2)
    report['passed'] = True
    report['progress'] = {'phase':'COMPLETED','task_id':None,'completed_tasks':len(report['tasks']),'total_tasks':len(manifest_tasks),'updated_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    bench.write(bench.ROOT/'reports/validation.json',report)
    print(json.dumps({'passed':True,'run':run,'tasks':len(report['tasks'])}),flush=True)


if __name__ == '__main__':
    main()
