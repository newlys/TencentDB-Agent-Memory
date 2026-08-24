# adaptive_v1 A/B report

## Controlled same-repository routing

| Metric | static | adaptive_v1 | absolute delta | relative change |
|---|---:|---:|---:|---:|
| Skill retrieval hit rate | 100.0% | 100.0% | 0.0% | 0.0% |
| Precision@K | 0.05 | 1.00 | 0.95 | 1900.0% |
| MRR | 1.00 | 1.00 | 0.00 | 0.0% |
| nDCG@K | 1.00 | 1.00 | 0.00 | 0.0% |
| Final K | 20.00 | 1.00 | -19.00 | -95.0% |
| Injected cl100k tokens | 1607.78 | 44.00 | -1563.78 | -97.3% |
| Route latency ms | 44.80 | 368.44 | 323.64 | 722.4% |

## Task 1 regression

| Metric | static | adaptive_v1 | absolute delta | relative change |
|---|---:|---:|---:|---:|
| Effective call rate | 100.0% | 100.0% | 0.0% | 0.0% |
| False call rate | 0.0% | 20.0% | 20.0% | n/a |
| Correct tool selection | 100.0% | 71.4% | -28.6% | -28.6% |
| Injected cl100k tokens | 8176.50 | 7324.00 | -852.50 | -10.4% |

## WorkBuddy coding smoke A/B

| Metric | static | adaptive_v1 | absolute delta | relative change |
|---|---:|---:|---:|---:|
| pass@1 | 100.0% | 100.0% | 0.0% | 0.0% |
| Average turns | 20.00 | 20.83 | 0.83 | 4.2% |
| Average total tokens | 181638.17 | 185324.50 | 3686.33 | 2.0% |
| Skill tool hit rate | 16.7% | 0.0% | -16.7% | -100.0% |

Natural-extraction and controlled-Gold-Skill results remain separate. Coding success without a relevant Skill hit is not counted as Skill benefit.
