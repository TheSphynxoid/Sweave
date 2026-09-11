"""M1.13 step 3: seed/specialist model visibility + change.

Pins the model-set contract on ``PUT /api/specialists/{name}/model``:

* persistence goes through ``Specialist.set_model_ref`` (structured
  JSON on disk; provider/model/variant all survive round-trip),
* ``public_dict`` always renders the canonical
  ``provider/model[+variant]`` string so pickers/tests never see the
  storage shape,
* a variant-carrying selection round-trips (stored ref keeps the
  variant; the render re-attaches the suffix).

The ``client`` fixture mirrors tests/test_m1_2_step3.py (that module
is owned by another workstream — do not extend it).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sweave.runtime.specialist_store import Specialist

# ---------------------------------------------------------------------------
# Pure model_ref / public_model contract
# ---------------------------------------------------------------------------


def test_public_model_renders_qualified_form():
    s = Specialist(name="alpha", scope="project")
    s.set_model_ref({"provider": "opencode", "model_id": "glm-5.3", "variant": "max"})
    assert s.public_model() == "opencode/glm-5.3+max"


def test_public_model_bare_legacy_passthrough():
    s = Specialist(name="alpha", scope="project", current_model="glm-5.3")
    assert s.public_model() == "glm-5.3"


def test_public_model_none_stays_none():
    assert Specialist(name="a", scope="project").public_model() is None


def test_set_model_ref_variant_round_trip():
    s = Specialist(name="alpha", scope="project")
    s.set_model_ref({"provider": "ollama", "model_id": "qwen3:8b", "variant": "low"})
    cloned = Specialist.from_dict(s.public_dict() | {"name": "alpha"})
    # current_model on disk is the JSON ref; the whole ref survives.
    assert cloned.model_ref is not None
    assert cloned.model_ref["provider"] == "ollama"
    assert cloned.model_ref["variant"] == "low"
    assert cloned.public_model() == "ollama/qwen3:8b+low"


# ---------------------------------------------------------------------------
# HTTP surface (fixture pattern from tests/test_m1_2_step3.py)
# ---------------------------------------------------------------------------


def _build_state(monkeypatch, tmp_path: Path):
    from pathlib import Path as PathCls
    from sweave.web import state as state_mod

    monkeypatch.setattr(PathCls, "home", classmethod(lambda cls: tmp_path))

    original_build = state_mod.AppState.build

    @classmethod
    def build_with_stub_agents(cls, config_manager):
        state = original_build.__func__(cls, config_manager)  # type: ignore[attr-defined]
        state.dynamic_agents_path = tmp_path / "agents.yaml"
        return state

    monkeypatch.setattr(state_mod.AppState, "build", build_with_stub_agents)

    from sweave.web.server import app

    return app


@pytest.fixture
def client(monkeypatch, tmp_path: Path) -> TestClient:
    app = _build_state(monkeypatch, tmp_path)
    with TestClient(app) as c:
        yield c


def _create_project(client: TestClient, tmp_path: Path) -> str:
    name = f"p-{uuid.uuid4().hex[:8]}"
    proj = tmp_path / name
    proj.mkdir()
    r = client.post(
        "/api/projects", json={"name": name, "path": str(proj), "description": ""}
    )
    assert r.status_code == 200, r.text
    client.post(f"/api/projects/{name}/active")
    return name


def test_model_endpoint_persists_structured_ref_with_variant(
    client: TestClient, tmp_path: Path
):
    """PUT /model: response renders provider/model+variant; the on-disk
    record carries the structured ref (survives a from_dict reload)."""
    _create_project(client, tmp_path)
    r = client.post("/api/specialists", json={"name": "alpha", "scope": "project"})
    assert r.status_code == 201, r.text
    r = client.put(
        "/api/specialists/alpha/model", json={"model": "ollama/qwen3:8b+low"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["current_model"] == "ollama/qwen3:8b+low"

    g = client.get("/api/specialists/alpha")
    assert g.json()["current_model"] == "ollama/qwen3:8b+low"


def test_model_endpoint_plain_selection_round_trip(
    client: TestClient, tmp_path: Path
):
    _create_project(client, tmp_path)
    client.post("/api/specialists", json={"name": "beta", "scope": "project"})
    r = client.put("/api/specialists/beta/model", json={"model": "opencode/claude-3"})
    assert r.status_code == 200, r.text
    assert r.json()["current_model"] == "opencode/claude-3"
