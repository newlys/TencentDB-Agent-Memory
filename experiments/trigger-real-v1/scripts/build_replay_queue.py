#!/usr/bin/env python3
"""Build a deterministic, outcome-stratified 100-root WorkBuddy replay queue."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
from collections import defaultdict
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
REPLAYS = ROOT / "replays"
RESULTS = ROOT / "results"


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def reachable(url: str) -> bool:
    try:
        with urlopen(url, timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def docker_ready() -> bool:
    executable = shutil.which("docker")
    if not executable:
        return False
    try:
        result = subprocess.run([executable, "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=8)
        return result.returncode == 0
    except Exception:
        return False


def select(rows: list[dict], count: int) -> list[dict]:
    strata: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        family_state = "clustered" if row["family_id"] else "long_tail"
        strata[(family_state, row["execution_result"]["status"])].append(row)
    for values in strata.values():
        values.sort(key=lambda row: hashlib.sha256(row["root_id"].encode()).hexdigest())
    selected = []
    depth = 0
    keys = sorted(strata)
    while len(selected) < count:
        added = False
        for key in keys:
            if depth < len(strata[key]):
                selected.append(strata[key][depth])
                added = True
                if len(selected) == count:
                    break
        if not added:
            break
        depth += 1
    if len(selected) != count:
        raise RuntimeError(f"Could select only {len(selected)} replay roots")
    return selected


def main() -> int:
    rows = read_jsonl(DATA / "selected-roots.jsonl")
    selected = select(rows, 100)
    REPLAYS.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    queue = []
    for row in selected:
        queue.append({
            "root_id": row["root_id"],
            "repo": row["repo"],
            "base_commit": row["base_commit"],
            "problem_statement": row["problem_statement"],
            "image_name": row["evaluator"]["image_name"],
            "fail_to_pass": row["evaluator"]["fail_to_pass"],
            "pass_to_pass": row["evaluator"]["pass_to_pass"],
            "family_id": row["family_id"],
            "split": row["chronological_split"],
            "native_status": row["execution_result"]["status"],
            "seed": 20260824,
            "required_arms": ["baseline_empty_skill_store", "skill_latest_family_version"],
            "status": "queued",
        })
    queue_path = REPLAYS / "queue.jsonl"
    with queue_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in queue:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    user_profile = Path(os.environ.get("USERPROFILE", ""))
    workbuddy_node = user_profile / ".workbuddy" / "binaries" / "node" / "versions" / "22.22.2" / "node.exe"
    workbuddy_cli = Path("D:/WorkBuddy/resources/app.asar.unpacked/cli/dist/codebuddy.js")
    checks = {
        "queue_count": len(queue),
        "workbuddy_node": workbuddy_node.exists(),
        "workbuddy_cli": workbuddy_cli.exists(),
        "baseline_user_key_present": bool(os.environ.get("TDAI_BASELINE_USER_KEY")),
        "memory_core_healthy": reachable("http://127.0.0.1:8420/health"),
        "memory_proxy_healthy": reachable("http://127.0.0.1:8096/health"),
        "docker_ready": docker_ready(),
    }
    blockers = [key for key, value in checks.items() if key != "queue_count" and not value]
    status = {
        "schema_version": 1,
        "status": "ready" if not blockers else "not_executed",
        "checks": checks,
        "blockers": blockers,
        "completed_pairs": 0,
        "required_pairs": 100,
        "result_file": "replays/paired-results.jsonl",
        "truthfulness_note": "Native OpenHands outcomes are not substituted for WorkBuddy A/B outcomes."
    }
    (RESULTS / "workbuddy-replay-status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
