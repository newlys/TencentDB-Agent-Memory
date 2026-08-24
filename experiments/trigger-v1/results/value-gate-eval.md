# precision_v1 value-gate evaluation

Cases: 44 (22 should extract / 22 should not).

| Metric | Result |
|---|---:|
| LLM calls avoided | 16 (36.4%) |
| Skip precision | 100.0% |
| Positive pass-through recall | 100.0% |
| Unsafe false skips | 0 |

The gate is a cost/safety prefilter, not the final extraction classifier. `review` and `extract` both continue to the reviewer LLM.
