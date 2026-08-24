# trigger-v1 boundary evaluation

Dataset: 4 families, 96 cases, 338 batches, 60 positive boundaries.

| Strategy | Precision | Recall | F1 | Exact cases | Extractions/case | Redundant | Missed SOP | Judge calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| sop_v1_score_0.60 | 100.0% | 73.3% | 84.6% | 83.3% | 0.46 | 0.0% | 26.7% | 0 |
| sop_v1_score_0.68 | 100.0% | 73.3% | 84.6% | 83.3% | 0.46 | 0.0% | 26.7% | 0 |
| sop_v1_score_0.76 | 100.0% | 73.3% | 84.6% | 83.3% | 0.46 | 0.0% | 26.7% | 0 |
| sop_v1_score_0.84 | 100.0% | 60.0% | 75.0% | 75.0% | 0.38 | 0.0% | 40.0% | 0 |
| fixed_tool_calls_3 | 100.0% | 40.0% | 57.1% | 62.5% | 0.25 | 0.0% | 60.0% | 0 |
| fixed_tool_calls_2 | 50.0% | 60.0% | 54.5% | 62.5% | 0.75 | 50.0% | 40.0% | 0 |
| fixed_tool_calls_5 | 0.0% | 0.0% | 0.0% | 37.5% | 0.00 | 0.0% | 100.0% | 0 |
| fixed_tool_calls_10 | 0.0% | 0.0% | 0.0% | 37.5% | 0.00 | 0.0% | 100.0% | 0 |

`fixed_tool_calls_10` is the current tool-count baseline (the 40KB cap is also simulated).
All `sop_v1` variants are deterministic and have zero judge calls.

