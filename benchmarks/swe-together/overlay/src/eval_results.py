"""Small, dependency-free helpers for interpreting Harbor result.json files."""
from __future__ import annotations

from typing import Any


def verifier_reward(result: object) -> float | None:
    """Return the persisted verifier reward, preserving zero as valid."""
    if not isinstance(result, dict):
        return None
    verifier = result.get("verifier_result")
    if not isinstance(verifier, dict):
        return None
    rewards: Any = verifier.get("rewards")
    value = rewards.get("reward") if isinstance(rewards, dict) else rewards
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def result_has_verifier_reward(result: object) -> bool:
    """Whether result.json represents a scored PASS/FAIL, not infra failure."""
    return verifier_reward(result) is not None
