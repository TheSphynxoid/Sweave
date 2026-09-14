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


@pytest.fixture(autouse=True)
def _fast_system_send_bound(monkeypatch):
    """System-prompt first-byte bound shrunk for the whole suite.

    ``SpecialistRuntime._bounded_system_send`` (2026-09-13) waits for
    the first streamed byte before letting the send run on. A test
    double that never invokes ``on_chunk`` would otherwise stall the
    suite for the PRODUCTION bound (950s) before tripping —
    deterministic 16-minute per-test hangs. Every test that
    specifically exercises the bound patches it explicitly anyway
    (see tests/test_m2_1_followup_hardening.py). Tests that need the
    real 950s value patch it back inside the test.
    """
    import sweave.runtime.specialist_runtime as rt

    monkeypatch.setattr(rt, "PRE_MODEL_TIMEOUT_SECONDS", 1.0)


def repo_config_pair(tmp_path: Path, src: Path | None = None) -> Path:
    """Tmp copies of the repo config + registry (or a synthetic seed).

    Hygiene: no test loads the live repo CWD (config.yaml is a working
    artifact; models.yaml / models.meta.json are generated). Copies
    ``config.yaml`` / ``models.yaml`` / ``rules.yaml`` /
    ``models.custom.yaml`` / ``models.meta.json`` from the repo root
    when present, rewires the tmp config's registry/rules paths to
    the tmp copies, and returns the tmp config path.

    Fresh-clone resilience: ``models.yaml`` is untracked and may be
    absent — then a minimal synthetic registry (plus a matching
    ``models.default``) is planted so live-data assertions
    (non-empty qualified registry, default in registry) still hold.
    ``src`` overrides the repo root (tests the fallback itself).
    """
    import shutil

    import yaml

    root = src if src is not None else Path(__file__).parent.parent
    for name in (
        "config.yaml",
        "models.yaml",
        "rules.yaml",
        "models.custom.yaml",
        "models.meta.json",
    ):
        origin = root / name
        if origin.exists():
            shutil.copy(origin, tmp_path / name)
    if not (tmp_path / "models.yaml").exists():
        (tmp_path / "models.yaml").write_text(
            yaml.safe_dump(
                {
                    "models": {
                        "providers": {
                            "opencode": ["a-model"],
                            "ollama": ["b-model"],
                        }
                    }
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    if not (tmp_path / "config.yaml").exists():
        (tmp_path / "config.yaml").write_text(
            yaml.safe_dump({"models": {}}, sort_keys=False), encoding="utf-8"
        )
    cfg_doc = yaml.safe_load(
        (tmp_path / "config.yaml").read_text(encoding="utf-8")
    )
    if not isinstance(cfg_doc, dict):
        cfg_doc = {}
    models = cfg_doc.get("models")
    if not isinstance(models, dict):
        models = {}
        cfg_doc["models"] = models
    models["registry_path"] = str(tmp_path / "models.yaml")
    models["rules_path"] = str(tmp_path / "rules.yaml")
    if not models.get("default"):
        models["default"] = "opencode/a-model"
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(cfg_doc, sort_keys=False), encoding="utf-8"
    )
    return tmp_path / "config.yaml"
