#!/usr/bin/env python3
"""Acquire real task roots by joining immutable public task and trajectory data.

The script deliberately reads only Parquet columns needed for the manifest. It
does not manufacture messages, task outcomes, Skill names, or lifecycle labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import duckdb


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "sources.json"
DATA = ROOT / "data"
RESULTS = ROOT / "results"
CACHE = ROOT / ".cache"


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(value: Any) -> str:
    payload = value if isinstance(value, bytes) else canonical(value)
    return hashlib.sha256(payload).hexdigest()


def hf_url(source: dict[str, Any], file_name: str) -> str:
    return f"https://huggingface.co/datasets/{source['repository']}/resolve/{source['revision']}/{file_name}"


def parse_array(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, list) else [decoded]
        except json.JSONDecodeError:
            return [value]
    return [value]


def pr_number(instance_id: str) -> str | None:
    match = re.search(r"-(\d+)$", instance_id)
    return match.group(1) if match else None


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def load_sources() -> tuple[dict[str, Any], dict[str, Any]]:
    registry = json.loads(CONFIG.read_text(encoding="utf-8"))
    sources = {item["id"]: item for item in registry["sources"]}
    task = sources["swe-rebench"]
    trajectory = sources["swe-rebench-openhands"]
    if task["status"] != "enabled" or trajectory["status"] != "enabled":
        raise RuntimeError("Core sources must be enabled")
    return task, trajectory


def ensure_trajectory_index(connection: duckdb.DuckDBPyConnection, source: dict[str, Any], refresh: bool) -> Path:
    """Materialize only public trajectory index columns into an ignored cache."""
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / f"{source['id']}-{source['revision']}.parquet"
    if target.exists() and not refresh:
        return target
    if target.exists():
        target.unlink()
    remote = hf_url(source, source["files"][0])
    temporary = target.with_suffix(".parquet.partial")
    if temporary.exists():
        temporary.unlink()
    escaped_remote = remote.replace("'", "''")
    escaped_target = str(temporary).replace("\\", "/").replace("'", "''")
    connection.execute(
        f"COPY (SELECT trajectory_id, instance_id, repo, exit_status, resolved FROM read_parquet('{escaped_remote}')) "
        f"TO '{escaped_target}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    temporary.replace(target)
    return target


def acquire_rows(candidate_count: int, refresh_index: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    task_source, trajectory_source = load_sources()
    task_urls = [hf_url(task_source, name) for name in task_source["files"]]
    connection = duckdb.connect()
    connection.execute("SET enable_progress_bar=false")
    connection.execute("SET memory_limit='6GB'")
    trajectory_index = ensure_trajectory_index(connection, trajectory_source, refresh_index)
    query = """
        WITH trace_summary AS (
          SELECT
            instance_id,
            any_value(repo) AS trace_repo,
            count(*)::INTEGER AS rollout_count,
            sum(CASE WHEN resolved = 1 THEN 1 ELSE 0 END)::INTEGER AS resolved_count,
            list(trajectory_id ORDER BY trajectory_id) AS trajectory_ids
          FROM read_parquet(?)
          GROUP BY instance_id
        )
        SELECT
          roots.instance_id,
          roots.repo,
          roots.base_commit,
          roots.created_at,
          'python' AS language,
          roots.license_name AS license,
          roots.problem_statement,
          roots.image_name,
          roots.FAIL_TO_PASS,
          roots.PASS_TO_PASS,
          traces.rollout_count,
          traces.resolved_count,
          traces.trajectory_ids
        FROM read_parquet(?) AS roots
        INNER JOIN trace_summary AS traces USING (instance_id)
        WHERE roots.instance_id IS NOT NULL
          AND length(trim(roots.problem_statement)) >= 20
          AND length(trim(roots.base_commit)) >= 7
          AND length(trim(roots.license_name)) > 0
          AND length(trim(roots.image_name)) > 0
          AND traces.trace_repo = roots.repo
        QUALIFY row_number() OVER (PARTITION BY roots.instance_id ORDER BY roots.created_at) = 1
        ORDER BY md5(roots.instance_id)
        LIMIT ?
    """
    records = connection.execute(query, [str(trajectory_index), task_urls, candidate_count]).fetchall()
    columns = [column[0] for column in connection.description]
    connection.close()

    rows: list[dict[str, Any]] = []
    for record in records:
        raw = dict(zip(columns, record))
        instance_id = str(raw["instance_id"])
        repo = str(raw["repo"])
        number = pr_number(instance_id)
        trajectories = sorted({str(value) for value in parse_array(raw["trajectory_ids"])})
        resolved = int(raw["resolved_count"] or 0)
        rollouts = int(raw["rollout_count"] or 0)
        task_identity = {
            "source": task_source["id"],
            "repo": repo,
            "base_commit": str(raw["base_commit"]),
            "instance_id": instance_id,
        }
        provenance = {
            "task_source_url": task_source["url"],
            "task_revision": task_source["revision"],
            "trajectory_source_url": trajectory_source["url"],
            "trajectory_revision": trajectory_source["revision"],
            "instance_id": instance_id,
        }
        outcome = "mixed" if resolved and resolved < rollouts else ("native_success" if resolved else "native_failure")
        row = {
            "root_id": f"real-{sha256(task_identity)[:20]}",
            "source_dataset": task_source["id"],
            "source_revision": task_source["revision"],
            "instance_id": instance_id,
            "repo": repo,
            "issue_url": f"https://github.com/{repo}/issues/{number}" if number else None,
            "pull_request_url": f"https://github.com/{repo}/pull/{number}" if number else None,
            "base_commit": str(raw["base_commit"]),
            "created_at": str(raw["created_at"]),
            "language": str(raw["language"]),
            "repository_license": str(raw["license"]),
            "dataset_license": task_source["dataset_license"],
            "problem_statement": str(raw["problem_statement"]),
            "native_trajectory_ids": trajectories,
            "evaluator": {
                "fail_to_pass": parse_array(raw["FAIL_TO_PASS"]),
                "pass_to_pass": parse_array(raw["PASS_TO_PASS"]),
                "image_name": str(raw["image_name"]),
            },
            "execution_result": {
                "native_resolved_rollouts": resolved,
                "native_failed_rollouts": rollouts - resolved,
                "status": outcome,
            },
            "content_hashes": {
                "task_sha256": sha256({"identity": task_identity, "problem_statement": raw["problem_statement"], "evaluator": [raw["FAIL_TO_PASS"], raw["PASS_TO_PASS"]]}),
                "provenance_sha256": sha256(provenance),
            },
            "family_id": None,
            "family_confidence": None,
            "chronological_split": "unassigned",
            "boundary_annotations": [],
            "skill_events": [],
        }
        rows.append(row)

    stats = {
        "task_source": task_source["id"],
        "task_revision": task_source["revision"],
        "trajectory_source": trajectory_source["id"],
        "trajectory_revision": trajectory_source["revision"],
        "qualified_candidates": len(rows),
        "unique_repositories": len({row["repo"] for row in rows}),
        "native_success_roots": sum(row["execution_result"]["native_resolved_rollouts"] > 0 for row in rows),
        "native_failure_only_roots": sum(row["execution_result"]["native_resolved_rollouts"] == 0 for row in rows),
    }
    return rows, stats


def freeze(rows: list[dict[str, Any]], freeze_count: int) -> list[dict[str, Any]]:
    """Select without outcome labels, preferring repositories with >=10 roots.

    Round-robin selection prevents one giant repository from dominating while
    retaining enough chronological density to discover recurring patterns.
    """
    by_repo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_repo[row["repo"]].append(row)
    eligible = {repo: sorted(items, key=lambda item: (item["created_at"], item["instance_id"])) for repo, items in by_repo.items() if len(items) >= 10}
    ranked_repos = sorted(eligible, key=lambda repo: (-len(eligible[repo]), repo))
    selected: list[dict[str, Any]] = []
    seen_problem_hashes: set[str] = set()
    depth = 0
    while len(selected) < freeze_count:
        added = False
        for repo in ranked_repos:
            if depth < len(eligible[repo]):
                candidate = eligible[repo][depth]
                problem_hash = hashlib.sha256(" ".join(candidate["problem_statement"].lower().split()).encode("utf-8")).hexdigest()
                if problem_hash not in seen_problem_hashes:
                    seen_problem_hashes.add(problem_hash)
                    selected.append(candidate)
                    added = True
                    if len(selected) == freeze_count:
                        break
        if not added:
            break
        depth += 1
    if len(selected) != freeze_count:
        raise RuntimeError(f"Only {len(selected)} roots survive the repository-density gate; need {freeze_count}")
    return sorted(selected, key=lambda row: row["root_id"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-count", type=int, default=5000)
    parser.add_argument("--freeze-count", type=int, default=1000)
    parser.add_argument("--refresh-index", action="store_true")
    args = parser.parse_args()
    if args.candidate_count < args.freeze_count or args.freeze_count < 1:
        parser.error("candidate-count must be >= freeze-count >= 1")

    DATA.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    rows, stats = acquire_rows(args.candidate_count, args.refresh_index)
    if len(rows) < args.candidate_count:
        raise RuntimeError(f"Source join returned {len(rows)} candidates; refusing to pad to {args.candidate_count}")
    frozen = freeze(rows, args.freeze_count)
    write_jsonl(DATA / "candidates.jsonl", rows)
    write_jsonl(DATA / "selected-roots.jsonl", frozen)

    repo_counts = Counter(row["repo"] for row in frozen)
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selection_policy": "pre-task fields only; repositories with >=10 candidates; deterministic chronological round-robin",
        "candidate_count": len(rows),
        "selected_count": len(frozen),
        "selected_sha256": sha256(frozen),
        "source_stats": stats,
        "selected_repository_count": len(repo_counts),
        "selected_repository_counts": dict(sorted(repo_counts.items(), key=lambda item: (-item[1], item[0]))),
        "release_status": "provisional_pending_family_and_utility_review",
    }
    (DATA / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
