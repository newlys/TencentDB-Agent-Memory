#!/usr/bin/env python3
"""Run the frozen 6-repo / 26-task native Baseline evaluation serially.

The driver deliberately knows task ordering for orchestration and reporting,
but sends only a stable repo-level session ID to MemoryProxy.  Each task is
still executed by a separate Harbor trial with its official Docker image,
instruction, User Simulator, and verifier.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
SWE_ROOT = HERE.parent
SRC = SWE_ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))

from repo_sessions import plan_repo_sessions
from eval_results import verifier_reward
from tencentdb_baseline_service import resume as resume_service
from tencentdb_baseline_service import start as start_service
from tencentdb_baseline_service import stop as stop_service
from token_guard import usage_stats


# 3 repo / 7 task，预期提取 3 个 SOP 族：
#   entireio/cli         : 0ec2e9 → 2f5833 → cd4662
#   peteromallet/dataclaw: windows-path-fix → anonymizer-tests
#   nagi-ovo/gemini-voyager: 64c72f → f519c2
TASKS = [
    "cli-task-0ec2e9", "cli-task-2f5833", "cli-task-cd4662",
    "dataclaw-windows-path-fix", "dataclaw-anonymizer-tests",
    "gemini-voyager-task-64c72f", "gemini-voyager-task-f519c2",
]
# 本次压缩排除的所有 task（含 hard 题），仅用于 manifest 记录
HARD_EXCLUDED = [
    "pi-mono-tool-execution-write-error", "pi-mono-foreign-toolcall-fix",
    "pi-mono-parallel-tool-stall", "pi-mono-extensions-event-refactor",
    "rudel-task-468289",
    "cli-task-577e8c", "cli-task-70c88c", "cli-task-408b8c", "cli-task-33e050",
    "gemini-voyager-task-16a5c7", "gemini-voyager-task-4bddaf", "gemini-voyager-task-aa88f5",
    "reigh-preset-data-flow", "reigh-taskspane-lightbox-bug",
    "reigh-timeline-multiselect", "reigh-radix-props-cleanup", "reigh-timeline-mode-cleanup",
    "rudel-task-491983", "rudel-task-d1ddb8",
]


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(20):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


def task_dirs_for(task_id: str, trials_dir: Path) -> list[Path]:
    prefix = task_id[:32] + "__"
    if not trials_dir.exists():
        return []
    return sorted(
        (item for item in trials_dir.iterdir() if item.is_dir() and item.name.startswith(prefix)),
        key=lambda item: item.stat().st_mtime,
    )


def buffer_snapshot(run_dir: Path, session_id: str) -> dict[str, Any]:
    root = run_dir / "core-data" / "skill_buffer"
    session_dirs = list(root.glob(f"*/*/*/{session_id}")) if root.exists() else []
    if not session_dirs:
        return {
            "session_id": session_id, "exists": False,
            "tool_call_count": 0, "byte_count": 0, "archive_count": 0,
        }
    directory = session_dirs[0]
    meta_path = directory / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    archives = sorted(directory.glob("data-*.jsonl"))
    return {
        "session_id": session_id,
        "exists": True,
        "path": str(directory),
        "tool_call_count": int(meta.get("tool_call_count") or 0),
        "byte_count": int(meta.get("byte_count") or 0),
        "last_archived_at_ms": meta.get("last_archived_at_ms"),
        "archive_count": len(archives),
        "archives": [item.name for item in archives],
    }


def skill_heads(run_dir: Path) -> list[dict[str, Any]]:
    database = run_dir / "core-data" / "vectors.db"
    if not database.is_file():
        return []
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        connection.execute("pragma busy_timeout=10000")
        rows = connection.execute(
            "select skill_id, version, name, description from skills "
            "where is_head=1 order by name"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    return [
        {"skill_id": row[0], "version": row[1], "name": row[2], "description": row[3]}
        for row in rows
    ]


def parse_agent_trace(path: Path) -> dict[str, Any]:
    tool_ids: set[str] = set()
    assistant_ids: set[str] = set()
    searches = 0
    views = 0
    if not path.is_file():
        return {"tool_calls": 0, "model_calls": 0, "skill_search": 0, "skill_view": 0}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "assistant":
            continue
        message = event.get("message") or {}
        if message.get("id"):
            assistant_ids.add(str(message["id"]))
        for block in message.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("id"):
                tool_ids.add(str(block["id"]))
            command = str((block.get("input") or {}).get("command") or "")
            searches += int("/v3/skill/search" in command)
            views += int("/v3/skill/get-by-name" in command)
    return {
        "tool_calls": len(tool_ids), "model_calls": len(assistant_ids),
        "skill_search": searches, "skill_view": views,
    }


def user_turns(agent_dir: Path) -> tuple[int, int]:
    simulator_calls = 0
    messages = 0
    for decision in agent_dir.glob("episode-*/user_decision.json"):
        simulator_calls += 1
        try:
            messages += int(bool(json.loads(decision.read_text(encoding="utf-8")).get("has_message")))
        except (OSError, json.JSONDecodeError):
            pass
    return 1 + messages, simulator_calls


def result_reward(result: dict[str, Any]) -> float | None:
    return verifier_reward(result)


def observable_task_dirs(
    task_id: str, trials_dir: Path, preexisting_dirs: set[Path],
) -> list[Path]:
    """Return trials that this resume is allowed to observe.

    Old trials with a real verifier reward are legitimate completed work and
    may be skipped by run_eval. Old INFRA_ERROR result.json files must remain
    invisible to the observer, otherwise it marks every task processed and
    terminates the freshly started runner before it can create replacement
    trials.
    """
    observable: list[Path] = []
    for trial in task_dirs_for(task_id, trials_dir):
        resolved = trial.resolve()
        if resolved not in preexisting_dirs:
            observable.append(trial)
            continue
        result_path = trial / "result.json"
        if not result_path.is_file():
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if result_reward(result) is not None:
            observable.append(trial)
    return observable


def update_counts(state: dict[str, Any]) -> None:
    terminal = [item for item in state["tasks"] if item.get("status") in {"PASS", "FAIL", "INFRA_ERROR"}]
    state["completed"] = len(terminal)
    state["pass"] = sum(item.get("status") == "PASS" for item in terminal)
    state["fail"] = sum(item.get("status") == "FAIL" for item in terminal)
    state["infra_error"] = sum(item.get("status") == "INFRA_ERROR" for item in terminal)
    state["last_updated_at"] = now()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--core-port", type=int, default=49420)
    parser.add_argument("--proxy-port", type=int, default=49096)
    parser.add_argument("--adapter-port", type=int, default=49097)
    parser.add_argument("--agent-timeout", type=int, default=1800)
    parser.add_argument("--variant", choices=("native-baseline", "ours_v3"),
                        default="native-baseline")
    parser.add_argument(
        "--input-token-budget", type=int, default=20_000_000,
        help="Maximum uncached Proxy input-token delta for this launch/resume.",
    )
    parser.add_argument(
        "--min-cache-hit-ratio", type=float, default=0.60,
        help="Abort when the early token-weighted cache-hit ratio stays below this value.",
    )
    parser.add_argument(
        "--cache-check-after-requests", type=int, default=6,
        help="Number of new successful Proxy requests before enforcing the cache ratio.",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume an existing run: reuse its run_dir, service, and completed "
             "trials. PENDING/RUNNING/INFRA_ERROR tasks are re-run; PASS/FAIL "
             "trials are skipped via run_eval --skip-existing.",
    )
    args = parser.parse_args()

    plan = plan_repo_sessions(TASKS, SWE_ROOT / "tasks")
    total_tasks = len(plan)
    run_dir = (SWE_ROOT / "integration" / "runs" / args.run).resolve()
    expected_parent = (SWE_ROOT / "integration" / "runs").resolve()
    if run_dir.parent != expected_parent:
        raise ValueError("run path escaped integration/runs")
    if run_dir.exists():
        if not args.resume:
            raise FileExistsError(f"run already exists: {run_dir} (use --resume to continue)")
    else:
        run_dir.mkdir(parents=True)
    trials_dir = run_dir / "trials"
    status_path = run_dir / "status.json"
    if not (run_dir / "manifest.json").exists():
        manifest = {
            "schema_version": "swe-together-evaluation/1.1",
            "run_id": args.run,
            "variant": args.variant,
            "method_revision": (
                "task-skill-consumption-controller-r5"
                if args.variant == "ours_v3" else "upstream-native-baseline"
            ),
            "workers": 1,
            "model": "deepseek-v4-flash",
            "user_simulator_model": "deepseek-v4-flash",
            "task_boundary_visible_to_proxy": False,
            "boundary_input": "query-only; no benchmark task id or verifier data",
            "task_environment_policy": "official-independent-harbor-trial",
            "task_order_source": "frozen-explicit-list",
            "hard_excluded": HARD_EXCLUDED,
            "ports": {"core": args.core_port, "proxy": args.proxy_port, "session_adapter": args.adapter_port},
            "tasks": [item.to_dict() for item in plan],
            "created_at": now(),
        }
        write_json(run_dir / "manifest.json", manifest)
    state: dict[str, Any] = {
        "variant": args.variant, "run_id": args.run,
        "total_tasks": total_tasks, "completed": 0, "pass": 0, "fail": 0,
        "infra_error": 0, "current_repo": "", "current_task": "",
        "current_phase": "preparing", "process_started_at": now(),
        "last_updated_at": now(), "tasks": [], "status": "STARTING",
        "ports": manifest["ports"] if 'manifest' in dir() else {"core": args.core_port, "proxy": args.proxy_port, "session_adapter": args.adapter_port},
    }
    write_json(status_path, state)

    service_started = False
    try:
        if args.resume:
            # Preserve the original Memory/Skill state and identity.  Resume
            # only restarts the three local service processes around the
            # existing configs and agent-proxy descriptor.
            service = resume_service(
                args.run, args.core_port, args.proxy_port, args.adapter_port,
                variant=args.variant,
            )
        else:
            service = start_service(
                args.run, args.core_port, args.proxy_port, args.adapter_port,
                allow_existing_run_dir=True, variant=args.variant,
            )
        service_started = True
        descriptor = Path(service["descriptor"])
        state.update(
            status="RUNNING", current_phase="preparing",
            service_mode="resumed" if args.resume else "fresh",
        )
        update_counts(state)
        write_json(status_path, state)

        state["tasks"] = [
            {**item.to_dict(), "status": "PENDING", "current_phase": "preparing"}
            for item in plan
        ]
        task_states = {item["task_id"]: item for item in state["tasks"]}
        last_buffers = {
            item.session_id: buffer_snapshot(run_dir, item.session_id) for item in plan
        }
        last_skills = skill_heads(run_dir)
        update_counts(state)
        write_json(status_path, state)

        stdout_path = run_dir / "runner.stdout.log"
        stderr_path = run_dir / "runner.stderr.log"
        command = [
            sys.executable, str(SRC / "run_eval.py"),
            "--model", "deepseek/deepseek-v4-flash",
            "--user-model", "deepseek/deepseek-v4-flash",
            "--tag", args.run, "--agent-type", "claude-code",
            "--env-type", "docker", "--workers", "1",
            "--agent-timeout", str(args.agent_timeout),
            "--tasks", ",".join(TASKS),
            "--trials-dir", str(trials_dir),
            "--agent-proxy-descriptor", str(descriptor),
            "--repo-sessions",
            "--skip-existing",
        ]
        state["runner"] = {
            "command": command, "stdout": str(stdout_path), "stderr": str(stderr_path)
        }
        preexisting_trial_dirs = {
            trial.resolve()
            for planned in plan
            for trial in task_dirs_for(planned.task_id, trials_dir)
        }
        state["runner"]["preexisting_trials_ignored_unless_verified"] = len(
            preexisting_trial_dirs
        )
        processed: set[str] = set()

        def observe(final: bool = False) -> None:
            nonlocal last_skills
            for planned in plan:
                task_state = task_states[planned.task_id]
                candidates = observable_task_dirs(
                    planned.task_id, trials_dir, preexisting_trial_dirs,
                )
                trial = candidates[-1] if candidates else None
                if not trial:
                    continue
                if task_state["status"] == "PENDING":
                    task_state.update(
                        status="RUNNING", start_time=now(), current_phase="agent",
                        trial_directory=str(trial),
                        buffer_before=last_buffers[planned.session_id],
                    )
                result_path = trial / "result.json"
                if not result_path.is_file() or planned.task_id in processed:
                    verifier = trial / "verifier"
                    decisions = list((trial / "agent").glob("episode-*/user_decision.json"))
                    if verifier.exists() and any(verifier.iterdir()):
                        task_state["current_phase"] = "verifier"
                    elif decisions and time.time() - max(item.stat().st_mtime for item in decisions) < 5:
                        task_state["current_phase"] = "user_simulator"
                    else:
                        task_state["current_phase"] = "agent"
                    continue

                result = json.loads(result_path.read_text(encoding="utf-8"))
                reward = result_reward(result)
                exception = result.get("exception_info") or {}
                exception_type = exception.get("exception_type")
                timeout_failure = exception_type in {"AgentTimeoutError", "TimeoutError"}
                if reward is not None:
                    task_status = "PASS" if reward > 0 else "FAIL"
                    error_category = None
                elif timeout_failure:
                    task_status = "FAIL"
                    error_category = "agent_timeout"
                else:
                    task_status = "INFRA_ERROR"
                    error_category = exception_type or "missing_verifier_result"
                agent_dir = trial / "agent"
                trace = parse_agent_trace(agent_dir / "claude-code.txt")
                turns, simulator_calls = user_turns(agent_dir)
                agent_result = result.get("agent_result") or {}
                after_buffer = buffer_snapshot(run_dir, planned.session_id)
                after_skills = skill_heads(run_dir)
                before_heads = {(x["skill_id"], x["version"]) for x in last_skills}
                changed_heads = [
                    x for x in after_skills
                    if (x["skill_id"], x["version"]) not in before_heads
                ]
                task_state.update(
                    status=task_status,
                    current_phase="completed" if task_status != "INFRA_ERROR" else "infra_error",
                    end_time=now(), reward=reward, passed=task_status == "PASS",
                    user_turns=turns, user_simulator_calls=simulator_calls,
                    user_simulator_real_followups=max(0, turns - 1),
                    tool_calls=trace["tool_calls"], model_calls=trace["model_calls"],
                    token_usage={
                        "input_tokens": agent_result.get("n_input_tokens"),
                        "cache_tokens": agent_result.get("n_cache_tokens"),
                        "output_tokens": agent_result.get("n_output_tokens"),
                    },
                    agent_latency_seconds=(
                        (datetime.fromisoformat(result["agent_execution"]["finished_at"].replace("Z", "+00:00"))
                         - datetime.fromisoformat(result["agent_execution"]["started_at"].replace("Z", "+00:00"))).total_seconds()
                        if result.get("agent_execution") else None
                    ),
                    verifier_status="completed" if reward is not None else "missing",
                    error_category=error_category,
                    error_message=exception.get("exception_message"),
                    buffer_after=after_buffer,
                    skill_activity={
                        "archive_triggered": after_buffer["archive_count"] > task_state["buffer_before"]["archive_count"],
                        "archive_count_delta": after_buffer["archive_count"] - task_state["buffer_before"]["archive_count"],
                        "extraction_observed_by_task_end": bool(changed_heads),
                        "skills_created_or_updated_observed": changed_heads,
                        "retrieval_observed": trace["skill_search"] > 0,
                        "skill_search_calls": trace["skill_search"],
                        "skill_view_calls": trace["skill_view"],
                        "injection_enabled": True,
                        "injection_profile": (
                            "task-skill-consumption-controller-r5"
                            if args.variant == "ours_v3" else "session-scoped-native"
                        ),
                    },
                )
                last_buffers[planned.session_id] = after_buffer
                last_skills = after_skills
                processed.add(planned.task_id)

            active = next((item for item in state["tasks"] if item["status"] == "RUNNING"), None)
            if active:
                state.update(
                    current_repo=active["repo"], current_task=active["task_id"],
                    current_phase=active["current_phase"],
                )
            elif not final:
                next_task = next((item for item in state["tasks"] if item["status"] == "PENDING"), None)
                if next_task:
                    state.update(
                        current_repo=next_task["repo"], current_task=next_task["task_id"],
                        current_phase="preparing",
                    )
            update_counts(state)
            write_json(status_path, state)

        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            # Force UTF-8 for the child: Windows GBK default breaks Harbor's
            # rich progress spinner (UnicodeEncodeError \u280f) and Claude Code
            # trajectory decoding ('gbk' codec can't decode) on zh-CN hosts.
            runner_env = os.environ.copy()
            runner_env["PYTHONUTF8"] = "1"
            runner_env["PYTHONIOENCODING"] = "utf-8"
            runner_env["PYTHONUNBUFFERED"] = "1"
            guard_baseline = usage_stats(run_dir)
            process = subprocess.Popen(
                command, cwd=SWE_ROOT, stdout=stdout, stderr=stderr, env=runner_env
            )
            state["runner"]["pid"] = process.pid
            state["runner"]["started_at"] = now()
            state["usage_guard"] = {
                "input_token_budget": args.input_token_budget,
                "min_cache_hit_ratio": args.min_cache_hit_ratio,
                "cache_check_after_requests": args.cache_check_after_requests,
                "baseline": guard_baseline,
                "status": "MONITORING",
            }
            write_json(status_path, state)
            all_results_at = None
            while process.poll() is None:
                observe()
                usage = usage_stats(run_dir)
                new_requests = usage["requests"] - guard_baseline["requests"]
                uncached_delta = usage["total_input_tokens"] - guard_baseline["total_input_tokens"]
                cached_delta = usage["total_cache_read_tokens"] - guard_baseline["total_cache_read_tokens"]
                token_denominator = uncached_delta + cached_delta
                cache_ratio = cached_delta / token_denominator if token_denominator > 0 else 0.0
                new_402 = usage["provider_402_errors"] > guard_baseline["provider_402_errors"]
                budget_exceeded = uncached_delta > args.input_token_budget
                cache_failed = (
                    new_requests >= args.cache_check_after_requests
                    and cache_ratio < args.min_cache_hit_ratio
                )
                state["usage_guard"].update(
                    status="TRIPPED" if (new_402 or budget_exceeded or cache_failed) else "MONITORING",
                    new_requests=new_requests,
                    uncached_input_tokens=uncached_delta,
                    cache_read_input_tokens=cached_delta,
                    cache_hit_token_ratio=cache_ratio,
                    new_provider_402=new_402,
                    budget_exceeded=budget_exceeded,
                    cache_ratio_failed=cache_failed,
                    updated_at=now(),
                )
                write_json(run_dir / "guard.json", state["usage_guard"])
                write_json(status_path, state)
                if new_402 or budget_exceeded or cache_failed:
                    if new_402:
                        stop_reason = "provider_402_balance"
                    elif budget_exceeded:
                        stop_reason = "input_token_budget_exceeded"
                    else:
                        stop_reason = "cache_hit_ratio_below_floor"
                    state["runner"]["guard_stop_reason"] = stop_reason
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                        )
                    else:
                        process.terminate()
                    break
                if len(processed) == len(plan):
                    all_results_at = all_results_at or time.monotonic()
                    # LiteLLM may leave a non-daemon cost-map fetch alive on
                    # Windows after Harbor has written every result. The outer
                    # status/summary is authoritative, so bound shutdown only.
                    if time.monotonic() - all_results_at >= 30:
                        process.terminate()
                        state["runner"]["shutdown_after_all_results"] = True
                        break
                time.sleep(5)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            state["runner"]["exit_code"] = process.returncode
            state["runner"]["ended_at"] = now()
        observe(final=True)
        for task_state in state["tasks"]:
            if task_state["status"] in {"PENDING", "RUNNING"}:
                task_state.update(
                    status="INFRA_ERROR", current_phase="infra_error", end_time=now(),
                    verifier_status="missing", error_category="runner_ended_without_result",
                )

        if args.variant == "ours_v3":
            # EOF flush is asynchronous: enqueue the last predicted task from
            # every repo session without delaying the completed verifier run.
            import urllib.request
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{args.adapter_port}/ours/flush",
                    data=b"{}", headers={"content-type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(request, timeout=60) as response:
                    state["ours_v3_eof_flush"] = json.load(response)
            except Exception as exc:
                state["ours_v3_eof_flush_warning"] = f"{type(exc).__name__}: {exc}"
            controller_state = run_dir / "ours-v3-state.json"
            if controller_state.is_file():
                state["ours_v3"] = json.loads(controller_state.read_text(encoding="utf-8"))

        state.update(
            status="COMPLETED" if state["infra_error"] == 0 else "COMPLETED_WITH_INFRA_ERRORS",
            current_repo="", current_task="", current_phase="completed", end_time=now(),
            final_skill_heads=skill_heads(run_dir),
        )
        update_counts(state)
        write_json(status_path, state)
        write_json(run_dir / "summary.json", state)
        return 0 if state["infra_error"] == 0 else 2
    except BaseException as exc:
        state.update(
            status="INFRA_ERROR", current_phase="infra_error",
            error=f"{type(exc).__name__}: {exc}", end_time=now(),
        )
        update_counts(state)
        write_json(status_path, state)
        write_json(run_dir / "summary.json", state)
        raise
    finally:
        if service_started:
            try:
                stop_service(args.run)
            except Exception as exc:
                state["service_stop_warning"] = f"{type(exc).__name__}: {exc}"
                update_counts(state)
                write_json(status_path, state)


if __name__ == "__main__":
    raise SystemExit(main())
