#!/usr/bin/env python3
"""Read-only verification of the skill chain for a baseline run.

Answers one question: is the repo-session routing actually working, i.e. is
the session_header_adapter receiving traffic and is MemoryProxy accumulating
buffer under `swe-together__<owner>__<repo>` sessions (which is what triggers
archive + skill extraction at tool_call>=10 / bytes>=40KB)?

Exit codes:
  0 = skill chain confirmed (adapter traffic + swe-together__ session seen)
  1 = run exists but skill chain not yet confirmed
  2 = run dir missing / unreadable
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

RUN_DIR = Path(__file__).resolve().parent / "runs"

REPO_SESSIONS = [
    "swe-together__entireio__cli",
    "swe-together__peteromallet__dataclaw",
    "swe-together__nagi-ovo__gemini-voyager",
    "swe-together__togetherbench__reigh",
    "swe-together__obsessiondb__rudel",
]


def adapter_traffic(run_dir: Path) -> list[str]:
    lines = []
    log = run_dir / "session-adapter.stdout.log"
    if log.is_file():
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            if "[adapter]" in line:
                lines.append(line.strip())
    return lines


def proxy_session_keys(run_dir: Path) -> Counter:
    keys = Counter()
    for jsonl in (run_dir / "proxy-logs").glob("*.jsonl"):
        for line in jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.search(r'"sessionKey":"([^"]+)"', line)
            if m:
                keys[m.group(1)] += 1
    return keys


def buffer_snapshots(run_dir: Path) -> dict:
    out = {}
    root = run_dir / "core-data" / "skill_buffer"
    if not root.exists():
        return out
    for session_id in REPO_SESSIONS:
        dirs = list(root.glob(f"*/*/*/{session_id}"))
        if not dirs:
            continue
        d = dirs[0]
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8")) if (d / "meta.json").is_file() else {}
        archives = list(d.glob("data-*.jsonl"))
        out[session_id] = {
            "tool_call_count": int(meta.get("tool_call_count") or 0),
            "byte_count": int(meta.get("byte_count") or 0),
            "archive_count": len(archives),
        }
    return out


def skill_heads(run_dir: Path) -> list[dict]:
    db = run_dir / "core-data" / "vectors.db"
    if not db.is_file():
        return []
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        rows = conn.execute(
            "select skill_id, version, name from skills where is_head=1 order by name"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [{"skill_id": r[0], "version": r[1], "name": r[2]} for r in rows]


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_skill_chain.py <run_id>", file=sys.stderr)
        return 2
    run_dir = RUN_DIR / sys.argv[1]
    if not run_dir.is_dir():
        print(f"run dir missing: {run_dir}", file=sys.stderr)
        return 2

    traffic = adapter_traffic(run_dir)
    keys = proxy_session_keys(run_dir)
    swe_keys = {k: v for k, v in keys.items() if k.startswith("swe-together__")}
    random_keys = {k: v for k, v in keys.items() if not k.startswith("swe-together__")}
    buffers = buffer_snapshots(run_dir)
    heads = skill_heads(run_dir)

    print(f"=== run: {run_dir.name} ===")
    print(f"adapter traffic lines : {len(traffic)}")
    for line in traffic[-5:]:
        print(f"  {line}")
    print(f"proxy sessions        : {len(keys)} total, "
          f"{len(swe_keys)} swe-together__, {len(random_keys)} random-UUID")
    for k, v in sorted(swe_keys.items(), key=lambda x: -x[1]):
        print(f"  swe: {k} = {v} reqs")
    print(f"buffer (swe sessions) : {len(buffers)} non-empty")
    for sid, b in buffers.items():
        print(f"  {sid}: tool_calls={b['tool_call_count']} bytes={b['byte_count']} archives={b['archive_count']}")
    print(f"skill heads (vectors) : {len(heads)}")
    for h in heads:
        print(f"  {h['name']}  v{h['version']}")

    confirmed = bool(traffic) and bool(swe_keys)
    if confirmed:
        print("\nRESULT: skill chain CONFIRMED (adapter traffic + swe-together__ session)")
        return 0
    print("\nRESULT: not yet confirmed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
