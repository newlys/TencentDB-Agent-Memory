#!/usr/bin/env python3
"""Task-aware Ours_v3 controller for the SWE-Together session adapter.

The controller sees only user text already present in Claude's wire request.  It
uses the frozen L1.5 boundary, archives the previous predicted task, retrieves
and selects one SOP, then injects a compact materialized workflow into the
request system field.  Benchmark task ids and verifier data are never inputs.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BOUNDARY_RUNNER = PROJECT_ROOT / "experiments" / "query-boundary-l15-v1" / "live-query-boundary.ts"
PINNED_NODE = Path("C:/Users/cheng/.workbuddy/binaries/node/versions/22.22.2/node.exe")
METHOD_REVISION = "task-skill-consumption-controller-r5"
TOP_K = 3
CONTEXT_BUDGET = 3200

EXTRACTION_GUIDANCE = """This archive contains one predicted task.

Task query:
{task_query}

Extract only executable and reusable procedures demonstrated by this task.
A skill must describe a workflow that a future agent can apply to another task.
Include applicability, preconditions, constraints, ordered actions, decision
points, expected outputs, and validation or rollback steps. Repository
background and user preferences are not standalone skills. Prefer a mechanism-
or workflow-based lower-kebab-case name. Repository, framework, task, file, and
incident names are evidence only unless the workflow is genuinely product-
specific. Before creating a skill, inspect existing skills and UPDATE one whose
intended outcome, applicability, and core workflow substantially match. A
localized correction without a reusable procedure should produce Nothing to save."""

SELECTOR_SYSTEM = """Select whether one retrieved Skill contains a reusable
workflow for the current coding task. Compare intended outcome, applicability,
and core workflow at the mechanism level; repository/framework differences
alone are not mismatches. Select at most one candidate. Prefer selection when
the workflow is plausibly reusable; reject lexical overlap without workflow
overlap. Return JSON only: {\"decision\":\"view\",\"rank\":1,\"reason\":\"short\"}
or {\"decision\":\"none\",\"rank\":null,\"reason\":\"short\"}."""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _one_line(value: object, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def latest_user_text(body: dict[str, Any]) -> str | None:
    """Return the newest real user text, ignoring tool_result-only messages."""
    for message in reversed(body.get("messages") or []):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            texts = [
                str(block.get("text") or "").strip()
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
                and str(block.get("text") or "").strip()
            ]
            if texts:
                return "\n".join(texts)
    return None


class OursV3Controller:
    def __init__(self, config: dict[str, Any], client: ClientSession):
        ours = config["ours_v3"]
        self.client = client
        self.core_url = str(ours["core_url"]).rstrip("/")
        self.identity = dict(ours["identity"])
        self.state_path = Path(ours["state_path"])
        self.events_path = Path(ours["events_path"])
        self.api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is required for Ours_v3")
        if self.state_path.is_file():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        else:
            self.state: dict[str, Any] = {
                "schema_version": "swe-together-ours-v3/1.0",
                "variant": "ours_v3", "method_revision": METHOD_REVISION,
                "sessions": {}, "last_session_id": None, "updated_at": _now(),
            }
        self.locks: dict[str, asyncio.Lock] = {}
        _write_json(self.state_path, self.state)

    def _persist(self) -> None:
        self.state["updated_at"] = _now()
        _write_json(self.state_path, self.state)

    def _event(self, value: dict[str, Any]) -> None:
        value = {"timestamp": _now(), **value}
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")

    async def _boundary(self, recent: list[str], current: str) -> dict[str, Any]:
        if not recent:
            return {"decision": "new_task", "llm_called": False, "usage": {"total": 0}}
        process = await asyncio.create_subprocess_exec(
            str(PINNED_NODE), "--import", "tsx/esm", str(BOUNDARY_RUNNER),
            cwd=str(PROJECT_ROOT / "MemoryCore"),
            env=os.environ.copy(), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        payload = json.dumps({"recentQueries": recent[-6:], "currentQuery": current}).encode()
        stdout, stderr = await asyncio.wait_for(process.communicate(payload), timeout=180)
        if process.returncode:
            raise RuntimeError(f"L1.5 boundary failed: {stderr.decode(errors='replace')[-1000:]}")
        return json.loads(stdout)

    def _identity_body(self) -> dict[str, Any]:
        return {
            "user_id": self.identity["user_id"], "team_id": self.identity["team_id"],
            "agent_id": self.identity["agent_id"], "task_id": self.identity.get("task_id"),
        }

    async def _core(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"content-type": "application/json", "authorization": "Bearer local",
                   "x-tdai-service-id": "default"}
        async with self.client.post(f"{self.core_url}{path}", json=body, headers=headers) as response:
            value = await response.json(content_type=None)
            if response.status >= 400 or value.get("code") not in (0, 200):
                raise RuntimeError(f"Core {path} failed: HTTP {response.status}: {value}")
            return value.get("data") or {}

    async def _archive(self, session_id: str, anchor: str) -> dict[str, Any]:
        reason = EXTRACTION_GUIDANCE.format(task_query=anchor.strip())[:2000]
        started = time.monotonic()
        data = await self._core("/v3/skill/conversation/force-archive", {
            "space_id": "default", "user_id": self.identity["user_id"],
            "team_id": self.identity["team_id"], "agent_id": self.identity["agent_id"],
            "session_id": session_id,
            "reason": reason,
        })
        return {**data, "task_query_sha256": hashlib.sha256(anchor.encode()).hexdigest(),
                "latency_ms": round((time.monotonic() - started) * 1000, 3)}

    async def _search(self, query: str) -> dict[str, Any]:
        started = time.monotonic()
        data = await self._core("/v3/skill/search", {
            "user_id": self.identity["user_id"], "team_id": self.identity["team_id"],
            "agent_id": self.identity["agent_id"], "query": query.strip(),
            "top_k": TOP_K, "mode": "bm25",
        })
        candidates = [
            {key: item.get(key) for key in ("skill_id", "name", "description", "snippet", "score")}
            for item in (data.get("items") or [])[:TOP_K]
        ]
        return {"query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                "latency_ms": round((time.monotonic() - started) * 1000, 3),
                "candidates": candidates}

    async def _select(self, query: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        if not candidates:
            return {"decision": "none", "rank": None, "reason": "no_candidates",
                    "usage": {"input": 0, "output": 0, "total": 0}}
        public = [{"rank": index, "name": item.get("name"),
                   "description": _one_line(item.get("description"), 700),
                   "snippet": _one_line(item.get("snippet"), 900), "score": item.get("score")}
                  for index, item in enumerate(candidates, 1)]
        payload = {"model": "deepseek-v4-flash", "temperature": 0, "max_tokens": 256,
                   "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": SELECTOR_SYSTEM},
                                {"role": "user", "content": json.dumps({"current_task": query,
                                                                          "candidates": public},
                                                                         ensure_ascii=False)}]}
        started = time.monotonic()
        async with self.client.post("https://api.deepseek.com/chat/completions", json=payload,
                                    headers={"authorization": f"Bearer {self.api_key}"}) as response:
            body = await response.json(content_type=None)
            if response.status >= 400:
                return {"decision": "none", "rank": None, "reason": f"selector_http_{response.status}",
                        "metrics_complete": False}
        raw = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
        parsed = json.loads(raw)
        rank = parsed.get("rank")
        decision = str(parsed.get("decision") or "none").lower()
        if decision != "view" or not isinstance(rank, int) or not 1 <= rank <= len(candidates):
            decision, rank = "none", None
        usage = body.get("usage") or {}
        inp = int(usage.get("prompt_tokens") or 0); out = int(usage.get("completion_tokens") or 0)
        return {"decision": decision, "rank": rank, "reason": _one_line(parsed.get("reason"), 400),
                "latency_ms": round((time.monotonic() - started) * 1000, 3),
                "usage": {"input": inp, "output": out,
                          "total": int(usage.get("total_tokens") or inp + out)}}

    async def _materialize(self, candidate: dict[str, Any]) -> dict[str, Any]:
        data = await self._core("/v3/skill/get-by-name", {
            **self._identity_body(), "skill_name": candidate["name"],
            "include_content": True, "include_manifest": True,
        })
        content = str(data.get("content") or "")
        return {"skill_id": data.get("skill_id") or candidate.get("skill_id"),
                "skill_name": data.get("name") or candidate.get("name"),
                "skill_version": data.get("version"), "content": content,
                "content_chars": len(content),
                "content_sha256": hashlib.sha256(content.encode()).hexdigest()}

    @staticmethod
    def _compact(materialized: dict[str, Any]) -> str:
        content = materialized["content"]
        body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", content, count=1, flags=re.DOTALL)
        sections: dict[str, str] = {}
        matches = list(re.finditer(r"^##\s+(.+?)\s*$", body, flags=re.MULTILINE))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
            sections[match.group(1).strip().lower()] = body[match.end():end].strip()
        chosen = []
        for heading, limit in (("when to use", 600), ("workflow", 1350),
                               ("decision rules", 650), ("validation", 500)):
            if sections.get(heading):
                chosen.append(f"### {heading.title()}\n{_one_line(sections[heading], limit)}")
        compact = ("\n".join(chosen) if chosen else _one_line(body, 2400))[:CONTEXT_BUDGET - 550]
        return ("<task_skill_context>\nA task-scoped selector materialized this reusable workflow. "
                "Apply it to the current repository, inspect only the relevant code, make the smallest "
                "invariant-preserving change, and run targeted tests.\n"
                f"Skill: {_one_line(materialized['skill_name'], 180)}\n{compact}\n</task_skill_context>")

    @staticmethod
    def _inject_system(body: dict[str, Any], context: str) -> None:
        system = body.get("system")
        if isinstance(system, str):
            body["system"] = system + "\n\n" + context
        elif isinstance(system, list):
            body["system"] = [*system, {"type": "text", "text": context}]
        else:
            body["system"] = [{"type": "text", "text": context}]

    async def process(self, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        query = latest_user_text(body)
        if not query:
            return body
        lock = self.locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            previous_session_id = self.state.get("last_session_id")
            if previous_session_id and previous_session_id != session_id:
                previous = self.state["sessions"].get(previous_session_id) or {}
                if previous.get("active_queries"):
                    archive = await self._archive(previous_session_id,
                                                  previous["active_queries"][0])
                    previous.setdefault("archives", []).append({**archive, "repo_eof_flush": True})
                    previous["active_queries"] = []
                    previous["current_context"] = ""
                    self._event({"session_id": previous_session_id,
                                 "type": "repo_eof_archive", "archive": archive})
            self.state["last_session_id"] = session_id
            session = self.state["sessions"].setdefault(session_id, {
                "active_queries": [], "last_query_sha256": None, "predicted_tasks": 0,
                "current_context": "", "boundaries": [], "archives": [], "retrievals": [],
            })
            query_sha = hashlib.sha256(query.encode()).hexdigest()
            if query_sha != session.get("last_query_sha256"):
                boundary = await self._boundary(session["active_queries"], query)
                boundary.update({"query_sha256": query_sha, "observed_at": _now()})
                session["boundaries"].append(boundary)
                if boundary["decision"] == "new_task":
                    if session["active_queries"]:
                        archive = await self._archive(session_id, session["active_queries"][0])
                        session["archives"].append(archive)
                    session["predicted_tasks"] += 1
                    session["active_queries"] = [query]
                    search = await self._search(query)
                    selector = await self._select(query, search["candidates"])
                    retrieval: dict[str, Any] = {"predicted_task": session["predicted_tasks"],
                                                  "search": search, "selector": selector,
                                                  "status": "REJECTED"}
                    if selector.get("decision") == "view":
                        selected = search["candidates"][selector["rank"] - 1]
                        materialized = await self._materialize(selected)
                        context = self._compact(materialized)
                        retrieval.update(status="MATERIALIZED_BEFORE_AGENT",
                                         selected_rank=selector["rank"],
                                         materialization={k: v for k, v in materialized.items()
                                                          if k != "content"},
                                         injected_chars=len(context),
                                         injected_sha256=hashlib.sha256(context.encode()).hexdigest())
                        session["current_context"] = context
                    else:
                        session["current_context"] = ""
                    session["retrievals"].append(retrieval)
                    self._event({"session_id": session_id, "type": "new_task", **retrieval})
                else:
                    session["active_queries"].append(query)
                    self._event({"session_id": session_id, "type": "same_task",
                                 "query_sha256": query_sha, "boundary": boundary})
                session["last_query_sha256"] = query_sha
                self._persist()
            if session.get("current_context"):
                self._inject_system(body, session["current_context"])
            return body

    async def flush(self) -> dict[str, Any]:
        flushed = []
        for session_id, session in self.state["sessions"].items():
            if session.get("active_queries"):
                archive = await self._archive(session_id, session["active_queries"][0])
                session["archives"].append({**archive, "eof_flush": True})
                session["active_queries"] = []
                session["current_context"] = ""
                flushed.append(session_id)
        self._persist()
        return {"flushed_sessions": flushed, "count": len(flushed)}
