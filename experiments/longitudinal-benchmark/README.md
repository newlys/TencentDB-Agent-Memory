# Longitudinal multi-repository benchmark

This layer composes existing executable repository suites into one ordered experiment:

```text
Experiment run
  -> Repo session 1: fresh Claude Code session, multiple task/user turns
  -> Repo session 2: another fresh Claude Code session
  -> Repo session 3: another fresh Claude Code session
```

Gold task boundaries remain inside the controller and are never sent to Claude Code or MemoryProxy. Each repository suite continues to own its damage patches, grader, Oracle responses, reset logic, and event stream.

## Validate without model calls

```powershell
python experiments/longitudinal-benchmark/validate_plan.py `
  --plan experiments/longitudinal-benchmark/plans/click-flask-fastapi-no-skill.json `
  --check-images
```

Validation checks ordered session ordinals, unique repos, pinned commits, complete environment locks, Docker images, English user messages, and a common model/budget/boundary protocol.

## Start the three-repository No-Skill run

```powershell
.\experiments\longitudinal-benchmark\launch.ps1 `
  -Plan .\experiments\longitudinal-benchmark\plans\click-flask-fastapi-no-skill.json
```

The launcher returns after the outer driver creates its progress file. It does not wait for the experiment to finish.

Live progress:

```text
experiments/longitudinal-benchmark/runs/<experiment-run-id>/progress.json
```

Final aggregate:

```text
experiments/longitudinal-benchmark/runs/<experiment-run-id>/result.json
```

Per-repository raw trajectories and metrics remain in each suite's existing `reports/<child-run-id>.json` and `runtime/runs/<child-run-id>/pilot/` paths. The outer report records those paths plus separate `experiment_run_id` and `claude_session_id` fields. Langfuse is optional and disabled when no `-LangfuseConfig` is supplied.

## Start Ours

Ours keeps the native Skill extractor, worker, search, and view tools, but uses
query-only L1.5 task boundaries to force task-scoped asynchronous archives. It
adds a search/view instruction only to the first Agent request of each predicted
task; the benchmark user message is unchanged.

```powershell
.\experiments\longitudinal-benchmark\launch.ps1 `
  -Plan .\experiments\longitudinal-benchmark\plans\click-flask-fastapi-ours.json
```

The same `progress.json`, `result.json`, child reports, raw stream JSONL, archive
evidence, and local extraction audit apply. Ours additionally records frozen
profiles, every boundary decision and token cost, force-archive IDs and query
isolation evidence, extraction CREATE/UPDATE/NOOP outcomes, and real
`skill_search`/`skill_view` ordering.

Ours_v3 keeps the Ours_v2 boundary and SOP extraction unchanged, disables the
session-scoped skill catalogue and curl recipes, and adds task-scoped Skill
consumption. A small task-only selector chooses at most one relevant candidate;
the Driver materializes and deterministically compacts its full workflow before
the Agent starts repository work. This avoids relying on the Agent to remember
or correctly execute `skill_view`. Clearly unrelated candidates are skipped:

```powershell
.\experiments\longitudinal-benchmark\launch.ps1 `
  -Plan .\experiments\longitudinal-benchmark\plans\click-flask-fastapi-ours-v3.json
```

The consumption audit records retrieval, selection, materialization, injected
content hash/size, rejection, and failure states without converting Skill-use
behavior into a grader result. Private SOP-family annotations distinguish a
correct skip from a missed reuse opportunity after the run.

The default launch above is local-only: it does not initialize or export
Langfuse. Agent workspaces and graders run in the suite's pinned Docker images;
MemoryCore and MemoryProxy remain one shared host service so native asynchronous
Skill state is preserved across the three isolated repository sessions.

## Current scope

`no-skill`, native `baseline`, `ours`, `ours_v2`, and `ours_v3` are accepted. Baseline starts MemoryCore and MemoryProxy once at experiment scope and shares the original asynchronous Skill store across repository sessions. It does not publish benchmark task boundaries or wait between repositories; only the final repository performs a natural asynchronous drain. Ours variants share the same native service and asynchronous worker, disable native size/count archives for those variants only, and archive exclusively on predicted L1.5 boundaries plus EOF.

An interrupted Baseline can resume from its last fully completed repository session without mutating the failed run:

```powershell
.\experiments\longitudinal-benchmark\launch.ps1 `
  -Plan .\experiments\longitudinal-benchmark\plans\click-flask-fastapi-baseline.json `
  -Run <new-run-id> `
  -ResumeFrom <interrupted-run-id>
```

The platform copies the native Skill/metadata/Proxy persistence into the new run and reuses completed-session metrics. Resume is rejected if the interrupted repository already made model calls, because silently replaying paid work would corrupt cost measurements.
