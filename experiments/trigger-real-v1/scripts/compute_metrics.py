#!/usr/bin/env python3
"""Compute the required paired metrics from completed WorkBuddy A/B results."""

from __future__ import annotations

import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "replays" / "paired-results.jsonl"
OUTPUT = ROOT / "results" / "paired-metrics.json"


def wilson(successes: int, total: int, z: float = 1.96) -> list[float]:
    if total == 0:
        return [0.0, 0.0]
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return [max(0.0, centre - margin), min(1.0, centre + margin)]


def total_tokens(run: dict) -> int:
    return sum(run[key] for key in ["agent_input_tokens", "agent_output_tokens", "boundary_tokens", "review_tokens", "distillation_tokens"])


def summarize(rows: list[dict], arm: str) -> dict:
    runs = [row[arm] for row in rows]
    passed = sum(run["pass"] for run in runs)
    extracted = sum(run["skill_created"] or run["skill_updated"] for run in runs)
    hits = sum(run["skill_hit"] for run in runs)
    return {
        "pass_at_1": passed / len(runs),
        "pass_at_1_wilson_95": wilson(passed, len(runs)),
        "avg_total_tokens": sum(total_tokens(run) for run in runs) / len(runs),
        "avg_turns": sum(run["turns"] for run in runs) / len(runs),
        "skill_extraction_rate": extracted / len(runs),
        "skill_hit_rate": hits / len(runs),
    }


def main() -> int:
    if not INPUT.exists():
        raise SystemExit("No paired results: refusing to fabricate metrics")
    rows = [json.loads(line) for line in INPUT.read_text(encoding="utf-8").splitlines() if line]
    if len(rows) != 100:
        raise SystemExit(f"Expected exactly 100 paired results, found {len(rows)}")
    schema = json.loads((ROOT / "schemas" / "paired-replay.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    for row in rows:
        validator.validate(row)
    if len({row["root_id"] for row in rows}) != 100:
        raise SystemExit("Duplicate paired root_id")
    baseline = summarize(rows, "baseline")
    skill = summarize(rows, "skill")
    beneficial_hits = sum(row["skill"]["skill_hit"] and (row["skill"]["pass"] and not row["baseline"]["pass"] or row["skill"]["pass"] == row["baseline"]["pass"] and total_tokens(row["skill"]) < total_tokens(row["baseline"])) for row in rows)
    negative_transfer = sum(row["skill"]["skill_hit"] and row["baseline"]["pass"] and not row["skill"]["pass"] for row in rows)
    output = {
        "schema_version": 1,
        "pairs": len(rows),
        "baseline": baseline,
        "skill": skill,
        "paired_delta": {key: skill[key] - baseline[key] for key in ["pass_at_1", "avg_total_tokens", "avg_turns", "skill_extraction_rate", "skill_hit_rate"]},
        "beneficial_skill_hit_rate": beneficial_hits / len(rows),
        "negative_transfer_rate": negative_transfer / len(rows),
    }
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
