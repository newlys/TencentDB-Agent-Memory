# Trigger v3：1000 套独立 SOP 数据集

本版本用于评测“何时结束一个 SOP 并触发 Skill 审查”以及审查后的 `create / update / nothing` 决策。`trigger-v2` 原样保留；v2 的 1105 条是 221 个语义根的相关扰动，v3 的主集则包含 1000 个独立任务根。

## 数据规模

- 100 个 SOP 族，每族 10 套按顺序执行的独立任务。
- 100 个 `create`、150 个 `update`、750 个 `nothing`。
- 200 条 calibration、200 条 development、600 条 final test；按族隔离。
- 每个任务派生 2 个内部非结束前缀、1 个终态验证点和 1 个关闭总结点，共 4000 条相关边界观测，不能作为独立置信区间样本。

## 文件

- `datasets/sop-roots.jsonl`：1000 套 canonical SOP。
- `datasets/boundary-observations.jsonl`：派生边界观测。
- `datasets/provenance.jsonl`：逐条来源、许可证和证据哈希。
- `catalog/skill-targets.json`：100 个目标 Skill 及版本哈希。
- `gold-skills/*/SKILL.md`：每族最终金标 Skill。
- `results/dataset-validation.json`：冻结验证结果。

## 构建与验证

```powershell
..\..\MemoryCore\node_modules\.bin\tsx.cmd scripts\build-dataset.ts
..\..\MemoryCore\node_modules\.bin\tsx.cmd scripts\validate-dataset.ts
..\..\MemoryCore\node_modules\.bin\tsx.cmd scripts\run-model-label-audit.ts
..\..\MemoryCore\node_modules\.bin\tsx.cmd scripts\run-model-boundary-audit.ts
..\..\MemoryCore\node_modules\.bin\tsx.cmd scripts\build-quality-report.ts
```

## 证据口径与限制

数据中的领域命令输出是由确定性评测 fixture 重放并带有证据回执的观察值，不代表曾对生产系统执行。公开仓库和官方文档用于约束工作流结构，原文不被转载。这个口径避免伪称生产执行，但它仍不能替代未来在真实容器、虚拟机或 SWE-bench 仓库中的端到端复现。

`create + update` 的金标比例为 25%，只描述本数据集生命周期分布，不代表最终最优 Skill 提取率。最终参数必须只用 calibration/development 调整，并在冻结的 final test 上报告结果。

模型复核会把金标隐藏，只提供历史 Skill 与 transcript，并报告动作一致率、边界一致率、Cohen's κ 和全部分歧 ID。该结果依赖 `DASHSCOPE_API_KEY`，支持断点续跑。
