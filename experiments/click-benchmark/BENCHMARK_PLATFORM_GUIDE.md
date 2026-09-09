# 可复用动态 Skill Benchmark 全流程

## 1. 平台边界

平台执行固定循环：

```text
有序用户事件 → 准备 Broken Repo → 真实 Agent → hidden grader
             → PASS / 预定义 Oracle feedback / max-turn FAIL
             → 安全 reset → 下一个用户事件
```

导入新仓库时，除了 repo、Query、Oracle 和 grader，还必须提供固定 commit、damage/gold patch、
任务与 conversation probe 顺序、最大用户轮数、grader 状态优先级、运行时镜像和 suite 配置。
Agent、状态机、安全 reset、Claude Code、共同实验协议、进度写盘和 Langfuse 回读属于平台。

平台不会把 benchmark task id、SOP family、gold、grader 结果或人为“任务已结束”消息发给 Agent、
MemoryProxy 或未来的 Ours 模块。仓库切换和 reset 是评测器内部动作。

## 2. 需要导入什么、放在哪里

引擎位于 `experiments/click-benchmark/`。新 suite 可放在任意目录，通过
`BENCHMARK_SUITE_ROOT` 指向它；无需复制或修改 `bench.py`、`session_driver.py`、`validate.py`。

```text
<suite>/
  Dockerfile                     # hidden grader 的固定依赖
  Dockerfile.agent               # Agent 的测试工具环境
  container_entrypoint.py        # 仓库需要的运行时适配
  environment.lock.json          # 构建后记录不可变 image ID
  private/
    suite.json                    # repo、镜像、模型、验证与扩展状态
    manifest.json                 # Task 列表和连续用户事件
    protocol.json                 # 当前两组共同预算、轮次、语言和异常规则
    grader.py                     # 接口：grader.py TASK_ID STATE
    tasks/<TASK_ID>/
      task.json                   # Query、Oracle、状态优先级、max turns
      damage.patch                # base commit -> Broken State
      gold.patch                  # Broken State -> 正确行为
  reports/                        # 可公开审查的进度/结果
  runtime/                        # 本地完整轨迹与 workspace
```

Click 的实例就是当前目录：repo 为 `repo/click`，固定 commit 为
`36baa15ff831b939a22bc527cd76ce653ef6f66d`；suite 配置在 `private/suite.json`；6 个任务在
`private/tasks/*`；9 个用户事件在 `private/manifest.json`；Oracle 是各题 `task.json` 的
`feedback_by_state`；hidden grader 是 `private/grader.py`；共同协议在 `private/protocol.json`。

### `suite.json`

```json
{
  "schema_version": 1,
  "benchmark_id": "my-repo-v1",
  "workspace_owner": "benchmark-platform-v1:my-repo-v1",
  "repository": {
    "source_path": "../../repo/my-repo",
    "base_commit": "完整的 40 位 commit"
  },
  "images": {
    "grader": "my-repo-benchmark:local",
    "agent": "my-repo-benchmark-agent:local",
    "no_skill_proxy": "click-pilot-proxy:local",
    "claude_cli": "click-pilot-claude:local"
  },
  "validation": {
    "agent_visible_probe_file": "Agent workspace 必然存在的文件",
    "pythonpath": "/workspace/src",
    "upstream_command": ["项目的", "完整测试命令"]
  },
  "agent": {"model": "deepseek-v4-flash", "language": "en"},
  "extensions": {
    "ours": {
      "status": "not_connected",
      "note": "等最终方法确定后再接入"
    }
  }
}
```

`task.json` 至少包含 `task_id`、`base_commit`、`initial_query`、`max_user_turns`、
`grader_priority`、`feedback_by_state`、`success_condition`。每个失败状态必须恰好有一条确定性
Oracle 回复。当前正式协议统一使用英文；比较其他语言时应建立新的冻结 suite/version。

`manifest.json` 的 task event 顺序必须与 `tasks` 完全一致。`pure_noise` 不携带任务信息且预期
不改代码；`contextual_follow_up` 携带上下文或偏好，不能当 pure noise 计分。

## 3. 一次性构建和无付费验收

在项目根目录运行：

```powershell
$engine = (Resolve-Path 'experiments/click-benchmark').Path
$suite = $engine                         # 新仓库时改为新 suite 的绝对路径
$env:BENCHMARK_SUITE_ROOT = $suite

# 契约、commit 与 patch 预检；不调用 Agent/LLM
python "$engine/validate_import.py" --suite $suite

# 标签应与 suite.json 一致
docker build -f "$suite/Dockerfile" -t click-benchmark:local $suite
python "$engine/bench.py" lock-environment
docker build --build-arg EVALUATOR_IMAGE=click-benchmark:local `
  -f "$suite/Dockerfile.agent" -t click-benchmark-agent:local $suite

# 平台共用的无 Skill 观测代理与固定 Claude Code CLI
docker build -f "$engine/pilot/Dockerfile.proxy" -t click-pilot-proxy:local .
docker build --build-arg AGENT_IMAGE=click-benchmark-agent:local `
  -f "$engine/pilot/Dockerfile.claude" -t click-pilot-claude:local "$engine/pilot"
python "$engine/bench.py" lock-agent-environment

# 每题 Broken FAIL -> Gold PASS -> Reset FAIL；仍不调用 Agent/LLM
python -m unittest discover -s "$engine/tests" -v
python "$engine/validate.py"
python "$engine/audit_environment.py"
# 启停 No-Skill/Baseline 服务并检查原生 Skill API；不调用 Agent/LLM
python "$engine/runtime_preflight.py"
```

每个仓库必须从自己的 Agent 镜像构建独立 Claude CLI 镜像，并在 `suite.json` 中使用独立标签。不能复用 Click 的最终 CLI 镜像，因为其中继承了 Click 专用入口；只复用同一份 `Dockerfile.claude` 和固定 Claude Code 版本。四个验收入口全部通过后才进入实验。

## 4. 密钥与 Langfuse

密钥不进入 suite、task、命令参数或 Agent 容器：

```powershell
$env:DEEPSEEK_API_KEY = '<secret>'
```

Langfuse 配置文件格式为：

```json
{"host":"https://...","publicKey":"pk-...","secretKey":"sk-..."}
```

默认读取 `D:/cc-proxy-smoke/langfuse-20260904/langfuse.private.json`，也可传
`--langfuse-config <path>`。No-Skill 经过不含 TencentDB Memory/Skill 的轻量 transport proxy，
只用于模型访问和观测；Baseline 使用原生 MemoryProxy/Core。Agent 拿不到任何密钥。

Langfuse 用一个 session 聚合连续会话；Agent 请求和 Memory/Skill 抽取是独立 observation。服务停止
并 flush 后，Driver 自动用官方 CLI 回读 session，核对本地 Agent generation。最终一致性未完成时写
`observability.status=INCOMPLETE`，不会伪装完整。
这种“每次调用保留 observation、用 session 聚合多轮会话”的组织遵循
[Langfuse observability best practices](https://langfuse.com/docs/observability/best-practices)；输入、期望行为与
运行结果分离则参考 [Langfuse datasets](https://langfuse.com/academy/datasets)。

## 5. 启动当前两组实验

前台单组运行：

```powershell
$env:BENCHMARK_SUITE_ROOT = (Resolve-Path 'experiments/click-benchmark').Path

python experiments/click-benchmark/session_driver.py --run click-noskill-001 --variant no-skill
python experiments/click-benchmark/session_driver.py --run click-baseline-001 --variant baseline
```

正式并行启动并立即返回：

```powershell
& experiments/click-benchmark/launch_formal.ps1 `
  -Suite experiments/click-benchmark `
  -Variants no-skill,baseline
```

只启动一组：

```powershell
& experiments/click-benchmark/launch_formal.ps1 -Variants baseline
```

run id 不可复用。No-Skill 与 Baseline 可以并行；Baseline 端口占用会明确失败。

当前定义：

- No-Skill：无 TencentDB Skill 提取、存储或注入。
- Baseline：原生 TencentDB-Agent-Memory；原生阈值触发、异步提取、review、library 和注入。

`ours` 此时故意没有 CLI variant。`suite.json` 只保留 `extensions.ours.status=not_connected`，防止把
尚未确定的方法误当成正式实现。以后接入时必须复用相同 Agent、任务资产、grader、reset、语言、内部
预算、Oracle 与指标接口；只允许替换经过确认的 Ours 差异点，并通过独立协议验收后才开放命令。

## 6. 实时进度与最终指标

后台启动索引：

```text
<suite>/reports/formal-launch-latest.json
```

每组实时、原子覆写的 JSON：

```text
<suite>/reports/<run-id>.json
```

它会经历 STARTING、服务 READY、Agent、TURN_COMPLETE、Task reset、final drain 和终态。
更完整但默认不提交的内部状态为 `<suite>/runtime/runs/<run-id>/pilot/session.json`。

结束后主要产物：

```text
reports/<run-id>.json                                  汇总、任务状态、指标、完整性
runtime/runs/<run-id>/pilot/dialogue.jsonl             Query/Oracle/Agent 对话
runtime/runs/<run-id>/pilot/turn-NNN/stream.jsonl      Claude 全事件和工具轨迹
runtime/runs/<run-id>/evaluations/*/result.json        hidden grader 状态
runtime/runs/<run-id>/pilot/baseline/                  Core/Proxy 日志、Skill DB
runtime/runs/<run-id>/pilot/langfuse-observations.json Langfuse API 回读
```

最终 `metrics` 含用户 Task turns、Agent internal turns、独立 model calls、tool calls、Agent 耗时、
input/output/cache tokens。`tasks` 含 PASS/FAIL、用户轮数和
reset SHA；`skill_snapshots` 保存发布的 Skill head；`observability` 汇总 Langfuse generation、injection
span、Memory/Skill generation 与 token。`metrics_completeness` 标出各部分是否完整。

完整 run 应满足 `status=COMPLETED`、每题 `reset_verified=true`、
`metrics_completeness.langfuse=COMPLETE`。正常或中断都会在 `finally` 再安全 reset 当前 workspace；
`INTERRUPTED` 的部分结果不得混入正式比较。

## 7. 当前验收

截至 2026-09-05：Click 导入预检通过（6 tasks / 9 events），21 个平台单元测试通过；新配置入口对
SK06-T01 实测 Broken grader FAIL、reset 后相同 SHA、grader 再次 FAIL。六题完整验证见
`reports/validation.json`。本轮未发起付费 Agent 实验。

平台已具备“导入 suite 资产后预检并启动 No-Skill/Baseline”的入口，并为 Ours 留有显式但不可运行的
扩展位。不同构建系统或需要数据库/服务的仓库可能要调整 suite Dockerfile 和 grader 依赖，但不应
修改会话状态机或共同实验协议。
