# swe-together-baseline-22 分析报告

**生成时间**：2026-09-08 20:55
**实验状态**：手动中止（19/21 已处理，rudel-task-491983 RUNNING 中断 / rudel-task-d1ddb8 PENDING 未启动）
**实验结论**：**本批数据不可作为 Baseline** — skill 链路完全失效，实际等同 No-Skill 版。

---

## 1. 顶层指标

| 指标 | 值 |
|---|---|
| 任务总数 | 21 |
| 有效完成（PASS+FAIL） | 13 |
| INFRA_ERROR | 6 |
| 未完成（中止时） | 2（RUNNING 1 + PENDING 1） |
| PASS | 6 |
| FAIL | 7 |
| **pass@1（有效样本）** | **6/13 = 46.2%** |
| pass@1（全样本，INFRA 计 0） | 6/21 = 28.6% |
| 平均 reward（有效样本） | 0.31 |

## 2. 按 Repo 分布

| repo | PASS | FAIL | INFRA | pass@1 | avg_reward |
|---|---|---|---|---|---|
| entireio/cli | 2 | 4 | 1 | 2/6 = 33% | 0.18 |
| peteromallet/dataclaw | 1 | 0 | 1 | 1/1 = 100% | 0.56 |
| nagi-ovo/gemini-voyager | 2 | 3 | 0 | 2/5 = 40% | 0.29 |
| togetherbench/reigh | 1 | 0 | 4 | 1/1 = 100% | 0.55 |
| obsessiondb/rudel | 0 | 0 | 0 | 0/0（未完成） | — |

注：reigh/rudel 的 INFRA 占比畸高，是 ghcr.io 拉镜像失败 + setup code 7 集中爆发，与代理 / 镜像仓库连通性相关，不能反映 agent 能力。

## 3. 执行效率（13 个有效样本）

| 指标 | 总和 | 平均 | 最小 | 最大 |
|---|---|---|---|---|
| tool_calls | 1600 | 123.1 | 52 | 179 |
| model_calls | 1452 | 111.7 | 53 | 171 |
| user_turns | 56 | 4.3 | 1 | 10 |
| real_followups | 43 | 3.3 | 0 | 8 |
| agent_latency | — | 1518s（25.3min） | 405s | 1811s |

- **超时率**：13 个里 7 个达到 1800s 超时（54%），说明 agent 经常陷入循环 / 卡在某一工具上。
- **tool/model 比值** ≈ 1.10，合理。
- 平均每任务 ~25 分钟，单 thread 跑 21 题理论需 8-9 小时（含 INFRA 重试）。

## 4. Skill 链路（关键问题）

| 指标 | 值 | 备注 |
|---|---|---|
| skill_search_calls 总和 | **0** | 期望 >0 |
| skill_view_calls 总和 | **0** | 期望 >0 |
| archive_triggered 任务数 | **0/21** | 期望 ≥ 1（10 tool_calls 触发） |
| extraction_observed 任务数 | **0/21** | 期望 ≥ 1（40KB 触发） |
| buffer_after 非空任务数 | **0/21** | 期望同 repo 第 2+ 个任务有累积 |

**根因**：`integration/session_header_adapter.py`（aiohttp adapter，监听 :49097）从未被 Claude Code 调用。config.json 的 `ANTHROPIC_BASE_URL` 正确指向 adapter，但 Claude Code 直接用了 base_url，绕过 adapter → 所有 session_id 都是随机 UUID，`swe-together__<owner>__<repo>` 形态的 session 一个都没有 → MemoryProxy 把每次请求当新 session，buffer 永远从 0 开始，更谈不上 archive/extraction/injection。

**结果**：本批数据在「skill 是否提升 pass@1」这个问题上完全无信息量，应视为 **No-Skill 跑了一次的对照组**，不是真 Baseline。

## 5. INFRA_ERROR 根因分布（6 个）

| task_id | 错误类型 | 详情 |
|---|---|---|
| cli-task-408b8c | setup exit 7 | agent/setup 阶段失败 |
| dataclaw-anonymizer-tests | image resolve | ghcr.io 拉镜像失败（EOF） |
| reigh-preset-data-flow | image resolve | ghcr.io EOF |
| reigh-taskspane-lightbox-bug | image resolve | ghcr.io EOF |
| reigh-timeline-multiselect | image resolve | ghcr.io EOF |
| reigh-radix-props-cleanup | image resolve | ghcr.io TLS handshake timeout |
| reigh-timeline-mode-cleanup | image resolve | （重试中又失败） |

重试时 reigh 4 个里成功 1 个（taskspane-lightbox-bug 拿到 PASS），剩余 4 个还是 ghcr.io 间歇性失败 → 代理稳定性不足以撑完整跑。

## 6. 与"真正 Baseline"的差距

| 维度 | 当前 | 真 Baseline 要求 |
|---|---|---|
| adapter 被使用 | ❌ 0 调用 | ✅ 每个请求都过 adapter |
| session_id 形态 | 随机 UUID | `swe-together__<owner>__<repo>` |
| 同 repo buffer 累积 | ❌ 永远 0 | ✅ 第 2+ 题起点非 0 |
| archive/extraction | ❌ 0 次 | ✅ 同 repo ≥ 1 次触发 |
| skill_search/view 注入 | ❌ 0 次 | ✅ agent 周期内可见 ≥ 1 次 |
| INFRA 占比 | 6/19 = 32% | <10%（代理稳定后） |
| 样本量 | 13 有效 | 21/21 全部有效 |

## 7. 下一步行动计划

1. **修 adapter 不被调用的问题**（最高优先级）
   - 排查 Claude Code 启动命令：确认 `--agent-proxy-descriptor` 指向的 JSON 里 base_url 是否真的是 `http://127.0.0.1:49097` 而非直连 core。
   - 在 adapter 加一行启动日志 + 请求计数器，启动后立即发一个 ping 请求验证 adapter 收到。
   - 必要时把 adapter 改成同步前置（在 run_eval 启动 agent 前做健康检查）。

2. **修 ghcr.io 拉镜像稳定性**
   - 选项 A：预拉镜像脚本，启动前 `docker pull` 所有任务的 image，失败重试 3 次。
   - 选项 B：配置 Docker Desktop 镜像代理 + 重试机制。

3. **重跑真 Baseline**
   - 清空当前 baseline-22 的 trials（或换新 run_id `swe-together-baseline-v3`）。
   - 启动后立即验证 adapter log 有流量、首题结束后 buffer 有非 0 计数。
   - 21 题全跑，目标 INFRA < 2，有效样本 ≥ 19。

4. **复用本批数据作为 No-Skill 对照**
   - 13 个有效样本的 pass@1=46.2% 可作为 No-Skill 基线，等真 Baseline 跑完后做差异分析。

---

## 附：中止时进程清单（已全部清理）

| 角色 | PID | 端口 | 状态 |
|---|---|---|---|
| runner (run_eval) | 19508 | — | 已杀 |
| core (MemoryProxy) | 25260 | 49420 | 已杀 |
| proxy | 7660 | 49096 | 已杀 |
| adapter | 17016 | 49097 | 已杀 |
| 残留容器 | — | — | 2 个 reigh 容器已 `docker rm -f` |
