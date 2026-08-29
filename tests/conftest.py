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
