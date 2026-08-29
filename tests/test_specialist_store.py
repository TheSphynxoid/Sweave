"""Tests for sweave.runtime.specialist_store (M1.2 step 1).

Covers:
* Specialist dataclass (defaults, validation, round-trip, field-filter).
* GlobalSpecialistStore (anchored path, atomic write, load roundtrip).
* ProjectSpecialistStore (per-project, creates .sweave subdir).
* SpecialistResolver (resolution order, shadowing, orchestrator
  singleton auto-seed, orchestrator 409s, seed read-only view,
  list_resolved shape and dedup).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sweave.agents.loader import AgentDefinition
from sweave.runtime.specialist_store import (
    ORCHESTRATOR_NAME,
    GlobalSpecialistStore,
    ProjectSpecialistStore,
    Specialist,
    SpecialistResolver,
)


# --- Specialist dataclass --------------------------------------------------


def test_specialist_default_status():
    s = Specialist(name="backend")
    assert s.name == "backend"
    assert s.scope == "project"
    assert s.is_orchestrator is False
    assert s.role_ref is None
    assert s.description == ""
    assert s.system_prompt == ""
    assert s.harness == "opencode"
    assert s.current_model is None
    assert s.session_id is None


def test_specialist_orchestrator_flag():
    s = Specialist(name="orchestrator", is_orchestrator=True, scope="project")
    assert s.is_orchestrator is True


def test_specialist_validates_name_in_constructor():
    """__post_init__ runs the regex on any non-empty name.

    The empty-string case is tested separately: the validator rejects
    "" if called explicitly, but the dataclass tolerates the default
    (no-name) construction. The validator's job is to be a guard
    for the *public* API; the dataclass is a passive data holder.
    """
    # VALID_NAME: lowercase + digits + _-, 1-63 chars, must start with letter/digit
    with pytest.raises(ValueError):
        Specialist(name="Backend")  # uppercase
    with pytest.raises(ValueError):
        Specialist(name="-leading-dash")  # must start with letter/digit
    with pytest.raises(ValueError):
        Specialist(name="with space")  # spaces
    with pytest.raises(ValueError):
        Specialist(name="a" * 64)  # too long
    # Default-construction (no name) is fine
    Specialist()
    # An explicit name that *happens* to be empty is currently allowed
    # at the dataclass level (the validator runs on non-empty); the
    # public API (create/update) calls _validate_name directly so an
    # empty string there raises.
    Specialist(name="")


def test_validate_name_rejects_empty_string():
    """The validator itself rejects '' (called by the public API)."""
    from sweave.runtime.specialist_store import _validate_name

    with pytest.raises(ValueError):
        _validate_name("")


def test_specialist_round_trip():
    s = Specialist(
        name="backend",
        scope="global",
        is_orchestrator=False,
        role_ref="backend",
        description="Backend work",
        system_prompt="you are a backend specialist",
        harness="opencode",
        current_model="gpt-4",
    )
    d = s.to_dict()
    # Re-parse: to_dict serialises datetimes as strings
    s2 = Specialist.from_dict(d)
    assert s2.name == s.name
    assert s2.scope == s.scope
    assert s2.is_orchestrator == s.is_orchestrator
    assert s2.role_ref == s.role_ref
    assert s2.description == s.description
    assert s2.system_prompt == s.system_prompt
    assert s2.harness == s.harness
    assert s2.current_model == s.current_model


def test_specialist_from_dict_ignores_unknown_fields():
    s = Specialist.from_dict(
        {
            "name": "x",
            "role_ref": "backend",
            "future_field": "ignored",
            "another_v3": 42,
        }
    )
    assert s.name == "x"
    assert s.role_ref == "backend"


def test_specialist_public_dict_omits_internal_fields():
    s = Specialist(name="x", description="d", current_model="m")
    pub = s.public_dict()
    assert pub["name"] == "x"
    assert pub["description"] == "d"
    assert pub["current_model"] == "m"
    # Internal fields stripped
    assert "schema_version" not in pub
    assert "session_id" not in pub
    assert "created_at" not in pub
    assert "updated_at" not in pub


# --- GlobalSpecialistStore -------------------------------------------------


def test_global_store_default_path_is_home_anchored(tmp_path: Path):
    """The anchored path must NOT depend on CWD."""
    import os

    orig_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)  # CWD = tmp_path (NOT the home-anchored location)
        store = GlobalSpecialistStore()
        expected = Path.home() / ".sweave" / "agents.yaml"
        assert store.file_path == expected
    finally:
        os.chdir(orig_cwd)


def test_global_store_persists_and_reloads(tmp_path: Path):
    file_path = tmp_path / "agents.yaml"
    store1 = GlobalSpecialistStore(file_path)
    rec = Specialist(
        name="alpha", scope="global", role_ref="backend", current_model="m1",
    )
    store1.upsert(rec)
    assert file_path.exists()
    # Fresh store reads from the same file
    store2 = GlobalSpecialistStore(file_path)
    loaded = store2.get("alpha")
    assert loaded is not None
    assert loaded.role_ref == "backend"
    assert loaded.current_model == "m1"


def test_global_store_corrupt_file_loads_as_empty(tmp_path: Path):
    bad = tmp_path / "agents.yaml"
    bad.write_text("not valid json {", encoding="utf-8")
    store = GlobalSpecialistStore(bad)
    # Corrupt file -> empty store (warning logged, no exception)
    assert store.list() == []


def test_global_store_delete_returns_true_on_hit_false_on_miss(tmp_path: Path):
    store = GlobalSpecialistStore(tmp_path / "agents.yaml")
    store.upsert(Specialist(name="x"))
    assert store.delete("x") is True
    assert store.delete("x") is False  # already gone


# --- ProjectSpecialistStore ------------------------------------------------


def test_project_store_creates_sweave_subdir(tmp_path: Path):
    project = tmp_path / "myproject"
    project.mkdir()
    sweave_dir = project / ".sweave"
    assert not sweave_dir.exists()
    ProjectSpecialistStore(project)
    assert sweave_dir.is_dir()


def test_project_store_persists_per_project(tmp_path: Path):
    p1 = tmp_path / "p1"
    p2 = tmp_path / "p2"
    p1.mkdir()
    p2.mkdir()
    s1 = ProjectSpecialistStore(p1)
    s2 = ProjectSpecialistStore(p2)
    s1.upsert(Specialist(name="only-p1"))
    s2.upsert(Specialist(name="only-p2"))
    assert (p1 / ".sweave" / "agents.json").exists()
    assert (p2 / ".sweave" / "agents.json").exists()
    # Cross-project isolation
    assert s1.get("only-p2") is None
    assert s2.get("only-p1") is None


# --- SpecialistResolver ----------------------------------------------------


def _resolver_with_global(global_path: Path) -> SpecialistResolver:
    """Build a resolver with a private global store (no CWD dependency)."""
    r = SpecialistResolver()
    r.global_store = GlobalSpecialistStore(global_path)
    r._seed_defs = {}  # start without seeds for clean resolution tests
    return r


def test_resolution_project_wins_over_global(tmp_path: Path):
    p = tmp_path / "p"
    p.mkdir()
    r = _resolver_with_global(tmp_path / "g.yaml")
    r.global_store.upsert(Specialist(name="alpha", scope="global", current_model="g-model"))
    r._project_store(p).upsert(Specialist(name="alpha", scope="project", current_model="p-model"))
    rec = r.resolve("alpha", project_dir=p)
    assert rec is not None
    assert rec.current_model == "p-model"  # project shadowed global


def test_resolution_global_wins_over_seed(tmp_path: Path):
    from sweave.agents.loader import AgentDefinition

    r = SpecialistResolver()
    r.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
    r.global_store.upsert(Specialist(name="alpha", scope="global", current_model="g-model"))
    r._seed_defs = {
        "alpha": AgentDefinition(
            role="alpha", name="alpha", description="seed alpha",
            prompt="seed prompt", harness="opencode",
        ),
    }
    rec = r.resolve("alpha")
    assert rec is not None
    assert rec.current_model == "g-model"  # global shadowed seed
    assert rec.scope == "global"


def test_resolution_falls_through_to_seed(tmp_path: Path):
    from sweave.agents.loader import AgentDefinition

    r = SpecialistResolver()
    r.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
    r._seed_defs = {
        "alpha": AgentDefinition(
            role="alpha", name="alpha", description="seed alpha",
            prompt="seed prompt", harness="opencode",
        ),
    }
    rec = r.resolve("alpha")
    assert rec is not None
    assert rec.scope == "seed"
    assert rec.system_prompt == "seed prompt"
    assert rec.role_ref == "alpha"  # seed.role -> Specialist.role_ref (amendment A)


def test_resolution_unknown_returns_none(tmp_path: Path):
    r = _resolver_with_global(tmp_path / "g.yaml")
    r._seed_defs = {}
    assert r.resolve("nope") is None


def test_resolve_orchestrator_auto_seeds_on_first_call(tmp_path: Path):
    p = tmp_path / "p"
    p.mkdir()
    r = SpecialistResolver()
    r.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
    r._seed_defs = {
        ORCHESTRATOR_NAME: AgentDefinition(
            role=ORCHESTRATOR_NAME,
            name=ORCHESTRATOR_NAME,
            description="orchestrator seed",
            prompt="you are the orchestrator",
            harness="opencode",
        ),
    }
    rec = r.resolve_orchestrator(project_dir=p)
    assert rec is not None
    assert rec.is_orchestrator is True
    assert rec.name == ORCHESTRATOR_NAME
    assert rec.role_ref == ORCHESTRATOR_NAME
    # Second call returns the persisted record (not a fresh auto-seed)
    rec2 = r.resolve_orchestrator(project_dir=p)
    assert rec2 is not None
    # Same identity (same updated_at would differ; same created_at is the
    # proof we hit the store, not the auto-seed path)
    assert rec2.created_at == rec.created_at


def test_resolve_orchestrator_returns_existing_record_without_reseeding(tmp_path: Path):
    p = tmp_path / "p"
    p.mkdir()
    r = SpecialistResolver()
    r.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
    # Pre-seed manually
    r._project_store(p).upsert(Specialist(name=ORCHESTRATOR_NAME, is_orchestrator=True))
    rec = r.resolve_orchestrator(project_dir=p)
    assert rec is not None
    assert rec.description == ""  # not the seed's description


def test_resolve_orchestrator_auto_seed_disabled_returns_none_when_missing(tmp_path: Path):
    p = tmp_path / "p"
    p.mkdir()
    r = SpecialistResolver()
    r.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
    r._seed_defs = {}  # no orchestrator seed
    rec = r.resolve_orchestrator(project_dir=p, auto_seed=False)
    assert rec is None


def test_resolve_orchestrator_missing_seed_logs_warning(tmp_path: Path, caplog):
    p = tmp_path / "p"
    p.mkdir()
    r = SpecialistResolver()
    r.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
    r._seed_defs = {}
    with caplog.at_level("WARNING"):
        rec = r.resolve_orchestrator(project_dir=p, auto_seed=True)
    assert rec is None
    assert "Orchestrator seed missing" in caplog.text


def test_resolve_orchestrator_name_never_in_resolve(tmp_path: Path):
    """The orchestrator name is excluded from the general resolve() path."""
    p = tmp_path / "p"
    p.mkdir()
    r = SpecialistResolver()
    r.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
    r._project_store(p).upsert(
        Specialist(name=ORCHESTRATOR_NAME, is_orchestrator=True, scope="project")
    )
    # Even with a real orchestrator record, resolve("orchestrator", ...) returns None
    assert r.resolve(ORCHESTRATOR_NAME, project_dir=p) is None
    # resolve_orchestrator is the only path
    assert r.resolve_orchestrator(project_dir=p) is not None


def test_create_refuses_is_orchestrator_flag(tmp_path: Path):
    p = tmp_path / "p"
    p.mkdir()
    r = SpecialistResolver()
    r.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
    rec = Specialist(name="oops", is_orchestrator=True, scope="project")
    with pytest.raises(ValueError, match="is_orchestrator"):
        r.create(rec, project_dir=p)


def test_create_refuses_reserved_orchestrator_name(tmp_path: Path):
    p = tmp_path / "p"
    p.mkdir()
    r = SpecialistResolver()
    r.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
    rec = Specialist(name=ORCHESTRATOR_NAME, is_orchestrator=False, scope="project")
    with pytest.raises(ValueError, match="reserved"):
        r.create(rec, project_dir=p)


def test_create_refuses_collision_in_scope(tmp_path: Path):
    p = tmp_path / "p"
    p.mkdir()
    r = SpecialistResolver()
    r.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
    r._project_store(p).upsert(Specialist(name="alpha"))
    with pytest.raises(ValueError, match="already exists"):
        r.create(Specialist(name="alpha", scope="project"), project_dir=p)


def test_create_global_requires_no_project_dir(tmp_path: Path):
    r = SpecialistResolver()
    r.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
    r.create(Specialist(name="alpha", scope="global"))  # no project_dir
    assert r.global_store.get("alpha") is not None


def test_create_project_scope_without_project_dir_raises(tmp_path: Path):
    r = SpecialistResolver()
    r.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
    with pytest.raises(ValueError, match="project_dir"):
        r.create(Specialist(name="alpha", scope="project"))


def test_delete_refuses_orchestrator_name(tmp_path: Path):
    p = tmp_path / "p"
    p.mkdir()
    r = SpecialistResolver()
    r.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
    r._project_store(p).upsert(
        Specialist(name=ORCHESTRATOR_NAME, is_orchestrator=True, scope="project")
    )
    with pytest.raises(ValueError, match="reserved"):
        r.delete(ORCHESTRATOR_NAME, project_dir=p)
    # The orchestrator record is still there
    assert r.resolve_orchestrator(project_dir=p) is not None


def test_list_resolved_includes_seeds_when_requested(tmp_path: Path):
    from sweave.agents.loader import AgentDefinition

    p = tmp_path / "p"
    p.mkdir()
    r = SpecialistResolver()
    r.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
    r._seed_defs = {
        "alpha": AgentDefinition(
            role="alpha", name="alpha", description="seed alpha",
            prompt="seed", harness="opencode",
        ),
        "orchestrator": AgentDefinition(
            role="orchestrator", name="orchestrator", description="orch seed",
            prompt="you are the orchestrator", harness="opencode",
        ),
    }
    r._project_store(p).upsert(Specialist(name="project-only"))

    # include_seeds=True (default): project + seeds, but NOT orchestrator
    out = r.list_resolved(project_dir=p)
    names = [s.name for s in out]
    assert "project-only" in names
    assert "alpha" in names
    assert "orchestrator" not in names

    # include_seeds=False: project only
    out2 = r.list_resolved(project_dir=p, include_seeds=False)
    names2 = [s.name for s in out2]
    assert names2 == ["project-only"]


def test_list_resolved_dedups_shadowing(tmp_path: Path):
    p = tmp_path / "p"
    p.mkdir()
    r = SpecialistResolver()
    r.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
    r._seed_defs = {}  # isolate from real seeds
    r._project_store(p).upsert(Specialist(name="alpha"))
    r.global_store.upsert(Specialist(name="alpha", scope="global"))
    out = r.list_resolved(project_dir=p)
    assert len(out) == 1
    assert out[0].scope == "project"  # project wins


def test_list_resolved_order_project_first_then_global(tmp_path: Path):
    p = tmp_path / "p"
    p.mkdir()
    r = SpecialistResolver()
    r.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
    r._seed_defs = {}  # isolate from real seeds
    r._project_store(p).upsert(Specialist(name="z-project"))
    r._project_store(p).upsert(Specialist(name="a-project"))
    r.global_store.upsert(Specialist(name="b-global", scope="global"))
    r.global_store.upsert(Specialist(name="a-global", scope="global"))
    out = r.list_resolved(project_dir=p)
    names = [s.name for s in out]
    # Project first, sorted: a-project, z-project. Then global, sorted: a-global, b-global.
    assert names == ["a-project", "z-project", "a-global", "b-global"]


def test_drop_project_forgets_in_memory_store(tmp_path: Path):
    p = tmp_path / "p"
    p.mkdir()
    r = SpecialistResolver()
    r.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
    s = r._project_store(p)
    s.upsert(Specialist(name="alpha"))
    assert r._project_stores  # has it
    assert r.drop_project(p) is True
    assert p not in {str(Path(k).resolve()) for k in r._project_stores}


def test_drop_project_unknown_returns_false(tmp_path: Path):
    p = tmp_path / "p"
    p.mkdir()
    r = SpecialistResolver()
    assert r.drop_project(p) is False
