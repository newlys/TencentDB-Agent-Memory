# adaptive_v1 实验简报

## 受控同仓任务族路由（9 个未见任务，100 个干扰 Skill）

| 指标 | static | adaptive_v1 | 变化 |
|---|---:|---:|---:|
| Skill retrieval hit rate | 100% | 100% | 持平 |
| Precision@K | 0.05 | 1.00 | +0.95 |
| MRR / nDCG@K | 1.00 / 1.00 | 1.00 / 1.00 | 持平 |
| 平均最终 K | 20 | 1 | -95.0% |
| 平均注入 Token（cl100k） | 1,607.8 | 44.0 | -97.3% |
| 平均路由延迟 | 44.8 ms | 368.4 ms | +323.6 ms |
| adaptive rerank P95 总延迟 | — | 594 ms | — |

结论：受控 Gold Skill 场景中，Dynamic Top-k 在不损失命中率的情况下基本消除了干扰 Skill，但增加了约 0.32 秒平均路由开销。

## Task 1 回归（原 12 个用例）

| 指标 | static | adaptive_v1 | 变化 |
|---|---:|---:|---:|
| 有效调用率 | 100% | 100% | 持平 |
| 正确工具选择率 | 100% | 71.4% | -28.6 个百分点 |
| 误调用率 | 0% | 20% | +20 个百分点 |
| 平均注入 Token（cl100k） | 8,176.5 | 7,324.0 | -10.4% |
| 平均 Turn | 7.75 | 7.83 | +1.1% |

主要失败：两个 memory 正例分别额外调用 knowledge、skill；`none_git_concept` 负例误调用 memory。因此当前版本没有通过“有效调用率不下降且误调用率不升高”的 Task 1 门槛。

## WorkBuddy coding smoke（6 题，每题一次）

| 指标 | static | adaptive_v1 | 变化 |
|---|---:|---:|---:|
| pass@1 | 100% | 100% | 持平 |
| 平均 Turn | 20.00 | 20.83 | +4.2% |
| 平均总 Token | 181,638 | 185,325 | +2.0% |
| Skill 工具命中率 | 16.7% | 0% | -16.7 个百分点 |
| Skill 抽取增量 | 0 | 0 | 持平 |

六题均通过，但没有发生归档或 Skill 抽取；adaptive 也没有实际加载 Skill。因此普通 coding 成功不能计为 Skill 收益，当前 smoke 结果未达到 Turn -15%、Token -20% 的目标。

## 当前判断

`adaptive_v1` 已证明 rerank + confidence-aware Dynamic Top-k 能显著提升路由纯度并压缩 Skill listing；但端到端 Agent 指标尚未改善，并出现工具误调用退化。下一轮应优先隔离动态 Skill listing 对其他工具触发的影响、缩短 listing 外层强制提示，并补齐能真实触发“归档 → 抽取 → 后续复用”的任务序列。
