# Current-version optimization baseline

Run date: 2026-08-21. This report records the unoptimized behavior requested by
`optimization-tasks.md`; it does not claim an optimized result.

## Environment

- Agent: WorkBuddy CLI (`glm-5.1` is rewritten by MemoryProxy to the named upstream model).
- Stack: WorkBuddy → MemoryProxy → MemoryCore, plus a deterministic local knowledge service.
- Assets: 100 text-only, MIT-licensed Skills: 2 from `obra/superpowers` and 98 from
  `alirezarezvani/claude-skills`. Only `SKILL.md` was imported; scripts and resources were excluded.
- Task 1 model: `qwen3.7-plus` (used after the other model's free quota was exhausted).
- Task 2 model: `qwen3-coder-next`.
- Current Skill parameters: `toolCallThreshold=10`, `bytesThreshold=40KB`,
  `maxIterations=16`, transcript head/tail `8000/32000` chars, BM25 `topK=20`,
  `charBudgetPercent=0.01`.

## Task 1 — prompt injection and tool choice

12 cases: 7 positive (3 Skill, 2 Memory, 2 Knowledge) and 5 unrelated coding negatives.

| Metric | Current version |
|---|---:|
| Run success | 100.0% (12/12) |
| Effective call rate | 100.0% (7/7) |
| Correct tool selection rate | 71.4% (5/7) |
| False call rate | 0.0% (0/5) |
| Average injected tool/asset text | 8,630 cl100k tokens / 20,727 chars |
| Prefix stability | 8.3% (12 unique hashes across 12 cases) |
| Average total input tokens | 105,203 |
| Average turns | 11.0 |

The two strict-selection failures were Memory prompts that called both Memory
and Knowledge. All five negative cases avoided injected cloud tools. Injection
text changed in every case, so the current prompt prefix is not stable across
the suite.

## Task 2 — Skill mechanism

Six deterministic JavaScript repair tasks were verified failing before the run
and executed once each. Tests passed after every WorkBuddy run.

| Metric | Current version |
|---|---:|
| pass@1 | 100.0% (6/6) |
| Agent completion rate | 100.0% (6/6) |
| Average turns | 20.0 |
| Average agent input tokens | 200,523 |
| Average agent output tokens | 860 |
| Average total tokens incl. observed distillation (lower bound) | 201,383 |
| Skill hit rate | 0.0% |
| Archive trigger rate | 0.0% |
| Skill distillation requests | 0 |
| Extracted Skill delta | 0 |

The task suite therefore establishes a successful but expensive no-reuse
baseline: the agent solved all fixtures, while the current archive/extraction
path produced no new Skill and no later task reused one. Since no distillation
request occurred, the lower-bound total equals agent input plus output tokens.

## Artifacts

- Machine-readable task 1 rows: `task1-baseline.json`
- Machine-readable task 2 rows: `task2-baseline.json`
- Skill provenance and content hashes: `../corpus/manifest.json`
- Raw prompts and model responses are intentionally gitignored.
