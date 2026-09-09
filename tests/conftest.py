"""Shared fixtures for the pytest suite.

The fixtures here intentionally avoid touching the real
``~/.sweave`` directory. Each test gets a fresh temp directory via the
``tmp_project_manager`` fixture so atomic-write / locking tests can
exercise the real ``ProjectManager`` code path without polluting state.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

from sweave.projects import ProjectManager


@pytest.fixture
def tmp_home(monkeypatch) -> Iterator[Path]:
    """Redirect ``Path.home()`` to a temp dir for the test.

    Restores the original at teardown. ``ProjectManager`` defaults to
    ``Path.home() / ".sweave"``; this is how we keep tests off the real
    home directory.
    """
    original_home = Path.home
    tmp = Path(tempfile.mkdtemp(prefix="sweave-pytest-home-"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp))
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        monkeypatch.setattr(Path, "home", original_home)


@pytest.fixture
def tmp_project_manager(tmp_home) -> Iterator[ProjectManager]:
    """Fresh ProjectManager backed by a temp home, projects dir, config file.

    The default constructor already points at ``~/.sweave`` so the
    ``tmp_home`` fixture above covers it.
    """
    # The real module-level singleton is what we want to test; replace it
    # for the duration of the test.
    import sweave.projects as p

    original = p.project_manager
    pm = ProjectManager()
    try:
        yield pm
    finally:
        # Reset to a fresh PM pointing at the real home so other tests
        # aren't affected. The next test will re-create one.
        p.project_manager = original


@pytest.fixture(autouse=True)
def _isolate_project_manager_singleton(tmp_home, monkeypatch) -> Iterator[ProjectManager]:
    """Redirect the global ``project_manager`` singleton at a temp home.

    The HTTP stack (`sweave/api/projects.py`, `sweave/web/server.py`
    lifespan, ChatLoop/JobRunner wiring) operates on the import-time
    singleton from `sweave.projects` — `Path.home` patches alone
    cannot redirect an already-built object, so every `POST
    /api/projects` in the suite used to land `p-*` / `proj-*` junk in
    the REAL `~/.sweave/projects` (2026-09-09 incident).

    This swaps the singleton — including the two modules that
    early-bound it via `from ... import project_manager`
    (`sweave.api.projects`, `sweave.web.server`; all routers resolve
    it lazily at call time) — for a tmp-backed instance, per test.
    `tmp_home` runs first, so the fresh manager inherits the temp
    home; `monkeypatch` restores all three bindings at teardown.
    """
    import sweave.api.projects as api_projects
    import sweave.projects as projects_mod
    import sweave.web.server as server_mod

    fresh = ProjectManager()
    monkeypatch.setattr(projects_mod, "project_manager", fresh)
    monkeypatch.setattr(api_projects, "project_manager", fresh)
    monkeypatch.setattr(server_mod, "project_manager", fresh)
    try:
        yield fresh
    finally:
        shutil.rmtree(fresh.base_path, ignore_errors=True)
