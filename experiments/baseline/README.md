# Optimization baseline

This directory contains the reproducible local baseline for task 1 (proxy
system-prompt injection) and task 2 (Skill mechanism) in
`optimization-tasks.md`.

The checked-in configuration contains no API keys. Runtime configs, logs,
downloaded source repositories, and raw model transcripts are ignored. Summary
metrics, manifests, benchmark cases, and scripts are intended to be versioned.

The baseline uses WorkBuddy as the coding agent, MemoryProxy as its OpenAI
Responses endpoint, and the standalone MemoryCore gateway. OpenClaw is not
required.

## Contents

- `config/`: current-version Core and Proxy configuration.
- `corpus/manifest.json`: provenance and hashes for the 100-Skill corpus.
- `cases/`: deterministic task 1 prompts and six initially failing task 2 fixtures.
- `scripts/`: stack launcher, corpus importer, capture proxy, and both benchmark runners.
- `results/baseline-report.md`: the consolidated current-version baseline.

## Reproduce

With `DASHSCOPE_API_KEY`, `AFAC_QWEN_BASE_URL`, and `TDAI_BASELINE_USER_KEY`
set in the environment:

```powershell
.\experiments\baseline\scripts\start-baseline.ps1 -Restart
node .\experiments\baseline\scripts\run-task1-baseline.mjs
node .\experiments\baseline\scripts\run-task2-baseline.mjs
```

The launcher requires Node.js 22 and automatically uses WorkBuddy's bundled
Node.js when it is installed in the standard location.
