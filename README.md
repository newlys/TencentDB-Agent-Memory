# 基于 Task 优化 Skill 链路

> 本仓库基于 [TencentCloud/TencentDB-Agent-Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory) 开展实验性优化。代码仓库：<https://github.com/newlys/TencentDB-Agent-Memory>。

本工作研究 **Task-aware Skill**：在持续 Coding Session 中先识别可独立完成的 Task，再围绕 Task 提取、检索和注入可执行 SOP。目标不是保存更多 Skill，而是减少多个目标混入同一 Skill，并让后续真正匹配的任务在开始探索前获得可复用流程。

```text
连续用户交互
    ↓
Task Boundary
    ↓
Task-scoped SOP Extraction
    ↓
Skill Store
    ↓
新 Task 到来
    ↓
Task-scoped Retrieval / Selection
    ↓
Pre-agent Skill Materialization
    ↓
Coding Agent
```

## 1. 方法

最终方法只围绕 Skill 链路增加三项 Task-aware 能力，原生 Skill 存储、异步 Worker 和工具接口继续复用 TencentDB-Agent-Memory。

### 1.1 Query-only Task Boundary

Task-aware 模式根据用户 Query 判断：

```text
当前 Task 最近的 User Queries + 当前 User Query
→ same_task / new_task
```

`new_task` 时异步归档上一 Task；`same_task` 时继续累计。Boundary 不接收 benchmark task ID、grader、Gold patch、SOP family 或 Assistant/Tool 轨迹。

主要实现：

- [`MemoryCore/src/offload_server/prompts/task-boundary-prompt.ts`](MemoryCore/src/offload_server/prompts/task-boundary-prompt.ts)
- [`MemoryCore/src/offload_server/parsers/task-boundary-parser.ts`](MemoryCore/src/offload_server/parsers/task-boundary-parser.ts)
- [`experiments/query-boundary-l15-v1/live-query-boundary.ts`](experiments/query-boundary-l15-v1/live-query-boundary.ts)

### 1.2 Task-scoped SOP Extraction

每次 Reviewer 面向一个预测 Task 的完整轨迹及 anchor query，只提取未来 Agent 可以执行和复用的 SOP/workflow。Skill 应描述：

```text
Applicability / Preconditions
→ Ordered Workflow
→ Decision Rules
→ Validation
→ Failure Handling / Rollback
```

仓库背景和用户偏好暂不考虑为 Skill；只有直接约束 SOP 时才作为上下文保留。Skill 身份由：

```text
intended outcome + applicability + core workflow
```

决定，而不是 Repository、Framework、Task ID、文件名或一次故障。创建前检查既有 Skill；同 SOP 优先 UPDATE；单个预测 Task 最多一次主要 Skill mutation；一次性局部修改返回 `Nothing to save`。

主要实现：

- [`MemoryCore/src/core/skill/prompts/skill-review-prompt-task-sop-v2.ts`](MemoryCore/src/core/skill/prompts/skill-review-prompt-task-sop-v2.ts)
- [`MemoryCore/src/core/skill/skill-extractor.ts`](MemoryCore/src/core/skill/skill-extractor.ts)
- [`MemoryCore/src/core/skill/skill-config.ts`](MemoryCore/src/core/skill/skill-config.ts)
- [`MemoryCore/src/core/tdai-core.ts`](MemoryCore/src/core/tdai-core.ts)

### 1.3 Task-scoped Skill Consumption

原生 Baseline 在 Session 上下文中暴露可用 Skill，由 Agent 自主 `skill_view`。最终方法将 listing 生命周期缩小到当前 Task：

```text
Current Task Anchor
→ BM25 Top-3
→ Task-only Selector
→ 最多选择一个 Skill
→ 读取完整 Skill
→ 压缩 Workflow / Decision / Validation
→ Agent 首次仓库操作前注入
```

这解决了早期版本中“检索正确但 Agent 没有调用 `skill_view`”的问题。Task-aware 模式通过兼容配置关闭 Session 初始 `<available_skills>` / `<skill_tools>`，不自动修改其他变体。

主要实现：

- [`MemoryProxy/src/config.ts`](MemoryProxy/src/config.ts)
- [`MemoryProxy/src/injection/index.ts`](MemoryProxy/src/injection/index.ts)
- [`experiments/longitudinal-benchmark/longitudinal_driver.py`](experiments/longitudinal-benchmark/longitudinal_driver.py)
- [`benchmarks/swe-together/overlay/integration/ours_v3_controller.py`](benchmarks/swe-together/overlay/integration/ours_v3_controller.py)

### 1.4 版本演进

文档版本与实验目录中的历史名称对应如下：

| 报告版本 | 核心变化 |
|---|---|
| Ours_v0 | Boundary 切分 Task；在边界异步归档；仍依赖 Agent 主动 search/view。 |
| Ours_v1 | 增加 SOP-only Reviewer、机制级命名、单 Task 主要写入限制和 Task-scoped listing。 |
| Ours_v2 | 增加消费控制器：Driver 检索、轻量选择、物化并在 Agent 工作前注入，不再依赖 Agent 主动 view。 |

更完整的实现和复现说明：

- [方法说明](benchmarks/METHOD.md)
- [Benchmark 总入口](benchmarks/README.md)

## 2. 测评集合

### 2.1 测评集 A：Custom Longitudinal SOP Benchmark

自建测评集用于定向检查 Boundary、SOP 提取和跨 Task/跨 Repository 复用：

| 项目 | 数量 |
|---|---:|
| Repository | 3（Click、Flask、FastAPI） |
| 正式 Task | 18 |
| SOP Task | 15 |
| 人工预期 SOP Family | 7 |
| 具有前序复用机会的 Transfer Task | 8 |
| Non-SOP Task | 3 |
| Noise / contextual events | 8 |

每个 Task 固定：

```text
Pinned Base Commit
→ Damage Patch
→ Initial Query
→ Agent
→ Hidden Grader
→ Oracle Feedback（若仍可继续）
→ PASS / FAIL
```

Gold Patch 只用于验证 `Broken FAIL → Gold PASS → Reset FAIL`，不提供给 Agent、Boundary、Reviewer 或检索器。不同 Task 的工作树独立恢复，Memory/Skill Session 按 Repository 连续。

详见 [Custom Benchmark](benchmarks/custom-platform/README.md)。

### 2.2 测评集 B：SWE-Together

盲测使用 SWE-Together 的冻结子集：

| 项目 | 数量 |
|---|---:|
| Repository | 3（entireio/cli、peteromallet/dataclaw、nagi-ovo/gemini-voyager） |
| Task | 7 |

原始 Instruction、Docker、Base Commit、User Simulator 和 Verifier 均保持不变。Claude Code 经 TencentDB-Agent-Memory Proxy 调用上游模型；User Simulator 独立调用模型，不读取实验组 Skill。

选择 SWE-Together 而不是仅使用 SWE-bench 类静态 Issue-to-Patch 数据，是因为本研究需要观察真实的多轮用户 turn 、需求追加和 Task切换。SWE-Together 的 User Simulator 能在 Agent执行过程中自动生成后续用户回复。

这 7 题按 Repository 连续运行，但它们只是探索性主题分组，**不是人工 Gold 标注的同 SOP transfer families**。

详见 [SWE-Together Integration](benchmarks/swe-together/README.md)。

## 3. 实验协议与公平性

### 3.1 统一调用链

```text
Benchmark → Claude Code CLI → MemoryProxy → deepseek-v4-flash

SWE-Together User Simulator → deepseek-v4-flash（独立链路）
```

### 3.2 Session 与环境

- 不同 Repository 使用不同 Proxy/Memory logical session。
- 同一 Repository 的多个 Task 共享 Proxy/Memory session，使 Buffer 能自然累计。
- Custom Benchmark 中，一个 Repository 内的 Claude Code 对话使用同一 session ID，后续 Turn 通过 `--resume` 连续。
- SWE-Together 保留官方独立 Trial/Docker；跨 Task 只共享 Repo 级 Proxy/Memory session，不合并物理代码环境。
- 每个 Task 使用自己的固定 Base Commit/容器或执行 reset；前一 Task 的代码修改不污染后一 Task。
- Gold Task Boundary 不发送给 Agent 或 Memory 系统。

### 3.3 Claude Code 隔离

实验使用隔离容器、禁用 MCP 与外部 settings source，并使用实验专属 Session目录；不读取用户机器上的全局 Claude 配置或跨实验持久化 Memory。

### 3.4 成本口径

- `Agent non-cache token = input_tokens + output_tokens`。
- `Method overhead = Boundary + Extraction + Retrieval/Selector`。
- “含蒸馏总 Token”使用 Agent non-cache 加 Method overhead。
- `cache_read_input_tokens` 单独报告，不与新计算 Token 按同一成本直接相加。

## 4. 实验结果

### 4.1 Custom Longitudinal Benchmark（18 Task）

最终严格对比采用：

- Baseline：`long-baseline-extraction-audit-20260909-v1`；
- Ours：`long-ours-v3-final-r5-20260909`。
- 轻量结果产物：[Baseline](benchmarks/results/custom-longitudinal/baseline/result.json) / [Ours_v3](benchmarks/results/custom-longitudinal/ours-v3/result.json)。

No-Skill pilot 完成了链路验证，但没有形成与当前换题后18题完全一致的一次完整 run，因此不将不完整结果混入严格总表。

| 指标 | 优化方向 |  | Baseline | Ours |
|---|---:|---:|---:|---:|
| 最终任务通过率 | ↑ |  | 18/18（100%） | 18/18（100%） |
| 首轮 Pass@1 | ↑ |  | **18/18（100%）** | 17/18（94.4%，SK06-T01 多 1 个 User Turn，该题无 Skill 候选，非 Skill 优化直接导致） |
| 平均 User Turn | ↓ | **1.00** | 1.06 |
| 平均 Agent Internal Turn | ↓ |  | **26.56** | 38.67 |
| 平均 Agent non-cache Token | ↓ |  | **93,822** | 127,879 |
| 平均含蒸馏总 Token | ↓ |  | **167,373** | 180,877 |
| 平均 Tool Calls | ↓ |  | **25.28** | 38.00 |
| 最终独立 Skill | 适中 |  | 3 个 Repo级聚合 Skill | 4 个机制级 SOP Skill |
| 有效 Task提取事件 | 适中 |  | 非 Task口径 | 6/15 SOP Task（4 CREATE、2 UPDATE） |
| 预期复用任务 Top-3 Recall | ↑ |  | 0 | 5/8（62.5%） |
| 预期复用任务实际物化 | ↑ |  | 0 | 2/8（25.0%） |
| 已提取 Skill 后续被物化 | ↑ |  | 0/3 | 2/4（50.0%） |

Skill 机制链路开销对比
Baseline 原生 Reviewer 的15次可恢复调用共消耗1,323,909 Token；而Ours_v3 在 Task-aware 方法链路上的额外开销为 953,965 Token，其中包括 Boundary 判断、Task-scoped Skill 提取和 Skill 检索：

```text
Ours Boundary       15,844
Ours Extraction    926,735
Ours Retrieval      11,386
合计                953,965
```

这里统计的是 Skill 方法侧开销，不包含 Coding Agent 解题 Token。与 Baseline 原生 Skill 提取相比，Ours_v3 的方法侧开销减少 369,944 Token，下降约 27.9%。但 Ours_v3 的 Coding Agent 调用更多，因此将 Agent 解题和方法开销合并后，Ours_v3 的非缓存 Token 并未整体下降。

#### Task Boundary

| 指标 | 结果 |
|---|---:|
| 评价事件 | 27 |
| Boundary Accuracy | 100%（27/27） |
| Precision | 100% |
| Recall | 100% |
| 过切率 | 0%（0/9 same-task events） |
| 漏切率 | 0%（0/18 new-task events） |
| Boundary LLM调用 | 24（每个 Repo 首 Query 直接初始化） |
| 平均 Token / LLM decision | 660 |
| 平均延迟 / LLM decision | 1.96 s |

早期在 SWE-Together Boundary 样本上探索过向量相似度和硬字段。收益不稳定：同一 Task 的排错、修改、验证 Query 可能语义距离很远，不同 Task 又可能因 Repository和关键词相同而高度相似；硬字段对表达形式敏感，增加复杂度但没有带来稳定增益。因此最终采用 Query-only 目标连续性判断，并通过 Prompt约束而不是向量阈值做决策。

#### Skill 提取质量

Baseline 最终形成：

```text
click-source-bug-triage
flask-source-bug-triage
fastapi-source-bug-triage
```

原生 SkillExtractor 没有强制添加仓库名；这些名称来自原生 Reviewer 允许 project-specific Skill，且缺少针对跨仓库 SOP 的抽象约束。

Ours 最终形成：

```text
layered-input-precedence
all-path-resource-cleanup
sequence-multivalue-collection
final-matching-release-cleanup
```

Ours 将 Repository作为案例证据，而以目标、适用性和 workflow 作为 Skill身份；同机制跨仓库任务可 UPDATE 同一个 Skill。

#### Skill 检索、消费与局部收益

8个预期复用任务中，正确 Skill 进入 Top-3 为5/8，最终在 Agent仓库操作前注入为2/8。主要瓶颈从“Agent不调用 view”转移为“Selector 对跨 Framework候选仍偏保守”。

明确的正向案例 `FL-SK01-T02 → layered-input-precedence`：

| 指标 | Baseline | Ours | 变化 |
|---|---:|---:|---:|
| Internal Turns | 29 | 16 | -44.8% |
| 耗时 | 192 s | 91 s | -52.8% |
| Agent non-cache input + output | 49.2K | 34.7K | -29.5% |

### 4.2 SWE-Together（3 Repo / 7 Task）

轻量结果产物：[Baseline v6](benchmarks/results/swe-together/baseline-v6/summary.json) / [Ours_v3 r6](benchmarks/results/swe-together/ours-v3-r6/summary.json) / [生成的分析报告](benchmarks/results/swe-together/ours-v3-r6/analysis.md)。

| 指标 | Baseline v6 | Ours_v3 r6 | 变化 |
|---|---:|---:|---:|
| PASS | 3/7（42.9%） | **4/7（57.1%）** | +14.3 pp |
| Reward总和 | 1.644 | **2.410** | +0.766 |
| 平均 Reward | 0.2349 | **0.3443** | +46.6% |
| 平均 User Turn | **3.14** | 3.86 | +22.7% |
| 平均 Model Calls | **110.57** | 118.57 | +7.2% |
| 平均 Tool Calls | **123.00** | 131.71 | +7.1% |
| 平均 Agent耗时 | 1,613.9 s | **1,582.1 s** | -2.0% |
| 平均非缓存 Token |  ——| 约 1.08M/Task | 由于测评环境早期不稳定，存在断点重跑的情况，导致记录丢失 |

Ours 的 Task链路记录：

| 指标 | 结果 |
|---|---:|
| 观察 Query | 27 |
| 预测 Task | 7 |
| `same_task` | 20 |
| Boundary Token | 23,261 |
| Reviewer generations | 8 |
| Extraction Token | 1,674,392 |
| 最终机制级 Skill | 4 |
| Task检索 | 7 |
| 有候选的检索 | 3 |
| Selector调用/Token | 3 / 1,189 |
|  |

最终 Skill包括：

```text
change-detection-git-status-fallback
cross-platform-compat-retrofit
case-insensitive-regex-presence-prefilter
break-mutation-observer-feedback-loop
```

Token方面，Baseline v6 由多次在测评平台刚搭建时测评不稳定，丢失了部分 token 数据，部分入选 Trial 运行于缓存修复前且缺少终态 usage；Ours 是缓存健康的新 run。

## 5. 结论与局限

本工作完成了：

```text
Task Boundary
→ Task-scoped SOP Extraction
→ Mechanism-level Skill Identity
→ Task-scoped Retrieval
→ Pre-agent Materialization
```

现有证据支持：

1. 不泄露 Gold边界时，Query-only task 分割可在自建连续轨迹中准确识别 Task切换；
2. Task-scoped Reviewer 相比 Baseline 更容易产生机制级、跨仓库可复用的 SOP；
3. 正确 Skill被及时注入时，个别高匹配任务的 Turn、时间和 non-cache Token显著下降；

主要局限：

- 自建集只有18题，Agent轨迹随机性仍会显著影响总体指标；
- Ours 在 `SK06-T01` 多用了一个 Oracle Turn，真实结果予以保留；
- 当前 Selector偏保守，正确 Top-3候选并不总能转化为消费；
- SWE-Together 冻结7题不是 Gold同 SOP transfer set，且 Baseline历史 Trial存在缓存状态混杂；
- 当前研究集中于 Coding SOP，尚未系统覆盖偏好、长期用户记忆和一般事实性 Memory。

后续需要在更大、明确具有跨 Task SOP复用机会的连续任务集上重复运行，并将缓存健康、异步 Skill可见时间和完整成本采集固定为实验协议。

## 6. 复现

- [Benchmark 总入口](benchmarks/README.md)
- [方法说明](benchmarks/METHOD.md)
- [Custom Benchmark 一键入口](benchmarks/custom-platform/README.md)
- [SWE-Together 一键入口](benchmarks/swe-together/README.md)

核心方法配置：

```text
variant = ours_v3
boundary_profile = query_only_l15
extraction_profile = task_scoped_sop_v2
retrieval_profile = task_scoped_skill_consumption_v3
```

---

原项目版权和许可证遵循 [MIT License](LICENSE)；感谢 TencentDB Agent Memory 与 SWE-Together 的开源工作。
