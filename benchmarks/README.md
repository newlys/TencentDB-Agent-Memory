# Reproducible evaluation harnesses

This directory is the lightweight, public entry point for the two evaluation
platforms used by the task-aware Skill work.  It intentionally excludes API
keys, Docker layers, repository checkouts, hidden graders, Gold patches,
trajectories, and run outputs.

## What is included

| Platform | Purpose | Entry point |
| --- | --- | --- |
| Custom longitudinal benchmark | Deterministic Broken/Gold/Oracle tasks grouped as one Claude session per repository | [`custom-platform/README.md`](custom-platform/README.md) |
| SWE-Together | Official multi-user-turn tasks and verifier, routed through TencentDB-Agent-Memory | [`swe-together/README.md`](swe-together/README.md) |

Both platforms use the same experimental invariants:

- Claude Code durable auto-memory is disabled by using isolated containers and
  an explicit empty Claude configuration directory per trial.
- one repository maps to one logical Memory session;
- tasks in that repository run in deterministic order while their code
  workspaces remain independently reset;
- different repositories use different session IDs but share the experiment's
  Skill store;
- Baseline retains upstream session-scoped Skill injection;
- `ours_v3` uses query-only L1.5 boundaries, task-scoped SOP extraction, and
  driver-side Skill selection/materialization (`task-skill-consumption-controller-r5`);
- model, User Simulator, timeout, grader, and task order are held constant
  across compared variants.

Set secrets only in the process environment.  No command here writes them to a
manifest:

```powershell
$env:DEEPSEEK_API_KEY = '<your key>'
```

Run artifacts are written under the selected platform's `runs/` directory and
are ignored by Git.
