# adaptive_v1 evaluation

This directory contains the reproducible Task 2 evaluation introduced with adaptive Skill routing.

1. Start an isolated profile: `powershell -File ../baseline/scripts/start-baseline.ps1 -Restart -Profile static` (repeat later with `adaptive_v1`).
2. Seed the three controlled Gold Skills: `node scripts/seed-gold-skills.mjs`.
3. Run retrieval evaluation: `$env:EVAL_PROFILE='static'; node scripts/run-routing-eval.mjs`.
4. Run the same command after restarting with `adaptive_v1`.
5. Optionally run the unchanged Task 1 and WorkBuddy coding suites with `EVAL_PROFILE` set.
6. Generate the A/B report: `node scripts/build-comparison-report.mjs`.

`datasets/coding-families.json` freezes the three six-task families. The first three tasks in each family are extraction warm-up and the last three are held-out evaluation. `datasets/mbpp.json` freezes the official MBPP warm-up and evaluation IDs and requires network-disabled execution. Natural extraction and controlled Gold Skill tracks must be reported separately.
