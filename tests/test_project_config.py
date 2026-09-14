"""Two-file config tests (user ruling: global + per-project layers).

Global ``config.yaml`` holds defaults for every project;
``{project}/.sweave/config.yaml`` overlays ``models`` / ``routing`` /
``harness`` field-by-field. Task-scoped resolution (the delegation's
/ session's own project — never the UI-focused active project) drives
specialist lookup, turn budgets, retry budgets, model defaults, and
the harness tier.

* manager: merge, precedence, ignored sections, invalid fallback,
  missing file, model + routing project accessors.
* harness tier: project sits between specialist and config.
* JobRunner: the factory receives the delegation's project.
* ChatLoop: per-turn scope (timeout/retries/harness/model) comes from
  the turn's own project overlay.
* endpoint: GET effective config (+404, +no-overlay shape).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


def _write_global(tmp_path: Path):
    providers = {"opencode": ["a-model"], "ollama": ["b-model"]}
    (tmp_path / "models.yaml").write_text(
        yaml.safe_dump({"models": {"providers": providers}}, sort_keys=False),
        encoding="utf-8",
    )
    # Routing scalars live in rules.yaml (the config.yaml routing
    # block is superseded at load).
    (tmp_path / "rules.yaml").write_text(
        yaml.safe_dump(
            {"routes": [], "fallback": "llm", "turn_timeout_s": 900, "turn_retries": 1},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "models": {
                    "registry_path": str(tmp_path / "models.yaml"),
                    "rules_path": str(tmp_path / "rules.yaml"),
                    "default": "opencode/a-model",
                },
                "harness": {"default": "opencode"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return config_path


def _manager(tmp_path: Path):
    from sweave.config.manager import ConfigManager

    cm = ConfigManager(config_path=_write_global(tmp_path))
    cm.load()
    return cm


def _overlay(proj_dir: Path, doc: dict) -> Path:
    sweave_dir = proj_dir / ".sweave"
    sweave_dir.mkdir(parents=True, exist_ok=True)
    path = sweave_dir / "config.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Manager layer
# ---------------------------------------------------------------------------


def test_no_overlay_returns_global(tmp_path: Path):
    cm = _manager(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    effective = cm.get_for_project(proj)
    assert effective.models.default == "opencode/a-model"
    assert effective.routing.turn_timeout_s == 900
    assert cm.overlay_sections_for(proj) == []
    assert cm.get_for_project(None).models.default == "opencode/a-model"


def test_overlay_merges_models_routing_harness(tmp_path: Path):
    cm = _manager(tmp_path)
    proj = tmp_path / "proj"
    _overlay(
        proj,
        {
            "models": {"default": "ollama/b-model"},
            "routing": {"turn_timeout_s": 60, "turn_retries": 5},
            "harness": {"default": "sweave-engine"},
        },
    )
    effective = cm.get_for_project(proj)
    assert effective.models.default == "ollama/b-model"
    assert effective.routing.turn_timeout_s == 60
    assert effective.routing.turn_retries == 5
    assert effective.harness.default == "sweave-engine"
    # Unmentioned fields inherit global.
    assert effective.models.registry_path == str(tmp_path / "models.yaml")
    assert cm.overlay_sections_for(proj) == ["harness", "models", "routing"]


def test_overlay_ignores_global_only_and_unknown_sections(tmp_path: Path):
    cm = _manager(tmp_path)
    proj = tmp_path / "proj"
    _overlay(
        proj,
        {
            "server": {"port": 9999},
            "memory": {"backend": "none"},
            "git": {"auto_pr": False},
            "nope": {"x": 1},
            "routing": {"turn_retries": 0},
        },
    )
    effective = cm.get_for_project(proj)
    assert effective.server.port == 8080
    assert effective.routing.turn_retries == 0
    assert cm.overlay_sections_for(proj) == ["git", "memory", "nope", "routing", "server"]


def test_overlay_invalid_section_falls_back_to_global(tmp_path: Path):
    cm = _manager(tmp_path)
    proj = tmp_path / "proj"
    _overlay(proj, {"routing": {"turn_retries": 99}})
    effective = cm.get_for_project(proj)
    assert effective.routing.turn_retries == 1


def test_overlay_bad_yaml_falls_back_to_global(tmp_path: Path):
    cm = _manager(tmp_path)
    proj = tmp_path / "proj"
    sweave_dir = proj / ".sweave"
    sweave_dir.mkdir(parents=True, exist_ok=True)
    (sweave_dir / "config.yaml").write_text("[[[not yaml", encoding="utf-8")
    assert cm.get_for_project(proj).models.default == "opencode/a-model"


def test_default_model_for_project_precedence(tmp_path: Path):
    cm = _manager(tmp_path)
    proj = tmp_path / "proj"
    # No overlay: global default.
    assert cm.get_default_model_for_project(proj) == "opencode/a-model"
    assert cm.get_default_model_for_project(None) == "opencode/a-model"
    # Selectable overlay default wins.
    _overlay(proj, {"models": {"default": "ollama/b-model"}})
    assert cm.get_default_model_for_project(proj) == "ollama/b-model"
    # Unselectable overlay default: global chain applies.
    _overlay(proj, {"models": {"default": "nope/nothing"}})
    assert cm.get_default_model_for_project(proj) == "opencode/a-model"


def test_resolve_model_project_dir(tmp_path: Path):
    cm = _manager(tmp_path)
    proj = tmp_path / "proj"
    _overlay(proj, {"models": {"default": "ollama/b-model"}})
    assert cm.resolve_model("orchestrator") == "opencode/a-model"
    assert cm.resolve_model("orchestrator", project_dir=proj) == "ollama/b-model"
    assert cm.resolve_model("orchestrator", override="x/y") == "x/y"


def test_routing_for_project(tmp_path: Path):
    cm = _manager(tmp_path)
    proj = tmp_path / "proj"
    assert cm.get_routing_for_project(None).turn_retries == 1
    _overlay(proj, {"routing": {"turn_timeout_s": 30}})
    assert cm.get_routing_for_project(proj).turn_timeout_s == 30
    assert cm.get_routing_for_project(proj).turn_retries == 1


# ---------------------------------------------------------------------------
# Harness tier
# ---------------------------------------------------------------------------


def test_project_tier_between_specialist_and_config(monkeypatch):
    from sweave.harness.base import harness_registry, resolve_harness_name

    monkeypatch.delenv("SWEAVE_MOCK_OPENCODE", raising=False)
    monkeypatch.setitem(harness_registry._harnesses, "proj-h", object())
    monkeypatch.setitem(harness_registry._harnesses, "spec-h", object())
    # Project beats config...
    assert resolve_harness_name(None, None, "opencode", project_default="proj-h") == (
        "proj-h", "project",
    )
    # ...but loses to the specialist record...
    assert resolve_harness_name(None, "spec-h", "opencode", project_default="proj-h") == (
        "spec-h", "specialist",
    )
    # ...and to the per-task override; unknown project falls through.
    assert resolve_harness_name("spec-h", None, "opencode", project_default="proj-h") == (
        "spec-h", "override",
    )
    assert resolve_harness_name(None, None, "opencode", project_default="nope") == (
        "opencode", "config",
    )


# ---------------------------------------------------------------------------
# JobRunner: task scope, not focus scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_factory_receives_delegation_project(tmp_path: Path):
    """The factory is called with (agent, delegation.project_name) —
    never the UI-focused active project."""
    from sweave.runtime.delegation_store import Delegation, PerProjectDelegationStores
    from sweave.runtime.job_runner import JobRunner

    seen: list[tuple] = []

    def _ok(agent, project):
        seen.append((agent, project))
        return None

    class _StubRuntime:
        async def run(self, **kwargs):
            return "ok"

    runner = JobRunner(
        delegate_tool=None,  # type: ignore[arg-type]
        delegation_stores=PerProjectDelegationStores(),
        project_dir_resolver=lambda name: tmp_path,
        traces_dir=tmp_path / "traces",
        specialist_runtime=_StubRuntime(),  # type: ignore[arg-type]
        specialist_factory=_ok,  # type: ignore[arg-type]
        turn_timeout=30.0,
    )
    d = await runner.submit(agent="backend", task="t", project_name="proj-A")
    terminal = await runner.wait(d.delegation_id, timeout=10)
    assert terminal is not None and terminal.status in ("review", "done", "failed")
    assert seen == [("backend", "proj-A")]


@pytest.mark.asyncio
async def test_per_delegation_budget_and_harness_tier(tmp_path: Path):
    """Overlay timeout + harness default apply to that project's
    delegations only."""
    from sweave.runtime.delegation_store import PerProjectDelegationStores
    from sweave.runtime.job_runner import JobRunner

    budgets: list[float] = []
    harnesses: list = []

    class _StubRuntime:
        async def run(self, **kwargs):
            harnesses.append(kwargs.get("project_harness_default"))
            return "ok"

    def _overlay_config(name):
        if name == "proj-A":
            return SimpleNamespace(
                routing=SimpleNamespace(turn_timeout_s=60.0),
                harness=SimpleNamespace(default="proj-harness"),
            )
        return None

    runner = JobRunner(
        delegate_tool=None,  # type: ignore[arg-type]
        delegation_stores=PerProjectDelegationStores(),
        project_dir_resolver=lambda name: tmp_path,
        traces_dir=tmp_path / "traces",
        specialist_runtime=_StubRuntime(),  # type: ignore[arg-type]
        specialist_factory=lambda agent, project=None: None,
        turn_timeout=900,
        project_config_resolver=_overlay_config,
    )
    from sweave.runtime.delegation_store import Delegation

    store = await runner.stores.for_project(tmp_path)
    d = Delegation(agent="a", task="t", project_name="proj-A")
    assert runner._turn_budget_for(d) == 60.0
    assert runner._turn_budget_label_for(d) == "60"
    assert runner._project_harness_for(d) == "proj-harness"
    d2 = Delegation(agent="a", task="t", project_name="other")
    assert runner._turn_budget_for(d2) == 900.0
    assert runner._turn_budget_label_for(d2) == "900"
    assert runner._project_harness_for(d2) is None
    await store.add(d)
    await store.add(d2)


# ---------------------------------------------------------------------------
# ChatLoop: per-turn scope from the turn's own project
# ---------------------------------------------------------------------------


def _project_manager(tmp_path: Path):
    from sweave.projects import ProjectManager

    pm = ProjectManager(base_path=tmp_path / "sweave-home")
    pm.create_project("demo", path=tmp_path)
    return pm


@pytest.mark.asyncio
async def test_chat_turn_uses_own_project_scope(tmp_path: Path):
    """Timeout, retries, harness tier, model and factory all resolve
    against the session's project overlay — not singletons."""
    from sweave.chat.loop import ChatLoop
    from sweave.runtime.delegation_store import PerProjectDelegationStores

    pm = _project_manager(tmp_path)
    seen_factory: list[tuple] = []
    seen_model: list[tuple] = []
    seen_run: list[dict] = []

    def _factory(name, project=None):
        seen_factory.append((name, project))
        return None

    def _model(agent, project=None):
        seen_model.append((agent, project))
        return "overlay/model"

    class _StubRuntime:
        async def run(self, **kwargs):
            seen_run.append(kwargs)
            return "scoped reply"

    def _overlay_config(name):
        assert name == "demo"
        return SimpleNamespace(
            routing=SimpleNamespace(turn_timeout_s=120.0, turn_retries=7),
            harness=SimpleNamespace(default="proj-harness"),
        )

    chat = ChatLoop(
        project_manager=pm,
        specialist_runtime=_StubRuntime(),  # type: ignore[arg-type]
        specialist_factory=_factory,  # type: ignore[arg-type]
        project_dir_resolver=lambda name: tmp_path,
        delegation_stores=PerProjectDelegationStores(),
        event_bus=None,
        turn_timeout=1800.0,
        turn_retries=3,
        model_resolver=_model,  # type: ignore[arg-type]
        project_config_resolver=_overlay_config,  # type: ignore[arg-type]
    )
    session = pm.create_session("demo", session_name="s1")
    result = await chat.run_turn(session_id=session.id, user_content="hi")
    assert result["content"] == "scoped reply"
    assert seen_factory == [("orchestrator", "demo")]
    assert seen_model == [("orchestrator", "demo")]
    assert seen_run and seen_run[0].get("max_retries") == 7
    assert seen_run[0].get("project_harness_default") == "proj-harness"


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

from tests.test_m1_2_step3 import _build_state, _create_project  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path: Path) -> TestClient:
    app = _build_state(monkeypatch, tmp_path)
    with TestClient(app) as c:
        yield c


def test_effective_config_endpoint(client: TestClient, tmp_path: Path):
    name = _create_project(client, tmp_path)
    # No overlay: global config, empty provenance.
    r = client.get(f"/api/projects/{name}/config/effective")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["project"] == name
    assert data["overlay_present"] is False
    assert data["overlay_sections"] == []
    assert "routing" in data["config"]
    # With overlay: merged values + provenance.
    (tmp_path / name / ".sweave").mkdir(parents=True, exist_ok=True)
    (tmp_path / name / ".sweave" / "config.yaml").write_text(
        yaml.safe_dump(
            {"routing": {"turn_retries": 0}, "models": {"default": "x/y"}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    r = client.get(f"/api/projects/{name}/config/effective")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["overlay_present"] is True
    assert sorted(data["overlay_sections"]) == ["models", "routing"]
    assert data["config"]["routing"]["turn_retries"] == 0
    # Unknown project: 404.
    assert client.get("/api/projects/nope/config/effective").status_code == 404
