#!/usr/bin/env python3
"""Validate provenance, uniqueness, leakage controls, splits, and release gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RESULTS = ROOT / "results"


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def normalized_problem(text: str) -> str:
    return " ".join(text.lower().split())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)

    candidates = read_jsonl(DATA / "candidates.jsonl")
    frozen = read_jsonl(DATA / "selected-roots.jsonl")
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    families = json.loads((DATA / "families.json").read_text(encoding="utf-8"))
    registry = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
    schema = json.loads((ROOT / "schemas" / "task-root.schema.json").read_text(encoding="utf-8"))
    source_by_id = {source["id"]: source for source in registry["sources"]}
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def fail(code: str, detail: Any) -> None:
        errors.append({"code": code, "detail": detail})

    if len(candidates) < 5000:
        fail("candidate_count", len(candidates))
    if len(frozen) != 1000:
        fail("frozen_count", len(frozen))

    candidate_ids = [row["root_id"] for row in candidates]
    frozen_ids = [row["root_id"] for row in frozen]
    if len(set(candidate_ids)) != len(candidate_ids):
        fail("duplicate_candidate_root_id", len(candidate_ids) - len(set(candidate_ids)))
    if len(set(frozen_ids)) != len(frozen_ids):
        fail("duplicate_frozen_root_id", len(frozen_ids) - len(set(frozen_ids)))
    if not set(frozen_ids).issubset(set(candidate_ids)):
        fail("frozen_not_candidate", sorted(set(frozen_ids) - set(candidate_ids))[:20])

    root_keys: set[tuple[str, str, str, str]] = set()
    trajectory_owner: dict[str, str] = {}
    normalized: dict[str, str] = {}
    blocked_sources = {source["id"] for source in registry["sources"] if source["status"] == "license_blocked"}
    synthetic_markers = ["observed actual output", "a reproducible task in", "sop completed successfully"]

    for index, row in enumerate(frozen):
        schema_errors = sorted(validator.iter_errors(row), key=lambda item: list(item.path))
        for schema_error in schema_errors:
            fail("schema", {"root_id": row.get("root_id"), "path": list(schema_error.path), "message": schema_error.message})
        key = (row["source_dataset"], row["repo"], row["base_commit"], row["instance_id"])
        if key in root_keys:
            fail("duplicate_root_key", key)
        root_keys.add(key)
        source = source_by_id.get(row["source_dataset"])
        if not source or source["status"] != "enabled":
            fail("disabled_or_unknown_source", {"root_id": row["root_id"], "source": row["source_dataset"]})
        if row["source_dataset"] in blocked_sources:
            fail("license_blocked_source_used", row["root_id"])
        if source and row["source_revision"] != source.get("revision"):
            fail("revision_mismatch", row["root_id"])
        if not row["repository_license"] or re.search(r"unknown|noassertion|none", row["repository_license"], re.I):
            fail("unknown_repository_license", row["root_id"])
        if not row["evaluator"]["fail_to_pass"] or not row["evaluator"]["image_name"]:
            fail("missing_deterministic_evaluator", row["root_id"])
        if not row["native_trajectory_ids"]:
            fail("missing_native_trajectory", row["root_id"])
        for trajectory_id in row["native_trajectory_ids"]:
            previous = trajectory_owner.setdefault(trajectory_id, row["root_id"])
            if previous != row["root_id"]:
                fail("trajectory_cross_root", {"trajectory_id": trajectory_id, "roots": [previous, row["root_id"]]})

        identity = {"source": row["source_dataset"], "repo": row["repo"], "base_commit": row["base_commit"], "instance_id": row["instance_id"]}
        expected_task_hash = digest({"identity": identity, "problem_statement": row["problem_statement"], "evaluator": [row["evaluator"]["fail_to_pass"], row["evaluator"]["pass_to_pass"]]})
        if expected_task_hash != row["content_hashes"]["task_sha256"]:
            fail("task_hash_mismatch", row["root_id"])
        trajectory_source = source_by_id["swe-rebench-openhands"]
        provenance = {
            "task_source_url": source["url"],
            "task_revision": source["revision"],
            "trajectory_source_url": trajectory_source["url"],
            "trajectory_revision": trajectory_source["revision"],
            "instance_id": row["instance_id"],
        }
        if digest(provenance) != row["content_hashes"]["provenance_sha256"]:
            fail("provenance_hash_mismatch", row["root_id"])
        text = normalized_problem(row["problem_statement"])
        if text in normalized:
            fail("exact_duplicate_problem", [normalized[text], row["root_id"]])
        normalized[text] = row["root_id"]
        if any(marker in text for marker in synthetic_markers):
            fail("synthetic_template_marker", row["root_id"])

    # High-threshold textual review queue: recurrence is allowed; near identity is not.
    signatures: dict[tuple[str, int, str], list[tuple[str, str]]] = defaultdict(list)
    for row in frozen:
        text = normalized_problem(row["problem_statement"])
        bucket = (row["repo"], len(text) // 100, text[:24])
        signatures[bucket].append((row["root_id"], text))
    near_duplicates = []
    for values in signatures.values():
        for left_index in range(len(values)):
            for right_index in range(left_index + 1, len(values)):
                left_id, left = values[left_index]
                right_id, right = values[right_index]
                ratio = SequenceMatcher(None, left, right, autojunk=False).ratio()
                if ratio >= 0.97:
                    near_duplicates.append({"left": left_id, "right": right_id, "similarity": round(ratio, 6), "decision": "quarantine_required"})
    if near_duplicates:
        fail("unhandled_near_duplicates", near_duplicates)

    family_members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in frozen:
        if row["family_id"]:
            family_members[row["family_id"]].append(row)
    for family in families["families"]:
        members = family_members.get(family["family_id"], [])
        years = {row["created_at"][:4] for row in members}
        if len(members) < 10 or len(years) < 2:
            fail("family_admission", {"family_id": family["family_id"], "members": len(members), "years": sorted(years)})
        splits = {row["chronological_split"] for row in members}
        if "zero_shot_holdout" in splits and len(splits) != 1:
            fail("zero_shot_split_leakage", family["family_id"])
        if family["status"] != "pending_utility_review":
            fail("family_prematurely_accepted", family["family_id"])

    expected_hash = digest(sorted(frozen, key=lambda row: row["root_id"]))
    if manifest.get("selected_sha256") != expected_hash:
        fail("manifest_hash_mismatch", {"expected": expected_hash, "actual": manifest.get("selected_sha256")})

    split_counts = Counter(row["chronological_split"] for row in frozen)
    license_counts = Counter(row["repository_license"] for row in frozen)
    outcome_counts = Counter(row["execution_result"]["status"] for row in frozen)
    report = {
        "schema_version": 1,
        "status": "collection_pass" if not errors else "fail",
        "strict": args.strict,
        "counts": {
            "candidates": len(candidates),
            "selected": len(frozen),
            "unique_root_keys": len(root_keys),
            "native_trajectories": len(trajectory_owner),
            "candidate_families": len(families["families"]),
            "near_duplicate_pairs": len(near_duplicates),
        },
        "split_counts": dict(split_counts),
        "license_counts": dict(license_counts),
        "outcome_counts": dict(outcome_counts),
        "errors": errors,
        "warnings": warnings,
    }
    (RESULTS / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if errors and args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
