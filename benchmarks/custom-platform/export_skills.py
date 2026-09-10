"""Export active Skill heads from a benchmark MemoryCore SQLite store."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3


def safe_name(value: str) -> str:
    name = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    if not name:
        raise ValueError(f"Skill name is not exportable: {value!r}")
    return name


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    database = args.database.resolve(strict=True)
    args.output.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("pragma table_info(skills)")}
        required = {"skill_id", "version", "is_head", "name", "description", "content", "content_hash"}
        if not required.issubset(columns):
            raise RuntimeError(f"Unsupported skills schema; missing {sorted(required - columns)}")
        rows = connection.execute(
            "select skill_id, version, name, description, content, content_hash "
            "from skills where is_head = 1 and status = 'active' order by name"
        ).fetchall()

    source_files = {}
    for suffix in ("", "-wal", "-shm"):
        source = Path(f"{database}{suffix}")
        if source.is_file():
            source_files[source.name] = {
                "bytes": source.stat().st_size,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
    manifest = {
        "schema_version": "benchmark_skill_export/1.0",
        "source_run": args.run_id,
        "source_database_files": source_files,
        "skill_count": len(rows),
        "skills": [],
    }
    for skill_id, version, name, description, content, stored_hash in rows:
        directory = args.output / safe_name(name)
        directory.mkdir(parents=True, exist_ok=True)
        normalized = str(content).replace("\r\n", "\n").replace("\r", "\n")
        if not normalized.endswith("\n"):
            normalized += "\n"
        destination = directory / "SKILL.md"
        destination.write_text(normalized, encoding="utf-8", newline="\n")
        manifest["skills"].append({
            "skill_id": skill_id,
            "version": version,
            "name": name,
            "description": description,
            "stored_content_hash": stored_hash,
            "exported_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            "path": f"{safe_name(name)}/SKILL.md",
        })

    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Exported {len(rows)} active Skill heads to {args.output}")


if __name__ == "__main__":
    main()
