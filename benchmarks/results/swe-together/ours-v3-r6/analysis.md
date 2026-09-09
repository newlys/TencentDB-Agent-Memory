# SWE-Together Baseline vs Ours_v3 r6

## Scope

- Frozen suite: 3 repositories, 7 tasks, workers=1.
- Coding Agent and User Simulator: `deepseek-v4-flash`.
- Baseline: `swe-together-baseline-v6`.
- Ours: `swe-together-ours-v3-r6-v1`, revision `task-skill-consumption-controller-r6-explicit-turn`.
- Both runs reached the native verifier for all seven tasks and report no final infrastructure error.

## Outcome and execution metrics

| Metric | Baseline | Ours_v3 r6 | Change |
|---|---:|---:|---:|
| PASS | 3/7 (42.9%) | 4/7 (57.1%) | +14.3 pp |
| Reward sum | 1.644 | 2.410 | +0.766 |
| Mean reward | 0.2349 | 0.3443 | +46.6% |
| User turns | 22 | 27 | +22.7% |
| Model calls | 774 | 830 | +7.2% |
| Tool calls | 861 | 922 | +7.1% |
| Agent latency | 11,297.0 s | 11,074.6 s | -2.0% |

| Task | Baseline | Ours | Reward B -> O | User turns B -> O | Model calls B -> O | Tool calls B -> O |
|---|---|---|---:|---:|---:|---:|
| `cli-task-0ec2e9` | FAIL | FAIL | 0 -> 0 | 6 -> 6 | 145 -> 134 | 172 -> 150 |
| `cli-task-2f5833` | PASS | PASS | .15 -> .55 | 1 -> 1 | 136 -> 122 | 159 -> 135 |
| `cli-task-cd4662` | FAIL | FAIL | 0 -> 0 | 5 -> 2 | 80 -> 90 | 74 -> 97 |
| `dataclaw-windows-path-fix` | PASS | PASS | .494 -> .56 | 4 -> 10 | 66 -> 200 | 76 -> 201 |
| `dataclaw-anonymizer-tests` | PASS | PASS | 1 -> 1 | 1 -> 3 | 127 -> 76 | 153 -> 84 |
| `gemini-voyager-task-64c72f` | FAIL | FAIL | 0 -> 0 | 2 -> 3 | 108 -> 113 | 115 -> 148 |
| `gemini-voyager-task-f519c2` | FAIL | PASS | 0 -> .30 | 3 -> 2 | 112 -> 95 | 112 -> 107 |

## Token comparability

The final selected Baseline is assembled through several resume/skip-existing runs. One selected task has no terminal Claude result usage, and early selected trials were generated before the cache fix. The Ours run is fresh and has a healthy cache throughout. Therefore a complete seven-task token comparison is not methodologically valid.

Raw recoverable Claude result records (six tasks; `cli-task-2f5833` lacks a terminal usage record in both runs):

| Raw recovered usage | Baseline | Ours_v3 r6 |
|---|---:|---:|
| Non-cached input | 28,458,439 | 3,469,371 |
| Output | 589,250 | 696,171 |
| Cache read | 26,472,064 | 57,927,296 |
| Non-cache input + output | 29,047,689 | 4,165,542 |

This large difference must not be attributed to Task-aware Skills: the Baseline set mixes cache-broken early trials with cache-valid resumed trials. Ours reports a healthy run-level cache-read ratio of about 93.9%. For the later cache-valid Baseline subset, Ours still uses fewer fresh tokens, but no Skill was consumed there either, so it remains trajectory/cache evidence rather than Skill-reuse evidence.

## Ours Task Boundary

- Observed queries: 27.
- Predicted task boundaries: 7.
- `same_task`: 20.
- Boundary LLM calls: 24 (the first query of each repository uses the deterministic new-task rule).
- Boundary usage: 19,596 input + 3,665 output = 23,261 tokens.
- Boundary latency: 62.58 seconds total, 2.61 seconds per LLM decision.

The semantic behavior is plausible rather than merely copying official task IDs:

- An Entire CLI instruction beginning `Implement the following plan...` was classified as continuation of the preceding diagnosis.
- A DataClaw follow-up asking to compile regexes and optimize the anonymizer was separated from the preceding Windows-review task as a new task.

## Extraction, retrieval, and consumption

Task-scoped extraction:

- Reviewer generations: 8.
- Extraction usage: 1,648,744 input + 25,648 output = 1,674,392 tokens.
- Final Skill heads: 4.
- Skill names are mechanism/workflow based: `change-detection-git-status-fallback`, `cross-platform-compat-retrofit`, `case-insensitive-regex-presence-prefilter`, and `break-mutation-observer-feedback-loop`.

Task-scoped retrieval:

- Searches: 7.
- Searches with candidates: 3.
- Selector calls: 3.
- Selector usage: 1,114 input + 75 output = 1,189 tokens.
- Selected candidates: 0.
- Skill materializations/injections: 0.

The three candidate sets were rejected as workflow mismatches. In particular, the DataClaw anonymizer optimization saw the prior cross-platform Skill but correctly rejected it as unrelated. The first Gemini task's Skill was only extracted asynchronously after its work, and the following Gemini task found no candidate.

Total directly measured Ours Task-layer LLM overhead is approximately 1,698,842 non-cache tokens:

```text
Boundary       23,261
Extraction  1,674,392
Selector        1,189
Total       1,698,842
```

## Attribution conclusion

Ours improves PASS from 3/7 to 4/7 and mean reward from 0.2349 to 0.3443, with slightly lower aggregate latency. It worsens user turns, model calls, and tool calls.

There is no evidence that the outcome improvement was caused by downstream Skill reuse: no Skill was selected or materialized for any task. The PASS flip on `gemini-voyager-task-f519c2`, higher partial rewards, and task-level efficiency changes must be treated as run-to-run trajectory, User Simulator, prompt, and cache effects.

What this run does support is narrower:

1. L1.5 can find semantically meaningful boundaries inside SWE-Together conversations instead of blindly following benchmark task IDs.
2. Task-scoped extraction produces more mechanism-level, reusable Skill identities than the repository-specific Baseline heads.
3. This seven-task selection does not contain a timely downstream task that matches an already-extracted Skill, so it does not validate the final retrieval-to-consumption-to-efficiency link.

