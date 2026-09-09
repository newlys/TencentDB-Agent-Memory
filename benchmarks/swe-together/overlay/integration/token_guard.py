#!/usr/bin/env python3
"""Guard the running baseline: enforce a 20M input-token budget.

Scans the run's proxy usage logs every 60s, accumulates input_tokens, and
force-stops the runner when the budget is exceeded. Also reports cache-hit
ratio and writes a live `guard.json` so progress is observable.
"""
from __future__ import annotations

import glob
import json
import subprocess
import sys
import time
from pathlib import Path

RUNS_ROOT = Path(__file__).resolve().parent / "runs"
BUDGET = 20_000_000  # 20M uncached input tokens
INTERVAL = 10


def usage_stats(run_dir: Path) -> dict:
    total_in = 0
    total_cr = 0
    hit = 0
    tot = 0
    provider_402_errors = 0
    for f in glob.glob(str(run_dir / "proxy-logs" / "*.jsonl")):
        with open(f, encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("event") != "usage":
                    continue
                u = e.get("usage") or {}
                if "error" in u:
                    if int(u.get("status") or 0) == 402:
                        provider_402_errors += 1
                    continue
                tin = u.get("input_tokens") or 0
                cr = u.get("cache_read_input_tokens") or 0
                if tin <= 0:
                    continue
                total_in += tin
                total_cr += cr
                tot += 1
                if cr > 0:
                    hit += 1
    return {
        "total_input_tokens": total_in,
        "total_cache_read_tokens": total_cr,
        "requests": tot,
        "cache_hit_requests": hit,
        "cache_hit_ratio": (hit / tot) if tot else 0.0,
        "cache_hit_token_ratio": (
            total_cr / (total_in + total_cr) if total_in + total_cr else 0.0
        ),
        "provider_402_errors": provider_402_errors,
    }


def runner_pid(run_dir: Path) -> int | None:
    status = run_dir / "status.json"
    if not status.is_file():
        return None
    d = json.loads(status.read_text(encoding="utf-8"))
    return d.get("runner", {}).get("pid")


def write_guard(run_dir: Path, state: dict) -> None:
    (run_dir / "guard.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: token_guard.py <run_id>", file=sys.stderr)
        return 2
    run_dir = RUNS_ROOT / sys.argv[1]
    if not run_dir.is_dir():
        print(f"run dir missing: {run_dir}", file=sys.stderr)
        return 2

    # Capture the cumulative token count AT STARTUP as the baseline. The run's
    # proxy-logs accumulate across resumes, so a naive "total > budget" check
    # would re-trigger on tokens burned by an EARLIER (already-failed) resume —
    # e.g. the 402 incident that pushed 45.9M tokens. We only guard the DELTA
    # produced after this guard starts.
    baseline_in = usage_stats(run_dir)["total_input_tokens"]
    baseline_402 = usage_stats(run_dir)["provider_402_errors"]
    print(
        f"[guard] budget={BUDGET:,} run={run_dir.name} "
        f"baseline_in={baseline_in:,}",
        flush=True,
    )
    while True:
        stats = usage_stats(run_dir)
        pid = runner_pid(run_dir)
        delta = stats["total_input_tokens"] - baseline_in
        exceeded = delta > BUDGET
        new_402 = stats["provider_402_errors"] > baseline_402
        state = {
            "budget": BUDGET,
            "baseline_input_tokens": baseline_in,
            "delta_input_tokens": delta,
            "exceeded": exceeded,
            "new_provider_402": new_402,
            "runner_pid": pid,
            **stats,
        }
        write_guard(run_dir, state)
        print(
            f"[guard] delta={delta:,}/{BUDGET:,} "
            f"(total={stats['total_input_tokens']:,}) "
            f"hit={stats['cache_hit_ratio']*100:.0f}% "
            f"({stats['cache_hit_requests']}/{stats['requests']}) "
            f"pid={pid}",
            flush=True,
        )
        if exceeded or new_402:
            reason = "PROVIDER 402" if new_402 else "BUDGET EXCEEDED"
            print(f"[guard] {reason} — stopping runner pid={pid}", flush=True)
            if pid:
                subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                               capture_output=True)
            return 1
        if pid is None and stats["requests"] > 0:
            # runner already gone (finished or crashed) — stop guarding
            print("[guard] runner pid missing, run ended — exiting", flush=True)
            return 0
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
