"""Deterministic repo-level logical sessions for SWE-Together tasks.

This module only plans the Proxy/Memory view.  Harbor still creates one fresh
environment per task, so no repository working tree is shared between tasks.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote


_GITHUB_CLONE_RE = re.compile(
    r"\bgit\s+clone(?:\s+--[^\s]+(?:=\S+)?)?\s+"
    r"(?:https?://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?"
    r"(?=\s|\\|$)",
    re.IGNORECASE,
)
_GITHUB_REMOTE_RE = re.compile(
    r"\bgit\s+remote\s+add\s+origin\s+"
    r"(?:https?://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?"
    r"(?=\s|\\|$)",
    re.IGNORECASE,
)
# Thin-child Dockerfiles (e.g. reigh) use FROM ghcr.io/togetherbench/.../reigh-dev
# with no git clone. We recover the repo name from the base image.
_FROM_TOGETHERBENCH_RE = re.compile(
    r"^FROM\s+ghcr\.io/togetherbench/(?:[^/]+/)?(?P<repo>[A-Za-z0-9_.-]+?)-dev(?::|\s|$)",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class RepoSessionTask:
    task_id: str
    repo: str
    session_id: str
    task_order: int
    repo_order: int
    repo_task_order: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def repo_from_task_dir(task_dir: Path) -> str:
    """Read the official task Dockerfile and return canonical ``owner/repo``."""
    dockerfile = task_dir / "environment" / "Dockerfile"
    if not dockerfile.is_file():
        raise ValueError(f"task has no environment/Dockerfile: {task_dir.name}")
    text = dockerfile.read_text(encoding="utf-8", errors="strict")
    match = _GITHUB_CLONE_RE.search(text) or _GITHUB_REMOTE_RE.search(text)
    if match:
        return f"{match.group('owner').lower()}/{match.group('repo').lower()}"
    # Thin-child fallback: recover repo name from ghcr.io togetherbench base image.
    tb_match = _FROM_TOGETHERBENCH_RE.search(text)
    if tb_match:
        return f"togetherbench/{tb_match.group('repo').lower()}"
    raise ValueError(
        f"cannot recover GitHub repo from git clone in {dockerfile}"
    )


def session_id_for_repo(repo: str) -> str:
    """Create the stable, readable logical session ID required by the eval."""
    try:
        owner, name = repo.split("/", 1)
    except ValueError as exc:
        raise ValueError(f"repo must be owner/name, got {repo!r}") from exc

    def safe(part: str) -> str:
        value = re.sub(r"[^a-z0-9._-]+", "-", part.lower()).strip("-.")
        if not value:
            raise ValueError(f"repo contains an empty session component: {repo!r}")
        return value

    return f"swe-together__{safe(owner)}__{safe(name)}"


def plan_repo_sessions(
    task_names: list[str], tasks_root: Path
) -> list[RepoSessionTask]:
    """Group tasks by repo, preserving stable input order within each repo.

    Repository groups are ordered by their first appearance in ``task_names``.
    This makes an explicit ``--tasks`` list the reproducible source of truth and
    keeps the catalog's sorted order reproducible when no list is supplied.
    """
    grouped: dict[str, list[str]] = {}
    for task_id in task_names:
        repo = repo_from_task_dir(tasks_root / task_id)
        grouped.setdefault(repo, []).append(task_id)

    planned: list[RepoSessionTask] = []
    global_order = 0
    for repo_order, (repo, repo_tasks) in enumerate(grouped.items(), start=1):
        session_id = session_id_for_repo(repo)
        for repo_task_order, task_id in enumerate(repo_tasks, start=1):
            global_order += 1
            planned.append(
                RepoSessionTask(
                    task_id=task_id,
                    repo=repo,
                    session_id=session_id,
                    task_order=global_order,
                    repo_order=repo_order,
                    repo_task_order=repo_task_order,
                )
            )
    return planned


def proxy_url_for_session(base_url_template: str, session_id: str) -> str:
    """Resolve the private adapter URL without allowing path injection."""
    if "{session_id}" not in base_url_template:
        raise ValueError("proxy descriptor has no {session_id} URL template")
    return base_url_template.replace("{session_id}", quote(session_id, safe=""))
