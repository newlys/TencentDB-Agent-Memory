"""Real Claude Code continuous No-Skill pilot. Host controller only.

No gold is applied. Each container can see only its workspace and CLI session.
"""
import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import secrets
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import bench

LF_DEFAULT = Path(os.environ.get('BENCHMARK_LANGFUSE_CONFIG', 'langfuse.private.json'))
TOOLS = 'Read,Edit,Write,Bash,Glob,Grep'
SYSTEM_COMMON = ('Work only in /workspace. This is a sequence of independent coding requests in one conversation. '
          'Before each new request inspect the current files; the workspace may have been refreshed externally. '
          'Fix and test the current request. Do not use subagents, external source downloads or Claude Code durable auto memory. '
          'For a status-only or acknowledgement message, respond without changing files. '
          'Do not commit changes. Python imports the working source via PYTHONPATH.')
SYSTEM_NO_SKILL = SYSTEM_COMMON + ' Do not use skills or external memory.'
SYSTEM_BASELINE = SYSTEM_COMMON + ' Use only capabilities injected by the configured native TencentDB baseline when relevant.'
OURS_V1_PROFILES = {
    'boundary_profile': 'query_only_l15',
    'extraction_profile': 'task_scoped_v1',
    'retrieval_profile': 'task_search_view_v1',
}
OURS_V2_PROFILES = {
    'boundary_profile': 'query_only_l15',
    'extraction_profile': 'task_scoped_sop_v2',
    'retrieval_profile': 'task_scoped_skill_injection_v2',
}
OURS_V3_PROFILES = {
    'boundary_profile': 'query_only_l15',
    'extraction_profile': 'task_scoped_sop_v2',
    'retrieval_profile': 'task_scoped_skill_consumption_v3',
}
OURS_PROFILES = OURS_V1_PROFILES
OURS_V2_SEARCH_TOP_K = 3
OURS_V2_SKILL_BLOCK_CHAR_BUDGET = 4800
OURS_V2_METHOD_REVISION = 'task-sop-mandatory-view-r1'
OURS_V3_METHOD_REVISION = 'task-skill-consumption-controller-r6'
SKILL_GATE_HELPER = Path(__file__).with_name('skill_gate.mjs')
OURS_V3_SELECTOR_MODEL = os.environ.get('BENCHMARK_SELECTOR_MODEL', 'deepseek-v4-flash')
OPENAI_COMPATIBLE_BASE_URL = os.environ.get(
    'BENCHMARK_OPENAI_BASE_URL', 'https://api.deepseek.com'
).rstrip('/')
OURS_V3_SKILL_CONTEXT_CHAR_BUDGET = 3200
OURS_RETRIEVAL_BLOCK = '''
<task_skill_retrieval>
This is the first request of a newly detected task.

Before exploring or editing the repository:
1. Call skill_search exactly once using the task-only query below.
2. Inspect the returned name, description, snippet, and score.
3. If a candidate is clearly relevant to the underlying procedure, call
   skill_view exactly once for the best candidate and follow it.
4. If no candidate is relevant, continue without loading a skill.

Task-only search query:
{task_query}

Do not repeat search or view after they have already succeeded for this task.
</task_skill_retrieval>'''
OURS_V1_EXTRACTION_GUIDANCE = '''This archive contains one predicted task.

Task query:
{task_query}

Extract only reusable procedure demonstrated by this task.
Prefer a mechanism/invariant-based skill name in lower-kebab-case.
Do not name the skill after a repository, framework, file, or concrete task
unless the procedure is genuinely repository-specific.
When a framework-specific example demonstrates a broader invariant, name the
broader invariant and keep the framework only as a validation example. A
repository or framework name in the title is evidence that this abstraction
check must be repeated before creating the skill.
Before creating a new skill, inspect existing skills and update a semantically
matching procedure even when the repository differs.
A localized one-off correction with no reusable procedure should produce
Nothing to save.'''
OURS_V2_EXTRACTION_GUIDANCE = '''This archive contains one predicted task.

Task query:
{task_query}

Extract only executable and reusable procedures demonstrated by this task.
A skill must describe a workflow that a future agent can apply to another task.

Include relevant applicability, preconditions, constraints, ordered actions,
decision points, expected outputs, and validation or rollback steps.
Use the narrowest reusable applicability category, not a concrete source pair,
API surface, framework, file, or incident. When a later task has the same
intended outcome and core workflow but proves a new implementation surface,
UPDATE the existing Skill when both cases fit one honest applicability boundary.
A successful implementation is reusable only when it demonstrates a transferable
control-flow, data-flow, state-management, validation, compatibility,
resource-lifecycle, or transformation procedure with ordered decisions.

Repository background and user preferences are not standalone skills.
Include them only when they directly constrain when or how the procedure runs.

Prefer a mechanism- or workflow-based name in lower-kebab-case.
The skill name and core identity must be determined by intended outcome,
applicability, and core workflow. Repository, task, file, and concrete incident
names are evidence only. A framework or library may constrain applicability, but
the Skill name stays mechanism-based whenever the workflow can be stated without
that proper noun; otherwise abstract it.
Concrete repositories and tasks may appear only as examples or evidence.

Before creating a skill, inspect existing skills. Update an existing skill when
the intended outcome, applicability, and core workflow are substantially the
same, even when the repository or implementation differs.

A localized correction without a reusable procedure should produce
Nothing to save.'''
OURS_EXTRACTION_GUIDANCE = OURS_V1_EXTRACTION_GUIDANCE
BOUNDARY_RUNNER = Path(__file__).resolve().parents[1] / 'query-boundary-l15-v1' / 'live-query-boundary.ts'
SYSTEM = SYSTEM_NO_SKILL


def is_ours_variant(value):
    return value in ('ours', 'ours_v2', 'ours_v3')

def calculate_metrics(state):
    scored = [t for t in state['turns'] if t.get('metric_scope') != 'boundary_infrastructure']
    boundary = [t for t in state['turns'] if t.get('metric_scope') == 'boundary_infrastructure']
    def totals(turns):
        return {
            'agent_internal_turns': sum(t.get('agent_internal_turns') or 0 for t in turns),
            'model_calls': sum(t.get('model_calls') or 0 for t in turns),
            'tool_calls': sum(t.get('tool_calls') or 0 for t in turns),
            'agent_elapsed_seconds': round(sum(t.get('agent_elapsed_seconds') or 0 for t in turns),3),
            'usage': {
                key: sum(t.get('usage', {}).get(key, 0) or 0 for t in turns)
                for key in ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')
            },
        }
    metrics = {
        'user_turns': sum(t['kind'] in ('initial', 'oracle', 'initial_interrupted', 'oracle_interrupted') for t in scored),
        **totals(scored),
        'boundary_infrastructure': totals(boundary),
    }
    ours = state.get('ours') or {}
    boundary_usage = (ours.get('boundary_totals') or {}).get('usage') or {}
    extraction_usage = (ours.get('extraction_totals') or {}).get('usage') or {}
    retrievals = ours.get('retrievals') or []
    retrieval_usage = {
        key: sum(int((item.get('usage') or {}).get(key, 0) or 0) for item in retrievals)
        for key in ('input', 'output', 'total')
    }
    metrics['cost_breakdown'] = {
        'agent': metrics['usage'],
        'boundary': boundary_usage,
        'extraction': extraction_usage,
        # V1 search/view calls execute inside the Agent and are already included
        # in Agent usage. V2 retrieval is a native non-LLM search, so its token
        # usage is explicitly zero while latency and injected chars stay visible.
        'retrieval': retrieval_usage,
        'retrieval_tool_calls': sum(
            len((turn.get('skill_usage') or {}).get('search_calls') or []) +
            len((turn.get('skill_usage') or {}).get('view_calls') or []) +
            int((turn.get('task_skill_gate') or {}).get('gate_call_count') or 0)
            for turn in scored
        ),
        'retrieval_instruction_chars': sum(
            (turn.get('retrieval_instruction_chars') or 0) +
            (turn.get('task_skill_block_chars') or 0)
            for turn in scored
        ),
    }
    metrics['total_llm_tokens'] = (
        sum(metrics['usage'].values()) +
        int(boundary_usage.get('total', 0) or 0) +
        int(extraction_usage.get('total', 0) or 0) +
        int(retrieval_usage.get('total', 0) or 0)
    )
    if state.get('variant') == 'ours_v3':
        gates = [
            item['task_skill_gate'] for item in retrievals
            if isinstance(item.get('task_skill_gate'), dict)
        ]
        statuses = [gate.get('consumption_status') for gate in gates]
        metrics['skill_consumption_funnel'] = {
            'task_retrievals': len(gates),
            'tasks_with_candidates': sum(gate.get('gate_required') is True for gate in gates),
            'explicit_gate_decisions': sum(
                status not in ('NO_CANDIDATES', 'GATE_MISSED', 'CANDIDATES_SKIPPED')
                for status in statuses
            ),
            'view_delivered': sum(status in (
                'VIEWED_BEFORE_REPO', 'VIEWED_LATE', 'MATERIALIZED_BEFORE_AGENT',
            ) for status in statuses),
            'consumed_before_repo': (
                statuses.count('VIEWED_BEFORE_REPO')
                + statuses.count('MATERIALIZED_BEFORE_AGENT')
            ),
            'materialized_before_agent': statuses.count('MATERIALIZED_BEFORE_AGENT'),
            'viewed_late': statuses.count('VIEWED_LATE'),
            'rejected_before_repo': statuses.count('REJECTED_BEFORE_REPO'),
            'rejected_late': statuses.count('REJECTED_LATE'),
            'view_failed': statuses.count('VIEW_FAILED'),
            'gate_missed': statuses.count('GATE_MISSED'),
            'candidates_skipped': statuses.count('CANDIDATES_SKIPPED'),
            'no_candidates': statuses.count('NO_CANDIDATES'),
            'repeated_gate_calls': sum(gate.get('repeated_gate_call') is True for gate in gates),
            'view_network_attempts': sum(gate.get('view_network_attempt_count') or 0 for gate in gates),
        }
    return metrics


def persist_progress(state, pilot, report_path, phase, **details):
    state['progress'] = {'phase': phase, 'updated_at': now(), **details}
    bench.write(pilot/'session.json', state)
    bench.write(report_path, {**state, 'metrics': calculate_metrics(state)})


def set_checkpoint(state, safe, phase, **details):
    state['checkpoint'] = {
        'safe_to_resume': safe,
        'phase': phase,
        'recorded_at': now(),
        'claude_session_id': state['claude_session_id'],
        'completed_turns': len(state['turns']),
        **details,
    }


def export_langfuse(state, pilot, lf_file):
    """Export the complete session after producers stop and summarize it locally."""
    if lf_file is None:
        return {'status':'DISABLED','session_id':state['session_id']}
    lf = bench.read(lf_file)
    env = os.environ.copy()
    env.update(LANGFUSE_HOST=lf['host'],LANGFUSE_BASE_URL=lf['host'],LANGFUSE_PUBLIC_KEY=lf['publicKey'],LANGFUSE_SECRET_KEY=lf['secretKey'])
    npx = shutil.which('npx.cmd') or shutil.which('npx')
    if not npx:
        return {'status':'INCOMPLETE','error':'npx executable not found','session_id':state['session_id']}
    command = [
        npx,'--yes','langfuse-cli','api','observations','list',
        '--session-id',state['session_id'],'--fields','core,basic,io,metadata,model,usage,metrics,trace_context',
        '--limit','1000','--all','--max-items','10000','--json',
    ]
    last_error = None
    expected_agent = sum(t.get('model_calls') or 0 for t in state.get('turns',[]))
    expected_extractions = [
        item.get('task_id') for item in (state.get('ours') or {}).get('extractions', [])
        if item.get('task_id')
    ] if state.get('async_drain_completed') else []
    for attempt in range(1,9):
        try:
            result = subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',env=env,timeout=120)
        except Exception as error:
            last_error = str(error)
            time.sleep(5*attempt)
            continue
        if result.returncode == 0:
            try:
                envelope = json.loads(result.stdout)
                if envelope.get('status') == 200:
                    rows = envelope['body']['data']
                    bench.write(pilot/'langfuse-observations.json',envelope)
                    types = {}
                    for row in rows:
                        types[row.get('type','UNKNOWN')] = types.get(row.get('type','UNKNOWN'),0)+1
                    generations = [row for row in rows if row.get('type') == 'GENERATION']
                    agent_generations = [row for row in generations if row.get('metadata',{}).get('request_kind') == 'main']
                    injection_spans = [row for row in rows if str(row.get('name','')).startswith('[inject]')]
                    if len(agent_generations) < expected_agent:
                        last_error = f'Langfuse eventually consistent: agent {len(agent_generations)}/{expected_agent}'
                        time.sleep(min(30,5*attempt))
                        continue
                    missing_extractions = [
                        task_id for task_id in expected_extractions
                        if not any(str(row.get('name','')).startswith(f'skill-extract-{task_id}:') for row in generations)
                    ]
                    if missing_extractions:
                        last_error = f'Langfuse eventually consistent: missing extraction traces {missing_extractions}'
                        time.sleep(min(30,5*attempt))
                        continue
                    return {
                        'status':'COMPLETE', 'attempts':attempt, 'observations':len(rows),
                        'types':types, 'generation_count':len(generations),
                        'agent_generation_count':len(agent_generations),
                        'skill_or_memory_generation_count':len(generations)-len(agent_generations),
                        'injection_span_count':len(injection_spans),
                        'usage':{
                            'input':sum(row.get('inputUsage') or 0 for row in generations),
                            'output':sum(row.get('outputUsage') or 0 for row in generations),
                            'total':sum(row.get('totalUsage') or 0 for row in generations),
                        },
                        'artifact':str(pilot/'langfuse-observations.json'),
                        'session_id':state['session_id'],
                    }
            except Exception as error:
                last_error = str(error)
        else:
            last_error = result.stderr[-2000:] or f'exit {result.returncode}'
        time.sleep(min(30,5*attempt))
    return {'status':'INCOMPLETE','error':last_error,'session_id':state['session_id']}


def reconcile_ours_langfuse(state, pilot):
    """Attach per-archive async outcome and extraction cost from the final export."""
    if not is_ours_variant(state.get('variant')) or not (pilot/'langfuse-observations.json').is_file():
        return
    envelope = bench.read(pilot/'langfuse-observations.json')
    rows = (envelope.get('body') or {}).get('data') or []
    generations = [
        row for row in rows
        if row.get('type') == 'GENERATION' and str(row.get('name','')).startswith('skill-extract-')
    ]
    totals = {'input':0,'output':0,'total':0}
    for row in generations:
        totals['input'] += int(row.get('inputUsage') or 0)
        totals['output'] += int(row.get('outputUsage') or 0)
        totals['total'] += int(row.get('totalUsage') or 0)
    state['ours']['extraction_totals'] = {'generations':len(generations),'usage':totals}
    final_heads = (state.get('skill_snapshots') or [{}])[-1].get('heads') or []
    heads_by_name = {item.get('name'):item for item in final_heads}
    for archive in state['ours'].get('extractions', []):
        task_id = archive.get('task_id')
        prefix = f'skill-extract-{task_id}:'
        task_rows = [row for row in generations if str(row.get('name','')).startswith(prefix)]
        outputs = [str(row.get('output') or '') for row in task_rows]
        joined = '\n'.join(outputs)
        action = 'UNKNOWN'
        resulting_name = None
        patterns = (
            ('NOOP', r'(?i)nothing to save'),
            ('UPDATE', r'(?i)(?:updated|patched)\s+([a-z0-9][a-z0-9-]*)'),
            ('CREATE', r'(?i)created\s+([a-z0-9][a-z0-9-]*)'),
        )
        for candidate_action, pattern in patterns:
            match = re.search(pattern, joined)
            if match:
                action = candidate_action
                resulting_name = match.group(1) if match.lastindex else None
                break
        usage = {
            'input':sum(int(row.get('inputUsage') or 0) for row in task_rows),
            'output':sum(int(row.get('outputUsage') or 0) for row in task_rows),
            'total':sum(int(row.get('totalUsage') or 0) for row in task_rows),
        }
        archive.update({
            'async_status':'COMPLETED' if task_rows else archive.get('async_status','ENQUEUED'),
            'review_action':action if task_rows else None,
            'review_generation_count':len(task_rows),
            'distillation_usage':usage,
            'resulting_skill_name':resulting_name,
            'resulting_skill_id':(heads_by_name.get(resulting_name) or {}).get('skill_id'),
        })


def reconcile_ours_local(state, directory):
    """Reconcile async extraction from the native SQLite audit trail and local logs."""
    if not is_ours_variant(state.get('variant')) or not state.get('async_drain_completed'):
        return
    db = directory/'core-data'/'vectors.db'
    rows = []
    if db.is_file():
        connection = sqlite3.connect(f'file:{db.as_posix()}?mode=ro', uri=True, timeout=30)
        try:
            rows = connection.execute(
                'select skill_id, version, name, description, task_id '
                'from skills order by created_at_ms, version'
            ).fetchall()
        finally:
            connection.close()
    usage_by_task = {}
    log_path = directory/'core-logs'/'observability.log'
    if log_path.is_file():
        with log_path.open(encoding='utf-8', errors='replace') as handle:
            for line in handle:
                if 'skill.extractor.extract ' not in line and 'skill.extractor.query_generation ' not in line:
                    continue
                match = re.search(r'(\{.*\})\s*$', line)
                if not match:
                    continue
                try:
                    payload = json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue
                task_id = payload.get('task_id')
                if not task_id or 'total_tokens' not in payload:
                    continue
                usage = usage_by_task.setdefault(task_id, {'input':0,'output':0,'total':0,'llm_runs':0})
                usage['input'] += int(payload.get('input_tokens') or 0)
                usage['output'] += int(payload.get('output_tokens') or 0)
                usage['total'] += int(payload.get('total_tokens') or 0)
                usage['llm_runs'] += 1
    totals = {'input':0,'output':0,'total':0}
    for archive in state['ours'].get('extractions', []):
        task_id = archive.get('task_id')
        task_rows = [row for row in rows if row[4] == task_id]
        actions = [
            {
                'action':'CREATE' if row[1] == 1 else 'UPDATE',
                'skill_id':row[0], 'version':row[1], 'name':row[2],
                'description':row[3],
            }
            for row in task_rows
        ]
        usage = usage_by_task.get(task_id, {'input':0,'output':0,'total':0,'llm_runs':0})
        for key in totals:
            totals[key] += usage[key]
        archive.update({
            'async_status':'COMPLETED',
            'review_action':actions[0]['action'] if len(actions) == 1 else 'NOOP' if not actions else 'MULTIPLE',
            'review_actions':actions,
            'review_generation_count':usage['llm_runs'],
            'distillation_usage':{key:usage[key] for key in ('input','output','total')},
            'resulting_skill_name':actions[0]['name'] if len(actions) == 1 else None,
            'resulting_skill_id':actions[0]['skill_id'] if len(actions) == 1 else None,
            'local_reconcile_status':'COMPLETE',
        })
    state['ours']['extraction_totals'] = {
        'source':'local-native-audit', 'generations':sum(v['llm_runs'] for v in usage_by_task.values()),
        'usage':totals,
    }


def update_extraction_metrics_completeness(state):
    """Never report complete Ours_v2 cost when an observed extractor call has zero usage."""
    completeness = state.setdefault('metrics_completeness', {})
    if state.get('variant') not in ('ours_v2', 'ours_v3'):
        completeness.setdefault(
            'extraction_cost',
            'NOT_APPLICABLE' if not is_ours_variant(state.get('variant')) else 'COMPLETE',
        )
        return
    archives = (state.get('ours') or {}).get('extractions') or []
    if not state.get('async_drain_completed'):
        completeness['extraction_cost'] = 'DEFERRED'
        return
    missing = []
    for archive in archives:
        if archive.get('status') == 'empty':
            continue
        generations = int(archive.get('review_generation_count') or 0)
        total = int((archive.get('distillation_usage') or {}).get('total') or 0)
        if generations <= 0 or total <= 0:
            missing.append({
                'archive_id': archive.get('archive_id'),
                'task_id': archive.get('task_id'),
                'review_generation_count': generations,
                'total_tokens': total,
            })
    if missing:
        completeness['extraction_cost'] = 'INCOMPLETE'
        completeness['local_extraction_audit'] = 'INCOMPLETE'
        state.setdefault('ours', {})['extraction_cost_incomplete'] = missing
    else:
        completeness['extraction_cost'] = 'COMPLETE'
        state.setdefault('ours', {}).pop('extraction_cost_incomplete', None)


def wait_for_natural_skill_drain(directory, timeout=180):
    """Drain jobs the unmodified Proxy queued, only after the final user event."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        registries = list((directory/'core-data'/'skill_buffer').glob('*/*/*/_tasks.json'))
        pending = []
        for registry in registries:
            pending.extend(bench.read(registry).get('tasks', []))
        if not pending:
            return
        time.sleep(.5)
    raise RuntimeError('Baseline Skill extraction queue did not drain before service shutdown')


def skill_snapshot(directory, task_id):
    db = directory/'core-data'/'vectors.db'
    connection = sqlite3.connect(f'file:{db.as_posix()}?mode=ro', uri=True)
    try:
        rows = connection.execute(
            'select skill_id, version, name, description from skills where is_head=1 order by name'
        ).fetchall()
    finally:
        connection.close()
    return {
        'after_task': task_id,
        'captured_at': now(),
        'heads': [
            {'skill_id': row[0], 'version': row[1], 'name': row[2], 'description': row[3]}
            for row in rows
        ],
    }


def is_task_event(event):
    return event['event_type'] in ('sop', 'non-sop')


def tool_result_count(events):
    ids = set()
    for event in events:
        content = event.get('message',{}).get('content',[])
        if isinstance(content,list):
            ids.update(block['tool_use_id'] for block in content if isinstance(block,dict) and block.get('type')=='tool_result' and block.get('tool_use_id'))
    return len(ids)


def model_call_count(events):
    """Count unique upstream assistant generations in Claude stream-json."""
    ids = set()
    anonymous = 0
    for event in events:
        if event.get('type') != 'assistant':
            continue
        message_id = event.get('message', {}).get('id')
        if message_id:
            ids.add(message_id)
        else:
            anonymous += 1
    return len(ids) + anonymous


def _json_candidates(value):
    """Collect public search candidate fields without persisting full Skill bodies."""
    found = []
    def visit(item):
        if isinstance(item, dict):
            name = item.get('name') or item.get('skill_name')
            score = item.get('score')
            if isinstance(name, str) and (score is not None or 'description' in item or 'snippet' in item):
                found.append({
                    key: item.get(key) for key in ('skill_id','name','description','snippet','score')
                    if item.get(key) is not None
                })
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
    visit(value)
    unique = []
    seen = set()
    for item in found:
        marker = (item.get('skill_id'), item.get('name'), item.get('score'))
        if marker not in seen:
            seen.add(marker)
            unique.append(item)
    return unique


def skill_tool_usage(events):
    """Audit the real native skill_search/skill_view calls in Claude stream order."""
    tool_uses = []
    results = {}
    seen = set()
    for event in events:
        content = event.get('message', {}).get('content', [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use' and block.get('id') and block['id'] not in seen:
                seen.add(block['id'])
                tool_uses.append({
                    'ordinal': len(tool_uses) + 1,
                    'id': block['id'],
                    'tool': block.get('name'),
                    'input': block.get('input') if isinstance(block.get('input'), dict) else {},
                })
            elif block.get('type') == 'tool_result' and block.get('tool_use_id'):
                results[block['tool_use_id']] = block.get('content')
    search_attempts = []
    view_attempts = []
    first_repo_tool = None
    for call in tool_uses:
        command = str((call.get('input') or {}).get('command') or '')
        is_search = '/v3/skill/search' in command
        is_view = '/v3/skill/get-by-name' in command
        environment_probe = call.get('tool') == 'Bash' and re.search(r'\b(?:which|command\s+-v)\b', command)
        if not (is_search or is_view or environment_probe) and first_repo_tool is None:
            first_repo_tool = call['ordinal']
        if not (is_search or is_view):
            continue
        record = {key: call[key] for key in ('ordinal','id','tool')}
        record['before_first_repository_tool'] = first_repo_tool is None
        raw_result = results.get(call['id'])
        text_result = raw_result if isinstance(raw_result, str) else json.dumps(raw_result, ensure_ascii=False)
        parsed = None
        try:
            parsed = json.loads(text_result)
        except Exception:
            match = re.search(r'\{.*\}', text_result, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except Exception:
                    pass
        record['succeeded'] = isinstance(parsed, dict) and parsed.get('code') in (0, 200)
        if is_search:
            record['candidates'] = _json_candidates(parsed) if parsed is not None else []
            search_attempts.append(record)
        else:
            match = re.search(r'"(?:skill_name|name)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', command)
            record['selected_name'] = json.loads(f'"{match.group(1)}"') if match else None
            if isinstance(parsed, dict):
                data = parsed.get('data') or {}
                if isinstance(data, dict):
                    record['selected_skill_id'] = data.get('skill_id')
                    record['selected_version'] = data.get('version')
                    record['resolved_name'] = data.get('name')
            view_attempts.append(record)
    searches = [call for call in search_attempts if call['succeeded']]
    views = [call for call in view_attempts if call['succeeded']]
    return {
        'search_attempts':search_attempts,
        'view_attempts':view_attempts,
        'search_calls': searches,
        'view_calls': views,
        'search_attempt_count': len(search_attempts),
        'view_attempt_count': len(view_attempts),
        'search_count': len(searches),
        'view_count': len(views),
        'first_repository_tool_ordinal': first_repo_tool,
        'search_before_first_repository_tool': bool(searches and searches[0]['before_first_repository_tool']),
        'view_before_first_repository_tool': bool(views and views[0]['before_first_repository_tool']),
        'repeated_search': len(searches) > 1,
        'repeated_view': len(views) > 1,
    }


def task_skill_gate_usage(events, candidate_count):
    """Audit the Ours_v3 gate without treating helper execution as repo work."""
    tool_uses = []
    results = {}
    seen = set()
    for event in events:
        content = event.get('message', {}).get('content', [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use' and block.get('id') and block['id'] not in seen:
                seen.add(block['id'])
                tool_uses.append({
                    'ordinal': len(tool_uses) + 1,
                    'id': block['id'],
                    'tool': block.get('name'),
                    'input': block.get('input') if isinstance(block.get('input'), dict) else {},
                })
            elif block.get('type') == 'tool_result' and block.get('tool_use_id'):
                results[block['tool_use_id']] = block.get('content')

    first_repo_tool = None
    gate_calls = []
    for call in tool_uses:
        command = str((call.get('input') or {}).get('command') or '')
        is_gate = '/opt/benchmark/skill-gate.mjs' in command
        is_skill_bridge = '/skill-bridge/v3/skill/' in command
        environment_probe = call.get('tool') == 'Bash' and re.fullmatch(
            r'\s*(?:which\s+[^;&|]+|command\s+-v\s+[^;&|]+)\s*', command
        )
        if not (is_gate or is_skill_bridge or environment_probe) and first_repo_tool is None:
            first_repo_tool = call['ordinal']
        if not is_gate:
            continue
        raw = results.get(call['id'])
        text_result = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        parsed = None
        for line in reversed(text_result.splitlines()):
            try:
                value = json.loads(line)
                if isinstance(value, dict) and isinstance(value.get('gate'), dict):
                    parsed = value
                    break
            except Exception:
                continue
        match = re.search(
            r'skill-gate\.mjs\s+(view|reject-all|reject-after-view)\s+([a-f0-9]{24})',
            command,
        )
        gate = (parsed or {}).get('gate') or {}
        gate_calls.append({
            'ordinal': call['ordinal'],
            'tool_use_id': call['id'],
            'requested_action': match.group(1).upper().replace('-', '_') if match else None,
            'task_token': match.group(2) if match else None,
            'before_first_repository_tool': first_repo_tool is None,
            'status': gate.get('status'),
            'action': gate.get('action'),
            'candidate_index': gate.get('candidate_index'),
            'skill_id': gate.get('skill_id'),
            'skill_name': gate.get('skill_name'),
            'skill_version': gate.get('skill_version'),
            'rejection_reasons': gate.get('reasons'),
            'network_attempts': gate.get('attempts') or [],
            'content_bytes': gate.get('content_bytes'),
            'content_chars': gate.get('content_chars'),
            'content_sha256': gate.get('content_sha256'),
            'message': gate.get('message'),
            'validation_error': gate.get('validation_error'),
            'post_view_decision': gate.get('post_view_decision'),
            'post_view_reason': gate.get('post_view_reason'),
        })

    completed = next(
        (call for call in gate_calls if call.get('status') in ('VIEWED', 'REJECTED', 'VIEW_FAILED')),
        None,
    )
    if not candidate_count:
        consumption_status = 'NO_CANDIDATES'
    elif completed is None:
        # An explicit rejection is optional in r3.  The private evaluator uses
        # SOP-family annotations to distinguish a correct skip from a missed
        # reuse opportunity after the run.
        consumption_status = 'CANDIDATES_SKIPPED'
    elif completed['status'] == 'VIEW_FAILED':
        consumption_status = 'VIEW_FAILED'
    elif completed['status'] == 'VIEWED':
        consumption_status = (
            'VIEWED_BEFORE_REPO' if completed['before_first_repository_tool'] else 'VIEWED_LATE'
        )
    else:
        consumption_status = (
            'REJECTED_BEFORE_REPO' if completed['before_first_repository_tool'] else 'REJECTED_LATE'
        )
    primary_calls = [
        call for call in gate_calls
        if call.get('requested_action') in ('VIEW', 'REJECT_ALL')
    ]
    post_view = next(
        (call for call in gate_calls if call.get('post_view_decision') == 'REJECT_AFTER_VIEW'),
        None,
    )
    return {
        # Kept for result-schema compatibility: candidates create a Skill-use
        # decision point, but r3 does not require an explicit rejection action.
        'gate_required': bool(candidate_count),
        'explicit_rejection_required': False,
        'view_opportunity': bool(candidate_count),
        'gate_calls': gate_calls,
        'gate_call_count': len(gate_calls),
        'repeated_gate_call': len(primary_calls) > 1,
        'first_repository_tool_ordinal': first_repo_tool,
        'consumption_status': consumption_status,
        'selected_candidate_index': completed.get('candidate_index') if completed else None,
        'selected_skill_id': completed.get('skill_id') if completed else None,
        'selected_skill_name': completed.get('skill_name') if completed else None,
        'selected_skill_version': completed.get('skill_version') if completed else None,
        'view_network_attempt_count': len(completed.get('network_attempts') or []) if completed else 0,
        'view_content_bytes': completed.get('content_bytes') if completed else None,
        'view_content_chars': completed.get('content_chars') if completed else None,
        'view_content_sha256': completed.get('content_sha256') if completed else None,
        'rejection_reasons': completed.get('rejection_reasons') if completed else None,
        'post_view_decision': post_view.get('post_view_decision') if post_view else None,
        'post_view_reason': post_view.get('post_view_reason') if post_view else None,
    }


def _bounded_active_queries(active_queries, size=6):
    if len(active_queries) <= size:
        return list(active_queries)
    return [active_queries[0], *active_queries[-(size - 1):]]


def classify_task_boundary(active_queries, current_query):
    """Call the frozen L1.5 prompt/parser through the shared TypeScript implementation."""
    if not active_queries:
        return {
            'schema_version':'query_boundary_live/1.0', 'decision':'new_task',
            'taskBoundary':True, 'recent_queries':[], 'current_query':current_query,
            'llm_called':False, 'llm_attempts':0, 'llm_latency_ms':0,
            'usage':{'input':0,'output':0,'total':0}, 'model':None,
            'first_turn_policy':'new_task_without_llm',
        }
    node = Path(os.environ.get('BENCHMARK_NODE') or shutil.which('node') or '')
    if not node.is_file() or not BOUNDARY_RUNNER.is_file():
        return {
            'schema_version':'query_boundary_live/1.0', 'decision':'same_task',
            'taskBoundary':False, 'recent_queries':_bounded_active_queries(active_queries),
            'current_query':current_query, 'llm_called':False, 'llm_attempts':0,
            'llm_latency_ms':0, 'usage':{'input':0,'output':0,'total':0},
            'model':None, 'recovery_policy':'same_task_boundary_runtime_unavailable',
        }
    payload = {'recentQueries':_bounded_active_queries(active_queries),'currentQuery':current_query}
    transport_errors = []
    retry_delays = (2, 5, 10)
    for transport_attempt in range(1, len(retry_delays) + 2):
        try:
            result = subprocess.run(
                [str(node),'--import','tsx/esm',str(BOUNDARY_RUNNER)],
                cwd=Path(__file__).resolve().parents[2]/'MemoryCore', input=json.dumps(payload),
                text=True, capture_output=True, encoding='utf-8', errors='replace',
                env=os.environ.copy(), timeout=150,
            )
            if not result.returncode:
                boundary = json.loads(result.stdout)
                boundary['transport_attempts'] = transport_attempt
                if transport_errors:
                    boundary['transport_retry_errors'] = transport_errors
                return boundary
            detail = (result.stderr or result.stdout)[-2000:]
        except subprocess.TimeoutExpired as error:
            detail = f'boundary runner timeout after {error.timeout}s'

        transient = any(marker in detail.lower() for marker in (
            'fetch failed', 'econnreset', 'etimedout', 'connect timeout',
            'und_err_connect_timeout', 'eai_again', 'enotfound',
        ))
        transport_errors.append({'attempt': transport_attempt, 'error': detail})
        if not transient or transport_attempt > len(retry_delays):
            return {
                'schema_version':'query_boundary_live/1.0', 'decision':'same_task',
                'taskBoundary':False, 'recent_queries':_bounded_active_queries(active_queries),
                'current_query':current_query, 'llm_called':True,
                'llm_attempts':transport_attempt, 'llm_latency_ms':0,
                'usage':{'input':0,'output':0,'total':0}, 'model':None,
                'recovery_policy':'same_task_boundary_failed_open',
                'transport_retry_errors':transport_errors,
            }
        time.sleep(retry_delays[transport_attempt - 1])
    raise AssertionError('unreachable boundary retry state')


def task_extraction_reason(task_query, extraction_profile='task_scoped_v1'):
    guidance = (
        OURS_V2_EXTRACTION_GUIDANCE
        if extraction_profile == 'task_scoped_sop_v2'
        else OURS_V1_EXTRACTION_GUIDANCE
    )
    reason = guidance.format(task_query=task_query.strip())
    if len(reason) > 2000:
        suffix = guidance.format(task_query='')
        budget = max(1, 2000 - len(suffix))
        reason = guidance.format(task_query=task_query.strip()[:budget])
    return reason


def force_task_archive(service, session_id, task_query, extraction_profile='task_scoped_v1'):
    """Enqueue native extraction and return immediately; never wait for the Worker."""
    identity = service['identity']
    body = {
        'space_id':'default', 'user_id':identity['user_id'],
        'team_id':identity['team_id'], 'agent_id':identity['agent_id'],
        'session_id':session_id,
        'reason':task_extraction_reason(task_query, extraction_profile),
    }
    request = urllib.request.Request(
        f"http://127.0.0.1:{service['core_port']}/v3/skill/conversation/force-archive",
        json.dumps(body).encode('utf-8'),
        {'Content-Type':'application/json','Authorization':'Bearer local','x-tdai-service-id':'default'},
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    if result.get('code') not in (0, 200):
        raise RuntimeError(f"Ours force-archive failed: {result.get('code')} {result.get('message')}")
    data = result.get('data') or {}
    return {
        **data, 'task_query':task_query, 'reason':body['reason'],
        'enqueued_at':now(), 'request_latency_ms':round((time.monotonic()-started)*1000,3),
        'async_status':'ENQUEUED' if data.get('status') == 'archived' else str(data.get('status','UNKNOWN')).upper(),
    }


def archive_outbox_key(session_id, predicted_task, task_query):
    value = f'{session_id}\0{predicted_task}\0{task_query}'.encode('utf-8')
    return hashlib.sha256(value).hexdigest()


def enqueue_task_archive(state, pilot, report_path, service, session_id,
                         predicted_task, benchmark_task_id, task_query,
                         extraction_profile, closed_before_ordinal=None,
                         eof_flush=False):
    """Persist archive intent before the asynchronous enqueue, then ACK it."""
    outbox = state['ours'].setdefault('archive_outbox', [])
    key = archive_outbox_key(session_id, predicted_task, task_query)
    entry = next((item for item in outbox if item.get('key') == key), None)
    if entry is None:
        entry = {
            'key': key, 'status': 'PENDING', 'created_at': now(),
            'predicted_task': predicted_task,
            'benchmark_task_id': benchmark_task_id,
            'task_query': task_query,
            'closed_before_ordinal': closed_before_ordinal,
            'eof_flush': eof_flush,
        }
        outbox.append(entry)
        persist_progress(
            state, pilot, report_path, 'EXTRACTION_OUTBOX_PENDING',
            archive_outbox_key=key, active_task=predicted_task,
        )
    if entry.get('status') == 'ACKED':
        return entry.get('archive') or {}
    archive = force_task_archive(
        service, session_id, task_query, extraction_profile,
    )
    archive.update({
        'predicted_task': predicted_task,
        'benchmark_task_id': benchmark_task_id,
        'closed_before_ordinal': closed_before_ordinal,
        'eof_flush': eof_flush,
        'archive_outbox_key': key,
    })
    entry.update(status='ACKED', acked_at=now(), archive=archive)
    if not any(item.get('archive_outbox_key') == key
               for item in state['ours']['extractions']):
        state['ours']['extractions'].append(archive)
    persist_progress(
        state, pilot, report_path, 'EXTRACTION_ENQUEUED',
        archive_outbox_key=key, active_task=predicted_task,
    )
    return archive


def replay_pending_archives(state, pilot, report_path, service, session_id,
                            extraction_profile):
    pending = [
        dict(item) for item in state.get('ours', {}).get('archive_outbox', [])
        if item.get('status') != 'ACKED'
    ]
    for entry in pending:
        enqueue_task_archive(
            state, pilot, report_path, service, session_id,
            entry['predicted_task'], entry.get('benchmark_task_id'),
            entry['task_query'], extraction_profile,
            entry.get('closed_before_ordinal'), bool(entry.get('eof_flush')),
        )
    return len(pending)


def _public_skill_candidate(item):
    return {
        key: item.get(key)
        for key in ('skill_id', 'name', 'description', 'snippet', 'score')
        if item.get(key) is not None
    }


def search_task_skills(service, anchor_query, top_k=OURS_V2_SEARCH_TOP_K):
    """Use the native Core search once with only the active Task anchor."""
    identity = service['identity']
    body = {
        'user_id': identity['user_id'],
        'team_id': identity['team_id'],
        'agent_id': identity['agent_id'],
        'query': anchor_query.strip(),
        'top_k': top_k,
        'mode': 'bm25',
    }
    request = urllib.request.Request(
        f"http://127.0.0.1:{service['core_port']}/v3/skill/search",
        json.dumps(body).encode('utf-8'),
        {
            'Content-Type':'application/json',
            'Authorization':'Bearer local',
            'x-tdai-service-id':'default',
        },
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    latency_ms = round((time.monotonic() - started) * 1000, 3)
    if result.get('code') not in (0, 200):
        raise RuntimeError(
            f"Ours_v2 task skill search failed: {result.get('code')} {result.get('message')}"
        )
    candidates = [
        _public_skill_candidate(item)
        for item in ((result.get('data') or {}).get('items') or [])[:top_k]
    ]
    return {
        'search_query': anchor_query,
        'search_query_sha256': hashlib.sha256(anchor_query.encode('utf-8')).hexdigest(),
        'search_status': 'COMPLETE',
        'search_latency_ms': latency_ms,
        'top_k': top_k,
        'candidate_count': len(candidates),
        'candidates': candidates,
        # The native BM25 search does not invoke an LLM.
        'usage': {'input': 0, 'output': 0, 'total': 0},
    }


OURS_V3_SELECTOR_SYSTEM = '''Select whether one retrieved Skill contains a reusable
workflow for the current coding task. Compare intended outcome, applicability, and
core workflow at the mechanism level; repository/framework differences alone are not
mismatches. Select a candidate only when its intended outcome, applicability or
preconditions, ordered decisions, and validation semantics can be applied to the
current task without changing the essential workflow. Implementation surface,
framework, repository, language, and file names are weak evidence. Select at most one
candidate. Prefer a plausible transferable decision procedure, but reject lexical
overlap without workflow overlap. Return JSON only:
{"decision":"view","rank":1,"reason":"short reason"} or
{"decision":"none","rank":null,"reason":"short reason"}.'''


def _openai_usage(body):
    usage = body.get('usage') or {}
    input_tokens = int(usage.get('prompt_tokens') or usage.get('input_tokens') or 0)
    output_tokens = int(usage.get('completion_tokens') or usage.get('output_tokens') or 0)
    return {
        'input': input_tokens,
        'output': output_tokens,
        'total': int(usage.get('total_tokens') or input_tokens + output_tokens),
    }


def select_task_skill(anchor_query, candidates):
    """Use one small, task-only relevance call; never expose trajectory or gold labels."""
    if not candidates:
        return {
            'decision': 'none', 'rank': None, 'reason': 'no_candidates',
            'model': None, 'latency_ms': 0, 'attempts': 0,
            'usage': {'input': 0, 'output': 0, 'total': 0},
        }
    api_key = os.environ.get('DEEPSEEK_API_KEY')
    if not api_key:
        raise RuntimeError('DEEPSEEK_API_KEY is required for Ours_v3 task Skill selection')
    public = [
        {
            'rank': index,
            'name': item.get('name'),
            'description': _one_line(item.get('description'), 700),
            'snippet': _one_line(item.get('snippet'), 900),
            'score': item.get('score'),
        }
        for index, item in enumerate(candidates[:OURS_V2_SEARCH_TOP_K], 1)
    ]
    user_prompt = json.dumps(
        {'current_task': anchor_query, 'candidates': public},
        ensure_ascii=False, separators=(',', ':'),
    )
    payload = {
        'model': OURS_V3_SELECTOR_MODEL,
        'messages': [
            {'role': 'system', 'content': OURS_V3_SELECTOR_SYSTEM},
            {'role': 'user', 'content': user_prompt},
        ],
        'temperature': 0,
        'max_tokens': 256,
        'thinking': {'type': 'disabled'},
        'response_format': {'type': 'json_object'},
    }
    started = time.monotonic()
    errors = []
    for attempt, delay in enumerate((0, 2), 1):
        if delay:
            time.sleep(delay)
        request = urllib.request.Request(
            f'{OPENAI_COMPATIBLE_BASE_URL}/chat/completions',
            json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            {'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'},
        )
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                body = json.load(response)
            choice = ((body.get('choices') or [{}])[0].get('message') or {})
            raw = choice.get('content') or choice.get('reasoning_content') or '{}'
            parsed = json.loads(raw)
            decision = str(parsed.get('decision') or '').lower()
            rank = parsed.get('rank')
            if decision == 'view' and isinstance(rank, int) and 1 <= rank <= len(public):
                pass
            elif decision == 'none':
                rank = None
            else:
                raise ValueError(f'invalid selector decision: {parsed!r}')
            return {
                'decision': decision,
                'rank': rank,
                'reason': _one_line(parsed.get('reason'), 400),
                'model': OURS_V3_SELECTOR_MODEL,
                'latency_ms': round((time.monotonic() - started) * 1000, 3),
                'attempts': attempt,
                'usage': _openai_usage(body),
                'system_prompt_sha256': hashlib.sha256(
                    OURS_V3_SELECTOR_SYSTEM.encode('utf-8')
                ).hexdigest(),
                'input_sha256': hashlib.sha256(user_prompt.encode('utf-8')).hexdigest(),
            }
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as error:
            errors.append(f'{type(error).__name__}: {error}')
    return {
        'decision': 'none', 'rank': None, 'reason': 'selector_failed_open',
        'model': OURS_V3_SELECTOR_MODEL,
        'latency_ms': round((time.monotonic() - started) * 1000, 3),
        'attempts': len(errors), 'errors': errors,
        'usage': {'input': 0, 'output': 0, 'total': 0},
        'metrics_complete': False,
    }


def materialize_task_skill(service, candidate):
    """Fetch a selected Skill in the Driver before the Agent starts repository work."""
    identity = service['identity']
    body = {
        'user_id': identity['user_id'],
        'team_id': identity['team_id'],
        'agent_id': identity['agent_id'],
        'task_id': identity.get('task_id'),
        'skill_name': candidate['name'],
        'include_content': True,
        'include_manifest': True,
    }
    started = time.monotonic()
    attempts = []
    for attempt, delay in enumerate((0, 1), 1):
        if delay:
            time.sleep(delay)
        request = urllib.request.Request(
            f"http://127.0.0.1:{service['core_port']}/v3/skill/get-by-name",
            json.dumps(body).encode('utf-8'),
            {'Content-Type':'application/json', 'Authorization':'Bearer local',
             'x-tdai-service-id':'default'},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
            attempts.append({'attempt': attempt, 'http_status': 200,
                             'business_code': result.get('code')})
            if result.get('code') in (0, 200):
                data = result.get('data') or {}
                content = str(data.get('content') or '')
                return {
                    'status': 'MATERIALIZED', 'attempts': attempts,
                    'latency_ms': round((time.monotonic() - started) * 1000, 3),
                    'skill_id': data.get('skill_id') or candidate.get('skill_id'),
                    'skill_name': data.get('name') or candidate.get('name'),
                    'skill_version': data.get('version'),
                    'content': content,
                    'content_chars': len(content),
                    'content_sha256': hashlib.sha256(content.encode('utf-8')).hexdigest(),
                }
            return {
                'status': 'VIEW_FAILED', 'attempts': attempts,
                'latency_ms': round((time.monotonic() - started) * 1000, 3),
                'error': f"business_code={result.get('code')}: {result.get('message')}",
            }
        except (urllib.error.URLError, TimeoutError) as error:
            attempts.append({'attempt': attempt, 'error': f'{type(error).__name__}: {error}'})
    return {
        'status': 'VIEW_FAILED', 'attempts': attempts,
        'latency_ms': round((time.monotonic() - started) * 1000, 3),
        'error': attempts[-1].get('error') if attempts else 'unknown',
    }


def prepare_task_skill_consumption(service, anchor_query, candidates):
    selector = select_task_skill(anchor_query, candidates)
    result = {'selector': selector, 'usage': dict(selector['usage'])}
    if selector['decision'] != 'view':
        result.update(status='NO_CANDIDATES' if not candidates else 'REJECTED')
        return result
    rank = selector['rank']
    candidate = candidates[rank - 1]
    materialized = materialize_task_skill(service, candidate)
    result.update(
        status=materialized['status'], selected_candidate_rank=rank,
        selected_candidate={key: candidate.get(key) for key in
                            ('skill_id', 'name', 'description', 'snippet', 'score')},
        materialization={key: value for key, value in materialized.items() if key != 'content'},
    )
    if materialized['status'] == 'MATERIALIZED':
        result['content'] = materialized['content']
    return result


def render_materialized_skill_context(consumption):
    if consumption.get('status') != 'MATERIALIZED':
        return ''
    selected = consumption['selected_candidate']
    content = consumption.get('content') or ''
    # The Driver audits the complete viewed Skill, but the Coding Agent only
    # needs its reusable decision procedure.  Deterministic section extraction
    # avoids turning a useful SOP into thousands of tokens of examples/history.
    body = re.sub(r'^---\s*\n.*?\n---\s*\n', '', content, count=1, flags=re.DOTALL)
    sections = {}
    matches = list(re.finditer(r'^##\s+(.+?)\s*$', body, flags=re.MULTILINE))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[match.group(1).strip().lower()] = body[match.end():end].strip()
    chosen = []
    for heading, limit in (
        ('when to use', 450), ('when not to use', 300),
        ('required inputs', 300), ('workflow', 1150),
        ('decision rules', 450), ('validation', 350),
        ('failure handling / rollback', 300),
    ):
        text = sections.get(heading)
        if text:
            chosen.append(f'### {heading.title()}\n{_truncate_structured(text, limit)}')
    compact = '\n\n'.join(chosen) if chosen else _truncate_structured(body, 2400)
    max_content = max(0, OURS_V3_SKILL_CONTEXT_CHAR_BUDGET - 650)
    compact = _truncate_structured(compact, max_content)
    consumption['injected_content_chars'] = len(compact)
    consumption['injected_content_sha256'] = hashlib.sha256(
        compact.encode('utf-8')
    ).hexdigest()
    return (
        '<task_skill_context>\n'
        'The following reusable workflow was retrieved for the current task.\n'
        'Apply it only when its assumptions match the repository state, and verify its steps\n'
        'against the current code before making changes.\n'
        f'Skill: {_one_line(selected.get("name"), 180)}\n'
        f'{compact}\n'
        '</task_skill_context>'
    )


def compose_task_agent_message(original_query, skill_context):
    if not skill_context:
        return original_query
    return f'{skill_context}\n\n<current_request>\n{original_query}\n</current_request>'


def _one_line(value, limit):
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 1)] + '…'


def _truncate_structured(value, limit):
    """Bound prose while preserving Markdown headings, lists and decision structure."""
    text = str(value or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    text = re.sub(r'[ \t]+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    if len(text) <= limit:
        return text
    if limit <= 1:
        return '…'[:limit]
    window = text[:limit - 1]
    # Prefer a complete paragraph or list item; otherwise retain the bounded text.
    cuts = [window.rfind('\n\n'), window.rfind('\n- '), window.rfind('\n1. '),
            window.rfind('. '), window.rfind('; ')]
    cut = max(cuts)
    if cut >= max(80, int(limit * 0.55)):
        window = window[:cut].rstrip()
    return window.rstrip() + '…'


def render_task_skill_block(
    anchor_query,
    candidates,
    session_id='session-required',
    service_id='default',
    proxy_base_url='http://proxy:8096',
):
    """Render a deterministic bounded task-only Skill listing."""
    query = _one_line(anchor_query, 1800)
    prefix = (
        '<task_scoped_skills>\n'
        '## Skills for this task (mandatory)\n'
        'These candidates were retrieved specifically for the current task.\n'
        'Before exploring or editing the repository, compare each candidate\'s intended outcome,\n'
        'applicability, and core workflow with the current task.\n\n'
        f'Current task:\n{query}\n\nCandidates:\n'
    )
    suffix = (
        '\n\nIf any candidate has substantive relevance in even one of intended outcome,\n'
        'applicability, or core workflow, you MUST first execute the view_command for\n'
        'the highest-ranked such candidate. Inspect the complete workflow before deciding\n'
        'whether to follow or reject it. A different repository or framework is not by\n'
        'itself a reason to reject a candidate before viewing it.\n'
        'Call skill_view at most once. REJECT_ALL only when every candidate has merely\n'
        'lexical overlap and a different underlying workflow. If there are no candidates,\n'
        'continue without loading a skill.\n'
        'The task search is already complete; do not call skill_search.\n'
        '</task_scoped_skills>'
    )
    selected = list(candidates[:OURS_V2_SEARCH_TOP_K])
    if not selected:
        body = '(none)'
    else:
        available = max(300, OURS_V2_SKILL_BLOCK_CHAR_BUDGET - len(prefix) - len(suffix))
        per_candidate = max(180, available // len(selected))
        rendered = []
        for item in selected:
            skill_id = _one_line(item.get('skill_id'), 160)
            name = _one_line(item.get('name'), 180)
            score = _one_line(item.get('score'), 80)
            view_body = json.dumps({
                'skill_name': name,
                'include_content': True,
                'include_manifest': True,
            }, ensure_ascii=False, separators=(',', ':'))
            view_command = (
                f"curl -sSk -X POST {proxy_base_url.rstrip('/')}/skill-bridge/v3/skill/get-by-name "
                "-H 'content-type: application/json' "
                f"-H 'x-tdai-service-id: {service_id}' "
                f"-H 'x-conversation-id: {session_id}' "
                f"-d '{view_body}'"
            )
            fixed = (
                f'- skill_id: {json.dumps(skill_id, ensure_ascii=False)}\n'
                f'  name: {json.dumps(name, ensure_ascii=False)}\n'
                f'  score: {json.dumps(score, ensure_ascii=False)}\n'
                f'  view_command: {json.dumps(view_command, ensure_ascii=False)}\n'
            )
            text_budget = max(40, per_candidate - len(fixed) - 34)
            description = _one_line(item.get('description'), text_budget // 2)
            snippet = _one_line(item.get('snippet'), text_budget - len(description))
            rendered.append(
                fixed
                + f'  description: {json.dumps(description, ensure_ascii=False)}\n'
                + f'  snippet: {json.dumps(snippet, ensure_ascii=False)}'
            )
        body = '\n'.join(rendered)
    block = prefix + body + suffix
    if len(block) > OURS_V2_SKILL_BLOCK_CHAR_BUDGET:
        closing = '\n</task_scoped_skills>'
        block = block[:OURS_V2_SKILL_BLOCK_CHAR_BUDGET - len(closing)] + closing
    return block


def archive_evidence(directory, session_id, archived_at_ms):
    matches = list((directory/'core-data'/'skill_buffer').glob(
        f'*/*/*/{session_id}/data-{archived_at_ms}.jsonl'
    ))
    if len(matches) != 1:
        return {'archive_evidence_status':'INCOMPLETE','matching_files':len(matches)}
    payload = bench.read(matches[0])
    messages = payload.get('messages') or []
    user_messages = [item.get('content','') for item in messages if item.get('role') == 'user']
    return {
        'archive_evidence_status':'COMPLETE', 'archive_artifact':str(matches[0]),
        'message_count':len(messages), 'user_message_count':len(user_messages),
        'user_messages':user_messages,
        'message_roles':{role:sum(item.get('role') == role for item in messages)
                         for role in sorted({item.get('role') for item in messages if item.get('role')})},
    }


def ours_system_prompt(anchor_query):
    block = OURS_RETRIEVAL_BLOCK.format(task_query=anchor_query.strip())
    return SYSTEM_BASELINE + '\n\n' + block, block


def ours_v2_system_prompt(anchor_query, candidates, session_id='session-required'):
    block = render_task_skill_block(anchor_query, candidates, session_id=session_id)
    return SYSTEM_BASELINE + '\n\n' + block, block


def task_skill_gate_token(session_id, predicted_task, anchor_query):
    source = f'{session_id}\0{predicted_task}\0{anchor_query}'.encode('utf-8')
    return hashlib.sha256(source).hexdigest()[:24]


def build_task_skill_gate_config(service, session_id, task_token, candidates):
    return {
        'schema_version': 1,
        'task_token': task_token,
        'proxy_base_url': f"http://host.docker.internal:{service['proxy_port']}",
        'session_id': session_id,
        'service_id': 'default',
        'candidates': [
            {
                'skill_id': item.get('skill_id'),
                'name': item.get('name'),
            }
            for item in candidates[:OURS_V2_SEARCH_TOP_K]
        ],
    }


def render_task_skill_gate_block(anchor_query, candidates, task_token):
    query = _one_line(anchor_query, 1800)
    selected = list(candidates[:OURS_V2_SEARCH_TOP_K])
    prefix = (
        f'<new_task_skill_action token="{task_token}">\n'
        'NEW TASK. This block supersedes every Skill decision from earlier tasks.\n'
        'A previous VIEW or REJECT does not close this new block.\n'
        'Before repository inspection, decide whether a candidate workflow is reusable.\n'
        'If one is plausibly relevant, its view_command must be your FIRST tool call.\n\n'
        f'Current task:\n{query}\n\nCandidates:\n'
    )
    if not selected:
        body = '(none)'
        suffix = (
            '\n\nNo Skill action is required. Continue with the task.\n'
            '</new_task_skill_action>'
        )
    else:
        available = max(300, OURS_V2_SKILL_BLOCK_CHAR_BUDGET - len(prefix) - 1350)
        per_candidate = max(180, available // len(selected))
        rendered = []
        for index, item in enumerate(selected, 1):
            fixed = (
                f'- candidate_index: {index}\n'
                f'  skill_id: {json.dumps(_one_line(item.get("skill_id"), 160), ensure_ascii=False)}\n'
                f'  name: {json.dumps(_one_line(item.get("name"), 180), ensure_ascii=False)}\n'
                f'  score: {json.dumps(_one_line(item.get("score"), 80), ensure_ascii=False)}\n'
                f'  view_command: node /opt/benchmark/skill-gate.mjs view {task_token} {index}\n'
            )
            text_budget = max(40, per_candidate - len(fixed) - 34)
            description = _one_line(item.get('description'), text_budget // 2)
            snippet = _one_line(item.get('snippet'), text_budget - len(description))
            rendered.append(
                fixed
                + f'  description: {json.dumps(description, ensure_ascii=False)}\n'
                + f'  snippet: {json.dumps(snippet, ensure_ascii=False)}'
            )
        body = '\n'.join(rendered)
        reject_examples = ' '.join(
            f'{index}:<reason>' for index in range(1, len(selected) + 1)
        )
        suffix = (
            '\n\nChoose the candidate whose intended outcome or core workflow best matches the\n'
            'current task, then run that candidate\'s view_command as your FIRST tool call.\n'
            'A different repository, framework, file, or concrete symptom is not a mismatch.\n'
            'When a workflow is plausibly reusable or you are uncertain, VIEW it before deciding.\n\n'
            'When every candidate is clearly unrelated, continue without loading a Skill.\n'
            'You may optionally record that decision with:\n'
            f'   node /opt/benchmark/skill-gate.mjs reject-all {task_token} {reject_examples}\n\n'
            'Replace each <reason> with exactly one of: outcome_mismatch,\n'
            'applicability_mismatch, workflow_mismatch, lexical_only.\n\n'
            'After VIEW succeeds, use the full workflow when it fits. If VIEW_FAILED, continue\n'
            'without probing endpoints or retrying manually. Do not call skill_search or use an\n'
            'alternate HTTP client. This action belongs to this NEW TASK; it is not a repeated\n'
            'gate call and it does not apply to later same-task or Oracle follow-ups.\n'
            '</new_task_skill_action>'
        )
    block = prefix + body + suffix
    if len(block) > OURS_V2_SKILL_BLOCK_CHAR_BUDGET:
        closing = '\n</new_task_skill_action>'
        block = block[:OURS_V2_SKILL_BLOCK_CHAR_BUDGET - len(closing)] + closing
    return block


def ours_v3_system_prompt(anchor_query, candidates, task_token):
    block = render_task_skill_gate_block(anchor_query, candidates, task_token)
    # Lead with the per-task action. In long resumed sessions, a block placed
    # after stable repository instructions was mistaken for an already-closed
    # gate from a previous task.
    return block + '\n\n' + SYSTEM_BASELINE, block


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def suite_slug():
    return re.sub(r'[^a-z0-9-]+','-',bench.suite_config()['benchmark_id'].lower()).strip('-')[:24]


def append(path, item):
    with path.open('a',encoding='utf-8') as handle:
        handle.write(json.dumps(item,ensure_ascii=False)+'\n')


def image(tag):
    return bench.command(['docker','image','inspect',tag,'--format','{{.Id}}']).stdout.decode().strip()


def submission_ready(exit_code, final):
    """Budget exhaustion is gradable; missing output/auth/transport failures aren't."""
    return bool(final and (final.get('subtype') in ('error_max_turns','error_wall_timeout') or
                          (exit_code == 0 and not final.get('is_error'))))


def partial_usage(events):
    """Best available usage for a stream that has no terminal result event."""
    latest = {}
    for event in events:
        if event.get('type') != 'assistant':
            continue
        message = event.get('message', {})
        message_id = message.get('id')
        if message_id:
            latest[message_id] = message.get('usage') or {}
    return {
        key: sum((usage.get(key) or 0) for usage in latest.values())
        for key in ('input_tokens','output_tokens','cache_creation_input_tokens','cache_read_input_tokens')
    }


def preflight_agent_workspace(run, cli_image):
    """Exercise the real suite-specific CLI entrypoint without model access."""
    workspace = bench.checked_workspace(run)
    probe_file = bench.suite_config()['validation']['agent_visible_probe_file']
    script = (
        "from pathlib import Path; "
        f"assert Path('/workspace/{probe_file}').is_file(); "
        "assert not Path('/logs').exists(); "
        "assert not Path('/grader.py').exists(); "
        "assert not Path('/private').exists()"
    )
    bench.command([
        'docker','run','--rm','--network','none','--read-only',
        '--tmpfs','/tmp:rw,nosuid,exec,size=128m',
        '--mount',f'type=bind,src={workspace},dst=/workspace,readonly',
        cli_image,'python','-c',script,
    ])
    return {'status':'PASS','checked_at':now(),'cli_image':cli_image,'probe_file':probe_file}


def capture_conversation_probe(workspace):
    """Capture required state identity and a best-effort full audit snapshot."""
    digest = bench.fingerprint(workspace)
    result = {'noise_submission_sha256':digest,'noise_snapshot_status':'COMPLETE'}
    try:
        snapshot = bench.snapshot_workspace(workspace,digest)
        result['noise_submission_artifact'] = str(snapshot['path'])
    except Exception as error:
        # The hash is sufficient to decide whether noise changed the submission.
        # Losing an auxiliary full copy must not discard a paid Turn or stop the run.
        result['noise_snapshot_status'] = 'INCOMPLETE'
        result['noise_snapshot_error'] = str(error)
    return result


def start_proxy(run, directory, lf_file, token):
    lf = bench.read(lf_file) if lf_file is not None else None
    if not os.environ.get('DEEPSEEK_API_KEY'):
        raise RuntimeError('DEEPSEEK_API_KEY is required; never put it in task files')
    name, network = f'{suite_slug()}-pilot-proxy-'+run, f'{suite_slug()}-pilot-net-'+run
    bench.command(['docker','network','create','--internal',network])
    env = os.environ.copy()
    env.update(PILOT_PROXY_TOKEN=token,BENCHMARK_LANGFUSE_ENABLED='1' if lf else '0')
    if lf:
        env.update(LANGFUSE_BASE_URL=lf['host'].replace('localhost','host.docker.internal').replace('127.0.0.1','host.docker.internal'),LANGFUSE_PUBLIC_KEY=lf['publicKey'],LANGFUSE_SECRET_KEY=lf['secretKey'])
    directory.mkdir(parents=True,exist_ok=True)
    args = ['docker','run','-d','--name',name,'--network','bridge','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','1g','--mount',f'type=bind,src={directory},dst=/logs']
    for key in ('DEEPSEEK_API_KEY','PILOT_PROXY_TOKEN','BENCHMARK_LANGFUSE_ENABLED'):
        args += ['-e',key]
    if lf:
        for key in ('LANGFUSE_BASE_URL','LANGFUSE_PUBLIC_KEY','LANGFUSE_SECRET_KEY'):
            args += ['-e',key]
    try:
        bench.command(args+[image(bench.image_tag('no_skill_proxy'))],env=env)
        bench.command(['docker','network','connect','--alias','proxy',network,name])
        for _ in range(30):
            check = subprocess.run(['docker','exec',name,'node','-e',"fetch('http://localhost:8096/health').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"],capture_output=True)
            if check.returncode == 0:
                return name,network
            running = bench.command(['docker','inspect',name,'--format','{{.State.Running}}']).stdout.decode().strip()
            if running != 'true':
                raise RuntimeError('Pilot Proxy failed to start; inspect its logs')
            time.sleep(1)
        raise RuntimeError('Pilot Proxy health timeout')
    except BaseException:
        logs = subprocess.run(['docker','logs',name],capture_output=True)
        (directory/'startup.log').write_bytes(logs.stdout+logs.stderr)
        subprocess.run(['docker','rm','-f',name],capture_output=True)
        subprocess.run(['docker','network','rm',network],capture_output=True)
        raise


def wait_health(url, process, label, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'{label} exited during startup')
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                body = json.load(response)
            if body.get('status') == 'ok':
                return
        except Exception:
            pass
        time.sleep(.5)
    raise RuntimeError(f'{label} health timeout')


def start_baseline(run, directory, lf_file, resume=False, ours=False, ours_v2=False, ours_v3=False):
    """Start native MemoryProxy/Core with opt-in Ours trigger/injection profiles."""
    if not os.environ.get('DEEPSEEK_API_KEY'):
        raise RuntimeError('DEEPSEEK_API_KEY is required')
    root = Path(__file__).resolve().parents[2]
    node_command = os.environ.get('BENCHMARK_NODE') or shutil.which('node')
    if not node_command:
        raise RuntimeError('Node.js >= 22.16 is required and was not found on PATH')
    node = Path(node_command)
    core_port = int(os.environ.get('BENCHMARK_CORE_PORT', '28420'))
    proxy_port = int(os.environ.get('BENCHMARK_PROXY_PORT', '28096'))
    for port in (core_port, proxy_port):
        probe = subprocess.run(['powershell','-NoProfile','-Command',f"if(Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue){{exit 1}}"],capture_output=True)
        if probe.returncode:
            raise RuntimeError(f'Baseline port {port} is already in use')
    lf = bench.read(lf_file) if lf_file is not None else None
    directory.mkdir(parents=True, exist_ok=resume)
    private = directory/'private'
    private.mkdir(exist_ok=resume)
    posix = lambda p: p.resolve().as_posix()
    core_config = private/'core.yaml'
    ours_any = ours or ours_v2 or ours_v3
    sop_variant = ours_v2 or ours_v3
    service_variant = 'ours_v3' if ours_v3 else 'ours_v2' if ours_v2 else 'ours' if ours else 'baseline'
    extraction_review_profile = 'task_sop_v2' if sop_variant else 'legacy_v2'
    extraction_primary_write_limit = 1 if sop_variant else 0
    extraction_thresholds = '''
    # Ours archives only through L1.5-driven force-archive. These unreachable
    # safety limits disable the native 10-tool/40KB logical trigger without
    # changing the native asynchronous extractor or reviewer.
    toolCallThreshold: 2147483647
    archiveBytes: 2147483647''' if ours_any else ''
    core_langfuse = f'''  langfuse:
    enabled: true
    host: {json.dumps(lf['host'])}
    publicKey: {json.dumps(lf['publicKey'])}
    secretKey: {json.dumps(lf['secretKey'])}''' if lf else '''  langfuse:
    enabled: false'''
    core_config.write_text(f'''deployMode: standalone
stateBackend: local
server:
  host: 127.0.0.1
  port: {core_port}
data:
  baseDir: {posix(directory/'core-data')}
metadata:
  store:
    sqliteBaseDir: {posix(directory/'metadata')}
memory:
  storeBackend: sqlite
  embedding:
    provider: none
skill:
  enabled: true
  routing:
    mode: bm25
  extraction:
    enabled: true
{extraction_thresholds}
    trigger:
      profile: legacy
    valueGate:
      profile: legacy
    reviewPromptProfile: {extraction_review_profile}
    maxPrimaryWrites: {extraction_primary_write_limit}
    maxIterations: 16
observability:
{core_langfuse}
''', encoding='utf-8')
    core_env = os.environ.copy()
    core_env.update(TDAI_GATEWAY_CONFIG=str(core_config),TDAI_GATEWAY_API_KEY='',TDAI_LLM_API_KEY=os.environ['DEEPSEEK_API_KEY'],TDAI_LLM_BASE_URL=OPENAI_COMPATIBLE_BASE_URL,TDAI_LLM_MODEL=os.environ.get('BENCHMARK_EXTRACTION_MODEL', 'deepseek-v4-flash'),TDAI_DATA_DIR=str(directory/'core-data'),LOG_PATH=str(directory/'core-logs'))
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    handles = []
    processes = []
    try:
        core_out = (directory/'core.stdout.log').open('wb'); core_err = (directory/'core.stderr.log').open('wb')
        handles += [core_out,core_err]
        core = subprocess.Popen([str(node),'--import','tsx/esm','src/gateway/server.ts'],cwd=root/'MemoryCore',env=core_env,stdout=core_out,stderr=core_err,creationflags=flags)
        processes.append(core)
        wait_health(f'http://127.0.0.1:{core_port}/health',core,'MemoryCore')

        headers = {'Content-Type':'application/json','x-tdai-service-id':'default'}
        def call(path, body):
            request = urllib.request.Request(f'http://127.0.0.1:{core_port}{path}',json.dumps(body).encode(),headers)
            with urllib.request.urlopen(request,timeout=20) as response:
                result = json.load(response)
            if result.get('code',0) not in (0,200):
                raise RuntimeError(f'Baseline metadata init failed: {result.get("code")}')
            return result['data']
        if resume:
            identity = bench.read(private/'identity.json')
        else:
            admin = call('/v3/internal/meta/user/init-admin',{'username':f'{suite_slug()}-{run}'})
            headers['x-tdai-user-key'] = admin['user_key']
            team = call('/v3/meta/team/create',{'name':f'{bench.suite_config()["benchmark_id"]} benchmark {run}','owner_user_id':admin['user_id']})
            agent = call('/v3/meta/agent/create',{'name':f'{bench.suite_config()["benchmark_id"]} benchmark agent','team_id':team['team_id'],'owner_user_id':admin['user_id'],'prompt':'Fix and test the current coding task.'})
            task = call('/v3/meta/task/create',{'title':f'{bench.suite_config()["benchmark_id"]} benchmark session','team_id':team['team_id'],'creator_user_id':admin['user_id'],'linked_agents':[{'agent_id':agent['agent_id']} ]})
            identity = {'user_id':admin['user_id'],'user_key':admin['user_key'],'team_id':team['team_id'],'agent_id':agent['agent_id'],'task_id':task['task_id']}
            bench.write(private/'identity.json',identity)
            bench.write(directory/'identity.public.json',{k:v for k,v in identity.items() if k!='user_key'})

        proxy_config = private/'proxy.yaml'
        task_scoped_listing_config = '\n  injectSessionAvailableSkills: false' if sop_variant else ''
        task_scoped_tools_config = '\n  injectSkillTools: false' if ours_v3 else ''
        proxy_langfuse = f'''langfuse:
  enabled: true
  debug: true
  host: {json.dumps(lf['host'])}
  publicKey: {json.dumps(lf['publicKey'])}
  secretKey: {json.dumps(lf['secretKey'])}''' if lf else '''langfuse:
  enabled: false
  debug: false'''
        proxy_config.write_text(f'''server:
  host: 127.0.0.1
  port: {proxy_port}
  forwardTimeoutMs: 600000
upstream:
  url: {os.environ.get('BENCHMARK_ANTHROPIC_BASE_URL', 'https://api.deepseek.com/anthropic/v1')}
  apiKey: {json.dumps(os.environ['DEEPSEEK_API_KEY'])}
log:
  file: {posix(directory/'proxy-logs')}
  verbose: true
  level: debug
storage:
  enabled: true
  backend: sqlite
  sqlite:
    dbPath: {posix(directory/'proxy.db')}
auth:
  enabled: true
  url: http://127.0.0.1:{core_port}
  timeoutMs: 5000
sessionInit:
  enabled: true
  headerAutoSelect:
    enabled: true
  debugVerboseLogging: true
injection:
  enabled: true
  injectors: [skill]
  externalGatewayUrl: http://host.docker.internal:{proxy_port}
extraction:
  enabled: true
  extractors: [skill]
skill:
  endpoint: http://127.0.0.1:{core_port}
  serviceToken: local
  serviceId: default
  timeoutMs: 10000
  routingProfile: static
skillRuntime:
{task_scoped_listing_config}
{task_scoped_tools_config}
  allowLlmWrite: false
ccRequestRouting:
  enabled: true
{proxy_langfuse}
''',encoding='utf-8')
        proxy_out = (directory/'proxy.stdout.log').open('wb'); proxy_err = (directory/'proxy.stderr.log').open('wb')
        handles += [proxy_out,proxy_err]
        proxy = subprocess.Popen([str(node),'--import','tsx/esm','src/index.ts','--config',str(proxy_config)],cwd=root/'MemoryProxy',stdout=proxy_out,stderr=proxy_err,creationflags=flags)
        processes.append(proxy)
        wait_health(f'http://127.0.0.1:{proxy_port}/health',proxy,'MemoryProxy')
        skill_headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer local',
            'x-tdai-service-id': 'default',
        }
        listing_body = json.dumps({
            'team_id': identity['team_id'],
            'agent_id': identity['agent_id'],
            'query': 'baseline startup check',
        }).encode()
        listing_request = urllib.request.Request(
            f'http://127.0.0.1:{core_port}/v3/skill/listing', listing_body, skill_headers,
        )
        with urllib.request.urlopen(listing_request, timeout=20) as response:
            listing_result = json.load(response)
        if listing_result.get('code') not in (0, 200):
            raise RuntimeError('Baseline Skill API preflight failed')
        network = f'{suite_slug()}-baseline-net-'+run
        bench.command(['docker','network','create',network])
        return {
            'processes':processes,'handles':handles,'network':network,
            'proxy_port':proxy_port,'core_port':core_port,'identity':identity,
            'variant':service_variant,
            'native_auto_archive':not ours_any,
            'session_available_skills':not sop_variant,
            'session_skill_tools':not ours_v3,
        }
    except BaseException:
        for process in reversed(processes):
            process.terminate()
        for handle in handles:
            handle.close()
        raise


def external_baseline_from_environment(experiment_run_id):
    """Attach to one experiment-scoped native Baseline without owning it."""
    value = os.environ.get('BENCHMARK_EXTERNAL_BASELINE_CONFIG')
    if not value:
        return None
    path = Path(value).resolve()
    config = bench.read(path)
    if config.get('experiment_run_id') != experiment_run_id:
        raise RuntimeError('External Baseline descriptor belongs to another experiment')
    required = ('directory', 'network', 'proxy_port', 'core_port', 'identity')
    if any(key not in config for key in required):
        raise RuntimeError('External Baseline descriptor is incomplete')
    directory = Path(config['directory']).resolve()
    if not directory.is_dir() or not (directory/'private'/'identity.json').is_file():
        raise RuntimeError('External Baseline storage is unavailable')
    for port, label in ((config['core_port'], 'MemoryCore'), (config['proxy_port'], 'MemoryProxy')):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=5) as response:
                if json.load(response).get('status') != 'ok':
                    raise RuntimeError(f'{label} external health check failed')
        except Exception as error:
            raise RuntimeError(f'{label} external health check failed: {error}') from error
    network = subprocess.run(
        ['docker', 'network', 'inspect', config['network']], capture_output=True,
        text=True, encoding='utf-8', errors='replace',
    )
    if network.returncode:
        raise RuntimeError('External Baseline Docker network is unavailable')
    return config


def remove_owned_network(network, owned):
    """Remove only networks created by this repository-session process."""
    if network and owned:
        subprocess.run(['docker','network','rm',network],capture_output=True)


def container_no_proxy(env):
    """Keep benchmark-local service traffic out of host/image HTTP proxies."""
    values = []
    for raw in (env.get('NO_PROXY', ''), env.get('no_proxy', '')):
        values.extend(part.strip() for part in raw.split(',') if part.strip())
    values.extend(('host.docker.internal', 'proxy', 'localhost', '127.0.0.1', '::1'))
    return ','.join(dict.fromkeys(values))


def invoke_agent(run, session, ordinal, message, directory, network, token, cli_image, timeout, internal_budget=40, base_url='http://proxy:8096/claude-code/pilot', custom_headers=None, system_prompt=SYSTEM_NO_SKILL, skill_gate_config=None):
    rd = bench.run_dir(run)
    name = f'{suite_slug()}-pilot-agent-{run}-{ordinal}'
    session_dir = rd/'pilot/claude-session'
    session_dir.mkdir(exist_ok=True)
    directory.mkdir(parents=True,exist_ok=True)
    env = os.environ.copy()
    # Only a disposable proxy token reaches the Agent; vendor/observability keys do not.
    headers = {'X-Session-Id':session,'X-Pilot-Turn':str(ordinal),**(custom_headers or {})}
    model = bench.suite_config()['agent']['model']
    env.update(ANTHROPIC_API_KEY=token,ANTHROPIC_BASE_URL=base_url,ANTHROPIC_CUSTOM_HEADERS='\n'.join(f'{k}: {v}' for k,v in headers.items()),ANTHROPIC_MODEL=model,CLAUDE_CODE_MAX_OUTPUT_TOKENS='8192',MAX_THINKING_TOKENS='0')
    if skill_gate_config is not None:
        if not SKILL_GATE_HELPER.is_file():
            raise RuntimeError(f'Ours_v3 skill gate helper is missing: {SKILL_GATE_HELPER}')
        env['TDAI_TASK_SKILL_GATE_CONFIG_B64'] = base64.b64encode(
            json.dumps(skill_gate_config, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        ).decode('ascii')
    env['NO_PROXY'] = container_no_proxy(env)
    env['no_proxy'] = env['NO_PROXY']
    args = ['docker','run','--rm','-i','--name',name,'--network',network,'--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','256','--memory','2g','--cpus','2','--read-only','--tmpfs','/tmp:rw,nosuid,exec,size=512m','--mount',f'type=bind,src={bench.checked_workspace(run)},dst=/workspace','--mount',f'type=bind,src={session_dir},dst=/session']
    if skill_gate_config is not None:
        args += ['--mount', f'type=bind,src={SKILL_GATE_HELPER.resolve()},dst=/opt/benchmark/skill-gate.mjs,readonly']
    environment_keys = ['ANTHROPIC_API_KEY','ANTHROPIC_BASE_URL','ANTHROPIC_CUSTOM_HEADERS','ANTHROPIC_MODEL','CLAUDE_CODE_MAX_OUTPUT_TOKENS','MAX_THINKING_TOKENS','NO_PROXY','no_proxy']
    if skill_gate_config is not None:
        environment_keys.append('TDAI_TASK_SKILL_GATE_CONFIG_B64')
    for key in environment_keys:
        args += ['-e',key]
    args += [cli_image,'claude','--print','--bare','--safe-mode','--disable-slash-commands','--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--setting-sources','','--model',model,'--tools',TOOLS,'--allowedTools',TOOLS,'--permission-mode','dontAsk','--output-format','stream-json','--verbose','--max-turns',str(internal_budget),'--append-system-prompt',system_prompt]
    args += ['--session-id' if ordinal == 1 else '--resume',session]
    bench.write(directory/'invocation.json',{
        'argv':args,'session_id':session,'ordinal':ordinal,'started_at':now(),
        'user_message':message,
        'task_skill_gate': ({
            'task_token': skill_gate_config['task_token'],
            'candidate_count': len(skill_gate_config['candidates']),
        } if skill_gate_config is not None else None),
    })
    started = time.monotonic()
    timed_out = False
    with (directory/'stderr.log').open('w',encoding='utf-8') as err, (directory/'stream.jsonl').open('w',encoding='utf-8') as out:
        proc = subprocess.Popen(args,stdin=subprocess.PIPE,stdout=out,stderr=err,env=env)
        try:
            proc.communicate(message.encode('utf-8'),timeout=timeout)
        except subprocess.TimeoutExpired:
            # The container is the process boundary: removing it terminates the
            # Agent and every background tool before the workspace is graded.
            timed_out = True
            subprocess.run(['docker','rm','-f',name],capture_output=True)
            proc.kill()
            proc.wait()
        except BaseException:
            subprocess.run(['docker','rm','-f',name],capture_output=True)
            proc.kill()
            proc.wait()
            raise
    events = []
    for line in (directory/'stream.jsonl').read_text(encoding='utf-8').splitlines():
        try:
            event = json.loads(line)
            events.append(event)
        except json.JSONDecodeError:
            pass
    final = next((e for e in reversed(events) if e.get('type')=='result'),None)
    if timed_out:
        if not events:
            raise RuntimeError(f'Claude wall timeout produced no observable submission at turn {ordinal}')
        final = {
            'type':'result','subtype':'error_wall_timeout','is_error':True,'result':'',
            'num_turns':model_call_count(events),'usage':partial_usage(events),
            'wall_timeout_seconds':timeout,
        }
    bench.write(directory/'result.json',{
        'exit_code':proc.returncode,'elapsed_seconds':time.monotonic()-started,
        'final':final,'wall_timeout_submission':timed_out,'ended_at':now(),
    })
    # A bounded Agent loop is a real submission, not a transport failure. The
    # hidden grader still decides its state; the Oracle may continue next turn.
    if not submission_ready(proc.returncode, final):
        raise RuntimeError(f'Claude infrastructure/agent-loop failure at turn {ordinal}; see {directory}')
    return events,final


def recover_ours_boundary_state(source):
    ours_state = source.get('ours') or {}
    boundary_decisions = ours_state.get('boundary_decisions') or []
    active_task = ours_state.get('active_task')
    active_queries = []
    if active_task:
        for decision in reversed(boundary_decisions):
            if decision.get('active_task_after') == active_task and decision.get('decision') == 'new_task':
                start_ordinal = int(decision.get('ordinal') or 0)
                active_queries = [
                    item.get('current_query') for item in boundary_decisions
                    if int(item.get('ordinal') or 0) >= start_ordinal
                    and item.get('active_task_after') == active_task
                    and item.get('current_query')
                ]
                break
    predicted_task_ordinal = max(
        [
            int(match.group(1)) for value in [
                active_task,
                *[item.get('active_task_after') for item in boundary_decisions],
            ]
            if value and (match := re.fullmatch(r'predicted-task-(\d+)', value))
        ] or [0]
    )
    return active_queries, predicted_task_ordinal


def seed_child_checkpoint(source_run, new_run, rd, pilot, selected_events, state):
    """Copy a committed turn, or a reset in-flight turn, into a new run."""
    source_rd = bench.run_dir(source_run)
    source_workspace = bench.checked_workspace(source_run)
    source_report_path = bench.ROOT/'reports'/f'{source_run}.json'
    source = bench.read(source_report_path)
    committed = source.get('progress', {}).get('phase') == 'TURN_COMPLETE'
    in_flight = (
        source.get('progress', {}).get('phase') == 'INTERRUPTED'
        and source.get('checkpoint', {}).get('phase') == 'AGENT_IN_FLIGHT'
        and source.get('final_reset', {}).get('verified') is True
    )
    prepare_failed = (
        source.get('progress', {}).get('phase') == 'INTERRUPTED'
        and source.get('checkpoint', {}).get('safe_to_resume') is True
        and source.get('checkpoint', {}).get('phase') == 'EVENT_BOUNDARY_COMMITTED'
        and source.get('final_reset', {}).get('task_id')
        and source.get('final_reset', {}).get('sha256')
    )
    if not (committed or in_flight or prepare_failed) or not source.get('turns'):
        raise ValueError('Child checkpoint is neither a committed turn nor a reset in-flight turn')
    last = source['turns'][-1]
    if not last.get('ended_at') or not source.get('claude_session_id'):
        raise ValueError('Child checkpoint lacks a complete Agent result')
    event_ids = [event['event_id'] for event in selected_events]
    checkpoint_event = source.get('checkpoint', {}).get('event_id') if in_flight else last.get('event_id')
    if checkpoint_event not in event_ids:
        raise ValueError('Child checkpoint event is not in the current manifest')
    event_index = event_ids.index(checkpoint_event)
    controller = last.get('controller') or {}
    retry_current = in_flight or controller.get('status') == 'ACTIVE'
    event_start = event_index if retry_current else event_index + 1
    if event_start >= len(selected_events):
        raise ValueError('Child checkpoint has no remaining event')
    expected_sha = (
        source.get('final_reset', {}).get('sha256') if (in_flight or prepare_failed) else
        (last.get('grade') or {}).get('submission_sha256') or last.get('noise_submission_sha256')
    )
    observed_sha = bench.fingerprint(source_workspace)
    if expected_sha and observed_sha != expected_sha:
        raise ValueError('Child checkpoint workspace no longer matches its committed turn')

    if in_flight:
        # finally already restored this task to its deterministic Broken State.
        # Rebuild it through the platform instead of deep-copying repositories
        # such as FastAPI into another long Windows path.
        rebuilt_sha = bench.restore(new_run, source['checkpoint']['task_id'])
        if rebuilt_sha != observed_sha:
            raise ValueError('Rebuilt in-flight checkpoint differs from the recorded Broken State')
    elif not prepare_failed:
        shutil.copytree(source_workspace, rd/'workspace')
        bench.write(rd/'owner.json', {'owner':bench.suite_config()['workspace_owner'],'run':new_run})
    if not prepare_failed:
        for name in ('task-state.json', 'run-state.json'):
            shutil.copy2(source_rd/name, rd/name)
    source_session = source_rd/'pilot'/'claude-session'
    if not source_session.is_dir():
        raise ValueError('Child checkpoint has no Claude session artifact')
    shutil.copytree(source_session, pilot/'claude-session')
    dialogue = source_rd/'pilot'/'dialogue.jsonl'
    if dialogue.is_file():
        shutil.copy2(dialogue, pilot/'dialogue.jsonl')

    preserved = {
        key: source.get(key) for key in
        ('tasks','turns','skill_snapshots','agent_workspace_preflight','ours')
    }
    state.update({key:value for key,value in preserved.items() if value is not None})
    recovery_message = controller.get('next_message') if retry_current else None
    current_task = source.get('checkpoint', {}).get('task_id') if in_flight else last.get('task_id')
    if prepare_failed:
        current_task = selected_events[event_start]['event_id']
        prepared = bench.prepare(new_run, current_task)
        initial_grade = bench.grade(new_run)
        if initial_grade['passed'] or initial_grade['infrastructure_error']:
            raise RuntimeError('Resumed Initial Broken State did not produce expected FAIL')
        state.setdefault('tasks', {})[current_task] = {
            'initial_broken_sha256':prepared['broken_sha256'],
            'initial_grade':initial_grade['evaluation_id'],
        }
        recovery_message = prepared['initial_query']
    if in_flight:
        turn_ordinal = source['checkpoint']['ordinal']
        turn_dir = source_rd/'pilot'/f'turn-{turn_ordinal:03}'
        stream = turn_dir/'stream.jsonl'
        events = []
        if stream.is_file():
            for line in stream.read_text(encoding='utf-8').splitlines():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        invocation = bench.read(turn_dir/'invocation.json')
        run_state = bench.read(source_rd/'run-state.json')
        interrupted_kind = 'initial_interrupted' if run_state.get('turns_completed',0) == 0 else 'oracle_interrupted'
        interrupted_turn = {
            'ordinal':turn_ordinal,'event_id':checkpoint_event,'task_id':current_task,
            'kind':interrupted_kind,'user_message':invocation.get('user_message'),
            'started_at':invocation.get('started_at'),'ended_at':source.get('ended_at'),
            'agent_elapsed_seconds':source.get('checkpoint',{}).get(
                'interrupted_elapsed_seconds',
                source.get('checkpoint',{}).get('wall_timeout_seconds',900),
            ),
            'assistant_message':'','agent_stop_reason':'error_wall_timeout_uncommitted',
            'usage':partial_usage(events),'agent_internal_turns':model_call_count(events),
            'model_calls':model_call_count(events),'tool_calls':tool_result_count(events),
            'workspace_reset_after_interrupt':True,
        }
        resume_gate_reoffer = None
        if source.get('variant') == 'ours_v3':
            active_task = (source.get('ours') or {}).get('active_task')
            retrieval = next(
                (
                    item for item in reversed((source.get('ours') or {}).get('retrievals') or [])
                    if item.get('predicted_task') == active_task
                ),
                None,
            )
            if retrieval is not None:
                gate_usage = task_skill_gate_usage(events, retrieval.get('candidate_count', 0))
                interrupted_turn['task_skill_gate'] = gate_usage
                retrieval.update({
                    'selection': gate_usage.get('consumption_status'),
                    'selected_skill_name': gate_usage.get('selected_skill_name'),
                    'selected_skill_id': gate_usage.get('selected_skill_id'),
                    'selected_skill_version': gate_usage.get('selected_skill_version'),
                    'view_count': int(gate_usage.get('consumption_status') in ('VIEWED_BEFORE_REPO','VIEWED_LATE')),
                    'view_attempt_count': gate_usage.get('view_network_attempt_count', 0),
                    'view_before_first_repository_tool': gate_usage.get('consumption_status') == 'VIEWED_BEFORE_REPO',
                    'task_skill_gate': gate_usage,
                    'interrupted_at': source.get('ended_at'),
                })
                if (
                    retrieval.get('candidate_count', 0)
                    and gate_usage.get('consumption_status') in ('GATE_MISSED', 'CANDIDATES_SKIPPED')
                    and gate_usage.get('first_repository_tool_ordinal') is None
                ):
                    resume_gate_reoffer = retrieval
        state['turns'].append(interrupted_turn)
        recovery_message = (
            'The previous tool process was terminated by the evaluation harness. '
            'Continue the current request. The workspace has been restored to its initial broken state, '
            'so re-apply any necessary uncommitted changes.'
        )
    state['session_id'] = source['claude_session_id']
    state['claude_session_id'] = source['claude_session_id']
    state['langfuse_session_id'] = source['claude_session_id']
    state['resumed_from_child_run'] = source_run
    state['checkpoint_source_report'] = str(source_report_path)
    state['checkpoint_source_workspace_sha256'] = observed_sha
    state['checkpoint'] = {
        'safe_to_resume': True, 'phase':'TURN_COMMITTED', 'recorded_at':now(),
        'claude_session_id':source['claude_session_id'],
        'completed_turns':len(state['turns']), 'event_id':checkpoint_event,
        'workspace_sha256':observed_sha,
    }
    active_queries, predicted_task_ordinal = recover_ours_boundary_state(source)
    return {
        'event_start':event_start,
        'ordinal':len(state['turns']),
        'current':current_task,
        'resume_message':recovery_message,
        'recovery':in_flight,
        'active_queries':active_queries,
        'predicted_task_ordinal':predicted_task_ordinal,
        'resume_gate_reoffer':locals().get('resume_gate_reoffer'),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run',default='pilot-'+dt.datetime.now().strftime('%Y%m%d-%H%M%S'))
    parser.add_argument('--langfuse-config',type=Path,default=None,
                        help='Optional. Omit to keep observability local-only.')
    parser.add_argument('--timeout',type=int,default=900)
    parser.add_argument('--variant',choices=('no-skill','baseline','ours','ours_v2','ours_v3'),default='no-skill')
    parser.add_argument('--locale',choices=('en',),default='en',help='Benchmark user-visible messages are frozen in English to avoid cross-runtime encoding drift')
    parser.add_argument('--oracle-probe-task',choices=bench.read(bench.PRIVATE/'manifest.json')['tasks'],help='Separate integration probe: only this task, first real Agent turn capped to one internal iteration; excluded from difficulty estimates')
    parser.add_argument('--only-task',choices=bench.read(bench.PRIVATE/'manifest.json')['tasks'],help='Run one real task with the normal 40-iteration budget; valid for difficulty re-pilots')
    parser.add_argument('--resume-child-run',help='Prior child run stopped at a fully committed turn')
    args = parser.parse_args()
    if args.oracle_probe_task and args.only_task:
        parser.error('--oracle-probe-task and --only-task are mutually exclusive')
    with bench.locked(args.run) as rd:
        pilot = rd/'pilot'
        if pilot.exists():
            raise ValueError('Pilot run already exists; use a new run ID to avoid replaying paid calls')
        pilot.mkdir()
        driver_source = Path(__file__).read_bytes()
        (pilot/'driver.snapshot.py').write_bytes(driver_source)
        session = str(uuid.uuid4())
        token = secrets.token_urlsafe(32)
        cli_image = image(bench.image_tag('claude_cli'))
        protocol = bench.read(bench.PRIVATE/'protocol.json')
        report_path = bench.ROOT/'reports'/f'{args.run}.json'
        internal_limit = protocol['agent_internal_iteration_limit']
        baseline = args.variant == 'baseline'
        ours_v1 = args.variant == 'ours'
        ours_v2 = args.variant == 'ours_v2'
        ours_v3 = args.variant == 'ours_v3'
        ours_sop = ours_v2 or ours_v3
        ours = ours_v1 or ours_sop
        ours_profiles = (
            OURS_V3_PROFILES if ours_v3 else
            OURS_V2_PROFILES if ours_v2 else OURS_V1_PROFILES
        )
        memory_enabled = baseline or ours
        mode = (
            'ours-v3-task-skill-consumption-gate' if ours_v3 else
            'ours-v2-task-scoped-sop-and-injection' if ours_v2 else
            'ours-query-only-l15' if ours_v1 else
            'native-baseline' if baseline else 'no-skill'
        )
        experiment_run_id = os.environ.get('BENCHMARK_EXPERIMENT_RUN_ID', args.run)
        repo_name = os.environ.get('BENCHMARK_REPO_NAME', bench.suite_config()['benchmark_id'])
        repo_session_ordinal = int(os.environ.get('BENCHMARK_REPO_SESSION_ORDINAL', '1'))
        state = {
            'run': args.run,
            'benchmark_run_id': args.run,
            'experiment_run_id': experiment_run_id,
            'repo': repo_name,
            'repo_session_ordinal': repo_session_ordinal,
            # Keep the legacy field for existing reports, while making the two
            # external meanings explicit for longitudinal experiments.
            'session_id': session,
            'claude_session_id': session,
            'langfuse_session_id': session,
            'status':'RUNNING','started_at':now(),'mode':mode,'variant':args.variant,
            'method_revision':(
                OURS_V3_METHOD_REVISION if ours_v3 else
                OURS_V2_METHOD_REVISION if ours_v2 else None
            ),
            'locale':args.locale,'language_policy':'english-only-v1',
            'model':bench.suite_config()['agent']['model'],
            'skill_injection':baseline,'skill_extraction':baseline,
            'cli_image':cli_image,
            'proxy_image':None if memory_enabled else image(bench.image_tag('no_skill_proxy')),
            'protocol':protocol,'tasks':{},'turns':[],'skill_snapshots':[],
        }
        state['skill_injection'] = memory_enabled
        state['skill_extraction'] = memory_enabled
        state['skill_injection_lifecycle'] = (
            'task' if ours_sop else 'session' if memory_enabled else 'disabled'
        )
        state['observability_mode'] = 'langfuse+local' if args.langfuse_config else 'local-only'
        if ours:
            state['profiles'] = dict(ours_profiles)
            state['ours'] = {
                'method_revision':(
                    OURS_V3_METHOD_REVISION if ours_v3 else
                    OURS_V2_METHOD_REVISION if ours_v2 else None
                ),
                'profiles':dict(ours_profiles), 'active_task':None,
                'active_benchmark_task_id':None,
                'boundary_decisions':[], 'extractions':[], 'retrievals':[],
                'archive_outbox':[],
                'boundary_totals':{'calls':0,'latency_ms':0,'usage':{'input':0,'output':0,'total':0}},
                'extraction_totals':{'usage':{'input':0,'output':0,'total':0}},
                'native_auto_archive':False, 'async_extraction':True,
                'session_available_skills':not ours_sop,
                'session_skill_tools':not ours_v3,
            }
        state['driver_sha256'] = hashlib.sha256(driver_source).hexdigest()
        selected_events = bench.read(bench.PRIVATE/'manifest.json')['events']
        if args.oracle_probe_task:
            selected_events = [e for e in selected_events if e['event_id']==args.oracle_probe_task]
            state['mode'] = f'{args.variant}-oracle-integration-probe-not-difficulty-evaluation'
        elif args.only_task:
            selected_events = [e for e in selected_events if e['event_id']==args.only_task]
            state['mode'] = f'{args.variant}-single-task-smoke'
        state['planned_events'] = selected_events
        state['first_turn_internal_budget'] = 1 if args.oracle_probe_task else internal_limit
        resume_context = None
        if args.resume_child_run:
            if args.oracle_probe_task or args.only_task:
                parser.error('--resume-child-run cannot be combined with task selection')
            resume_context = seed_child_checkpoint(
                args.resume_child_run, args.run, rd, pilot, selected_events, state,
            )
            session = state['claude_session_id']
        persist_progress(state, pilot, report_path, 'STARTING')
        proxy = network = None
        network_owned = False
        baseline_service = None
        external_baseline = None
        baseline_directory = None
        base_url = 'http://proxy:8096/claude-code/pilot'
        agent_token = token
        agent_headers = None
        system_prompt = SYSTEM_NO_SKILL
        current = None
        try:
            if memory_enabled:
                external_baseline = external_baseline_from_environment(experiment_run_id)
                if external_baseline:
                    service = external_baseline
                    baseline_directory = Path(service['directory']).resolve()
                    if service.get('variant','baseline') != args.variant:
                        raise RuntimeError('External memory service variant does not match this session')
                else:
                    baseline_directory = pilot/args.variant
                    baseline_service = start_baseline(
                        args.run, baseline_directory, args.langfuse_config,
                        ours=ours_v1, ours_v2=ours_v2, ours_v3=ours_v3,
                    )
                    service = baseline_service
                    network_owned = True
                network = service['network']
                identity = service['identity']
                base_url = f"http://host.docker.internal:{service['proxy_port']}/claude-code/default"
                agent_token = identity['user_key']
                agent_headers = {'X-Team-Id':identity['team_id'],'X-Agent-Id':identity['agent_id'],'X-Task-Id':identity['task_id']}
                system_prompt = SYSTEM_BASELINE
                state['memory_service'] = {
                    **{k:service[k] for k in ('proxy_port','core_port')},
                    'scope':'experiment' if external_baseline else 'repository-session',
                }
                state['identity'] = {k:v for k,v in identity.items() if k!='user_key'}
                persist_progress(state, pilot, report_path, 'OURS_READY' if ours else 'BASELINE_READY')
                if ours and resume_context:
                    replayed = replay_pending_archives(
                        state, pilot, report_path, service, session,
                        ours_profiles['extraction_profile'],
                    )
                    state['ours']['archive_outbox_replayed'] = replayed
                    if replayed:
                        persist_progress(
                            state, pilot, report_path, 'EXTRACTION_OUTBOX_REPLAYED',
                            replayed=replayed,
                        )
            else:
                proxy,network = start_proxy(args.run,pilot/'proxy',args.langfuse_config,token)
                network_owned = True
            ordinal = resume_context['ordinal'] if resume_context else 0
            workspace_preflight_done = bool(state.get('agent_workspace_preflight'))
            event_start = resume_context['event_start'] if resume_context else 0
            current = resume_context['current'] if resume_context else None
            active_queries = list(resume_context.get('active_queries') or []) if resume_context else []
            predicted_task_ordinal = int(resume_context.get('predicted_task_ordinal') or 0) if resume_context else 0
            for event_index in range(event_start, len(selected_events)):
                event = selected_events[event_index]
                conversation_probe = not is_task_event(event)
                if not conversation_probe:
                    if resume_context and event_index == event_start and resume_context['resume_message']:
                        message = resume_context['resume_message']
                    else:
                        current = event['event_id']
                        prepared = bench.prepare(args.run,current)
                        initial_grade = bench.grade(args.run)
                        if initial_grade['passed'] or initial_grade['infrastructure_error']:
                            raise RuntimeError('Initial Broken State did not produce expected FAIL')
                        if not workspace_preflight_done:
                            state['agent_workspace_preflight'] = preflight_agent_workspace(args.run,cli_image)
                            workspace_preflight_done = True
                            persist_progress(state,pilot,report_path,'AGENT_WORKSPACE_READY',task_id=current)
                        state['tasks'][current] = {'initial_broken_sha256':prepared['broken_sha256'],'initial_grade':initial_grade['evaluation_id']}
                        message = prepared['initial_query']
                else:
                    message = event['user_message']
                while True:
                    ordinal += 1
                    recovering = bool(
                        resume_context and resume_context.get('recovery')
                        and event_index == event_start
                    )
                    kind = (
                        'recovery' if recovering else event['event_type'] if conversation_probe
                        else 'initial' if bench.read(rd/'run-state.json')['turns_completed']==0 else 'oracle'
                    )
                    turn_system_prompt = system_prompt
                    agent_message = message
                    retrieval_instruction = None
                    task_skill_block = None
                    task_skill_gate_config = None
                    task_skill_consumption = None
                    retrieval_record = None
                    boundary_record = None
                    if ours:
                        persist_progress(
                            state, pilot, report_path, 'BOUNDARY_RUNNING',
                            ordinal=ordinal, active_task=state['ours']['active_task'],
                        )
                        if recovering and active_queries:
                            boundary_record = {
                                'schema_version':'query_boundary_live/1.0',
                                'decision':'same_task', 'taskBoundary':False,
                                'recent_queries':_bounded_active_queries(active_queries),
                                'current_query':message,
                                'llm_called':False, 'llm_attempts':0, 'llm_latency_ms':0,
                                'usage':{'input':0,'output':0,'total':0},
                                'model':None, 'recovery_policy':'same_task_without_llm',
                            }
                        else:
                            boundary_record = classify_task_boundary(active_queries, message)
                        boundary_record.update({
                            'ordinal':ordinal, 'recorded_at':now(),
                            'active_task_before':state['ours']['active_task'],
                        })
                        if boundary_record['decision'] == 'new_task':
                            if active_queries:
                                persist_progress(
                                    state, pilot, report_path, 'EXTRACTION_ENQUEUE_RUNNING',
                                    ordinal=ordinal, active_task=state['ours']['active_task'],
                                )
                                archive = enqueue_task_archive(
                                    state, pilot, report_path, service, session,
                                    state['ours']['active_task'],
                                    state['ours'].get('active_benchmark_task_id'),
                                    active_queries[0], ours_profiles['extraction_profile'],
                                    closed_before_ordinal=ordinal,
                                )
                                if archive.get('archived_at_ms'):
                                    archive.update(archive_evidence(
                                        baseline_directory, session, archive['archived_at_ms'],
                                    ))
                                archive['incoming_query_excluded'] = message not in archive.get('user_messages', [])
                            predicted_task_ordinal += 1
                            active_queries = [message]
                            state['ours']['active_task'] = f'predicted-task-{predicted_task_ordinal:03}'
                            state['ours']['active_benchmark_task_id'] = current
                            if ours_sop:
                                persist_progress(
                                    state, pilot, report_path, 'TASK_SKILL_RETRIEVAL_RUNNING',
                                    ordinal=ordinal,
                                    active_task=state['ours']['active_task'],
                                )
                                retrieval_record = search_task_skills(service, message)
                                if ours_v3:
                                    task_skill_consumption = prepare_task_skill_consumption(
                                        service, message, retrieval_record['candidates'],
                                    )
                                    task_skill_block = render_materialized_skill_context(
                                        task_skill_consumption,
                                    )
                                    agent_message = compose_task_agent_message(
                                        message, task_skill_block,
                                    )
                                    retrieval_record['usage'] = task_skill_consumption['usage']
                                    retrieval_record['task_skill_consumption'] = {
                                        key: value for key, value in task_skill_consumption.items()
                                        if key != 'content'
                                    }
                                else:
                                    turn_system_prompt, task_skill_block = ours_v2_system_prompt(
                                        message, retrieval_record['candidates'], session_id=session,
                                    )
                                retrieval_record.update({
                                    'ordinal': ordinal,
                                    'predicted_task': state['ours']['active_task'],
                                    'benchmark_task_id': current,
                                    'task_skill_block_chars': len(task_skill_block),
                                    'task_skill_block_sha256': hashlib.sha256(
                                        task_skill_block.encode('utf-8')
                                    ).hexdigest(),
                                    'task_gate_token': None,
                                    'effective_agent_message_sha256': hashlib.sha256(
                                        agent_message.encode('utf-8')
                                    ).hexdigest(),
                                    'injected_at': now(),
                                })
                                state['ours']['retrievals'].append(retrieval_record)
                            else:
                                turn_system_prompt, retrieval_instruction = ours_system_prompt(message)
                        elif not recovering:
                            active_queries.append(message)
                        boundary_record['active_task_after'] = state['ours']['active_task']
                        boundary_record['benchmark_task_id'] = current
                        state['ours']['boundary_decisions'].append(boundary_record)
                        totals = state['ours']['boundary_totals']
                        totals['calls'] += int(bool(boundary_record.get('llm_called')))
                        totals['latency_ms'] = round(totals['latency_ms'] + float(boundary_record.get('llm_latency_ms') or 0),3)
                        for key in ('input','output','total'):
                            totals['usage'][key] += int((boundary_record.get('usage') or {}).get(key,0) or 0)
                        if ours_v3 and recovering:
                            prior_retrievals = [
                                item for item in state['ours'].get('retrievals', [])
                                if item.get('predicted_task') == state['ours']['active_task']
                            ]
                            prior = prior_retrievals[-1] if prior_retrievals else None
                            prior_consumption = (prior or {}).get('task_skill_consumption') or {}
                            selected = prior_consumption.get('selected_candidate')
                            if selected:
                                materialized = materialize_task_skill(service, selected)
                                task_skill_consumption = {
                                    'status': materialized['status'],
                                    'selector': prior_consumption.get('selector'),
                                    'usage': {'input': 0, 'output': 0, 'total': 0},
                                    'selected_candidate_rank': prior_consumption.get('selected_candidate_rank'),
                                    'selected_candidate': selected,
                                    'materialization': {
                                        key: value for key, value in materialized.items() if key != 'content'
                                    },
                                    'replayed_after_interrupt': True,
                                }
                                if materialized['status'] == 'MATERIALIZED':
                                    task_skill_consumption['content'] = materialized['content']
                                task_skill_block = render_materialized_skill_context(
                                    task_skill_consumption,
                                )
                                agent_message = compose_task_agent_message(message, task_skill_block)
                                retrieval_record = prior
                                retrieval_record['resume_materialization'] = {
                                    key: value for key, value in task_skill_consumption.items()
                                    if key != 'content'
                                }
                    turn = {'ordinal':ordinal,'event_id':event['event_id'],'task_id':current,'kind':kind,'user_message':message,'started_at':now()}
                    turn['user_query_sha256'] = hashlib.sha256(message.encode('utf-8')).hexdigest()
                    turn['benchmark_query_unmodified'] = True
                    turn['effective_agent_message_sha256'] = hashlib.sha256(
                        agent_message.encode('utf-8')
                    ).hexdigest()
                    if ours:
                        turn['predicted_task'] = state['ours']['active_task']
                        turn['boundary'] = boundary_record
                        turn['retrieval_instruction_injected'] = retrieval_instruction is not None
                        turn['retrieval_instruction_chars'] = len(retrieval_instruction or '')
                        turn['task_skill_block_injected'] = task_skill_block is not None
                        turn['task_skill_block_chars'] = len(task_skill_block or '')
                        turn['task_skill_block_sha256'] = (
                            hashlib.sha256(task_skill_block.encode('utf-8')).hexdigest()
                            if task_skill_block is not None else None
                        )
                        if retrieval_record is not None:
                            turn['task_skill_retrieval'] = {
                                key: retrieval_record[key]
                                for key in (
                                    'search_query', 'search_query_sha256', 'search_status',
                                    'search_latency_ms', 'top_k', 'candidate_count',
                                    'candidates', 'usage', 'task_skill_block_chars',
                                    'task_skill_block_sha256', 'task_gate_token',
                                    'effective_agent_message_sha256',
                                )
                            }
                            if ours_v3:
                                turn['task_skill_consumption'] = (
                                    retrieval_record.get('task_skill_consumption')
                                    or retrieval_record.get('resume_materialization')
                                )
                    append(pilot/'dialogue.jsonl',{'role':'user',**turn})
                    print(f"Turn {ordinal}: {event['event_id']} ({turn['kind']})",flush=True)
                    budget = state['first_turn_internal_budget'] if ordinal == 1 else internal_limit
                    turn['internal_budget'] = budget
                    set_checkpoint(
                        state, False, 'AGENT_IN_FLIGHT', ordinal=ordinal,
                        event_id=event['event_id'], task_id=current,
                    )
                    persist_progress(state, pilot, report_path, 'AGENT_RUNNING', ordinal=ordinal, event_id=event['event_id'], task_id=current, turn_kind=turn['kind'])
                    events,final = invoke_agent(
                        args.run,session,ordinal,agent_message,pilot/f'turn-{ordinal:03}',
                        network,agent_token,cli_image,args.timeout,budget,base_url,
                        agent_headers,turn_system_prompt,None,
                    )
                    turn['agent_elapsed_seconds'] = bench.read(pilot/f'turn-{ordinal:03}'/'result.json')['elapsed_seconds']
                    turn['assistant_message'] = final.get('result','')
                    turn['agent_stop_reason'] = final.get('subtype')
                    turn['usage'] = final.get('usage',{})
                    turn['agent_internal_turns'] = final.get('num_turns')
                    turn['model_calls'] = model_call_count(events)
                    turn['tool_calls'] = tool_result_count(events)
                    if ours:
                        turn['skill_usage'] = skill_tool_usage(events)
                        if ours_v3 and retrieval_record is not None:
                            consumption = task_skill_consumption or {}
                            materialization = consumption.get('materialization') or {}
                            status = consumption.get('status') or 'REJECTED'
                            turn['task_skill_gate'] = {
                                'gate_required': bool(retrieval_record.get('candidate_count')),
                                'controller_managed': True,
                                'consumption_status': (
                                    'MATERIALIZED_BEFORE_AGENT'
                                    if status == 'MATERIALIZED' else status
                                ),
                                'selected_candidate_index': consumption.get('selected_candidate_rank'),
                                'selected_skill_id': materialization.get('skill_id'),
                                'selected_skill_name': materialization.get('skill_name'),
                                'selected_skill_version': materialization.get('skill_version'),
                                'view_network_attempt_count': len(materialization.get('attempts') or []),
                                'view_content_chars': materialization.get('content_chars'),
                                'view_content_sha256': materialization.get('content_sha256'),
                                'view_before_first_repository_tool': status == 'MATERIALIZED',
                                'repeated_gate_call': False,
                            }
                        if retrieval_instruction is not None:
                            retrieval = {
                                'ordinal':ordinal,
                                'predicted_task':state['ours']['active_task'],
                                'search_query':active_queries[0],
                                'instruction_chars':len(retrieval_instruction),
                                **turn['skill_usage'],
                            }
                            state['ours']['retrievals'].append(retrieval)
                        elif retrieval_record is not None:
                            views = turn['skill_usage'].get('view_calls') or []
                            gate_usage = turn.get('task_skill_gate') or {}
                            gate_viewed = gate_usage.get('consumption_status') in (
                                'VIEWED_BEFORE_REPO', 'VIEWED_LATE', 'MATERIALIZED_BEFORE_AGENT',
                            )
                            if ours_v3:
                                selected_name = gate_usage.get('selected_skill_name')
                                selected_id = gate_usage.get('selected_skill_id')
                                selected_version = gate_usage.get('selected_skill_version')
                            else:
                                selected_name = views[0].get('selected_name') if views else None
                                selected_id = views[0].get('selected_skill_id') if views else None
                                selected_version = views[0].get('selected_version') if views else None
                            retrieval_record.update({
                                'agent_skill_search_count': turn['skill_usage'].get('search_count', 0),
                                'agent_skill_search_attempt_count': turn['skill_usage'].get('search_attempt_count', 0),
                                'manual_skill_view_attempt_count': turn['skill_usage'].get('view_attempt_count', 0),
                                'view_count': 1 if gate_viewed else turn['skill_usage'].get('view_count', 0),
                                'view_attempt_count': (
                                    gate_usage.get('view_network_attempt_count', 0)
                                    if ours_v3 else turn['skill_usage'].get('view_attempt_count', 0)
                                ),
                                'selected_skill_name': selected_name,
                                'selected_skill_id': selected_id,
                                'selected_skill_version': selected_version,
                                'selection': (
                                    gate_usage.get('consumption_status') if ours_v3 else
                                    'VIEW' if views else
                                    'NO_CANDIDATES' if not retrieval_record.get('candidates') else
                                    'REJECT_ALL'
                                ),
                                'repeated_view': (
                                    gate_usage.get('repeated_gate_call', False)
                                    if ours_v3 else turn['skill_usage'].get('repeated_view', False)
                                ),
                                'view_before_first_repository_tool': (
                                    gate_usage.get('consumption_status') in (
                                        'VIEWED_BEFORE_REPO', 'MATERIALIZED_BEFORE_AGENT',
                                    )
                                    if ours_v3 else turn['skill_usage'].get(
                                        'view_before_first_repository_tool', False
                                    )
                                ),
                                **({'task_skill_gate': gate_usage} if ours_v3 else {}),
                                'completed_at': now(),
                            })
                    turn['ended_at'] = now()
                    append(pilot/'dialogue.jsonl',{'role':'assistant',**turn})
                    if conversation_probe:
                        workspace = bench.checked_workspace(args.run)
                        turn.update(capture_conversation_probe(workspace))
                    else:
                        turn['controller'] = bench.end_turn(args.run, reset_terminal=False)
                        turn['grade'] = bench.read(rd/'latest-grade.json')
                        if turn['controller']['status'] != 'ACTIVE':
                            state['tasks'][current].update(
                                status=turn['controller']['status'],
                                user_turns=turn['controller']['turns_completed'],
                                terminal_submission_sha256=bench.fingerprint(bench.checked_workspace(args.run)),
                            )
                    state['turns'].append(turn)
                    set_checkpoint(
                        state, True, 'TURN_COMMITTED', ordinal=ordinal,
                        event_id=event['event_id'], task_id=current,
                        controller_status=(turn.get('controller') or {}).get('status'),
                        workspace_sha256=bench.fingerprint(bench.checked_workspace(args.run)),
                    )
                    persist_progress(state, pilot, report_path, 'TURN_COMPLETE', ordinal=ordinal, event_id=event['event_id'], task_id=current, turn_kind=turn['kind'])
                    print(f"Turn {ordinal}: {event['event_type'].upper() if conversation_probe else turn['controller']['status']}",flush=True)
                    if conversation_probe or turn['controller']['status'] != 'ACTIVE':
                        break
                    message = turn['controller']['next_message']

                next_event = selected_events[event_index + 1] if event_index + 1 < len(selected_events) else None
                at_task_boundary = next_event is None or is_task_event(next_event)
                if at_task_boundary and current:
                    # Measurement is deliberately non-blocking. This observes whatever
                    # the native asynchronous extractor has published at this instant;
                    # it never archives a buffer or waits before the next user query.
                    if memory_enabled:
                        state['skill_snapshots'].append(skill_snapshot(baseline_directory, current))

                    digest = bench.restore(args.run, current)
                    state['tasks'][current]['reset_sha256'] = digest
                    state['tasks'][current]['reset_verified'] = (
                        digest == state['tasks'][current]['initial_broken_sha256']
                    )
                    set_checkpoint(
                        state, True, 'EVENT_BOUNDARY_COMMITTED',
                        event_id=event['event_id'], task_id=current,
                        next_event_id=next_event['event_id'] if next_event else None,
                        workspace_sha256=digest,
                    )
                    persist_progress(
                        state, pilot, report_path, 'TASK_BOUNDARY_COMPLETE',
                        task_id=current, next_event_id=next_event['event_id'] if next_event else None,
                    )
            if memory_enabled:
                if ours and active_queries:
                    # EOF is the only non-boundary flush. The call returns after the
                    # native extraction job is queued, not after review completes.
                    persist_progress(
                        state, pilot, report_path, 'EOF_EXTRACTION_ENQUEUE_RUNNING',
                        active_task=state['ours']['active_task'],
                    )
                    archive = enqueue_task_archive(
                        state, pilot, report_path, service, session,
                        state['ours']['active_task'],
                        state['ours'].get('active_benchmark_task_id'),
                        active_queries[0], ours_profiles['extraction_profile'],
                        closed_before_ordinal=None, eof_flush=True,
                    )
                    if archive.get('archived_at_ms'):
                        archive.update(archive_evidence(
                            baseline_directory, session, archive['archived_at_ms'],
                        ))
                    active_queries = []
                # There is no following query to bias now. Keep services alive long
                # enough to finish queued work only at the final experiment session.
                final_experiment_session = os.environ.get('BENCHMARK_EXPERIMENT_FINAL_SESSION') == '1'
                if not external_baseline or final_experiment_session:
                    persist_progress(state, pilot, report_path, 'FINAL_ASYNC_DRAIN')
                    wait_for_natural_skill_drain(baseline_directory)
                    state['async_drain_completed'] = True
                    label = 'EXPERIMENT_END_AFTER_DRAIN' if external_baseline else 'SESSION_END_AFTER_DRAIN'
                    state['skill_snapshots'].append(skill_snapshot(baseline_directory, label))
                else:
                    # Observe immediately and let native asynchronous extraction overlap
                    # the next repository session, as it would in normal Proxy use.
                    state['skill_snapshots'].append(skill_snapshot(baseline_directory, 'SESSION_END_NO_WAIT'))
            state['status'] = 'COMPLETED'
        except BaseException as error:
            state['status'] = 'INTERRUPTED'
            state['error'] = str(error)
            raise
        finally:
            if current:
                try:
                    digest = bench.restore(args.run,current)
                    state['final_reset'] = {'task_id':current,'sha256':digest,'verified':digest==state['tasks'].get(current,{}).get('initial_broken_sha256')}
                except Exception as error:
                    state['final_reset'] = {'verified':False,'error':str(error)}
            if proxy:
                subprocess.run(['docker','stop','-t','20',proxy],capture_output=True)
                logs = subprocess.run(['docker','logs',proxy],capture_output=True)
                (pilot/'proxy/container.log').write_bytes(logs.stdout+logs.stderr)
                subprocess.run(['docker','rm',proxy],capture_output=True)
            remove_owned_network(network, network_owned)
            if baseline_service:
                for process in reversed(baseline_service['processes']):
                    process.terminate()
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        process.kill()
                for handle in baseline_service['handles']:
                    handle.close()
            state['observability'] = export_langfuse(state,pilot,args.langfuse_config)
            reconcile_ours_local(state, baseline_directory)
            reconcile_ours_langfuse(state,pilot)
            completed = state['status'] == 'COMPLETED'
            state['metrics_completeness'] = {
                'agent_stream_grader_reset':'COMPLETE' if completed else 'INCOMPLETE',
                'skill_snapshots':'COMPLETE' if memory_enabled and completed else 'NOT_APPLICABLE' if not memory_enabled else 'INCOMPLETE',
                'langfuse':state['observability']['status'],
                'local_trajectory':'COMPLETE' if completed else 'INCOMPLETE',
                'local_extraction_audit':'COMPLETE' if ours and completed and state.get('async_drain_completed') else 'DEFERRED' if ours and completed else 'NOT_APPLICABLE' if not ours else 'INCOMPLETE',
                'ours_extension':'COMPLETE' if ours and completed else 'NOT_APPLICABLE' if not ours else 'INCOMPLETE',
                'skill_consumption':(
                    'COMPLETE' if ours_v3 and completed else
                    'INCOMPLETE' if ours_v3 else 'NOT_APPLICABLE'
                ),
            }
            update_extraction_metrics_completeness(state)
            state['ended_at'] = now()
            persist_progress(state, pilot, report_path, state['status'])
        print(f'Pilot complete: {pilot}',flush=True)


if __name__ == '__main__':
    main()
