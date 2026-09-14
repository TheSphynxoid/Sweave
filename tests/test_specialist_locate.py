"""locate() — location-aware specialist lookup (2026-09-14).

Regression: the live ``Sweave/.sweave/agents.json`` holds full
``scope="global"`` copies (pre-2026-09-11 shape). The list showed
"global", the UI sent ``?scope=global``, the router resolved with
``project_dir=None`` (skipping the project file), fell through to the
seed view, and PUT 400d "seed view ... cannot be edited here".
``locate()`` searches by FILE LOCATION (project -> global -> seed) so
reads and writes agree.
"""

from __future__ import annotations

from pathlib import Path

from sweave.runtime.specialist_store import (
    ProjectSpecialistStore,
    Specialist,
    SpecialistResolver,
)


def _rec(name: str, scope: str, harness: str = "opencode") -> Specialist:
    return Specialist(
        name=name,
        scope=scope,  # type: ignore[arg-type]
        is_orchestrator=False,
        role_ref=None,
        description=f"{name} desc",
        system_prompt=f"{name} prompt",
        harness=harness,
        current_model=None,
    )


def _resolver(tmp_path: Path) -> SpecialistResolver:
    r = SpecialistResolver()
    r.global_store = __import__(
        "sweave.runtime.specialist_store", fromlist=["GlobalSpecialistStore"]
    ).GlobalSpecialistStore(tmp_path / "g.json")
    r._seed_defs = {}
    return r


def test_locate_project_record(tmp_path: Path):
    r = _resolver(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    ProjectSpecialistStore(proj).upsert(_rec("alpha", "project"))
    rec, loc = r.locate("alpha", project_dir=proj)
    assert loc == "project" and rec is not None and rec.name == "alpha"


def test_locate_mis_scoped_project_record(tmp_path: Path):
    """The live shape: scope='global' LABEL inside the PROJECT file."""
    r = _resolver(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    ProjectSpecialistStore(proj).upsert(_rec("backend-specialist", "global"))
    rec, loc = r.locate("backend-specialist", project_dir=proj)
    assert loc == "project" and rec is not None
    # ...while the old scope-hinted lookup misses the project file and
    # falls to the seed view (or None) — the 400 shape.
    assert r.resolve("backend-specialist", project_dir=None) is None


def test_locate_global_record(tmp_path: Path):
    r = _resolver(tmp_path)
    r.global_store.upsert(_rec("beta", "global"))
    rec, loc = r.locate("beta", project_dir=tmp_path / "proj")
    assert loc == "global" and rec is not None and rec.name == "beta"


def test_locate_project_shadows_global(tmp_path: Path):
    r = _resolver(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    ProjectSpecialistStore(proj).upsert(_rec("dup", "project", harness="sweave-engine"))
    r.global_store.upsert(_rec("dup", "global", harness="opencode"))
    rec, loc = r.locate("dup", project_dir=proj)
    assert loc == "project" and rec is not None and rec.harness == "sweave-engine"


def test_locate_unknown_and_orchestrator(tmp_path: Path):
    r = _resolver(tmp_path)
    assert r.locate("nope", project_dir=tmp_path) == (None, None)
    assert r.locate("orchestrator", project_dir=tmp_path) == (None, None)


def test_update_round_trip_on_located_store(tmp_path: Path):
    """Write-back goes to the file the record came from."""
    r = _resolver(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    ProjectSpecialistStore(proj).upsert(_rec("gamma", "global", harness="opencode"))
    rec, loc = r.locate("gamma", project_dir=proj)
    assert loc == "project" and rec is not None
    rec.harness = "sweave-engine"
    r.update(rec, project_dir=proj if loc == "project" else None)
    # Project file holds the edit; the global file never learned the name.
    assert ProjectSpecialistStore(proj).get("gamma").harness == "sweave-engine"  # type: ignore[union-attr]
    assert r.global_store.get("gamma") is None
