"""Per-seed harness overrides (2026-09-14).

Seeds own their prompt/description (config.yaml), but harness — like
model — is a per-seed runtime choice carried on the seed override
(``harness_override``; None = inherit the seed YAML). Covers:
* set -> merged view keeps the seed prompt, flips harness.
* model + harness choices compose (neither setter wipes the other).
* clearing (None) re-inherits the YAML.
* unknown harness names are refused (no stranded seeds).
* the list serves the merged VIEW (stale baked harness defaults on
  old overrides never leak into the pool).
* fold-migration preserves the effective harness of a materialized copy.
* HTTP: PUT harness-only on a seed -> 200; PUT prompt still 400.
* Project default_harness migrates to sweave-engine (display-only).
* _engine_session_resume: eng_ resumes, ses_/empty/fresh spawns.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import sweave.harness  # noqa: F401 (register engine + opencode harnesses)
from sweave.agents.loader import AgentDefinition
from sweave.projects import Project
from sweave.runtime.specialist_store import (
    GlobalSpecialistStore,
    Specialist,
    SpecialistResolver,
    _make_seed_override,
    parse_model_ref,
)
from sweave.runtime.specialist_runtime import _engine_session_resume


def _make_resolver(tmp_path: Path, harness: str = "sweave-engine") -> SpecialistResolver:
    r = SpecialistResolver()
    r.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
    r._seed_defs = {
        "backend": AgentDefinition(
            role="backend",
            name="backend-specialist",
            description="seed backend",
            prompt="seed prompt for backend",
            harness=harness,
        ),
    }
    return r


def test_set_seed_harness_merges_into_view(tmp_path: Path):
    r = _make_resolver(tmp_path)
    merged = r.set_seed_harness("backend-specialist", "opencode")
    assert merged.scope == "seed"
    assert merged.harness == "opencode"
    assert merged.system_prompt == "seed prompt for backend"
    rec = r.resolve("backend-specialist")
    assert rec is not None and rec.harness == "opencode"
    listed = [s for s in r.list_resolved() if s.name == "backend-specialist"]
    assert len(listed) == 1 and listed[0].harness == "opencode"


def test_model_and_harness_choices_compose(tmp_path: Path):
    r = _make_resolver(tmp_path)
    r.set_seed_model(
        "backend-specialist", parse_model_ref("zai-coding-plan/glm-5.3-flash+high")
    )
    r.set_seed_harness("backend-specialist", "opencode")
    rec = r.resolve("backend-specialist")
    assert rec is not None
    assert rec.harness == "opencode"
    assert rec.public_model() == "zai-coding-plan/glm-5.3-flash+high"
    # Setting the model again must not wipe the harness choice.
    r.set_seed_model("backend-specialist", parse_model_ref("openrouter/x+y"))
    rec2 = r.resolve("backend-specialist")
    assert rec2 is not None and rec2.harness == "opencode"


def test_clear_harness_reinherits_yaml(tmp_path: Path):
    r = _make_resolver(tmp_path, harness="sweave-engine")
    r.set_seed_harness("backend-specialist", "opencode")
    assert r.resolve("backend-specialist").harness == "opencode"  # type: ignore[union-attr]
    r.set_seed_harness("backend-specialist", None)
    assert r.resolve("backend-specialist").harness == "sweave-engine"  # type: ignore[union-attr]


def test_unknown_harness_refused(tmp_path: Path):
    r = _make_resolver(tmp_path)
    with pytest.raises(ValueError, match="unknown harness"):
        r.set_seed_harness("backend-specialist", "nope")
    with pytest.raises(ValueError, match="not a seed"):
        r.set_seed_harness("ghost", "opencode")


def test_list_hides_raw_override_stale_harness(tmp_path: Path):
    """An old override with a baked opencode default must not leak
    into the pool — the list serves the YAML-merged view."""
    r = _make_resolver(tmp_path, harness="sweave-engine")
    ov = _make_seed_override(
        name="backend-specialist",
        role_ref="backend",
        model_ref=parse_model_ref("zai/x"),
    )
    ov.harness = "opencode"  # stale baked default, pre-override era
    r.global_store.upsert(ov)
    listed = [s for s in r.list_resolved() if s.name == "backend-specialist"]
    assert len(listed) == 1
    assert listed[0].scope == "seed"
    assert listed[0].harness == "sweave-engine"


def test_fold_migration_preserves_effective_harness(tmp_path: Path, monkeypatch):
    from sweave.runtime import specialist_store as mod

    monkeypatch.setattr(
        mod, "_seed_name_to_role", lambda: {"backend-specialist": "backend"}
    )
    store = GlobalSpecialistStore(tmp_path / "g.yaml")
    store.upsert(
        Specialist(
            name="backend-specialist",
            scope="global",  # type: ignore[arg-type]
            role_ref="backend",
            description="stale copy",
            system_prompt="stale prompt",
            harness="opencode",
            current_model="zai/y",
        )
    )
    store2 = GlobalSpecialistStore(tmp_path / "g.yaml")
    ov = store2.get("backend-specialist")
    assert ov is not None and ov.scope == "seed"
    assert ov.harness_override == "opencode"
    assert ov.system_prompt == ""


def test_project_default_harness_migrates():
    assert Project.from_dict(
        {
            "name": "n",
            "path": "/tmp/x",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
    ).default_harness == "sweave-engine"
    assert Project.from_dict(
        {
            "name": "n",
            "path": "/tmp/x",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
            "default_harness": "opencode",
        }
    ).default_harness == "sweave-engine"
    assert Project.from_dict(
        {
            "name": "n",
            "path": "/tmp/x",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
            "default_harness": "custom",
        }
    ).default_harness == "custom"


def test_engine_session_resume_rule():
    assert _engine_session_resume("eng_abc123", False) is True
    assert _engine_session_resume("ses_abc123", False) is False  # opencode id: spawn
    assert _engine_session_resume("", False) is False
    assert _engine_session_resume("eng_abc123", True) is False  # fresh: spawn
    assert _engine_session_resume("junk", False) is False


# --- HTTP surface ----------------------------------------------------------

from tests.test_m1_2_step3 import _build_state, _create_project  # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path: Path) -> TestClient:
    app = _build_state(monkeypatch, tmp_path)
    with TestClient(app) as c:
        yield c


def test_put_harness_only_on_seed_ok(client: TestClient, tmp_path: Path):
    _create_project(client, tmp_path)
    r = client.put(
        "/api/specialists/backend-specialist", json={"harness": "opencode"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["scope"] == "seed"
    assert r.json()["harness"] == "opencode"
    # Prompt untouched (config.yaml still owns it).
    assert "You are a Backend" in r.json()["system_prompt"]
    # Unknown harness refused, prompt still refused.
    assert client.put(
        "/api/specialists/backend-specialist", json={"harness": "nope"}
    ).status_code == 400
    assert client.put(
        "/api/specialists/backend-specialist",
        json={"system_prompt": "custom"},
    ).status_code == 400


def test_put_harness_and_model_together_on_seed(client: TestClient, tmp_path: Path):
    _create_project(client, tmp_path)
    r = client.put(
        "/api/specialists/backend-specialist",
        json={
            "harness": "opencode",
            "current_model": "zai-coding-plan/glm-5.3-flash+high",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["harness"] == "opencode"
    assert r.json()["current_model"] == "zai-coding-plan/glm-5.3-flash+high"
