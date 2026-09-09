# SWE-Together integration

This overlay connects an upstream SWE-Together checkout to:

```text
SWE-Together task + User Simulator
  -> Claude Code CLI
  -> repo-session adapter
  -> TencentDB-Agent-Memory Proxy/Core
  -> DeepSeek V4 Flash
  -> official SWE-Together verifier
```

The original task instruction, Docker image, base commit, User Simulator, and
verifier are not modified.  The overlay adds repo-level logical sessions,
cache-prefix stabilization, resumable JSON progress, a token/cache guard, and
the `ours_v3` task-aware controller.  The included frozen evaluation list is 3
repositories / 7 tasks; edit `integration/run_baseline_26.py` to use another
explicit list.

## One-time setup

Place SWE-Together next to this repository root:

```text
TencentDB-Agent-Memory/
  SWE-Together/
  benchmarks/
```

The overlay was verified against upstream commit
`811a70a28ff20bfbeabf9a8b5ec42152d16c9b4f`. The installer warns when the
checkout differs so the exact environment can be restored before reproduction.

Create SWE-Together's documented Python environment, then apply the small
overlay:

```powershell
.\benchmarks\swe-together\install-overlay.ps1 -SWEPath .\SWE-Together
```

## One-command run

```powershell
$env:DEEPSEEK_API_KEY = '<your key>'
.\benchmarks\swe-together\run.ps1 -Variant baseline
.\benchmarks\swe-together\run.ps1 -Variant ours_v3
```

Optional parameters include `-Run`, `-AgentTimeout`, and the three local ports.
Runs are serial (`workers=1`) to preserve task order and stay within modest
Docker disk usage.

Live and final artifacts:

```text
SWE-Together/integration/runs/<run>/status.json
SWE-Together/integration/runs/<run>/guard.json
SWE-Together/integration/runs/<run>/summary.json
SWE-Together/integration/runs/<run>/ours-v3-state.json    # ours_v3 only
SWE-Together/integration/runs/<run>/ours-v3-events.jsonl # ours_v3 only
```

Resume after a network or quota interruption with `-Resume`.  Verified trials
are reused; incomplete/infra trials are rerun.  Memory and Skill persistence is
preserved.
