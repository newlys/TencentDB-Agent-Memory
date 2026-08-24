#!/usr/bin/env python3
"""Build the Chinese dataset card and visual mentor-alignment report."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RESULTS = ROOT / "results"
REPORTS = ROOT / "reports"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def pct(value: int, total: int) -> str:
    return f"{value / total * 100:.1f}%" if total else "0.0%"


def main() -> int:
    REPORTS.mkdir(parents=True, exist_ok=True)
    rows = read_jsonl(DATA / "selected-roots.jsonl")
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    families = json.loads((DATA / "families.json").read_text(encoding="utf-8"))
    validation = json.loads((RESULTS / "validation.json").read_text(encoding="utf-8"))
    replay = json.loads((RESULTS / "workbuddy-replay-status.json").read_text(encoding="utf-8"))
    splits = Counter(row["chronological_split"] for row in rows)
    outcomes = Counter(row["execution_result"]["status"] for row in rows)
    licenses = Counter(row["repository_license"] for row in rows)
    top_families = sorted(families["families"], key=lambda family: (-family["member_count"], family["family_id"]))

    family_table = "\n".join(
        f"| `{family['family_id']}` | {family['member_count']} | {family['repository_count']} | {', '.join(family['top_pre_task_terms'][:5])} | `{family['status']}` |"
        for family in top_families
    )
    source_report = f"""# 原生公开数据驱动的 Skill 评测集：研究与对齐报告

生成日期：2026-08-24
当前状态：**原生数据采集与静态门禁通过；尚未冻结**

## 结论

旧 `trigger-v3` 已通过独立 revert 提交撤销。新链路没有预写 100 个 Skill，也没有生成对话或预填 `create/update/nothing` 比例。本轮从固定 revision 的公开真实任务和原生 OpenHands 轨迹中联结出 **5,000 个候选任务根**，再按仅使用任务开始前字段的确定性规则选出 **1,000 个 provisional roots**。

这 1,000 个任务引用 **{validation['counts']['native_trajectories']:,} 条原生 agent rollout**，覆盖 **{manifest['selected_repository_count']} 个仓库**。严格校验结果为 `{validation['status']}`，近重复对为 {validation['counts']['near_duplicate_pairs']}。自然聚类只发现 {families['candidate_family_count']} 个候选族，而不是为了接近目标强制拆成 100 类。

WorkBuddy A/B 尚未执行：预检状态为 `{replay['status']}`，阻塞项为 `{', '.join(replay['blockers']) or '无'}`。这些结果不会由 OpenHands 原生结果替代，因此当前仓库不会生成 `frozen-roots.jsonl`。

## 数据血缘

```mermaid
flowchart LR
  A[SWE-rebench\n固定 revision] --> C[真实任务根]
  B[OpenHands trajectories\n固定 revision] --> D[原生 rollout 索引]
  C --> E[instance_id 严格联结]
  D --> E
  E --> F[5,000 candidates]
  F --> G[许可 / evaluator / 去重门禁]
  G --> H[1,000 selected roots]
  H --> I[pre-task-only 自然聚类]
  I --> J[31 pending family candidates]
  J --> K[100 对 WorkBuddy A/B]
  K --> L{{效用 + 标注一致率门禁}}
  L -->|通过| M[frozen release]
  L -->|未通过| N[保持 provisional]
```

## 数据漏斗

```mermaid
xychart-beta
  title "真实任务根质量漏斗"
  x-axis ["公开联结候选", "provisional selection", "自然聚类覆盖", "WorkBuddy完成", "最终冻结"]
  y-axis "任务根" 0 --> 5000
  bar [5000, 1000, {families['clustered_root_count']}, {replay['completed_pairs']}, 0]
```

| 阶段 | 数量 | 是否可当最终评测集 |
|---|---:|---|
| 固定 revision 联结候选 | 5,000 | 否 |
| 去重后的 provisional roots | 1,000 | 否，等待真实重放和复核 |
| 自然候选族覆盖 | {families['clustered_root_count']} | 仅用于族发现 |
| 长尾/噪声任务 | {families['unclustered_root_count']} | 保留，用于测试“不应提取” |
| 完成 WorkBuddy A/B | {replay['completed_pairs']} / 100 | 否 |
| 最终 frozen roots | 0 | 尚未发布 |

## 来源判断

| 来源 | 实际用途 | revision / 许可 | 决策 |
|---|---|---|---|
| [nebius/SWE-rebench](https://huggingface.co/datasets/nebius/SWE-rebench) | 当前真实任务根 | `89cdfbab4ab1bd8f5a658bb212d1b63624f4f881` / CC-BY-4.0 | 启用；与轨迹 ID 可严格联结 |
| [OpenHands SWE-rebench trajectories](https://huggingface.co/datasets/nebius/SWE-rebench-openhands-trajectories) | 原生成功、失败和混合轨迹 | `35455389ab51bf5e2306bfd436ef72d0f98bf882` / CC-BY-4.0 | 启用 |
| [SWE-rebench V2](https://huggingface.co/datasets/PrimeIntellect/SWE-rebench-V2) | 后续候选扩充 | `8851474cc41be8bce981362d1363d9f92e247b4b` / CC-BY-4.0 | 暂不联结；现有轨迹 release 与 V2 ID 不兼容 |
| [SWE-Gym](https://github.com/SWE-Gym/SWE-Gym) | 高密度同仓库扩充 | 代码与底层仓库许可分开登记 | 已注册，未混入本轮 1,000 条 |
| [OpenHands Feedback](https://huggingface.co/datasets/OpenHands/openhands-feedback) | 真实用户外部 holdout | MIT | sealed，不参与聚类和调参 |
| [Microsoft AIOpsLab](https://github.com/microsoft/AIOpsLab) | 运维真实运行框架 | MIT | 已注册，待独立运行 |
| [Cloud-OpsBench](https://github.com/LLM4Ops/Cloud-OpsBench) | 潜在运维 benchmark | 当前无明确许可 | `license_blocked`，不下载、不再分发 |
| [SWE-Lancer](https://openai.com/index/swe-lancer/) | 外部真实任务泛化 | 单独核验资产许可 | sealed，不参与族发现 |

## 分布

- 原生结果：成功 {outcomes['native_success']}、失败 {outcomes['native_failure']}、成功与失败 rollout 并存 {outcomes['mixed']}。
- 时间切分：discovery {splits['discovery']}、development {splits['development']}、lifecycle test {splits['lifecycle_test']}、整族 zero-shot {splits['zero_shot_holdout']}、未聚类长尾 {splits['unassigned']}。
- 许可证：{'; '.join(f'{name}={count}' for name, count in licenses.most_common())}。

## 自然候选 Skill 族

下表是由问题陈述聚类得到的证据候选，不是最终 Skill 名称或金标。所有候选都必须经过可复用工作流复核和 WorkBuddy 配对效用验证。

| 候选族 | 根数 | 仓库数 | 任务开始前高权重词 | 状态 |
|---|---:|---:|---|---|
{family_table}

## 与 mentor 对齐的决策点

1. 评测单位是独立真实任务根，不是 rollout 或切片；同一任务的所有轨迹永远同 split。
2. Skill 数量由真实重复模式决定。当前结果是 31 个候选族和 446 个长尾任务，不人为补成 100 类。
3. `create/update/nothing` 必须由后续 A/B 效用反推，不能按目标比例构造。
4. 核心指标保持为 pass@1、含蒸馏总 token、turn、提取率、命中率，并额外报告有益命中与负迁移。
5. 发布器是 fail-closed：100 对 WorkBuddy 重放、族级效用复核、边界一致率 ≥95% 和动作 κ ≥0.90 任一缺失都不生成冻结文件。

## 当前限制与下一批实验

- 当前 1,000 条主要是 Python 软件工程任务，不能代表云运维、Windows、网络和事故响应的总体分布。
- OpenHands 轨迹是针对真实 GitHub 任务产生的原生 agent-environment 轨迹，不等同于真实用户聊天；真实用户差异由 sealed holdout 单独测量。
- 本机 WorkBuddy 可执行文件存在，但缺少 baseline user key，MemoryCore/Proxy 未启动且 Docker daemon 未运行；因此没有伪造 A/B 结果。
- 31 个聚类中存在单仓库簇和宽泛词簇，它们可能在效用审查中被拒绝或拆分；当前均未标成 Skill。
"""
    (REPORTS / "native-data-research-zh.md").write_text(source_report, encoding="utf-8")

    card = f"""# Trigger Real v1 数据卡

- 状态：provisional，未冻结
- candidates：{manifest['candidate_count']}
- selected real roots：{manifest['selected_count']}
- 唯一仓库：{manifest['selected_repository_count']}
- 原生轨迹引用：{validation['counts']['native_trajectories']}
- 自然候选族：{families['candidate_family_count']}
- 严格静态校验：{validation['status']}
- WorkBuddy A/B：{replay['completed_pairs']} / {replay['required_pairs']}
- selected SHA-256：`{manifest['selected_sha256']}`

本数据卡明确不把 selected roots 称为最终冻结集。运行 `scripts/publish_freeze.py`
时，任何真实重放、效用复核或标注门禁缺失都会失败。
"""
    (REPORTS / "dataset-card-zh.md").write_text(card, encoding="utf-8")
    print(f"wrote {REPORTS / 'native-data-research-zh.md'}")
    print(f"wrote {REPORTS / 'dataset-card-zh.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
