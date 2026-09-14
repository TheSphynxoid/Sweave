"""M1.2 step 2 tests: AppState anchored path, model precedence chain,
legacy import, CWD-independence.

The precedence chain is the unit under test; we don't hardcode model
names -- we ask ConfigManager what the chain *should* return and
assert the chain honours the precedence (override > specialist >
role_ref > legacy). That keeps the tests stable across models.yaml
edits.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sweave.config.manager import ConfigManager
from sweave.runtime.specialist_store import (
    ORCHESTRATOR_NAME,
    GlobalSpecialistStore,
    Specialist,
    SpecialistResolver,
)
from sweave.tools import DelegateTaskTool


# ---- helpers -------------------------------------------------------------


def _make_config_manager(tmp_path: Path) -> ConfigManager:
    """Load tmp COPIES of the on-disk config so the tests track
    models.yaml edits without touching the live repo files (hygiene:
    config.yaml is a working artifact — ``load()`` persists legacy
    adoption and must never see the repo CWD).
    """
    import shutil
    import yaml as _yaml

    root = Path(__file__).parent.parent
    for name in (
        "config.yaml",
        "models.yaml",
        "rules.yaml",
        "models.custom.yaml",
        "models.meta.json",
    ):
        src = root / name
        if src.exists():
            shutil.copy(src, tmp_path / name)
    cfg_doc = _yaml.safe_load(
        (tmp_path / "config.yaml").read_text(encoding="utf-8")
    )
    models = cfg_doc.get("models")
    if isinstance(models, dict):
        models["registry_path"] = str(tmp_path / "models.yaml")
        models["rules_path"] = str(tmp_path / "rules.yaml")
        (tmp_path / "config.yaml").write_text(
            _yaml.safe_dump(cfg_doc, sort_keys=False), encoding="utf-8"
        )
    cm = ConfigManager(config_path=tmp_path / "config.yaml")
    cm.load()
    return cm


def _make_tool(
    tmp_path: Path, *, with_resolver: bool = True
) -> DelegateTaskTool:
    cm = _make_config_manager(tmp_path)
    tool = DelegateTaskTool(
        cm,
        None,  # type: ignore[arg-type] -- we never reach the worktree step
    )
    if with_resolver:
        resolver = SpecialistResolver()
        # Pin the resolver's global store at a tmp file so we don't touch home
        resolver.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
        resolver._seed_defs = {}
        tool.specialist_resolver = resolver
    return tool


# ---- _resolve_model precedence chain --------------------------------------


def test_model_precedence_task_override_wins(tmp_path: Path):
    """task_override is the highest priority; even if a specialist has
    current_model set, the per-call override is used."""
    tool = _make_tool(tmp_path)
    tool.specialist_resolver.global_store.upsert(
        Specialist(
            name="backend", scope="global", current_model="specialist-model"
        )
    )
    assert (
        tool._resolve_model("backend", task_override="explicit-override")
        == "explicit-override"
    )


def test_model_precedence_specialist_current_model_second(tmp_path: Path):
    """When no task_override and the specialist has current_model, that
    wins (even if role_ref is set; current_model > role_ref)."""
    tool = _make_tool(tmp_path)
    tool.specialist_resolver.global_store.upsert(
        Specialist(
            name="backend", scope="global",
            current_model="specialist-model",
            role_ref="backend",  # set; should be shadowed by current_model
        )
    )
    assert tool._resolve_model("backend", task_override=None) == "specialist-model"


def test_model_precedence_role_ref_third(tmp_path: Path):
    """No override, no current_model -> use role_ref via config.resolve_model.
    The resolved value should equal what config.resolve_model('backend')
    returns (whatever the on-disk models.yaml says for the 'backend' role).
    """
    tool = _make_tool(tmp_path)
    tool.specialist_resolver.global_store.upsert(
        Specialist(
            name="backend", scope="global", current_model=None, role_ref="backend"
        )
    )
    expected = tool.config.resolve_model("backend")
    assert tool._resolve_model("backend", task_override=None) == expected


def test_model_precedence_role_ref_unknown_falls_through(tmp_path: Path):
    """role_ref that doesn't match any models.yaml role -> falls through
    to the orchestrator's default."""
    tool = _make_tool(tmp_path)
    tool.specialist_resolver.global_store.upsert(
        Specialist(
            name="backend", scope="global", current_model=None, role_ref="gibberish"
        )
    )
    # config.resolve_model("gibberish") -> unknown role -> orchestrator default
    expected = tool.config.resolve_model("gibberish")
    assert tool._resolve_model("backend", task_override=None) == expected
    # And the fallback path *is* the orchestrator's default
    orch_default = tool.config.resolve_model("orchestrator")
    assert expected == orch_default


def test_model_precedence_unknown_agent_no_specialist(tmp_path: Path):
    """No specialist record, unknown agent name -> legacy fallback."""
    tool = _make_tool(tmp_path)
    expected = tool.config.resolve_model("nope")
    assert tool._resolve_model("nope", task_override=None) == expected
    # And the fallback is the orchestrator's default
    assert expected == tool.config.resolve_model("orchestrator")


def test_model_precedence_known_agent_no_specialist(tmp_path: Path):
    """No specialist record, agent name matches a models.yaml role -> legacy."""
    tool = _make_tool(tmp_path)
    expected = tool.config.resolve_model("backend")
    assert tool._resolve_model("backend", task_override=None) == expected


def test_model_precedence_no_resolver_uses_legacy(tmp_path: Path):
    """When the resolver is absent, the legacy path runs (no exception)."""
    tool = _make_tool(tmp_path, with_resolver=False)
    assert tool.specialist_resolver is None
    assert tool._resolve_model("backend", task_override=None) == tool.config.resolve_model("backend")
    assert tool._resolve_model("nope", task_override=None) == tool.config.resolve_model("nope")
    # And the explicit override still wins
    assert tool._resolve_model("backend", task_override="x") == "x"


# ---- AppState anchored path ----------------------------------------------


def test_anchored_path_is_home_relative(tmp_path: Path):
    """dynamic_agents_path default is ~/.sweave/agents.yaml regardless of CWD."""
    from sweave.web.state import AppState, _anchored_agents_path

    orig_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)  # foreign CWD
        assert _anchored_agents_path() == Path.home() / ".sweave" / "agents.yaml"
        state = AppState.build(_make_config_manager(tmp_path))
        assert state.dynamic_agents_path == Path.home() / ".sweave" / "agents.yaml"
    finally:
        os.chdir(orig_cwd)


def test_anchored_path_with_explicit_override(tmp_path: Path):
    """If a caller sets dynamic_agents_path explicitly, the anchored
    default is bypassed (this is how the test_routers_smoke fixture
    points at a tmp file)."""
    from sweave.web.state import AppState

    state = AppState.build(_make_config_manager(tmp_path))
    state.dynamic_agents_path = tmp_path / "custom.yaml"
    assert state.dynamic_agents_path == tmp_path / "custom.yaml"


def test_load_dynamic_agents_anchored(monkeypatch, tmp_path: Path):
    """load_dynamic_agents reads from the anchored path."""
    from sweave.web import state as state_mod
    from sweave.web.state import AppState

    monkeypatch.setattr(state_mod.Path, "home", classmethod(lambda cls: tmp_path))
    home_agents = tmp_path / ".sweave" / "agents.yaml"
    home_agents.parent.mkdir(parents=True, exist_ok=True)
    home_agents.write_text(
        "agents:\n"
        "  - name: alpha\n"
        "    role: backend\n"
        "    model: alpha-model\n"
        "    system_prompt: alpha prompt\n"
        "    worktree_path: ''\n"
        "    memory_bank: project\n"
        "    tools: []\n"
        "    env: {}\n"
        "    harness: opencode\n",
        encoding="utf-8",
    )
    orig_cwd = os.getcwd()
    try:
        foreign = tmp_path / "unrelated"
        foreign.mkdir(exist_ok=True)
        os.chdir(foreign)
        state = AppState.build(_make_config_manager(tmp_path))
        state.dynamic_agents_path = Path.home() / ".sweave" / "agents.yaml"
        import asyncio
        asyncio.run(state.load_dynamic_agents())
        assert "alpha" in state.dynamic_agents
        assert state.dynamic_agents["alpha"].model == "alpha-model"
    finally:
        os.chdir(orig_cwd)


# ---- Legacy import ------------------------------------------------------


def test_bootstrap_specialists_imports_when_file_absent(monkeypatch, tmp_path: Path):
    """If the anchored file doesn't exist but dynamic_agents has entries,
    bootstrap creates Specialists in the global store."""
    from sweave.web import state as state_mod
    from sweave.web.state import AppState

    monkeypatch.setattr(state_mod.Path, "home", classmethod(lambda cls: tmp_path))
    state = AppState.build(_make_config_manager(tmp_path))
    state.dynamic_agents_path = Path.home() / ".sweave" / "agents.yaml"
    from pathlib import Path as P
    from sweave.config.schemas import AgentSpec

    state.dynamic_agents["alpha"] = AgentSpec(
        name="alpha", role="backend", model="alpha-model",
        system_prompt="alpha prompt", worktree_path=P("."),
        memory_bank="project", tools=[], env={}, harness="opencode",
    )
    import asyncio
    asyncio.run(state.bootstrap_specialists())
    rec = state.ensure_specialist_resolver().global_store.get("alpha")
    assert rec is not None
    assert rec.scope == "global"
    assert rec.current_model == "alpha-model"
    assert rec.system_prompt == "alpha prompt"


def test_bootstrap_skips_when_anchored_file_present(monkeypatch, tmp_path: Path):
    """If the anchored file already exists, bootstrap is a no-op (the
    resolver reads the existing file on construction)."""
    from sweave.web import state as state_mod
    from sweave.web.state import AppState

    monkeypatch.setattr(state_mod.Path, "home", classmethod(lambda cls: tmp_path))
    anchored = tmp_path / ".sweave" / "agents.yaml"
    anchored.parent.mkdir(parents=True, exist_ok=True)
    anchored.write_text("agents: []\n", encoding="utf-8")

    state = AppState.build(_make_config_manager(tmp_path))
    state.dynamic_agents_path = anchored
    from pathlib import Path as P
    from sweave.config.schemas import AgentSpec
    state.dynamic_agents["alpha"] = AgentSpec(
        name="alpha", role="backend", model="alpha-model",
        system_prompt="", worktree_path=P("."), memory_bank="p",
        tools=[], env={}, harness="opencode",
    )
    import asyncio
    asyncio.run(state.bootstrap_specialists())
    assert state.ensure_specialist_resolver().global_store.get("alpha") is None


def test_bootstrap_skips_when_no_legacy_entries(monkeypatch, tmp_path: Path):
    """If the file is absent and dynamic_agents is empty, bootstrap is a no-op."""
    from sweave.web import state as state_mod
    from sweave.web.state import AppState

    monkeypatch.setattr(state_mod.Path, "home", classmethod(lambda cls: tmp_path))
    state = AppState.build(_make_config_manager(tmp_path))
    state.dynamic_agents_path = Path.home() / ".sweave" / "agents.yaml"
    import asyncio
    asyncio.run(state.bootstrap_specialists())
    assert not state.dynamic_agents_path.exists()
