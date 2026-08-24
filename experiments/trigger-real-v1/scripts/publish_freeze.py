#!/usr/bin/env python3
"""Fail-closed publisher for the final frozen benchmark."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RESULTS = ROOT / "results"


def main() -> int:
    validation = json.loads((RESULTS / "validation.json").read_text(encoding="utf-8"))
    replay = json.loads((RESULTS / "workbuddy-replay-status.json").read_text(encoding="utf-8"))
    families = json.loads((DATA / "families.json").read_text(encoding="utf-8"))
    blockers = []
    if validation["status"] != "collection_pass":
        blockers.append("collection_validation")
    if replay.get("completed_pairs") != 100 or replay.get("status") != "complete":
        blockers.append("workbuddy_100_paired_replays")
    pending = [family["family_id"] for family in families["families"] if family["status"] == "pending_utility_review"]
    if pending:
        blockers.append(f"family_utility_review:{len(pending)}")
    audit_path = RESULTS / "annotation-audit.json"
    if not audit_path.exists():
        blockers.append("annotation_audit")
    else:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if audit.get("boundary_agreement", 0) < 0.95 or audit.get("action_cohen_kappa", 0) < 0.90:
            blockers.append("annotation_thresholds")
    if blockers:
        print(json.dumps({"status": "not_frozen", "blockers": blockers}, ensure_ascii=False, indent=2))
        return 2
    shutil.copyfile(DATA / "selected-roots.jsonl", DATA / "frozen-roots.jsonl")
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    manifest["release_status"] = "frozen"
    manifest["frozen_count"] = manifest["selected_count"]
    manifest["frozen_sha256"] = manifest["selected_sha256"]
    (DATA / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "frozen", "count": manifest["frozen_count"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
