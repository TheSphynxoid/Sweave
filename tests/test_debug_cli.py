"""Debug CLI helpers (``sweave.cli.debug``): read-only inspection.

Pins the three lookups the throwaway incident scripts kept
rebuilding: session dump, delegation + children search, trace
tool-row summary. Hermetic (tmp home + tmp stores + synthetic
trace files); the CLI commands themselves stay thin rendering.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sweave.cli import debug as dbg


def _home_with_session(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    sessions = home / "projects" / "Sweave" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "Sweave-1.json").write_text(
        json.dumps(
            {
                "id": "Sweave-1",
                "project_name": "Sweave",
                "status": "active",
                "created_at": "2026-09-18T00:00:00",
                "messages": [
                    {"id": "u1", "role": "user", "content": "hi",
                     "timestamp": "2026-09-18T00:00:01", "metadata": {}},
                    {"id": "a1", "role": "assistant", "content": "hello",
                     "timestamp": "2026-09-18T00:00:02", "agent": "orchestrator",
                     "metadata": {"delegation_id": "chat-1", "tools": [
                         {"callID": "c1", "tool": "read",
                          "summary": "notes.txt"},
                     ]}},
                ],
            }
        ),
        encoding="utf-8",
    )
    return home


def test_find_session(tmp_path: Path):
    home = _home_with_session(tmp_path)
    found = dbg.find_session(home, "Sweave-1")
    assert found is not None
    project_name, data = found
    assert project_name == "Sweave"
    assert len(dbg.session_messages(data)) == 2
    assert dbg.find_session(home, "nope") is None


def test_find_delegation_and_children(tmp_path: Path):
    from sweave.projects import ProjectManager
    from sweave.runtime.delegation_store import (
        Delegation,
        PerProjectDelegationStores,
    )

    home = tmp_path / "home"
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir(parents=True)
    pm = ProjectManager(base_path=home)
    pm.create_project("demo", path=proj_dir)

    async def _seed():
        stores = PerProjectDelegationStores()
        store = await stores.for_project(proj_dir)
        parent = Delegation(agent="backend", task="build",
                            project_name="demo", status="review")
        await store.add(parent)
        child = Delegation(agent="reviewer", task="review it",
                           project_name="demo",
                           parent_task_id=parent.delegation_id,
                           status="queued")
        await store.add(child)
        return parent, child

    parent, child = asyncio.run(_seed())
    record, children, record_dir = asyncio.run(
        dbg.find_delegation(home, parent.delegation_id)
    )
    assert record is not None and record.delegation_id == parent.delegation_id
    assert record_dir == proj_dir
    assert [c.delegation_id for c in children] == [child.delegation_id]
    missing, orphans, _ = asyncio.run(dbg.find_delegation(home, "nope"))
    assert missing is None and orphans == []


def test_summarize_trace_tool_rows(tmp_path: Path):
    home = tmp_path / "home"
    traces = home / "traces"
    traces.mkdir(parents=True)
    (traces / "d-1.jsonl").write_text(
        "\n".join([
            json.dumps({"event": "tool.started", "callID": "c1",
                        "tool": "bash",
                        "state": {"status": "pending",
                                    "input": {"command": "git log"}}}),
            json.dumps({"event": "tool.completed", "callID": "c1",
                        "tool": "bash",
                        "state": {"status": "completed",
                                    "input": {"command": "git log"},
                                    "output": "abc ok"}}),
            json.dumps({"event": "tool.started", "callID": "c2",
                        "tool": "read",
                        "state": {"status": "pending",
                                    "input": {"filePath": "f.txt"}}}),
        ]),
        encoding="utf-8",
    )
    counts, rows = dbg.summarize_trace(home, "d-1")
    assert counts["tool.started"] == 2
    assert counts["tool.completed"] == 1
    by_id = {r["callID"]: r for r in rows}
    assert by_id["c1"]["summary"] == "git log"
    assert by_id["c2"]["summary"] == "f.txt"
    assert dbg.summarize_trace(home, "missing") == ({}, [])


def test_tool_summary_shapes():
    assert dbg._tool_summary("bash", {"input": {"command": "make"}}) == "make"
    assert dbg._tool_summary("read", {"input": {"filePath": "a", "offset": 3,
                                                "limit": 10}}) == "a (offset=3 limit=10)"
    assert dbg._tool_summary("grep", {"input": {"pattern": "p"}}) == "p"
    assert dbg._tool_summary("todo", {}) == ""
    assert dbg._tool_summary("mystery", {"output": "line1\nline2"}) == "line1"


def test_all_visibility_commands_registered():
    """The ``if __name__`` guard sits at file end: ``python -m``
    executes top-to-bottom, so an earlier guard ran the app before
    the bottom commands (log/tail/watch/session/delegation)
    registered — silently missing from the CLI while imports
    (tests, console script) saw them all."""
    from sweave.cli.main import app

    names = set()
    for cmd in app.registered_commands:
        name = getattr(cmd, "name", None) or getattr(cmd.callback, "__name__", "")
        names.add(str(name).replace("_", "-"))
    for expected in ("log", "tail", "watch", "session", "delegation"):
        assert expected in names, f"CLI command {expected!r} not registered"
