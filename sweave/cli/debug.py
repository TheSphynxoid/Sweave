"""Read-only inspection helpers for live sessions + delegations.

Replaces the throwaway per-incident scripts (session dumps,
delegation hunts, trace tool-row listings): one code path, rich
output, hermetic tests. All functions are read-only — nothing here
writes stores, traces, or sessions.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def sweave_home() -> Path:
    """The live Sweave home dir (same anchor the server uses)."""
    return Path.home() / ".sweave"


def find_session(
    home: Path, session_id: str
) -> tuple[str, dict[str, Any]] | None:
    """Find a session JSON across projects. Returns
    ``(project_name, session_dict)`` or None."""
    projects_dir = home / "projects"
    if not projects_dir.is_dir():
        return None
    for proj_dir in sorted(projects_dir.iterdir()):
        if not proj_dir.is_dir():
            continue
        path = proj_dir / "sessions" / f"{session_id}.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return proj_dir.name, data
    return None


def session_messages(session: dict[str, Any]) -> list[dict[str, Any]]:
    """Message list of a session dict (never raises on bad shapes)."""
    messages = session.get("messages")
    return messages if isinstance(messages, list) else []


async def find_delegation(
    home: Path, delegation_id: str
) -> tuple[Any | None, list[Any], Path | None]:
    """Find a delegation record across per-project stores.

    Returns ``(record_or_None, children, record_project_dir_or_None)``.
    ``children`` are records whose ``parent_task_id`` is the id (empty
    when the record itself is missing). Uses the real store classes
    so the lookup matches what the server sees.
    """
    from sweave.projects import ProjectManager
    from sweave.runtime.delegation_store import PerProjectDelegationStores

    pm = ProjectManager(base_path=home)
    try:
        pm.load()
        projects = pm.list_projects()
    except Exception:  # noqa: BLE001 — degrade to home fallback only
        projects = []
    dirs: list[Path] = []
    for proj in projects:
        try:
            dirs.append(Path(proj.path))
        except Exception:  # noqa: BLE001
            continue
    dirs.append(home)

    stores = PerProjectDelegationStores()
    record: Any | None = None
    record_dir: Path | None = None
    children: list[Any] = []
    seen: set[str] = set()
    for project_dir in dirs:
        try:
            store = await stores.for_project(project_dir)
        except Exception:  # noqa: BLE001
            continue
        try:
            rec = store.get(delegation_id)
        except Exception:  # noqa: BLE001
            rec = None
        if rec is not None and record is None:
            record = rec
            record_dir = project_dir
        try:
            rows = store.list()
        except Exception:  # noqa: BLE001
            rows = []
        for row in rows:
            rid = getattr(row, "delegation_id", None)
            if (
                getattr(row, "parent_task_id", None) == delegation_id
                and rid not in seen
            ):
                seen.add(rid)
                children.append(row)
    return record, children, record_dir


def summarize_trace(
    home: Path, delegation_id: str, tool_limit: int = 20
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Trace overview: event counts + latest tool rows (newest last).

    Each tool row is ``{callID, tool, status, summary}`` where
    summary prefers the command (bash), path/window (read), pattern
    (grep) — the same fields the UI cards render, so "bash with no
    info" reproduces here when the trace itself is thin.
    """
    from sweave.runtime.trace_log import read_trace

    events = read_trace(delegation_id, base_dir=home / "traces")
    counts: Counter[str] = Counter()
    latest: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        name = ev.get("event")
        counts[str(name)] += 1
        if name in ("tool.started", "tool.updated", "tool.completed", "tool.failed"):
            call_id = str(ev.get("callID") or "")
            if not call_id:
                continue
            if call_id not in latest:
                order.append(call_id)
            latest[call_id] = ev
    rows: list[dict[str, Any]] = []
    for call_id in order[-tool_limit:]:
        ev = latest[call_id]
        state = ev.get("state") if isinstance(ev.get("state"), dict) else {}
        rows.append(
            {
                "callID": call_id,
                "tool": ev.get("tool"),
                "status": state.get("status"),
                "summary": _tool_summary(ev.get("tool"), state),
            }
        )
    return dict(counts), rows


def _tool_summary(tool: Any, state: dict[str, Any]) -> str:
    """One-line audit text for a tool state (mirrors the UI cards)."""
    name = str(tool or "")
    inp = state.get("input")
    if not isinstance(inp, dict):
        inp = {}
    if name == "bash":
        return str(inp.get("command") or state.get("command") or "")
    if name == "read":
        path = str(inp.get("filePath") or inp.get("path") or "")
        offset = inp.get("offset")
        limit = inp.get("limit")
        if offset is not None or limit is not None:
            return f"{path} (offset={offset} limit={limit})"
        return path
    if name in ("grep", "glob"):
        return str(inp.get("pattern") or "")
    if name == "git":
        verb = str(inp.get("verb") or "")
        args = inp.get("args")
        return f"git {verb} {args or ''}".strip()
    if name in ("edit", "write"):
        return str(inp.get("filePath") or inp.get("path") or "")
    if name == "todo":
        return ""
    out = state.get("output")
    if isinstance(out, str) and out.strip():
        first = out.strip().splitlines()[0]
        return first[:120]
    return ""
