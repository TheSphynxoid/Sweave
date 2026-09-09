"""Opencode-native agents: managed agent map + per-message pin.

Pins the full-cutover contract (2026-09-09):

* The per-project ``opencode.json`` carries a managed ``agent`` map
  (``sweave-orchestrator`` / ``sweave-specialist``): custom prompts
  sourced from ``sweave/agents/*/config.yaml`` plus the per-role
  permission profiles. User-written same-name blocks are preserved.
* ``SpecialistRuntime.run`` pins every turn to the matching agent
  via ``body["agent"]``.
* The orchestrator no longer gets a one-off system message on
  session create (the agent prompt covers it); specialists keep
  their one-off role prompt on top of the generic charter.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True, scope="module")
def _mock_opencode_env():
    old = os.environ.get("SWEAVE_MOCK_OPENCODE")
    os.environ["SWEAVE_MOCK_OPENCODE"] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("SWEAVE_MOCK_OPENCODE", None)
        else:
            os.environ["SWEAVE_MOCK_OPENCODE"] = old


def test_agent_map_names_modes_and_markers():
    from sweave.runtime.mcp_config import (
        ORCHESTRATOR_AGENT_NAME,
        SPECIALIST_AGENT_NAME,
        _sweave_agent_map,
    )

    agents = _sweave_agent_map()
    assert set(agents) == {ORCHESTRATOR_AGENT_NAME, SPECIALIST_AGENT_NAME}
    for name, entry in agents.items():
        assert entry["mode"] == "primary"
        assert entry["_sweave_managed"] is True
        assert entry["description"]


def test_orchestrator_agent_prompt_comes_from_yaml():
    """The orchestrator agent prompt is the YAML spec prompt (single
    source of truth with the legacy one-off system message)."""
    from sweave.agents.loader import get_agent_definition
    from sweave.runtime.mcp_config import _sweave_agent_map

    seed = get_agent_definition("orchestrator")
    agents = _sweave_agent_map()
    orch = agents["sweave-orchestrator"]
    assert orch["prompt"]
    assert "Sweave Orchestrator" in orch["prompt"]
    if seed is not None and seed.prompt:
        assert orch["prompt"] == seed.prompt


def test_agent_permissions_split_mcp_and_git():
    from sweave.runtime.mcp_config import _sweave_agent_map

    agents = _sweave_agent_map()
    orch = agents["sweave-orchestrator"]
    spec = agents["sweave-specialist"]
    # Orchestrator: no sweave_* deny (it owns the MCP tools), git
    # mutation denied.
    assert orch["permission"].get("task") == "deny"
    assert "sweave_*" not in orch["permission"]
    assert orch["permission"]["bash"].get("git commit*") == "deny"
    # Specialist: sweave MCP tools denied, git left allowed.
    assert spec["permission"].get("task") == "deny"
    assert spec["permission"].get("sweave_*") == "deny"
    assert not any("git commit" in pat for pat in (spec["permission"].get("bash") or {}))


def test_ensure_mcp_config_merges_agents_and_preserves_user_ones(tmp_path: Path):
    import json

    from sweave.runtime.mcp_config import ensure_mcp_config

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    # A user-owned custom agent must survive the merge.
    seed = {
        "agent": {
            "sweave-specialist": {"description": "mine", "mode": "primary"},
            "my-helper": {"description": "user agent", "mode": "subagent"},
        }
    }
    (project_dir / "opencode.json").write_text(json.dumps(seed), encoding="utf-8")

    config = ensure_mcp_config(project_dir)
    agents = config["agent"]
    # User-owned sweave-specialist (no marker) is left alone...
    assert agents["sweave-specialist"]["description"] == "mine"
    # ...the user's own agent is untouched...
    assert agents["my-helper"]["mode"] == "subagent"
    # ...and the managed orchestrator agent is added.
    assert agents["sweave-orchestrator"]["_sweave_managed"] is True
    assert agents["sweave-orchestrator"]["mode"] == "primary"


def test_ensure_mcp_config_refreshes_managed_agents(tmp_path: Path):
    """Re-activation re-renders managed agents (prompt edits in the
    YAML specs land on the next activate)."""
    import json

    from sweave.runtime.mcp_config import ensure_mcp_config

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    ensure_mcp_config(project_dir)
    path = project_dir / "opencode.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["agent"]["sweave-orchestrator"]["prompt"]
    # Second run is stable (idempotent).
    ensure_mcp_config(project_dir)
    data2 = json.loads(path.read_text(encoding="utf-8"))
    assert data2["agent"] == data["agent"]


def _make_runtime():
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    return SpecialistRuntime(runners=ServeRunnerRegistry())


def _make_specialist(*, orchestrator: bool):
    from sweave.runtime.specialist_store import Specialist

    return Specialist(
        name="orchestrator" if orchestrator else "backend",
        scope="project",
        is_orchestrator=orchestrator,
        system_prompt="SEED PROMPT",
        harness="opencode",
        current_model=None,
    )


def _make_delegation(agent: str):
    from sweave.runtime.delegation_store import Delegation

    return Delegation(
        delegation_id="del-test-1",
        agent=agent,
        task="do the thing",
        status="running",
    )


@pytest.mark.asyncio
async def test_run_pins_agent_per_role(tmp_path: Path):
    """``body["agent"]`` follows the specialist flag: orchestrator
    turns run as ``sweave-orchestrator``, everything else as
    ``sweave-specialist``."""
    from sweave.runtime.trace_log import TraceLog

    seen: dict[str, Any] = {}

    async def fake_send(self, body, trace, on_chunk=None):
        seen.update(dict(body))
        return "ok"

    runtime = _make_runtime()
    runtime._send_message = fake_send  # type: ignore[assignment]
    trace = TraceLog("del-test-1", base_dir=tmp_path / "traces")

    await runtime.run(
        specialist=_make_specialist(orchestrator=True),
        delegation=_make_delegation("orchestrator"),
        worktree_path=tmp_path,
        message="hi",
        trace=trace,
    )
    assert seen.get("agent") == "sweave-orchestrator"

    seen.clear()
    await runtime.run(
        specialist=_make_specialist(orchestrator=False),
        delegation=_make_delegation("backend"),
        worktree_path=tmp_path,
        message="hi",
        trace=trace,
    )
    assert seen.get("agent") == "sweave-specialist"


@pytest.mark.asyncio
async def test_system_send_skipped_for_orchestrator_only(tmp_path: Path):
    """End-to-end (mock wire): a fresh orchestrator session gets NO
    one-off system message (the agent prompt covers it); a fresh
    specialist session still gets its role prompt."""
    from sweave.harness.opencode import OpenCodeProcess
    from sweave.runtime.trace_log import TraceLog

    runtime = _make_runtime()
    trace = TraceLog("del-test-2", base_dir=tmp_path / "traces")

    system_sends: list[str] = []
    orig_send = OpenCodeProcess.send

    async def spy_send(self, message, *args, **kwargs):
        if getattr(message, "type", "") == "system":
            system_sends.append(getattr(message, "content", ""))
        return await orig_send(self, message, *args, **kwargs)

    import sweave.runtime.specialist_runtime as rt_mod

    orig_fn = rt_mod.OpenCodeProcess.send
    rt_mod.OpenCodeProcess.send = spy_send  # type: ignore[assignment]
    try:
        await runtime.run(
            specialist=_make_specialist(orchestrator=True),
            delegation=_make_delegation("orchestrator"),
            worktree_path=tmp_path,
            message="hi",
            trace=trace,
        )
        assert system_sends == []
        await runtime.run(
            specialist=_make_specialist(orchestrator=False),
            delegation=_make_delegation("backend"),
            worktree_path=tmp_path,
            message="hi",
            trace=trace,
        )
        assert system_sends == ["SEED PROMPT"]
    finally:
        rt_mod.OpenCodeProcess.send = orig_fn  # type: ignore[assignment]
