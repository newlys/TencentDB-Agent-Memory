# 端到端测评指标契约

## pass@1（任务通过率）

本项目的 pass@1 定义为：每个任务只允许一次独立 agent 尝试，不挑选最佳候选；该次尝试必须由任务的隐藏或隔离测试判定为完全通过。设任务数为 `N`，首个提交通过指示量为 `s_i∈{0,1}`，则：

`pass@1 = (Σ s_i) / N`

这与 SWE-bench 的 single-attempt 口径一致：每个实例提交一个 prediction，并在容器中应用补丁、运行测试，resolution rate 即成功实例比例。OpenAI HumanEval 在多采样场景使用 `1-C(n-c,k)/C(n,k)` 的无偏 pass@k 估计；当每题只有一次真实尝试时，pass@1 就退化为上述样本均值。

严格要求：

- 只统计首个完整 agent attempt，失败后人工修正或第二次重跑不能计入 pass@1；
- 测试必须在 agent 不可见的隔离判分阶段执行，防止针对测试投机；
- 超时、崩溃、无补丁、基础环境失败分开记录；基础环境失败从主分母排除但必须报告；
- 同一任务跑多个随机 seed 时，报告各 seed 的 pass@1 均值和按 task 聚类 bootstrap 区间，不能使用 “任意一次成功” 代替 pass@1；
- Skill 与 baseline 必须使用同一任务、模型、seed、工具权限和最大 turn，进行配对比较。

参考实现与规范：[OpenAI HumanEval evaluator](https://github.com/openai/human-eval/blob/master/human_eval/evaluation.py)、[SWE-bench evaluation guide](https://github.com/SWE-bench/SWE-bench/blob/main/docs/guides/evaluation.md)、[SWE-bench submission checklist](https://github.com/swe-bench/experiments/blob/main/checklist.md)。交互式任务也采用完全成功比例，例如 InterCode 将 `reward == 1.0` 的样本数除以总样本数作为 Success Rate；AgentBench OS 在真实 Ubuntu Docker 中以确定性答案或操作目标计算 SR。

## 五项主指标

| 指标 | 计算口径 | 方向/解释 |
|---|---|---|
| 任务通过率 | 首次独立尝试完全通过数 / 有效任务数 | ↑ pass@1；同时报告配对差值和 95% CI |
| 平均 Token | `(agent input + output + 未被缓存的上下文 + boundary judge + reviewer/distillation + rerank) / 任务数` | ↓；Skill 生成成本按后续评测任务摊销，另报冷启动与稳态 |
| 平均 Turn | agent 完成或终止前的模型交互轮数均值 | ↓；超时任务按实际已消费 turn 计入 |
| Skill 提取率 | 唯一 accepted writes / 合格 SOP 边界 | 非单调；同时报 create/update/duplicate/unsafe、每百任务写入数和净有效提取率 |
| Skill 命中率 | 至少被后续任务真实注入并使用一次的 Skill 数 / 已提取 Skill 数 | ↑；另报任务覆盖率和有益命中率 |

“实际使用”不是向量检索到候选：必须满足 `hit.task_sequence > skill.extracted_sequence`，且 Skill 内容进入任务上下文或被 agent 显式打开；有益命中还要求该任务通过，或相对无 Skill 配对基线减少 turn/token 且不降低正确性。

## 辅助可信度指标

- 负迁移率：命中 Skill 后，baseline 通过但 Skill profile 失败的配对任务比例；
- 冗余写入率：应 update/nothing 却 create 的比例；
- 安全写入率：secret、opt-out、unsafe 样本发生任何写入的比例，门槛必须为 0；
- 生命周期效用：30/60/90 天至少一次有益命中的 Skill 比例；
- 统计：比例给 Wilson 区间，均值与配对差值按 task 聚类 bootstrap；扰动样本按 semantic root 聚类，不能当独立样本。
