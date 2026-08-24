# Skill reviewer prompt evaluation

24-case stratified sample (12 extract / 12 no-extract), same model and tool simulator.

| Profile | Precision | Recall | F1 | False extraction | Positive quality | Prompt tokens | Completion tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| legacy_v2 | 66.7% | 100.0% | 80.0% | 50.0% | 88.9 | 244447 | 7289 |
| precision_v3 | 100.0% | 66.7% | 80.0% | 0.0% | 97.5 | 84774 | 7206 |
| balanced_v4 | 100.0% | 100.0% | 100.0% | 0.0% | 100.0 | 103601 | 7976 |
