# Product-path Task-aware Skill reproduction

This directory verifies the distinction raised during review:

- the benchmark driver is one evaluation host;
- the normal `MemoryProxy` request path now also has an opt-in task-aware
  lifecycle and does not import or execute benchmark code.

The feature remains **disabled by default**. Native Baseline behavior is
unchanged unless `skillRuntime.taskAware.enabled=true`.

## One-command deterministic check

From PowerShell at the repository root:

```powershell
./benchmarks/product-task-aware/verify.ps1
```

The check proves, without spending model tokens, that a normal Proxy injection
hook performs this lifecycle:

```text
fresh user query
→ native Core BM25 search
→ selector decision
→ native Core get-by-name
→ full Skill materialized
→ compact workflow injected into system context
→ same block retained across agent tool loops
→ same-task follow-up does not retrieve again
→ new-task query force-archives the previous task
```

It also verifies that the public Proxy can identify a fresh human query without
the optional private cost-guard package. A pure `tool_result` is not mistaken
for a new user turn.

## Enabling the real Claude Code path

Merge the settings in [core.task-aware.yaml.example](core.task-aware.yaml.example)
into the Core config and the settings in
[proxy.task-aware.yaml.example](proxy.task-aware.yaml.example) into the Proxy
config. Set `DEEPSEEK_API_KEY`, then start Core and Proxy normally.

Point Claude Code at the existing Anthropic-compatible Proxy route. For the
local defaults below:

```powershell
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:8096/claude-code/default"
$env:ANTHROPIC_AUTH_TOKEN = "local"
claude --print "Inspect this repository and fix the reported problem."
```

The exact route prefix may differ in an authenticated deployment. Session
initialization and identity binding remain the existing TencentDB flow; this
feature does not replace them.

Expected Proxy log evidence:

```text
[task-aware-skill] session=... decision=new_task
[task-aware-skill] session=... retrieval=materialized skill=...
[injection] ✓ Hook "task-aware-skill-injector" ... at point "system.suffix"
```

On the next independent user request in the same Claude session:

```text
[task-aware-skill] session=... decision=new_task
[task-aware-skill] session=... previous_task=archive_enqueued
```

## What is and is not productized

The runtime path from Boundary through pre-agent Skill materialization is now
inside `MemoryProxy`; it no longer depends on
`experiments/click-benchmark/session_driver.py`. It uses the existing Core
`force-archive`, `search`, and `get-by-name` endpoints and the existing Proxy
injection pipeline.

The following boundaries remain intentional:

- Task extraction stays asynchronous. A Skill archived immediately before the
  current task may not be searchable until a later task.
- Core and Proxy each need their corresponding opt-in config because they are
  separate services. There is no hidden benchmark/gold input.
- End-of-session has no universal signal in the Anthropic API. The last active
  task is archived when the next new task arrives; evaluation drivers may still
  explicitly flush at EOF.
- The built-in state is persisted with the existing Proxy session state. A
  deployment that bypasses TencentDB session initialization uses in-process
  fallback state and should not expect restart durability.

## Full benchmark reproduction

The deterministic product-path check above validates wiring. To reproduce the
measured 18-task experiment, follow
[`benchmarks/custom-platform/README.md`](../custom-platform/README.md). The
benchmark still supplies Docker workspaces, Oracle replies, graders, and metric
collection; it is no longer required to supply the task-aware Skill lifecycle
for ordinary Proxy requests.
