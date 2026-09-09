# Benchmark result artifacts

This directory contains the lightweight, reviewable outputs referenced by the
repository report. Large runtime logs, model transcripts, databases, Docker
artifacts, and credentials are intentionally excluded.

## Custom longitudinal benchmark

- `custom-longitudinal/baseline/`: final 18-task native Baseline result and
  frozen plan snapshot (`long-baseline-extraction-audit-20260909-v1`).
- `custom-longitudinal/ours-v3/`: final 18-task task-aware result and frozen
  plan snapshot (`long-ours-v3-final-r5-20260909`).

The result files retain task-level grader, turn, model/tool-call, boundary,
extraction, retrieval, and skill-consumption records where applicable.

## SWE-Together

- `swe-together/baseline-v6/`: selected 3-repository / 7-task Baseline summary
  and manifest.
- `swe-together/ours-v3-r6/`: selected 3-repository / 7-task Ours summary,
  manifest, task-aware state, and generated analysis.
- `swe-together/no-skill-pilot/`: historical generated analysis from the
  earlier 19/21 pilot. As stated inside that report, its Skill chain was
  inactive and it must not be interpreted as the final native Baseline.

Absolute local paths inside raw JSON are provenance from the original Windows
run. Reproduction does not depend on those paths; use the commands documented
in the parent benchmark directories.
