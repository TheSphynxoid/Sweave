"""Atomic-write / corruption-safety tests for ProjectManager."""

from __future__ import annotations

import json
from pathlib import Path

from sweave.projects import ProjectManager


def test_save_session_is_atomic(tmp_project_manager: ProjectManager, tmp_path: Path):
    """save_session leaves no half-written file at the canonical path."""
    pm = tmp_project_manager
    project_path = tmp_path / "p-atomic"
    project_path.mkdir(parents=True, exist_ok=True)
    pm.create_project("p-atomic", project_path)
    s = pm.create_session("p-atomic", "S1")
    pm.append_message(s.id, "user", "hello")

    # Canonical meta file exists
    sfile = pm.projects_dir / "p-atomic" / "sessions" / f"{s.id}.json"
    assert sfile.exists()
    # Bodies live beside it, one row per line
    mfile = pm.projects_dir / "p-atomic" / "sessions" / f"{s.id}.messages.jsonl"
    assert mfile.exists()
    # No leftover tmp files
    leftovers = list((pm.projects_dir / "p-atomic" / "sessions").glob("*.tmp"))
    assert leftovers == [], f"tmp leftovers: {leftovers}"
    # Meta is valid JSON without bodies (lazy-load split)
    data = json.loads(sfile.read_text(encoding="utf-8"))
    assert data["id"] == s.id
    assert "messages" not in data
    assert data["message_count"] == 1


def test_load_skips_stray_tmp_files(tmp_project_manager: ProjectManager, tmp_path: Path):
    pm = tmp_project_manager
    project_path = tmp_path / "p-tmp"
    project_path.mkdir(parents=True, exist_ok=True)
    pm.create_project("p-tmp", project_path)
    sessions_dir = pm.projects_dir / "p-tmp" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    # Write a half-written tmp file mimicking an interrupted atomic write
    (sessions_dir / "garbage.json.tmp").write_text('{"id": "x", "brok', encoding="utf-8")

    # load() should not raise
    pm._projects.clear()
    pm._sessions.clear()
    pm.load()
    # The bad tmp file should not be loaded as a session
    assert all(not sid.startswith("garbage") for sid in pm._sessions)


def test_save_project_overwrites(tmp_project_manager: ProjectManager, tmp_path: Path):
    pm = tmp_project_manager
    project_path = tmp_path / "p-ow"
    project_path.mkdir(parents=True, exist_ok=True)
    p = pm.create_project("p-ow", project_path, description="v1")
    p.description = "v2"
    pm.save_project(p)
    pfile = pm.projects_dir / "p-ow" / "project.json"
    data = json.loads(pfile.read_text(encoding="utf-8"))
    assert data["description"] == "v2"


def test_load_skips_malformed_json(tmp_project_manager: ProjectManager, tmp_path: Path):
    pm = tmp_project_manager
    project_path = tmp_path / "p-bad"
    project_path.mkdir(parents=True, exist_ok=True)
    pm.create_project("p-bad", project_path)
    # Corrupt the project.json
    pfile = pm.projects_dir / "p-bad" / "project.json"
    pfile.write_text("not valid json", encoding="utf-8")
    pm._projects.clear()
    pm._sessions.clear()
    pm.load()
    assert "p-bad" not in pm._projects


def test_load_reads_utf8_session_content(tmp_project_manager: ProjectManager, tmp_path: Path):
    """Non-ASCII session content (emoji in a chat reply) round-trips.

    Writes are UTF-8 (atomic_write_json_sync); the read side must
    match. 2026-09-09: a live assistant reply containing an emoji
    crashed every lifespan load on cp1252-locale systems because the
    reads used the locale default encoding.
    """
    pm = tmp_project_manager
    project_path = tmp_path / "p-emoji"
    project_path.mkdir(parents=True, exist_ok=True)
    pm.create_project("p-emoji", project_path)
    s = pm.create_session("p-emoji", "S1")
    pm.append_message(s.id, "assistant", "I'm \U0001F916 the orchestrator, ready to help")

    pm._projects.clear()
    pm._sessions.clear()
    pm.load()  # must not raise
    assert s.id in pm._sessions
    # Boot is meta-only; bodies fault on open (lazy-load split).
    assert pm._sessions[s.id].messages == []
    assert pm.get_session(s.id).messages[-1].content == (
        "I'm \U0001F916 the orchestrator, ready to help"
    )
    assert "\U0001F916" in pm._sessions[s.id].messages[-1].content
