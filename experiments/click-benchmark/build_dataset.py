"""Materialize the reviewed blueprint. Run with bundled Python (openpyxl read only)."""
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import openpyxl

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent.parent
BASE = '36baa15ff831b939a22bc527cd76ce653ef6f66d'
CORE = 'src/click/core.py'


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    workbook = openpyxl.load_workbook(PROJECT / 'click_benchmark_blueprint_v0.xlsx', data_only=True)
    rows = list(workbook['Tasks'].values)
    candidates = [dict(zip(rows[0], row)) for row in rows[1:]]
    original = subprocess.check_output(['git', '-C', str(PROJECT / 'repo/click'), 'show', f'{BASE}:{CORE}']).decode()
    mutations = {
        'SK01-T01': ('        if value is UNSET:\n            envvar_value = self.value_from_envvar(ctx)', '        if value is UNSET or self.nargs == -1:\n            envvar_value = self.value_from_envvar(ctx)'),
        'SK01-T02': ('        value, source = super().consume_value(ctx, opts)\n', '        value, source = super().consume_value(ctx, opts)\n\n        if (\n            self.is_bool_flag\n            and not self.secondary_opts\n            and self._default_explicit\n            and source == ParameterSource.COMMANDLINE\n            and value is False\n        ):\n            value = self.get_default(ctx)\n'),
        'SK02-T01': ('            multi_rv = self.type.split_envvar_value(rv)', '            multi_rv = [rv]'),
        'SK05-T01': ('        if self._depth == 0:\n            exit_result', '        if self._depth == 0 and exc_type is None:\n            exit_result'),
        'SK06-T01': ('        return self.commands.get(cmd_name)', '        return self.commands.get(cmd_name.replace("-", "_"))'),
        'NS-01': ("            \" 'args' will contain remaining unparsed tokens.\",", "            \" 'params' will contain remaining unparsed tokens.\","),
    }
    queries = {
        'SK01-T01': 'When FOO is set, command-line values supplied to an optional variadic argument are ignored. For example, with FOO="env-a env-b", running the command with a b should return ("a", "b"), but does not. Please fix this while keeping environment-only and configured fallback behavior working.',
        'SK01-T02': 'I use separate --with-x and --without-x flags that control the same setting. With the enabling flag configured as the default, --without-x unexpectedly leaves the setting enabled. Please make explicit flags work, preserving no-argument behavior and the order of repeated flags.',
        'SK02-T01': 'An option accepting repeated values reads FOO="red blue" as one item instead of two. Options accepting pairs also mishandle environment input. Please restore counts, order and pair grouping without changing scalar options or command-line input.',
        'SK05-T01': 'Resources registered by a command are released after normal completion, but remain open when the command reports an error or is aborted. Please make cleanup reliable on failure and early exit too, without changing exit codes, error messages or normal resource behavior.',
        'SK06-T01': 'A subcommand registered as build-assets cannot be invoked under that name and is missing from help. Single-word commands still work. Please restore help and invocation for registered names, including aliases and nested groups, without accepting unknown names.',
        'NS-01': 'Reading Context.protected_args emits a deprecation warning saying that params will contain remaining unparsed tokens. The documented replacement is args. Correct that warning and add a focused regression test; do not change parsing behavior.',
    }
    reasons = {
        'SK01-T01': 'Retained source-precedence design; scope mutation to nargs=-1 and preserve UNSET fallback. Add default_map and source provenance checks.',
        'SK01-T02': 'Adjusted after real validation: adding a default winner in handle_parse_result did NOT break the task because shared-destination options both consume the same CLI source. Use Option.consume_value to replace explicit False with its explicit default while retaining source metadata; downstream equal-source arbitration exposes the error. Separate flags share a destination; slash-paired options remain regression controls.',
        'SK02-T01': 'Adjusted raw-string bypass to a singleton list. Raw string is rejected before observable collection loss; singleton keeps a valid container while losing token count and composite grouping. Include type-specific path separator.',
        'SK05-T01': 'Selected Context.__exit__ exceptional unwind gate. Context.exit already calls close explicitly; retain it as regression rather than claiming it is broken. Exceptions, Abort and direct Exit exercise the fault.',
        'SK06-T01': 'Selected inconsistent hyphen-to-underscore lookup normalization. Applies to any registered hyphenated name, not one hard-coded command. Listing and lookup checked separately.',
        'NS-01': 'Retained exact protected_args warning correction (args -> params). Query identifies the concrete user-visible contract; no reusable procedural skill expected.',
    }
    feedback = {
        'SK01-T01': ['With FOO set, the explicit a b values still do not win.', 'Please also check an absent or empty FOO and configured fallback values.', 'The reported origin of the resulting argument value is still incorrect.', 'A normal single-value argument no longer behaves correctly.'],
        'SK01-T02': ['Using --without-x still leaves the setting enabled.', 'Repeated flags or the alternative default configuration still return the wrong setting.', 'The reported origin does not match the value explicitly supplied on the command line.', 'The no-argument default or a normal slash-paired flag has changed.'],
        'SK02-T01': ['FOO="red blue" still does not produce two repeated values.', 'Pairs, repeated pairs or path lists still lose their expected grouping or order.', 'Malformed numeric environment input must still be rejected as a usage error.', 'Scalar, empty-environment or command-line values no longer behave as before.'],
        'SK05-T01': ['After the command reports an error, its registered resources are still not all released exactly once.', 'Abort or early-exit cases still leave resources open or change the exit result.', 'Resources no longer receive the original exception, or cleanup error handling has changed.', 'Normal or nested execution no longer cleans up once in reverse registration order.'],
        'SK06-T01': ['The registered build-assets command still cannot be invoked normally.', 'Help, aliases or nested hyphenated commands still disagree with the registered names.', 'An unregistered spelling must still fail as an unknown command.', 'Ordinary commands, hidden commands or configured case normalization no longer work as before.'],
        'NS-01': ['The warning still names the wrong replacement for remaining unparsed tokens.', 'The warning category or reported caller location has changed.', 'Repeated accesses no longer warn consistently.', 'The stored arguments or normal parsing behavior has changed.'],
    }
    tasks = []
    for candidate in candidates:
        tid = candidate['Task ID']
        old, new = mutations[tid]
        assert original.count(old) == 1, tid
        broken = original.replace(old, new)
        directory = ROOT / 'private/tasks' / tid
        directory.mkdir(parents=True, exist_ok=True)
        for name, before, after in [('damage', original, broken), ('gold', broken, original)]:
            patch = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile='a/'+CORE, tofile='b/'+CORE))
            if tid == 'NS-01' and name == 'gold':
                regression = '''import inspect
import warnings

import click


def test_protected_args_warning_contract():
    ctx = click.Context(click.Command("cli"))
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        line = inspect.currentframe().f_lineno + 1
        assert ctx.protected_args == []
    assert len(captured) == 1
    warning = captured[0]
    assert warning.category is DeprecationWarning
    assert str(warning.message) == (
        "'protected_args' is deprecated and will be removed in Click 9.0."
        " 'args' will contain remaining unparsed tokens."
    )
    assert warning.filename == __file__
    assert warning.lineno == line
'''
                patch += ''.join(difflib.unified_diff([], regression.splitlines(True), fromfile='/dev/null', tofile='b/tests/test_protected_args_warning_contract.py'))
            (directory / f'{name}.patch').write_text(patch, encoding='utf-8', newline='\n')
        states = ['primary', 'edge', 'exception', 'regression']
        task = dict(task_id=tid, repo='pallets/click', session='S01', session_order=candidate['Session Order'], task_type=candidate['Type'], skill_family=candidate['Skill ID'], skill_role=candidate['Role'], base_commit=BASE, damage_location=CORE, damage_type='small-source-mutation', damage_patch='damage.patch', gold_patch='gold.patch', initial_query=queries[tid], failure_symptom=candidate['Failure Symptom'], expected_sop=candidate['Expected SOP'], expected_skill_action=candidate['Expected Skill Action'], expected_retrieval=([] if candidate['Role'] in ('acquisition', 'non-sop') else [candidate['Skill ID']]), max_user_turns=3 if tid == 'NS-01' else 5, hidden_subgraders={s: f'../../grader.py {tid} {s}' for s in states}, grader_priority=states, feedback_by_state=dict(zip(states, feedback[tid])), success_condition='All subgraders pass; infrastructure errors never count as PASS.', reset_command=f'python bench.py reset --run RUN_ID --task {tid}', review_reason=reasons[tid], pilot_status='NOT_RUN', source_candidate=candidate)
        save(directory/'task.json', task)
        tasks.append(tid)
    noise = [dict(event_id='N-S01-01', session='S01', order=2, event_type='noise', after_task='SK01-T01', user_message='先别改别的，刚才那个问题是不是只在同时设置环境变量时出现？', expected_boundary='continuation / no new task'), dict(event_id='N-S01-02', session='S01', order=5, event_type='noise', after_task='SK01-T02', user_message='嗯，保持命令行测试方式就行。', expected_boundary='continuation / no new task')]
    events = [dict(event_id=t['Task ID'], order=t['Session Order'], event_type=t['Type']) for t in candidates] + noise
    save(ROOT/'private/manifest.json', dict(schema_version=1, dataset='click-v1-candidate', base_commit=BASE, tasks=tasks, events=sorted(events, key=lambda e:e['order']), blueprint_sha256=hashlib.sha256((PROJECT/'click_benchmark_blueprint_v0.xlsx').read_bytes()).hexdigest(), frozen=False, limitations=['Difficulty and cross-repo reuse require later pilot and other repositories.']))
    save(ROOT/'private/blueprint_snapshot.json', {s.title:list(s.values) for s in workbook})


if __name__ == '__main__':
    main()
