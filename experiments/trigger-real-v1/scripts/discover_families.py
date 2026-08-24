#!/usr/bin/env python3
"""Discover candidate recurring families from pre-task text only.

Clusters are hypotheses, not gold Skills. Acceptance remains pending until a
reviewer confirms a reusable workflow and paired downstream runs show utility.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from sklearn.cluster import HDBSCAN
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize


ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "data" / "selected-roots.jsonl"
FAMILIES = ROOT / "data" / "families.json"
MANIFEST = ROOT / "data" / "manifest.json"


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def normalize_text(text: str) -> str:
    text = re.sub(r"https?://\S+", " URL ", text.lower())
    text = re.sub(r"\b[0-9a-f]{7,40}\b", " HASH ", text)
    text = re.sub(r"\b\d+(?:\.\d+)+\b", " VERSION ", text)
    return re.sub(r"\s+", " ", text).strip()


def family_id(label: int, member_ids: list[str]) -> str:
    digest = hashlib.sha256("\n".join(sorted(member_ids)).encode()).hexdigest()[:12]
    return f"natural-{label:03d}-{digest}"


def split_members(members: list[dict], zero_shot: bool) -> None:
    ordered = sorted(members, key=lambda row: (row["created_at"], row["root_id"]))
    if zero_shot:
        for row in ordered:
            row["chronological_split"] = "zero_shot_holdout"
        return
    count = len(ordered)
    discovery_end = max(1, math.floor(count * 0.5))
    development_end = max(discovery_end + 1, math.floor(count * 0.7))
    development_end = min(development_end, count - 1)
    for index, row in enumerate(ordered):
        row["chronological_split"] = (
            "discovery" if index < discovery_end else
            "development" if index < development_end else
            "lifecycle_test"
        )


def main() -> int:
    rows = read_jsonl(FROZEN)
    documents = [normalize_text(row["problem_statement"]) for row in rows]
    vectorizer = TfidfVectorizer(
        strip_accents="unicode",
        stop_words="english",
        ngram_range=(1, 2),
        min_df=2,
        max_df=0.92,
        max_features=30000,
        sublinear_tf=True,
    )
    sparse = vectorizer.fit_transform(documents)
    dimensions = min(96, sparse.shape[0] - 1, sparse.shape[1] - 1)
    reduced = TruncatedSVD(n_components=dimensions, random_state=20260824).fit_transform(sparse)
    reduced = normalize(reduced)
    clusterer = HDBSCAN(min_cluster_size=10, min_samples=2, cluster_selection_method="eom")
    labels = clusterer.fit_predict(reduced)
    probabilities = clusterer.probabilities_

    grouped: dict[int, list[int]] = defaultdict(list)
    for index, label in enumerate(labels):
        if label >= 0:
            grouped[int(label)].append(index)

    feature_names = np.asarray(vectorizer.get_feature_names_out())
    candidates: list[dict] = []
    accepted_labels: set[int] = set()
    for label, indices in sorted(grouped.items()):
        members = [rows[index] for index in indices]
        unique_roots = {row["root_id"] for row in members}
        years = {str(row["created_at"])[:4] for row in members}
        repositories = {row["repo"] for row in members}
        if len(unique_roots) < 10 or len(years) < 2:
            continue
        accepted_labels.add(label)
        centroid = np.asarray(sparse[indices].mean(axis=0)).ravel()
        top_indices = centroid.argsort()[-12:][::-1]
        terms = [str(feature_names[index]) for index in top_indices if centroid[index] > 0]
        fid = family_id(label, [row["root_id"] for row in members])
        candidates.append({
            "family_id": fid,
            "status": "pending_utility_review",
            "member_count": len(members),
            "repository_count": len(repositories),
            "time_window_count": len(years),
            "years": sorted(years),
            "top_pre_task_terms": terms,
            "mean_cluster_confidence": round(float(np.mean(probabilities[indices])), 6),
            "evidence_root_ids": sorted(row["root_id"] for row in members),
            "acceptance_blockers": ["manual reusable-workflow review", "paired WorkBuddy A/B utility evidence"],
        })

    id_by_label = {}
    for candidate in candidates:
        member = candidate["evidence_root_ids"][0]
        label = int(labels[next(index for index, row in enumerate(rows) if row["root_id"] == member)])
        id_by_label[label] = candidate["family_id"]

    family_members: dict[str, list[dict]] = defaultdict(list)
    for index, row in enumerate(rows):
        label = int(labels[index])
        if label in accepted_labels:
            row["family_id"] = id_by_label[label]
            row["family_confidence"] = round(float(probabilities[index]), 6)
            family_members[row["family_id"]].append(row)
        else:
            row["family_id"] = None
            row["family_confidence"] = None
            row["chronological_split"] = "unassigned"

    ordered_families = sorted(family_members)
    holdout_count = math.floor(len(ordered_families) * 0.2)
    holdouts = set(sorted(ordered_families, key=lambda fid: hashlib.sha256(fid.encode()).hexdigest())[:holdout_count])
    for fid, members in family_members.items():
        split_members(members, fid in holdouts)

    write_jsonl(FROZEN, sorted(rows, key=lambda row: row["root_id"]))
    output = {
        "schema_version": 1,
        "algorithm": {
            "features": "problem_statement only; URLs, hashes, and versions normalized",
            "vectorizer": "word TF-IDF 1-2 grams",
            "projection": f"TruncatedSVD({dimensions}) with random_state=20260824",
            "clusterer": "HDBSCAN(min_cluster_size=10,min_samples=2,eom)",
        },
        "candidate_family_count": len(candidates),
        "clustered_root_count": sum(len(items) for items in family_members.values()),
        "unclustered_root_count": sum(row["family_id"] is None for row in rows),
        "zero_shot_family_count": len(holdouts),
        "zero_shot_family_ids": sorted(holdouts),
        "families": candidates,
    }
    FAMILIES.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["selected_sha256"] = hashlib.sha256(json.dumps(sorted(rows, key=lambda row: row["root_id"]), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    manifest["family_discovery"] = {
        "candidate_family_count": len(candidates),
        "clustered_root_count": output["clustered_root_count"],
        "unclustered_root_count": output["unclustered_root_count"],
        "zero_shot_family_count": len(holdouts),
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: output[key] for key in ["candidate_family_count", "clustered_root_count", "unclustered_root_count", "zero_shot_family_count"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
