"""Per-seed model overrides + seed materialization-leak fix (2026-09-11).

Covers:
* Seed model override set -> resolve() returns the merged model,
  list_resolved shows the seed under ``scope="seed"``.
* An explicit project/global record with the same name still shadows
  the seed (override merging does not change precedence).
* The resolver's write gates: create/update refuse ``scope="seed"``
  records (no materialization leak); set_seed_model writes only a
  minimal model-only override.
* The load-time fold-migration of a leaked materialized seed copy in
  the global store (idempotent, preserves the model, drops the
  prompt/description/session copy).
* HTTP surface: PUT /api/specialists/{name}/model accepts a seed
  (model.changed with scope="seed"); PUT with prompt changes is 400;
  the legacy PUT /api/agents/{name} never materializes a seed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sweave.agents.loader import AgentDefinition
from sweave.runtime.specialist_store import (
    GlobalSpecialistStore,
    Specialist,
    SpecialistResolver,
    _make_seed_override,
    _seed_name_to_role,
    parse_model_ref,
)


def _fake_seed_index(monkeypatch: pytest.MonkeyPatch, role: str, name: str) -> None:
    """Point the store's seed-name index at a fake seed pair."""
    monkeypatch.setattr(
        "sweave.runtime.specialist_store._seed_name_to_role",
        lambda: {name: role},
    )


def _make_resolver(tmp_path: Path, role: str, name: str) -> SpecialistResolver:
    r = SpecialistResolver()
    r.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
    r._seed_defs = {
        role: AgentDefinition(
            role=role, name=name, description=f"seed {role}",
            prompt=f"seed prompt for {role}", harness="opencode",
        ),
    }
    return r


# --- resolver-level: override set -> merged resolve + list -----------------


def test_set_seed_model_merges_into_resolve(tmp_path: Path):
    r = _make_resolver(tmp_path, "backend", "backend-specialist")
    merged = r.set_seed_model(
        "backend-specialist", parse_model_ref("zai-coding-plan/glm-5.3-flash+high")
    )
    assert merged.scope == "seed"
    assert merged.public_model() == "zai-coding-plan/glm-5.3-flash+high"
    # The merged view keeps the seed's prompt (config.yaml is the truth)
    assert merged.system_prompt == "seed prompt for backend"

    rec = r.resolve("backend-specialist")
    assert rec is not None
    assert rec.scope == "seed"
    assert rec.public_model() == "zai-coding-plan/glm-5.3-flash+high"

    listed = [s for s in r.list_resolved() if s.name == "backend-specialist"]
    assert len(listed) == 1
    assert listed[0].scope == "seed"
    assert listed[0].public_model() == "zai-coding-plan/glm-5.3-flash+high"


def test_seed_override_survives_store_round_trip(tmp_path: Path):
    r = _make_resolver(tmp_path, "backend", "backend-specialist")
    r.set_seed_model(
        "backend-specialist", parse_model_ref("zai-coding-plan/glm-5.3-flash+high")
    )
    # The override record on disk carries ONLY the model (+ identity).
    store = GlobalSpecialistStore(tmp_path / "g.yaml")
    ov = store.get("backend-specialist")
    assert ov is not None
    assert ov.scope == "seed"
    assert ov.public_model() == "zai-coding-plan/glm-5.3-flash+high"
    assert ov.system_prompt == ""
    assert ov.description == ""
    assert ov.session_id is None
    # And a fresh resolver reads it back (resolve merges again)
    r2 = _make_resolver(tmp_path, "backend", "backend-specialist")
    rec = r2.resolve("backend-specialist")
    assert rec is not None
    assert rec.public_model() == "zai-coding-plan/glm-5.3-flash+high"


def test_explicit_global_record_still_shadows_seed(tmp_path: Path):
    r = _make_resolver(tmp_path, "backend", "backend-specialist")
    r.set_seed_model(
        "backend-specialist", parse_model_ref("zai-coding-plan/glm-5.3-flash+high")
    )
    r.global_store.upsert(
        Specialist(name="backend-specialist", scope="global", current_model="custom/m")
    )
    rec = r.resolve("backend-specialist")
    assert rec is not None
    assert rec.scope == "global"
    assert rec.current_model == "custom/m"


def test_resolver_update_refuses_seed_records(tmp_path: Path):
    r = _make_resolver(tmp_path, "backend", "backend-specialist")
    view = r._seed_view("backend-specialist")
    assert view is not None
    rec = Specialist(name="backend-specialist", scope="seed", system_prompt="copy!")
    with pytest.raises(ValueError, match="read-only view"):
        r.update(rec)
    with pytest.raises(ValueError, match="seed"):
        r.create(Specialist(name="backend-specialist", scope="seed"))


def test_make_seed_override_keeps_minimal_shape():
    ov = _make_seed_override(
        name="backend-specialist",
        role_ref="backend",
        model_ref=parse_model_ref("p/m+high"),
    )
    assert ov.scope == "seed"
    assert ov.system_prompt == ""
    assert ov.description == ""
    assert ov.session_id is None
    assert json.loads(ov.current_model or "{}") == {
        "provider": "p", "model_id": "m", "variant": "high",
    }


# --- store-level: fold-migration of a materialized copy --------------------


def _write_materialized_copy(path: Path, name: str) -> None:
    path.write_text(
        json.dumps(
            {
                "specialists": [
                    Specialist(
                        name=name,
                        scope="global",
                        role_ref="backend",
                        description="stale copy",
                        system_prompt="stale prompt copy",
                        current_model="zai-coding-plan/glm-5.3-flash+high",
                        session_id="ses_old",
                    ).to_dict()
                ]
            }
        ),
        encoding="utf-8",
    )


def test_fold_migration_materialized_seed_copy(tmp_path: Path, monkeypatch):
    _fake_seed_index(monkeypatch, "backend", "backend-specialist")
    f = tmp_path / "agents.yaml"
    _write_materialized_copy(f, "backend-specialist")

    store = GlobalSpecialistStore(f)
    rec = store.get("backend-specialist")
    assert rec is not None
    assert rec.scope == "seed"
    assert rec.model_ref == parse_model_ref("zai-coding-plan/glm-5.3-flash+high")  # model kept
    assert rec.public_model() == "zai-coding-plan/glm-5.3-flash+high"
    assert rec.model_ref == parse_model_ref("zai-coding-plan/glm-5.3-flash+high")
    # The stale full-record copy is dropped
    assert rec.system_prompt == ""
    assert rec.description == ""
    assert rec.session_id is None
    # Fold-migration persisted the normalized file (one-time write)
    raw = json.loads(f.read_text(encoding="utf-8"))
    on_disk = [s for s in raw["specialists"] if s["name"] == "backend-specialist"]
    assert on_disk[0]["scope"] == "seed"
    assert on_disk[0]["system_prompt"] == ""

    # Idempotent: a fresh store loads the normalized file unchanged.
    store2 = GlobalSpecialistStore(f)
    rec2 = store2.get("backend-specialist")
    assert rec2 is not None
    assert rec2.scope == "seed"
    assert rec2.current_model == rec.current_model  # idempotent: same JSON shape


def test_fold_migration_leaves_unrelated_global_records_alone(
    tmp_path: Path, monkeypatch
):
    _fake_seed_index(monkeypatch, "backend", "backend-specialist")
    f = tmp_path / "agents.yaml"
    store = GlobalSpecialistStore(f)
    store.upsert(
        Specialist(name="software-engineer", scope="global", current_model="x/y")
    )
    assert store.get("software-engineer").scope == "global"  # not a seed name


def test_seed_name_to_role_shape():
    """The real seed index: backend/frontend/reviewer seeds carry
    -specialist names; the orchestrator self-maps."""
    idx = _seed_name_to_role()
    assert idx.get("backend-specialist") == "backend"
    assert "orchestrator" in idx


# --- HTTP surface ----------------------------------------------------------

from tests.test_m1_2_step3 import _build_state, _create_project  # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path: Path) -> TestClient:
    app = _build_state(monkeypatch, tmp_path)
    with TestClient(app) as c:
        yield c


def test_put_model_on_seed_writes_override_and_event(
    client: TestClient, tmp_path: Path
):
    _create_project(client, tmp_path)
    r = client.put(
        "/api/specialists/backend-specialist/model",
        json={"model": "zai-coding-plan/glm-5.3-flash+high"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["scope"] == "seed"
    assert data["current_model"] == "zai-coding-plan/glm-5.3-flash+high"
    # The list shows it under the seed group WITH the chosen model
    lst = client.get("/api/specialists").json()["specialists"]
    backend = [s for s in lst if s["name"] == "backend-specialist"]
    assert len(backend) == 1
    assert backend[0]["scope"] == "seed"
    assert backend[0]["current_model"] == "zai-coding-plan/glm-5.3-flash+high"
    # The override on disk keeps only the model (no materialized copy)
    disk = json.loads(
        (tmp_path / ".sweave" / "agents.yaml").read_text(encoding="utf-8")
    )
    entries = [s for s in disk["specialists"] if s["name"] == "backend-specialist"]
    assert entries[0]["scope"] == "seed"
    assert entries[0]["system_prompt"] == ""  # prompt copy dropped


def test_put_prompt_on_seed_is_400(client: TestClient, tmp_path: Path):
    _create_project(client, tmp_path)
    r = client.put(
        "/api/specialists/backend-specialist",
        json={"system_prompt": "custom prompt"},
    )
    assert r.status_code == 400
    assert "config.yaml" in r.json()["detail"]


def test_model_endpoint_rejects_unqualified_on_seed(
    client: TestClient, tmp_path: Path
):
    _create_project(client, tmp_path)
    r = client.put(
        "/api/specialists/backend-specialist/model", json={"model": "gmi"}
    )
    assert r.status_code == 400
    assert "qualified" in r.json()["detail"]


def test_legacy_agents_put_never_materializes_seed(
    client: TestClient, tmp_path: Path
):
    _create_project(client, tmp_path)
    # Model-only update: goes through the override, nothing materializes.
    r = client.put(
        "/api/agents/backend-specialist",
        json={"model": "zai-coding-plan/glm-5.3-flash+high"},
    )
    assert r.status_code == 200, r.text
    disk_path = tmp_path / ".sweave" / "agents.yaml"
    if disk_path.exists():
        raw = disk_path.read_text(encoding="utf-8")
        for entry in json.loads(raw)["specialists"]:
            if entry["name"] == "backend-specialist":
                assert entry["scope"] == "seed"
                assert entry["system_prompt"] == ""

    # Prompt edit on a seed: refused.
    r2 = client.put(
        "/api/agents/backend-specialist",
        json={"system_prompt": "custom"},
    )
    assert r2.status_code == 400
    assert "config.yaml" in r2.json()["detail"]

    # ...and the persistent store never gained a global/backend record.
    if disk_path.exists():
        raw = disk_path.read_text(encoding="utf-8")
        entries = [
            s for s in json.loads(raw)["specialists"]
            if s["name"] == "backend-specialist"
        ]
        assert all(e["scope"] == "seed" for e in entries)


def test_specialists_put_current_model_only_on_seed_ok(
    client: TestClient, tmp_path: Path
):
    _create_project(client, tmp_path)
    r = client.put(
        "/api/specialists/backend-specialist",
        json={"current_model": "zai-coding-plan/glm-5.3-flash+high"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["scope"] == "seed"
