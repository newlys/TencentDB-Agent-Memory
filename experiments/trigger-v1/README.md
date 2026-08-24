# Skill SOP boundary trigger experiment (trigger-v1)

This track isolates **when to extract** from skill quality and routing. It does
not modify the frozen `experiments/baseline` or the independent
`experiments/adaptive-v1` routing experiment.

## Dataset contract

`datasets/sop-boundaries.json` groups related deployment/operations tasks by
software family. Each case is an ordered stream of `conversation/add` batches.
`gold.boundaryAfter` contains zero-based batch indices after which a complete,
reusable SOP is available. `gold.boundaryBefore` marks topic-switch boundaries:
the old buffer should be archived before the indexed batch, leaving that batch
for the next SOP.

The initial set deliberately contains:

- positive completion with verification evidence;
- recovery from an error followed by verified completion;
- intermediate successes that must not trigger extraction;
- advice/explanation without an executed SOP;
- unfinished and failed workflows;
- a new unrelated task arriving after an implicit completion.

Cases are grouped rather than randomly mixed so later end-to-end experiments
can feed extracted skills from earlier tasks into later tasks in the same group.

## Metrics

- boundary precision/recall/F1;
- exact-case accuracy;
- extraction rate and redundant-trigger rate;
- missed-SOP rate;
- estimated judge calls and judge input characters.

Run from the repository root after dependencies are installed:

```bash
npx tsx experiments/trigger-v1/scripts/build-robustness-dataset.ts
npx tsx experiments/trigger-v1/scripts/run-boundary-eval.ts
npx tsx experiments/trigger-v1/scripts/run-value-gate-eval.ts
```

The reviewer and LLM-boundary runs call the configured OpenAI-compatible
endpoint and cache raw predictions. They are deliberately separate from the
deterministic test command so normal unit tests do not spend tokens.

## Reports

- `reports/experiment-report-zh.md`: methods, measurements, limitations, and
  rollout recommendation.
- `reports/mentor-alignment-zh.md`: decision memo for mentor alignment.
- `reports/dataset-card-zh.md`: dataset composition, provenance, and leakage
  controls.
- `reports/related-work-zh.md`: product and benchmark research.
