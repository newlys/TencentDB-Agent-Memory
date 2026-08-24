# Skill 触发与提取机制调研

## 产品机制对比

| 系统 | 记忆/规则的产生 | 何时生效 | 对本方案的启示 |
|---|---|---|---|
| Cursor Memories | 后台 sidecar 模型从对话被动生成候选，用户批准后保存 | 项目范围自动引用 | “触发候选”与“最终写入”必须分离，并保留审批/可见性 |
| Windsurf Memories/Rules | 自动记忆与用户规则并存 | always、模型判断、glob、manual；always 内容有持续上下文成本 | 生命周期与作用域和提取质量同等重要 |
| Claude Code | 通过层级化 `CLAUDE.md` 管理 | 企业、项目、用户、本地逐层加载 | 背景事实/偏好不应硬塞成 SOP，应该进入不同作用域 |
| Aider | 用户显式指定 conventions 文件，只读加载 | 每次会话 | 对稳定规范，显式来源比自动挖掘更可靠 |
| Continue | Rules 支持 glob、regex、description、alwaysApply | 可由模型基于 description 选择 | 模型判断适合语义灰区，不适合所有 turn 都调用 |
| OpenHands | Skill 支持 always、keyword、task 与 progressive disclosure | 任务初始化或关键词命中 | 多级门控和渐进加载可降低上下文成本 |
| Cline | 项目/全局 rules 文件 | 作用域内持久加载 | 生成 Skill 后仍需 scope、优先级、禁用和版本治理 |

官方资料： [Cursor Memories](https://docs.cursor.com/en/context/memories)、[Windsurf Memories](https://docs.windsurf.com/zh/windsurf/cascade/memories)、[Claude Code memory](https://docs.anthropic.com/zh-CN/docs/claude-code/memory)、[Aider conventions](https://aider.chat/docs/usage/conventions.html)、[Continue rules](https://docs.continue.dev/customize/deep-dives/rules)、[OpenHands skill architecture](https://docs.openhands.dev/sdk/arch/skill)、[Cline rules](https://docs.cline.bot/customization/cline-rules)。

共同结论不是“多生成”或“少生成”，而是把候选检测、价值判断、内容蒸馏、作用域和生命周期分开。自动化越强，越要抑制错误规则被长期注入后造成的负迁移。

## 模型是否应该判断提取时机

应该，但只放在不确定带，且先 shadow。模型边界判断能理解隐式完成、语义换题和跨表达同义关系；缺点是时延、成本、非确定性和 prompt injection 面。最合适的结构是：

1. 硬上限保证缓冲区不会无限长；
2. 确定性 SOP 信号处理高置信边界；
3. 仅对不确定带调用小模型，模型只返回结构化 `same_sop/completed/confidence`，无写权限；
4. 价值门和 reviewer 再决定是否真正写入。

本轮 qwen3.7-plus 只对结构门筛出的 21/79 个 batch 调用，边界 P/R/F1 均为 93.3%；关闭 thinking 后 completion tokens 从 19,397 降到 1,124，约减少 94.2%。因此它适合作为 shadow oracle 和困难样本挖掘器，当前不建议成为同步主路径。

## 测评基准

[AgentBench](https://github.com/THUDM/AgentBench) 提供 OS 交互任务和容器化环境，适合验证真实命令执行；[InterCode](https://github.com/princeton-nlp/intercode) 强调交互式代码环境；[SWE-agent trajectories](https://github.com/SWE-agent/SWE-agent/blob/main/docs/usage/trajectories.md) 和 [SWE-bench Experiments](https://github.com/SWE-bench/experiments) 适合轨迹级错误分析；[SWE-bench 论文](https://arxiv.org/abs/2310.06770) 说明真实仓库 issue 可以作为端到端任务来源。它们能补足人工对话集，但不能直接提供“正确 Skill 边界”，仍需把执行轨迹映射为任务切点并人工复核。
