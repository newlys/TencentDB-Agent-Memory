"""Run ordered repository suites as isolated Claude Code sessions.

No-Skill uses one stateless proxy per repository. Baseline and Ours use one
experiment-scoped MemoryCore/Proxy and Skill store shared by all repo sessions.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any
import uuid


ROOT = Path(__file__).resolve().parent
SESSION_DRIVER = ROOT.parent / "click-benchmark" / "session_driver.py"
VALIDATE_IMPORT = ROOT.parent / "click-benchmark" / "validate_import.py"
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}")
OURS_PROFILE_BY_VARIANT = {
    "ours": {
        "boundary_profile": "query_only_l15",
        "extraction_profile": "task_scoped_v1",
        "retrieval_profile": "task_search_view_v1",
    },
    "ours_v2": {
        "boundary_profile": "query_only_l15",
        "extraction_profile": "task_scoped_sop_v2",
        "retrieval_profile": "task_scoped_skill_injection_v2",
    },
    "ours_v3": {
        "boundary_profile": "query_only_l15",
        "extraction_profile": "task_scoped_sop_v2",
        "retrieval_profile": "task_scoped_skill_consumption_v3",
    },
}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read(path: Path, sharing_timeout: float = 30.0) -> dict[str, Any]:
    """Read JSON while tolerating transient Windows writer/reader locks."""
    deadline = time.monotonic() + sharing_timeout
    while True:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (PermissionError, json.JSONDecodeError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(.1)


def write(path: Path, value: Any, sharing_timeout: float = 30.0) -> None:
    """Atomically publish JSON, tolerating transient Windows readers.

    Editors, indexers, and progress monitors may briefly open the destination
    without FILE_SHARE_DELETE.  A unique temporary name avoids writer clashes;
    retrying replace makes observing progress unable to abort a paid run.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    deadline = time.monotonic() + sharing_timeout
    try:
        while True:
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
    finally:
        if temporary.exists():
            temporary.unlink()


def slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    if not result:
        raise ValueError(f"Cannot derive a safe slug from {value!r}")
    return result


def protocol_signature(protocol: dict[str, Any]) -> dict[str, Any]:
    """Fields that must remain identical across repository sessions."""
    return {
        key: protocol[key]
        for key in (
            "schema_version",
            "agent_internal_iteration_limit",
            "max_user_turns_source",
            "on_agent_internal_limit",
            "turn_counting",
            "task_boundary",
            "language",
            "primary_efficiency_metrics",
            "auxiliary_efficiency_metrics",
            "conversation_probes",
        )
    }


def resolve_plan(plan_path: Path, check_images: bool = False) -> dict[str, Any]:
    plan_path = plan_path.resolve()
    plan = read(plan_path)
    if plan.get("schema_version") != 1:
        raise ValueError("Longitudinal plan schema_version must be 1")
    if plan.get("variant") not in ("no-skill", "baseline", "ours", "ours_v2", "ours_v3"):
        raise ValueError("Longitudinal variant must be no-skill, baseline, ours, ours_v2, or ours_v3")
    expected_profiles = OURS_PROFILE_BY_VARIANT.get(plan.get("variant"))
    if expected_profiles:
        if plan.get("profiles") != expected_profiles:
            raise ValueError(f"{plan['variant']} plan must freeze profiles to {expected_profiles}")
    sessions = plan.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        raise ValueError("Plan must contain at least one repository session")
    if [item.get("session_ordinal") for item in sessions] != list(
        range(1, len(sessions) + 1)
    ):
        raise ValueError("session_ordinal must be contiguous and start at 1")

    resolved: list[dict[str, Any]] = []
    seen_repos: set[str] = set()
    reference: dict[str, Any] | None = None
    for item in sessions:
        repo = item.get("repo")
        if not isinstance(repo, str) or not repo or repo in seen_repos:
            raise ValueError("Each repository session needs a unique non-empty repo name")
        seen_repos.add(repo)
        suite_root = (plan_path.parent / item["suite_root"]).resolve()
        private = suite_root / "private"
        required = [
            private / "suite.json",
            private / "manifest.json",
            private / "protocol.json",
            suite_root / "environment.lock.json",
        ]
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise ValueError(f"Suite {repo} is incomplete: {missing}")
        suite = read(private / "suite.json")
        manifest = read(private / "manifest.json")
        if expected_profiles:
            extension = (suite.get("extensions") or {}).get(plan["variant"]) or {}
            suite_profiles = {key:extension.get(key) for key in expected_profiles}
            if extension.get("status") != "connected" or suite_profiles != expected_profiles:
                raise ValueError(f"Suite {repo} has no matching {plan['variant']} adapter configuration")
        only_task = item.get("only_task")
        if only_task is not None and only_task not in manifest.get("tasks", []):
            raise ValueError(f"Suite {repo} does not contain only_task {only_task}")
        protocol = read(private / "protocol.json")
        lock = read(suite_root / "environment.lock.json")
        if suite["repository"]["base_commit"] != manifest.get("base_commit"):
            raise ValueError(f"Suite {repo} has inconsistent base commits")
        if suite["agent"].get("language") != "en" or protocol["language"]["user_visible_messages"] != "en":
            raise ValueError(f"Suite {repo} violates the frozen English policy")
        signature = {
            "model": suite["agent"]["model"],
            "no_skill_proxy": suite["images"]["no_skill_proxy"],
            "protocol": protocol_signature(protocol),
        }
        if reference is None:
            reference = signature
        elif signature != reference:
            raise ValueError(f"Suite {repo} does not share the common experiment protocol")
        if not lock.get("agent_image_id") or not lock.get("image_id"):
            raise ValueError(f"Suite {repo} has no complete environment lock")
        if check_images:
            engine = subprocess.run(
                ['docker', 'info', '--format', '{{.ServerVersion}}'],
                capture_output=True, text=True, timeout=30,
            )
            if engine.returncode:
                raise RuntimeError('Docker engine unavailable: '+engine.stderr.strip()[-1000:])
            observed_images: dict[str, str] = {}
            for role, tag in (
                ("grader", suite["images"]["grader"]),
                ("agent", suite["images"]["agent"]),
                ("claude_cli", suite["images"]["claude_cli"]),
                ("no_skill_proxy", suite["images"]["no_skill_proxy"]),
            ):
                result = subprocess.run(
                    ["docker", "image", "inspect", tag, "--format", "{{.Id}}"],
                    capture_output=True, text=True,
                )
                if result.returncode:
                    raise ValueError(f"Suite {repo} is missing Docker image {tag}")
                observed_images[role] = result.stdout.strip()
            if observed_images["grader"] != lock["image_id"]:
                raise ValueError(f"Suite {repo} grader image differs from environment.lock.json")
            if observed_images["agent"] != lock["agent_image_id"]:
                raise ValueError(f"Suite {repo} agent image differs from environment.lock.json")
            if observed_images["claude_cli"] != lock.get("claude_cli_image_id"):
                raise ValueError(f"Suite {repo} Claude CLI image differs from environment.lock.json")
            source_value = Path(suite["repository"]["source_path"])
            source = source_value.resolve() if source_value.is_absolute() else (suite_root / source_value).resolve()
            probe = suite["validation"]["agent_visible_probe_file"]
            smoke = subprocess.run(
                [
                    "docker", "run", "--rm", "--network", "none",
                    "--mount", f"type=bind,src={source},dst=/workspace,readonly",
                    suite["images"]["claude_cli"], "python", "-c",
                    f"from pathlib import Path; assert Path('/workspace/{probe}').is_file()",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if smoke.returncode:
                raise ValueError(
                    f"Suite {repo} Claude CLI entrypoint failed on its repository: "
                    f"{(smoke.stderr or smoke.stdout)[-1000:]}"
                )
            version = subprocess.run(
                ["docker", "run", "--rm", suite["images"]["claude_cli"], "claude", "--version"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if version.returncode:
                raise ValueError(f"Suite {repo} Claude CLI version probe failed")
            cli_version = version.stdout.strip()
            if cli_version != lock.get("claude_cli_version"):
                raise ValueError(f"Suite {repo} Claude CLI version differs from environment.lock.json")
            if resolved and cli_version != resolved[0].get("claude_cli_version"):
                raise ValueError(f"Suite {repo} uses a different Claude CLI version")
        else:
            cli_version = None
        resolved.append(
            {
                **item,
                "repo": repo,
                "suite_root": str(suite_root),
                "benchmark_id": suite["benchmark_id"],
                "base_commit": suite["repository"]["base_commit"],
                "tasks": len(manifest["tasks"]),
                "events": len(manifest["events"]),
                "model": suite["agent"]["model"],
                "agent_image_id": lock["agent_image_id"],
                "claude_cli_image": suite["images"]["claude_cli"],
                "claude_cli_version": cli_version,
            }
        )
    return {**plan, "plan_path": str(plan_path), "sessions": resolved}


def aggregate_metrics(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    fields = (
        "user_turns",
        "agent_internal_turns",
        "model_calls",
        "tool_calls",
        "agent_elapsed_seconds",
    )
    result: dict[str, Any] = {
        field: round(sum((item.get("metrics") or {}).get(field, 0) or 0 for item in sessions), 3)
        for field in fields
    }
    result["usage"] = {
        key: sum(
            ((item.get("metrics") or {}).get("usage") or {}).get(key, 0) or 0
            for item in sessions
        )
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    }
    result["total_llm_tokens"] = sum(
        (item.get("metrics") or {}).get("total_llm_tokens", 0) or 0 for item in sessions
    )
    return result


def child_run_id(experiment_run_id: str, ordinal: int, repo: str) -> str:
    value = f"{experiment_run_id}-s{ordinal:02d}-{slug(repo)}"
    if len(value) > 64:
        raise ValueError(f"Derived child run ID is too long: {value}")
    return value


def load_session_runtime():
    """Load the reusable session service lifecycle without duplicating it."""
    driver_dir = SESSION_DRIVER.parent
    if str(driver_dir) not in sys.path:
        sys.path.insert(0, str(driver_dir))
    spec = importlib.util.spec_from_file_location("benchmark_session_runtime", SESSION_DRIVER)
    if not spec or not spec.loader:
        raise RuntimeError("Cannot load benchmark session runtime")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stop_baseline(service: dict[str, Any] | None) -> None:
    if not service:
        return
    for process in reversed(service.get("processes", [])):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
    for handle in service.get("handles", []):
        handle.close()
    network = service.get("network")
    if network:
        subprocess.run(["docker", "network", "rm", network], capture_output=True)


def resolve_resume_checkpoint(plan: dict[str, Any], resume_from: str) -> dict[str, Any]:
    if plan["variant"] not in ("baseline", "ours", "ours_v2", "ours_v3"):
        raise ValueError("Session checkpoint resume is valid only for memory-enabled variants")
    if not RUN_ID_RE.fullmatch(resume_from):
        raise ValueError("Resume run ID is invalid")
    prior_dir = (ROOT / "runs" / resume_from).resolve()
    if prior_dir.parent != (ROOT / "runs").resolve():
        raise ValueError("Resume run path escaped the longitudinal runs directory")
    result_path = prior_dir / "result.json"
    if not result_path.is_file():
        raise ValueError("Resume run has no terminal result.json")
    prior = read(result_path)
    if prior.get("status") != "INTERRUPTED":
        raise ValueError("Only an INTERRUPTED experiment can be resumed")
    if prior.get("experiment_id") != plan["experiment_id"] or prior.get("variant") != plan["variant"]:
        raise ValueError("Resume run does not match this experiment variant")
    expected_repos = [item["repo"] for item in plan["sessions"]]
    prior_repos = [item.get("repo") for item in prior.get("plan", {}).get("sessions", [])]
    if prior_repos != expected_repos:
        raise ValueError("Resume run repository sequence differs from the current plan")
    completed = []
    partial_session = None
    for entry in prior.get("sessions", []):
        if entry.get("status") == "COMPLETED":
            if len(completed) != entry.get("session_ordinal", 0) - 1:
                raise ValueError("Resume run does not contain a contiguous completed prefix")
            completed.append(entry)
            continue
        calls = ((entry.get("metrics") or {}).get("model_calls") or 0)
        # The outer monitor can be stale if it was interrupted while the child
        # was publishing. Never infer zero paid work from outer progress alone.
        child_report_value = entry.get("child_report")
        child = None
        if child_report_value:
            child_report = Path(child_report_value).resolve()
            if child_report.is_file():
                child = read(child_report, sharing_timeout=30)
                calls = max(calls, ((child.get("metrics") or {}).get("model_calls") or 0))
        if calls:
            committed = (
                child
                and child.get("progress", {}).get("phase") == "TURN_COMPLETE"
                and child.get("turns")
                and child["turns"][-1].get("ended_at")
            )
            reset_in_flight = (
                child
                and child.get("progress", {}).get("phase") == "INTERRUPTED"
                and child.get("checkpoint", {}).get("phase") == "AGENT_IN_FLIGHT"
                and child.get("final_reset", {}).get("verified") is True
                and child.get("turns")
            )
            prepare_failed = (
                child
                and child.get("progress", {}).get("phase") == "INTERRUPTED"
                and child.get("checkpoint", {}).get("safe_to_resume") is True
                and child.get("checkpoint", {}).get("phase") == "EVENT_BOUNDARY_COMMITTED"
                and child.get("final_reset", {}).get("task_id")
                and child.get("final_reset", {}).get("sha256")
                and child.get("turns")
            )
            if not (committed or reset_in_flight or prepare_failed):
                raise ValueError(
                    "Interrupted repository session has paid model calls without a recoverable checkpoint"
                )
            partial_session = {
                "session_ordinal": entry.get("session_ordinal"),
                "repo": entry.get("repo"),
                "child_run_id": entry.get("child_run_id"),
                "child_report": child_report_value,
                "committed_turns": len(child["turns"]),
                "model_calls": calls,
                "recovery_mode": (
                    "reset_in_flight" if reset_in_flight else
                    "prepare_failed_after_boundary" if prepare_failed else
                    "committed_turn"
                ),
            }
        break
    # A recoverable partial first repository is a valid checkpoint even when
    # there is no fully completed repository prefix yet.
    if (not completed and partial_session is None) or len(completed) >= len(plan["sessions"]):
        raise ValueError("Resume run has no usable incomplete suffix")
    source = prior_dir / plan["variant"]
    required = [source / "core-data", source / "metadata", source / "private" / "identity.json"]
    if any(not path.exists() for path in required):
        raise ValueError("Resume run has no complete Baseline persistence checkpoint")
    return {
        "run_id": resume_from,
        "result_path": str(result_path),
        "baseline_dir": source,
        "completed_sessions": completed,
        "partial_session": partial_session,
    }


def copy_baseline_checkpoint(source: Path, destination: Path) -> None:
    """Copy only persistent native Baseline state into a new immutable run."""
    if destination.exists():
        raise ValueError("Baseline checkpoint destination already exists")
    destination.mkdir(parents=True)
    for name in ("core-data", "metadata"):
        shutil.copytree(source / name, destination / name)
    # Extraction token accounting is written to the append-only local
    # observability log rather than the SQLite state. Preserve it across a
    # checkpoint resume so final reconciliation covers work paid for before
    # the interruption as well as work performed afterwards.
    source_core_logs = source / "core-logs"
    if source_core_logs.is_dir():
        shutil.copytree(source_core_logs, destination / "core-logs")
    private = destination / "private"
    private.mkdir()
    shutil.copy2(source / "private" / "identity.json", private / "identity.json")
    public_identity = source / "identity.public.json"
    if public_identity.is_file():
        shutil.copy2(public_identity, destination / "identity.public.json")
    proxy_db = source / "proxy.db"
    if proxy_db.is_file():
        shutil.copy2(proxy_db, destination / "proxy.db")


def public_child_state(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: report.get(key)
        for key in (
            "status",
            "progress",
            "claude_session_id",
            "langfuse_session_id",
            "started_at",
            "ended_at",
            "metrics",
            "metrics_completeness",
            "profiles",
            "ours",
            "observability",
            "checkpoint",
            "error",
        )
        if key in report
    }


def refresh_child_progress(entry: dict[str, Any], child_report: Path) -> bool:
    """Best-effort observer: progress-file contention must never stop an Agent."""
    try:
        report = read(child_report, sharing_timeout=.5)
    except (PermissionError, json.JSONDecodeError, OSError) as error:
        entry["progress_read_warning"] = {
            "observed_at": now(), "type": type(error).__name__, "message": str(error),
        }
        return False
    entry.update(public_child_state(report))
    entry.pop("progress_read_warning", None)
    return True


def annotate_private_skill_evaluation(state: dict[str, Any]) -> None:
    """Score retrieval/consumption after the run without exposing gold data.

    Family labels are read only after every Agent process and asynchronous
    extractor has finished. They are written to result artifacts, never to an
    Agent prompt, Proxy request, or MemoryCore input.
    """
    if state.get("variant") != "ours_v3":
        return
    reports: list[tuple[dict[str, Any], dict[str, Any], Path]] = []
    annotations: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in state.get("sessions", []):
        report_path = Path(entry["child_report"])
        report = read(report_path, sharing_timeout=30)
        gold_path = Path(entry["suite_root"]) / "private" / "gold_annotations.json"
        gold = read(gold_path) if gold_path.is_file() else {"tasks": {}}
        for task_id, annotation in (gold.get("tasks") or {}).items():
            annotations[(entry["repo"], task_id)] = annotation
        reports.append((entry, report, report_path))

    family_skill_ids: dict[str, set[str]] = {}
    for entry, report, _ in reports:
        for extraction in ((report.get("ours") or {}).get("extractions") or []):
            task_id = extraction.get("benchmark_task_id")
            family = (annotations.get((entry["repo"], task_id)) or {}).get("skill_family")
            if not family:
                continue
            for action in extraction.get("review_actions") or []:
                if action.get("skill_id"):
                    family_skill_ids.setdefault(family, set()).add(action["skill_id"])

    evaluations = []
    for entry, report, report_path in reports:
        repo_rows = []
        for retrieval in ((report.get("ours") or {}).get("retrievals") or []):
            task_id = retrieval.get("benchmark_task_id")
            annotation = annotations.get((entry["repo"], task_id)) or {}
            family = annotation.get("skill_family")
            expected_reuse = "reuse_existing_family" in str(
                annotation.get("expected_action")
                or annotation.get("expected_when_preceded_by_click")
                or annotation.get("expected_when_preceded_by_click_flask")
                or ""
            )
            expected_ids = family_skill_ids.get(family, set()) if family else set()
            candidates = retrieval.get("candidates") or []
            expected_ranks = [
                index for index, candidate in enumerate(candidates, 1)
                if candidate.get("skill_id") in expected_ids
            ]
            gate = retrieval.get("task_skill_gate") or {}
            selection = retrieval.get("selection") or gate.get("consumption_status")
            selected_id = retrieval.get("selected_skill_id") or gate.get("selected_skill_id")
            row = {
                "repo": entry["repo"], "task_id": task_id,
                "skill_family": family, "sop": annotation.get("sop"),
                "expected_reuse": expected_reuse,
                "expected_skill_ids": sorted(expected_ids),
                "expected_candidate_ranks": expected_ranks,
                "retrieval_recall_at_3": bool(expected_ranks) if expected_reuse else None,
                "selection": selection,
                "selected_skill_id": selected_id,
                "selected_expected_skill": selected_id in expected_ids if selected_id else False,
                "correct_consumption_before_repo": bool(
                    expected_reuse and selected_id in expected_ids
                    and gate.get("consumption_status") == "VIEWED_BEFORE_REPO"
                    and not gate.get("post_view_decision")
                ),
                "false_reject": bool(
                    expected_reuse and expected_ranks
                    and selection in (
                        "REJECTED_BEFORE_REPO", "REJECTED_LATE",
                        "GATE_MISSED", "CANDIDATES_SKIPPED",
                    )
                ),
                "wrong_skill_view": bool(
                    selected_id and expected_reuse and selected_id not in expected_ids
                ),
                "non_sop_view": bool(annotation.get("sop") is False and selected_id),
            }
            repo_rows.append(row)
            evaluations.append(row)
        report["private_skill_evaluation"] = {"visibility":"private-evaluator-only", "tasks":repo_rows}
        write(report_path, report)
        entry.update(public_child_state(report))

    expected = [row for row in evaluations if row["expected_reuse"]]
    state["private_skill_evaluation"] = {
        "visibility":"private-evaluator-only",
        "family_skill_ids": {key:sorted(value) for key,value in family_skill_ids.items()},
        "tasks":evaluations,
        "aggregate": {
            "expected_reuse_tasks":len(expected),
            "retrieval_recall_at_3":sum(row["retrieval_recall_at_3"] is True for row in expected),
            "correct_consumption_before_repo":sum(row["correct_consumption_before_repo"] for row in expected),
            "false_reject":sum(row["false_reject"] for row in expected),
            "wrong_skill_view":sum(row["wrong_skill_view"] for row in evaluations),
            "non_sop_view":sum(row["non_sop_view"] for row in evaluations),
        },
    }


def run_experiment(plan: dict[str, Any], run_id: str, langfuse_config: Path | None, timeout: int, resume_from: str | None = None) -> dict[str, Any]:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("Run ID must be 1-48 safe characters")
    run_dir = ROOT / "runs" / run_id
    if run_dir.exists():
        raise ValueError("Experiment run already exists; paid calls are never replayed")
    run_dir.mkdir(parents=True)
    progress_path = run_dir / "progress.json"
    driver_bytes = Path(__file__).read_bytes()
    checkpoint = resolve_resume_checkpoint(plan, resume_from) if resume_from else None
    state: dict[str, Any] = {
        "schema_version": 1,
        "experiment_run_id": run_id,
        "experiment_id": plan["experiment_id"],
        "variant": plan["variant"],
        "method_revision": plan.get("method_revision"),
        "status": "RUNNING",
        "started_at": now(),
        "plan": plan,
        "driver_sha256": hashlib.sha256(driver_bytes).hexdigest(),
        "observability_mode": "langfuse+local" if langfuse_config else "local-only",
        "sessions": [
            {**entry, "checkpoint_reused": True, "checkpoint_source_run": resume_from}
            for entry in (checkpoint["completed_sessions"] if checkpoint else [])
        ],
        "progress": {"phase": "STARTING", "updated_at": now()},
    }
    write(run_dir / "plan.snapshot.json", plan)
    write(progress_path, state)
    current: subprocess.Popen[bytes] | None = None
    baseline_service: dict[str, Any] | None = None
    try:
        memory_enabled = plan["variant"] in ("baseline", "ours", "ours_v2", "ours_v3")
        if memory_enabled:
            state["progress"] = {"phase": "MEMORY_SERVICE_STARTING", "updated_at": now()}
            write(progress_path, state)
            baseline_dir = run_dir / plan["variant"]
            if checkpoint:
                copy_baseline_checkpoint(checkpoint["baseline_dir"], baseline_dir)
                state["resume"] = {
                    "source_run": checkpoint["run_id"],
                    "source_result": checkpoint["result_path"],
                    "completed_sessions_reused": len(checkpoint["completed_sessions"]),
                    "next_session_ordinal": len(checkpoint["completed_sessions"]) + 1,
                    "persistent_state_copied": True,
                    "partial_session": checkpoint.get("partial_session"),
                }
            runtime = load_session_runtime()
            baseline_service = runtime.start_baseline(
                run_id, baseline_dir, langfuse_config.resolve() if langfuse_config else None, resume=bool(checkpoint),
                ours=plan["variant"] == "ours",
                ours_v2=plan["variant"] == "ours_v2",
                ours_v3=plan["variant"] == "ours_v3",
            )
            descriptor = {
                "schema_version": 1,
                "experiment_run_id": run_id,
                "variant": plan["variant"],
                "method_revision": plan.get("method_revision"),
                "directory": str(baseline_dir.resolve()),
                "network": baseline_service["network"],
                "proxy_port": baseline_service["proxy_port"],
                "core_port": baseline_service["core_port"],
                "identity": baseline_service["identity"],
            }
            descriptor_path = baseline_dir / "private" / "experiment-service.json"
            write(descriptor_path, descriptor)
            service_state = {
                "scope": "experiment",
                "shared_across_repo_sessions": True,
                "native_async_extraction": True,
                "forced_task_boundary": plan["variant"] in OURS_PROFILE_BY_VARIANT,
                "native_auto_archive": plan["variant"] == "baseline",
                "skill_listing_lifecycle": "task" if plan["variant"] in ("ours_v2", "ours_v3") else "session",
                "skill_tools_lifecycle": "task-gate" if plan["variant"] == "ours_v3" else "session",
                "proxy_port": baseline_service["proxy_port"],
                "core_port": baseline_service["core_port"],
            }
            state["baseline" if plan["variant"] == "baseline" else "ours"] = {
                **service_state,
                "method_revision": plan.get("method_revision"),
                **({"profiles":plan["profiles"]} if plan["variant"] in OURS_PROFILE_BY_VARIANT else {}),
            }
            state["progress"] = {"phase": "MEMORY_SERVICE_READY", "updated_at": now()}
            write(progress_path, state)
        completed_prefix = len(checkpoint["completed_sessions"]) if checkpoint else 0
        for item in plan["sessions"][completed_prefix:]:
            ordinal = item["session_ordinal"]
            repo = item["repo"]
            suite_root = Path(item["suite_root"])
            child_run = child_run_id(run_id, ordinal, repo)
            child_report = suite_root / "reports" / f"{child_run}.json"
            session_dir = run_dir / f"session-{ordinal:02d}-{slug(repo)}"
            session_dir.mkdir()
            entry: dict[str, Any] = {
                "session_ordinal": ordinal,
                "repo": repo,
                "benchmark_id": item["benchmark_id"],
                "suite_root": str(suite_root),
                "base_commit": item["base_commit"],
                "child_run_id": child_run,
                "child_report": str(child_report),
                "status": "STARTING",
                "started_at": now(),
            }
            state["sessions"].append(entry)
            state["progress"] = {
                "phase": "SESSION_STARTING",
                "updated_at": now(),
                "session_ordinal": ordinal,
                "repo": repo,
            }
            state["metrics"] = aggregate_metrics(state["sessions"])
            write(progress_path, state)
            env = os.environ.copy()
            env.update(
                BENCHMARK_SUITE_ROOT=str(suite_root),
                BENCHMARK_EXPERIMENT_RUN_ID=run_id,
                BENCHMARK_REPO_NAME=repo,
                BENCHMARK_REPO_SESSION_ORDINAL=str(ordinal),
            )
            if baseline_service:
                env["BENCHMARK_EXTERNAL_BASELINE_CONFIG"] = str(descriptor_path)
                env["BENCHMARK_EXPERIMENT_FINAL_SESSION"] = "1" if ordinal == len(plan["sessions"]) else "0"
            command = [
                sys.executable,
                str(SESSION_DRIVER),
                "--run",
                child_run,
                "--variant",
                plan["variant"],
                "--locale",
                "en",
                "--timeout",
                str(timeout),
            ]
            if langfuse_config:
                command += ["--langfuse-config", str(langfuse_config.resolve())]
            if item.get("only_task"):
                command += ["--only-task", item["only_task"]]
            partial = checkpoint.get("partial_session") if checkpoint else None
            if partial and partial.get("session_ordinal") == ordinal:
                command += ["--resume-child-run", partial["child_run_id"]]
            public_env_keys = [
                "BENCHMARK_SUITE_ROOT", "BENCHMARK_EXPERIMENT_RUN_ID",
                "BENCHMARK_REPO_NAME", "BENCHMARK_REPO_SESSION_ORDINAL",
            ]
            if baseline_service:
                public_env_keys.append("BENCHMARK_EXPERIMENT_FINAL_SESSION")
            write(session_dir / "invocation.json", {"argv": command, "env": {k: env[k] for k in public_env_keys}})
            stdout = (session_dir / "driver.stdout.log").open("wb")
            stderr = (session_dir / "driver.stderr.log").open("wb")
            try:
                current = subprocess.Popen(
                    command,
                    cwd=SESSION_DRIVER.parent,
                    env=env,
                    stdout=stdout,
                    stderr=stderr,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                while current.poll() is None:
                    if child_report.is_file():
                        refresh_child_progress(entry, child_report)
                    entry["status"] = entry.get("status") or "RUNNING"
                    state["progress"] = {
                        "phase": "SESSION_RUNNING",
                        "updated_at": now(),
                        "session_ordinal": ordinal,
                        "repo": repo,
                        "child_progress": entry.get("progress"),
                    }
                    state["metrics"] = aggregate_metrics(state["sessions"])
                    write(progress_path, state)
                    time.sleep(1)
                if child_report.is_file():
                    child = read(child_report, sharing_timeout=30)
                    entry.update(public_child_state(child))
                entry["exit_code"] = current.returncode
                entry["ended_at"] = now()
                if current.returncode != 0 or entry.get("status") != "COMPLETED":
                    entry["status"] = "INTERRUPTED"
                    raise RuntimeError(f"Repository session {ordinal} ({repo}) failed")
                state["progress"] = {
                    "phase": "SESSION_COMPLETE",
                    "updated_at": now(),
                    "session_ordinal": ordinal,
                    "repo": repo,
                }
                state["metrics"] = aggregate_metrics(state["sessions"])
                write(progress_path, state)
            finally:
                stdout.close()
                stderr.close()
            # Clear the process reference only after the child completed and its
            # terminal report was accepted.  Outer failures must still be able to
            # find and stop the active child.
            current = None
        session_ids = [item.get("claude_session_id") for item in state["sessions"]]
        if len(session_ids) != len(set(session_ids)) or any(not value for value in session_ids):
            raise RuntimeError("Claude session isolation could not be proven")
        if plan["variant"] in OURS_PROFILE_BY_VARIANT:
            # The final child drained the shared async queue. Re-export every
            # session now so early repositories also contain their completed
            # extraction traces and per-archive costs.
            runtime = load_session_runtime()
            state["progress"] = {"phase":"FINAL_EXTRACTION_RECONCILE", "updated_at":now()}
            write(progress_path, state)
            for entry in state["sessions"]:
                child_report = Path(entry["child_report"])
                child = read(child_report, sharing_timeout=30)
                pilot = Path(entry["suite_root"]) / "runtime" / "runs" / entry["child_run_id"] / "pilot"
                child["async_drain_completed"] = True
                child.setdefault("skill_snapshots", []).append(
                    runtime.skill_snapshot(baseline_dir, "EXPERIMENT_FINAL_RECONCILE")
                )
                child["observability"] = runtime.export_langfuse(
                    child, pilot, langfuse_config.resolve() if langfuse_config else None,
                )
                runtime.reconcile_ours_local(child, baseline_dir)
                runtime.reconcile_ours_langfuse(child, pilot)
                child["metrics_completeness"]["langfuse"] = child["observability"]["status"]
                child["metrics_completeness"]["local_extraction_audit"] = "COMPLETE"
                runtime.update_extraction_metrics_completeness(child)
                child["metrics"] = runtime.calculate_metrics(child)
                write(child_report, child)
                entry.update(public_child_state(child))
            annotate_private_skill_evaluation(state)
        state["status"] = "COMPLETED"
        state["progress"] = {"phase": "COMPLETED", "updated_at": now()}
    except BaseException as error:
        if current and current.poll() is None:
            current.terminate()
            try:
                current.wait(timeout=20)
            except subprocess.TimeoutExpired:
                current.kill()
                current.wait(timeout=10)
        state["status"] = "INTERRUPTED"
        state["error"] = str(error)
        state["progress"] = {"phase": "INTERRUPTED", "updated_at": now()}
        raise
    finally:
        stop_baseline(baseline_service)
        state["ended_at"] = now()
        state["metrics"] = aggregate_metrics(state["sessions"])
        write(progress_path, state)
        write(run_dir / "result.json", state)
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--run")
    parser.add_argument("--langfuse-config", type=Path, default=None,
                        help="Optional. Omit for local-only JSON/JSONL observability.")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check-images", action="store_true")
    parser.add_argument("--resume-from", help="Interrupted Baseline run ID with a safe completed-session checkpoint")
    args = parser.parse_args()
    plan = resolve_plan(args.plan, check_images=args.check_images)
    if args.dry_run:
        print(json.dumps({"status": "READY", "paid_calls": False, "plan": plan}, ensure_ascii=False, indent=2))
        return 0
    if not args.run:
        parser.error("--run is required unless --dry-run is used")
    result = run_experiment(plan, args.run, args.langfuse_config, args.timeout, args.resume_from)
    print(json.dumps({"status": result["status"], "result": str(ROOT / "runs" / args.run / "result.json")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
