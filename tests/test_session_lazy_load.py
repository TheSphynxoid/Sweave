"""Lazy-load session storage split (meta JSON + messages.jsonl).

Contract:
- boot (load) faults zero message bytes; shells carry message_count.
- get_session faults exactly one transcript; list/active paths never do.
- append_message is O(1): one jsonl line + meta bump, no history rewrite.
- save_messages rewrites the log (history edits only).
- v1 files (embedded "messages") migrate once on load.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sweave.projects import BODY_CACHE_CAP, ProjectManager


def _make_project(pm: ProjectManager, tmp_path: Path, name: str = "p-lazy") -> Path:
    project_path = tmp_path / name
    project_path.mkdir(parents=True, exist_ok=True)
    pm.create_project(name, project_path)
    return project_path


def test_boot_faults_no_bodies(tmp_project_manager: ProjectManager, tmp_path: Path):
    pm = tmp_project_manager
    _make_project(pm, tmp_path)
    s = pm.create_session("p-lazy", "S1")
    for i in range(3):
        pm.append_message(s.id, "user", f"hello {i}")

    pm._projects.clear()
    pm._sessions.clear()
    pm.load()

    shell = pm._sessions[s.id]
    assert shell.messages == []
    assert shell.message_count == 3
    assert s.id not in pm._bodies


def test_get_session_faults_one_transcript(
    tmp_project_manager: ProjectManager, tmp_path: Path
):
    pm = tmp_project_manager
    _make_project(pm, tmp_path)
    s1 = pm.create_session("p-lazy", "S1")
    s2 = pm.create_session("p-lazy", "S2")
    pm.append_message(s1.id, "user", "one")
    pm.append_message(s2.id, "user", "two")

    pm._projects.clear()
    pm._sessions.clear()
    pm.load()

    opened = pm.get_session(s1.id)
    assert [m.content for m in opened.messages] == ["one"]
    # The other session stays a shell.
    assert pm._sessions[s2.id].messages == []
    assert s2.id not in pm._bodies


def test_append_is_o1_not_a_rewrite(
    tmp_project_manager: ProjectManager, tmp_path: Path
):
    pm = tmp_project_manager
    _make_project(pm, tmp_path)
    s = pm.create_session("p-lazy", "S1")
    for i in range(50):
        pm.append_message(s.id, "user", "x" * 200)

    mfile = pm.projects_dir / "p-lazy" / "sessions" / f"{s.id}.messages.jsonl"
    before = mfile.stat().st_size
    pm.append_message(s.id, "user", "y" * 200)
    grew = mfile.stat().st_size - before
    # One ~200-char line, not a 50-row rewrite (~10KB+).
    assert grew < 1024, f"append rewrote history: +{grew} bytes"

    meta = json.loads(
        (pm.projects_dir / "p-lazy" / "sessions" / f"{s.id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert meta["message_count"] == 51
    assert "messages" not in meta


def test_save_messages_rewrites_history_edits(
    tmp_project_manager: ProjectManager, tmp_path: Path
):
    pm = tmp_project_manager
    _make_project(pm, tmp_path)
    s = pm.create_session("p-lazy", "S1")
    pm.append_message(s.id, "user", "original")

    opened = pm.get_session(s.id)
    opened.messages[0].metadata["superseded"] = True
    pm.save_messages(opened)

    pm._bodies.pop(s.id, None)
    opened.messages = []
    reloaded = pm.get_session(s.id)
    assert reloaded.messages[0].metadata["superseded"] is True
    assert reloaded.message_count == 1


def test_v1_file_migrates_once_on_load(
    tmp_project_manager: ProjectManager, tmp_path: Path
):
    pm = tmp_project_manager
    project_path = _make_project(pm, tmp_path, name="p-v1")
    sessions_dir = pm.projects_dir / "p-v1" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    legacy = {
        "id": "legacy-1",
        "project_name": "p-v1",
        "name": "Old",
        "created_at": "2026-09-01T00:00:00",
        "updated_at": "2026-09-01T00:00:00",
        "status": "active",
        "current_agent": None,
        "context": {},
        "messages": [
            {
                "id": "m1",
                "role": "user",
                "content": "hi",
                "timestamp": "2026-09-01T00:00:01",
                "agent": None,
                "tool_name": None,
                "tool_args": None,
                "tool_result": None,
                "metadata": {},
            }
        ],
        "children": [],
        "memory_bank": "session-legacy-1",
    }
    (sessions_dir / "legacy-1.json").write_text(
        json.dumps(legacy), encoding="utf-8"
    )

    pm._projects.clear()
    pm._sessions.clear()
    pm.load()

    shell = pm._sessions["legacy-1"]
    assert shell.message_count == 1
    assert shell.messages == []
    mfile = sessions_dir / "legacy-1.messages.jsonl"
    assert mfile.exists()
    meta = json.loads((sessions_dir / "legacy-1.json").read_text(encoding="utf-8"))
    assert "messages" not in meta
    assert meta["schema_version"] == 2
    assert pm.get_session("legacy-1").messages[0].content == "hi"


def test_eviction_drops_idle_bodies_keeps_count(
    tmp_project_manager: ProjectManager, tmp_path: Path, monkeypatch
):
    import sweave.projects as projects_mod

    monkeypatch.setattr(projects_mod, "BODY_CACHE_CAP", 2)
    pm = tmp_project_manager
    _make_project(pm, tmp_path)
    ids = []
    for name in ("A", "B", "C"):
        s = pm.create_session("p-lazy", name)
        pm.append_message(s.id, "user", f"msg-{name}")
        ids.append(s.id)

    pm._projects.clear()
    pm._sessions.clear()
    pm.load()

    pm.get_session(ids[0])
    pm.get_session(ids[1])
    pm.get_session(ids[2])
    assert len(pm._bodies) <= 2
    # Evicted shell keeps its count and re-faults on demand.
    assert pm._sessions[ids[0]].message_count == 1
    assert pm.get_session(ids[0]).messages[0].content == "msg-A"


def test_delete_removes_both_files(
    tmp_project_manager: ProjectManager, tmp_path: Path
):
    pm = tmp_project_manager
    _make_project(pm, tmp_path)
    s = pm.create_session("p-lazy", "S1")
    pm.append_message(s.id, "user", "bye")

    pm.delete_session(s.id)
    sessions_dir = pm.projects_dir / "p-lazy" / "sessions"
    assert not (sessions_dir / f"{s.id}.json").exists()
    assert not (sessions_dir / f"{s.id}.messages.jsonl").exists()
    assert s.id not in pm._bodies


def test_body_cache_cap_is_sane():
    assert BODY_CACHE_CAP >= 10
