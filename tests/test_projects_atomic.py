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
    s.add_message("user", "hello")
    pm.save_session(s)

    # Canonical file exists
    sfile = pm.projects_dir / "p-atomic" / "sessions" / f"{s.id}.json"
    assert sfile.exists()
    # No leftover tmp files
    leftovers = list((pm.projects_dir / "p-atomic" / "sessions").glob("*.tmp"))
    assert leftovers == [], f"tmp leftovers: {leftovers}"
    # File is valid JSON (the whole point of atomic write)
    data = json.loads(sfile.read_text(encoding="utf-8"))
    assert data["id"] == s.id


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
