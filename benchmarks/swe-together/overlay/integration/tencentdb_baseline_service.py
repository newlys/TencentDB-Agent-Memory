#!/usr/bin/env python3
"""Start/stop an isolated native TencentDB-Agent-Memory Baseline for SWE-Together."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


HERE = Path(__file__).resolve().parent
SWE_ROOT = HERE.parent
PROJECT_ROOT = SWE_ROOT.parent


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def wait_health(url: str, process: subprocess.Popen, label: str, timeout: int = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{label} exited during startup ({process.returncode})")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if json.load(response).get("status") == "ok":
                    return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{label} health timeout")


def assert_ports_free(ports: tuple[int, ...]) -> None:
    for port in ports:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("0.0.0.0", port))
        except OSError as exc:
            raise RuntimeError(f"port already in use: {port}") from exc
        finally:
            probe.close()


def api_call(core_port: int, headers: dict[str, str], path: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{core_port}{path}",
        json.dumps(body).encode(),
        headers,
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    if result.get("code", 0) not in (0, 200):
        raise RuntimeError(f"metadata call failed: {path}: {result.get('code')}")
    return result["data"]


def start(
    run_id: str,
    core_port: int,
    proxy_port: int,
    adapter_port: int,
    *,
    allow_existing_run_dir: bool = False,
    variant: str = "native-baseline",
) -> dict:
    if variant not in {"native-baseline", "ours_v3"}:
        raise ValueError(f"unsupported variant: {variant}")
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is required")
    assert_ports_free((core_port, proxy_port, adapter_port))

    run_dir = (SWE_ROOT / "integration" / "runs" / run_id).resolve()
    if run_dir.exists() and not allow_existing_run_dir:
        raise RuntimeError(f"integration run already exists: {run_dir}")
    if run_dir.exists() and any(
        (run_dir / name).exists() for name in ("private", "core-data", "metadata")
    ):
        raise RuntimeError(f"integration run already contains service state: {run_dir}")
    private = run_dir / "private"
    private.mkdir(parents=True)

    node = shutil.which("node")
    pinned = Path("C:/Users/cheng/.workbuddy/binaries/node/versions/22.22.2/node.exe")
    if pinned.is_file():
        node = str(pinned)
    if not node:
        raise RuntimeError("Node.js is unavailable")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    posix = lambda path: path.resolve().as_posix()

    core_config = private / "core.yaml"
    ours_v3 = variant == "ours_v3"
    extraction_thresholds = """
    toolCallThreshold: 2147483647
    archiveBytes: 2147483647""" if ours_v3 else ""
    review_profile = "task_sop_v2" if ours_v3 else "legacy_v2"
    max_primary_writes = 1 if ours_v3 else 0
    core_config.write_text(f"""deployMode: standalone
stateBackend: local
server:
  host: 127.0.0.1
  port: {core_port}
data:
  baseDir: {posix(run_dir / 'core-data')}
metadata:
  store:
    sqliteBaseDir: {posix(run_dir / 'metadata')}
memory:
  storeBackend: sqlite
  embedding:
    provider: none
skill:
  enabled: true
  routing:
    mode: bm25
  extraction:
    enabled: true
{extraction_thresholds}
    trigger:
      profile: legacy
    valueGate:
      profile: legacy
    reviewPromptProfile: {review_profile}
    maxPrimaryWrites: {max_primary_writes}
    maxIterations: 16
observability:
  langfuse:
    enabled: false
""", encoding="utf-8")

    core_env = os.environ.copy()
    core_env.update(
        TDAI_GATEWAY_CONFIG=str(core_config),
        TDAI_GATEWAY_API_KEY="",
        TDAI_LLM_API_KEY=api_key,
        TDAI_LLM_BASE_URL="https://api.deepseek.com",
        TDAI_LLM_MODEL="deepseek-v4-flash",
        TDAI_DATA_DIR=str(run_dir / "core-data"),
        LOG_PATH=str(run_dir / "core-logs"),
    )
    core_out = (run_dir / "core.stdout.log").open("wb")
    core_err = (run_dir / "core.stderr.log").open("wb")
    core = subprocess.Popen(
        [node, "--import", "tsx/esm", "src/gateway/server.ts"],
        cwd=PROJECT_ROOT / "MemoryCore", env=core_env,
        stdout=core_out, stderr=core_err, creationflags=flags,
    )
    processes = [core]
    handles = [core_out, core_err]
    try:
        wait_health(f"http://127.0.0.1:{core_port}/health", core, "MemoryCore", timeout=300)
        headers = {"Content-Type": "application/json", "x-tdai-service-id": "default"}
        admin = api_call(core_port, headers, "/v3/internal/meta/user/init-admin", {
            "username": f"swe-together-{run_id}",
        })
        headers["x-tdai-user-key"] = admin["user_key"]
        team = api_call(core_port, headers, "/v3/meta/team/create", {
            "name": f"SWE-Together Baseline {run_id}", "owner_user_id": admin["user_id"],
        })
        agent = api_call(core_port, headers, "/v3/meta/agent/create", {
            "name": "SWE-Together Claude Code", "team_id": team["team_id"],
            "owner_user_id": admin["user_id"], "prompt": "Solve the interactive coding task.",
        })
        task = api_call(core_port, headers, "/v3/meta/task/create", {
            "title": "SWE-Together interactive session", "team_id": team["team_id"],
            "creator_user_id": admin["user_id"],
            "linked_agents": [{"agent_id": agent["agent_id"]}],
        })
        identity = {
            "user_id": admin["user_id"], "user_key": admin["user_key"],
            "team_id": team["team_id"], "agent_id": agent["agent_id"],
            "task_id": task["task_id"],
        }

        proxy_config = private / "proxy.yaml"
        proxy_config.write_text(f"""server:
  host: 0.0.0.0
  port: {proxy_port}
  forwardTimeoutMs: 600000
upstream:
  url: https://api.deepseek.com/anthropic/v1
  apiKey: {json.dumps(api_key)}
log:
  file: {posix(run_dir / 'proxy-logs')}
  verbose: true
  level: debug
storage:
  enabled: true
  backend: sqlite
  sqlite:
    dbPath: {posix(run_dir / 'proxy.db')}
auth:
  enabled: true
  url: http://127.0.0.1:{core_port}
  timeoutMs: 5000
sessionInit:
  enabled: true
  headerAutoSelect:
    enabled: true
  debugVerboseLogging: true
injection:
  enabled: true
  injectors: [skill]
  externalGatewayUrl: http://host.docker.internal:{proxy_port}
extraction:
  enabled: true
  extractors: [skill]
skill:
  endpoint: http://127.0.0.1:{core_port}
  serviceToken: local
  serviceId: default
  timeoutMs: 10000
  routingProfile: static
skillRuntime:
  allowLlmWrite: false
  injectSessionAvailableSkills: {str(not ours_v3).lower()}
  injectSkillTools: {str(not ours_v3).lower()}
ccRequestRouting:
  enabled: true
langfuse:
  enabled: false
  debug: false
""", encoding="utf-8")
        proxy_out = (run_dir / "proxy.stdout.log").open("wb")
        proxy_err = (run_dir / "proxy.stderr.log").open("wb")
        handles += [proxy_out, proxy_err]
        proxy_env = os.environ.copy()
        # Dump outbound body md5 (system/messages prefix) to diagnose Anthropic
        # KV-cache misses when skill injection modifies the system prompt.
        proxy_env["PROXY_DEBUG_DUMP_OUTBOUND_MD5"] = "1"
        proxy = subprocess.Popen(
            [node, "--import", "tsx/esm", "src/index.ts", "--config", str(proxy_config)],
            cwd=PROJECT_ROOT / "MemoryProxy", env=proxy_env,
            stdout=proxy_out, stderr=proxy_err, creationflags=flags,
        )
        processes.append(proxy)
        wait_health(f"http://127.0.0.1:{proxy_port}/health", proxy, "MemoryProxy")

        adapter_config = private / "session-adapter.json"
        write_json(adapter_config, {
            "variant": variant,
            "target": f"http://127.0.0.1:{proxy_port}/claude-code/default",
            "headers": {
                "X-Team-Id": identity["team_id"],
                "X-Agent-Id": identity["agent_id"],
                "X-Task-Id": identity["task_id"],
            },
            "ours_v3": {
                "core_url": f"http://127.0.0.1:{core_port}",
                "identity": identity,
                "state_path": str(run_dir / "ours-v3-state.json"),
                "events_path": str(run_dir / "ours-v3-events.jsonl"),
            } if ours_v3 else None,
        })
        adapter_out = (run_dir / "session-adapter.stdout.log").open("wb")
        adapter_err = (run_dir / "session-adapter.stderr.log").open("wb")
        handles += [adapter_out, adapter_err]
        adapter = subprocess.Popen(
            [sys.executable, str(HERE / "session_header_adapter.py"),
             "--config", str(adapter_config), "--port", str(adapter_port)],
            cwd=SWE_ROOT, env=os.environ.copy(),
            stdout=adapter_out, stderr=adapter_err, creationflags=flags,
        )
        processes.append(adapter)
        wait_health(f"http://127.0.0.1:{adapter_port}/health", adapter, "Session adapter")

        descriptor = {
            "base_url": f"http://host.docker.internal:{proxy_port}/claude-code/default",
            "base_url_template": (
                f"http://host.docker.internal:{adapter_port}/session/"
                "{session_id}"
            ),
            "api_key": identity["user_key"],
            "model_alias": "claude-sonnet-4-6",
            "upstream_model": "deepseek-v4-flash",
            "headers": {
                "X-Team-Id": identity["team_id"],
                "X-Agent-Id": identity["agent_id"],
                "X-Task-Id": identity["task_id"],
            },
        }
        descriptor_path = private / "agent-proxy.json"
        write_json(descriptor_path, descriptor)
        state_private = {
            "run_id": run_id, "run_dir": str(run_dir),
            "core_pid": core.pid, "proxy_pid": proxy.pid, "adapter_pid": adapter.pid,
            "core_port": core_port, "proxy_port": proxy_port, "adapter_port": adapter_port,
            "descriptor": str(descriptor_path), "identity": identity,
        }
        write_json(private / "service.json", state_private)
        public = {
            "status": "READY", "run_id": run_id, "run_dir": str(run_dir),
            "core_pid": core.pid, "proxy_pid": proxy.pid, "adapter_pid": adapter.pid,
            "core_port": core_port, "proxy_port": proxy_port, "adapter_port": adapter_port,
            "descriptor": str(descriptor_path),
            "upstream_model": "deepseek-v4-flash",
            "variant": variant, "langfuse": "disabled",
        }
        write_json(run_dir / "state.json", public)
        return public
    except BaseException:
        for process in reversed(processes):
            process.terminate()
        raise
    finally:
        for handle in handles:
            handle.close()


def resume(run_id: str, core_port: int, proxy_port: int, adapter_port: int,
           *, variant: str | None = None) -> dict:
    """Restart services around an existing run without recreating its identity."""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is required")
    assert_ports_free((core_port, proxy_port, adapter_port))

    run_dir = (SWE_ROOT / "integration" / "runs" / run_id).resolve()
    expected_parent = (SWE_ROOT / "integration" / "runs").resolve()
    if run_dir.parent != expected_parent:
        raise ValueError("run path escaped integration/runs")
    private = run_dir / "private"
    required = {
        "core config": private / "core.yaml",
        "proxy config": private / "proxy.yaml",
        "adapter config": private / "session-adapter.json",
        "agent proxy descriptor": private / "agent-proxy.json",
        "service state": private / "service.json",
    }
    missing = [label for label, path in required.items() if not path.is_file()]
    if missing:
        raise RuntimeError(f"resume state is incomplete: {', '.join(missing)}")

    old_state = json.loads(required["service state"].read_text(encoding="utf-8"))
    adapter_value = json.loads(required["adapter config"].read_text(encoding="utf-8"))
    persisted_variant = str(adapter_value.get("variant") or "native-baseline")
    if variant is not None and variant != persisted_variant:
        raise RuntimeError(
            f"resume variant differs from persisted config: {variant} != {persisted_variant}"
        )
    recorded_ports = (
        int(old_state.get("core_port", -1)),
        int(old_state.get("proxy_port", -1)),
        int(old_state.get("adapter_port", -1)),
    )
    requested_ports = (core_port, proxy_port, adapter_port)
    if recorded_ports != requested_ports:
        raise RuntimeError(
            f"resume ports differ from persisted service state: "
            f"recorded={recorded_ports}, requested={requested_ports}"
        )

    node = shutil.which("node")
    pinned = Path("C:/Users/cheng/.workbuddy/binaries/node/versions/22.22.2/node.exe")
    if pinned.is_file():
        node = str(pinned)
    if not node:
        raise RuntimeError("Node.js is unavailable")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    processes: list[subprocess.Popen] = []
    handles = []
    try:
        core_env = os.environ.copy()
        core_env.update(
            TDAI_GATEWAY_CONFIG=str(required["core config"]),
            TDAI_GATEWAY_API_KEY="",
            TDAI_LLM_API_KEY=api_key,
            TDAI_LLM_BASE_URL="https://api.deepseek.com",
            TDAI_LLM_MODEL="deepseek-v4-flash",
            TDAI_DATA_DIR=str(run_dir / "core-data"),
            LOG_PATH=str(run_dir / "core-logs"),
        )
        core_out = (run_dir / "core.stdout.log").open("ab")
        core_err = (run_dir / "core.stderr.log").open("ab")
        handles += [core_out, core_err]
        core = subprocess.Popen(
            [node, "--import", "tsx/esm", "src/gateway/server.ts"],
            cwd=PROJECT_ROOT / "MemoryCore", env=core_env,
            stdout=core_out, stderr=core_err, creationflags=flags,
        )
        processes.append(core)
        wait_health(f"http://127.0.0.1:{core_port}/health", core, "MemoryCore", timeout=300)

        proxy_out = (run_dir / "proxy.stdout.log").open("ab")
        proxy_err = (run_dir / "proxy.stderr.log").open("ab")
        handles += [proxy_out, proxy_err]
        proxy_env = os.environ.copy()
        proxy_env["PROXY_DEBUG_DUMP_OUTBOUND_MD5"] = "1"
        proxy = subprocess.Popen(
            [node, "--import", "tsx/esm", "src/index.ts", "--config", str(required["proxy config"])],
            cwd=PROJECT_ROOT / "MemoryProxy", env=proxy_env,
            stdout=proxy_out, stderr=proxy_err, creationflags=flags,
        )
        processes.append(proxy)
        wait_health(f"http://127.0.0.1:{proxy_port}/health", proxy, "MemoryProxy")

        adapter_out = (run_dir / "session-adapter.stdout.log").open("ab")
        adapter_err = (run_dir / "session-adapter.stderr.log").open("ab")
        handles += [adapter_out, adapter_err]
        adapter = subprocess.Popen(
            [sys.executable, str(HERE / "session_header_adapter.py"),
             "--config", str(required["adapter config"]), "--port", str(adapter_port)],
            cwd=SWE_ROOT, env=os.environ.copy(),
            stdout=adapter_out, stderr=adapter_err, creationflags=flags,
        )
        processes.append(adapter)
        wait_health(f"http://127.0.0.1:{adapter_port}/health", adapter, "Session adapter")

        state_private = {
            **old_state,
            "core_pid": core.pid, "proxy_pid": proxy.pid, "adapter_pid": adapter.pid,
            "resumed": True, "descriptor": str(required["agent proxy descriptor"]),
        }
        write_json(required["service state"], state_private)
        public = {
            "status": "READY", "run_id": run_id, "run_dir": str(run_dir),
            "core_pid": core.pid, "proxy_pid": proxy.pid, "adapter_pid": adapter.pid,
            "core_port": core_port, "proxy_port": proxy_port, "adapter_port": adapter_port,
            "descriptor": str(required["agent proxy descriptor"]),
            "upstream_model": "deepseek-v4-flash",
            "variant": persisted_variant, "langfuse": "disabled", "resumed": True,
        }
        write_json(run_dir / "state.json", public)
        return public
    except BaseException:
        for process in reversed(processes):
            process.terminate()
        raise
    finally:
        for handle in handles:
            handle.close()


def stop(run_id: str) -> dict:
    run_dir = (SWE_ROOT / "integration" / "runs" / run_id).resolve()
    state_path = run_dir / "private" / "service.json"
    if not state_path.is_file():
        raise RuntimeError(f"service state not found: {state_path}")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    stopped = []
    for key in ("adapter_pid", "proxy_pid", "core_pid"):
        if key not in state:
            continue
        pid = int(state[key])
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pass
        stopped.append(pid)
    public_path = run_dir / "state.json"
    public = json.loads(public_path.read_text(encoding="utf-8"))
    public.update(status="STOPPED", stopped_pids=stopped)
    write_json(public_path, public)
    return public


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "stop"))
    parser.add_argument("--run", required=True)
    parser.add_argument("--core-port", type=int, default=38420)
    parser.add_argument("--proxy-port", type=int, default=38096)
    parser.add_argument("--adapter-port", type=int, default=38097)
    parser.add_argument("--variant", choices=("native-baseline", "ours_v3"),
                        default="native-baseline")
    args = parser.parse_args()
    result = start(args.run, args.core_port, args.proxy_port, args.adapter_port,
                   variant=args.variant) if args.action == "start" else stop(args.run)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
