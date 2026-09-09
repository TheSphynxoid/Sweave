"""M1.6 step 3 tests: Orchestrator wiring + parent gating.

Covers:
* Orchestrator prompt contains the ``defer`` tool contract
  (the prompt contract that tells the LLM how to dispatch work).
* ``mcp_config.ensure_mcp_config`` writes the per-project
  ``opencode.json`` idempotently (re-running is a no-op), preserves
  user-edited blocks (the ``_sweave_managed`` marker check), and
  never touches the user's home config.
* Project activation triggers the MCP config write
  (``POST /api/projects/{name}/active``).
* Parent gating: a delegation with children cannot transition to
  ``review`` until every child reaches ``done`` or ``failed``; the
  gate is bounded by ``turn_timeout`` so a stuck child doesn't
  wedge the parent.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sweave.runtime.delegation_store import Delegation


# ---------------------------------------------------------------------------
# Orchestrator prompt contains the defer tool contract
# ---------------------------------------------------------------------------


def test_orchestrator_prompt_contains_defer_tool_contract():
    """The orchestrator's seed prompt must tell the model how to
    use the ``defer`` tool: which arguments, which return values,
    which error shapes. The prompt is the model-visible contract;
    if it's missing, the orchestrator will not know to call the
    tool and the chain never starts.
    """
    from pathlib import Path as P

    prompt = (P(__file__).parent.parent / "sweave" / "agents" / "orchestrator" / "config.yaml").read_text(
        encoding="utf-8"
    )
    # Required pieces of the contract.
    for phrase in (
        "defer",
        "list_specialists",
        "caller_delegation_id",
        "target",
        "rejected:",
        "Never implement",
    ):
        assert phrase in prompt, f"orchestrator prompt missing: {phrase!r}"


# ---------------------------------------------------------------------------
# mcp_config.ensure_mcp_config
# ---------------------------------------------------------------------------


def test_ensure_mcp_config_writes_per_project_opencode_json(tmp_path: Path):
    """A fresh project gets a clean ``opencode.json`` with the
    sweave MCP block (managed marker set, command array, env block)."""
    from sweave.runtime.mcp_config import ensure_mcp_config

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    config = ensure_mcp_config(project_dir)
    path = project_dir / "opencode.json"
    assert path.exists()
    # The returned config has the sweave block.
    sweave = config["mcp"]["sweave"]
    assert sweave["type"] == "local"
    # Windowless interpreter on Windows (pythonw.exe never owns a
    # console, so the opencode-spawned MCP server can't flash a CMD
    # window); the plain interpreter elsewhere.
    exe = sweave["command"][0].lower()
    assert exe.endswith("python") or exe.endswith("python.exe") or exe.endswith("pythonw.exe")
    assert sweave["command"][1:] == ["-m", "sweave.mcp"]
    assert sweave["environment"]["SWEAVE_MCP_TOKEN"] == "{env:SWEAVE_MCP_TOKEN}"
    assert sweave["enabled"] is True
    assert sweave["timeout"] == 30000
    assert sweave["_sweave_managed"] is True

    # The on-disk JSON is valid and the marker is present.
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["mcp"]["sweave"]["_sweave_managed"] is True


def test_ensure_mcp_config_is_idempotent(tmp_path: Path):
    """Re-running ensure_mcp_config produces the same file (no
    duplicate keys, same shape, no destructive changes)."""
    from sweave.runtime.mcp_config import ensure_mcp_config

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    ensure_mcp_config(project_dir)
    first = (project_dir / "opencode.json").read_text(encoding="utf-8")
    ensure_mcp_config(project_dir)
    second = (project_dir / "opencode.json").read_text(encoding="utf-8")
    assert first == second


def test_ensure_mcp_config_preserves_user_edited_block(tmp_path: Path):
    """If the user wrote their own ``mcp.sweave`` block (no
    ``_sweave_managed`` marker), ensure_mcp_config must NOT
    clobber it. The marker is the contract.
    """
    from sweave.runtime.mcp_config import ensure_mcp_config

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    opencode_json = project_dir / "opencode.json"
    user_block = {
        "type": "local",
        "command": ["python", "-m", "user.mcp"],
        "environment": {"USER_TOKEN": "secret"},
        "enabled": True,
        "_sweave_managed": False,  # explicitly NOT managed
    }
    opencode_json.write_text(
        json.dumps({"mcp": {"sweave": user_block}}, indent=2),
        encoding="utf-8",
    )
    # Now run ensure_mcp_config -- it should be a no-op.
    config = ensure_mcp_config(project_dir)
    # The user's block is preserved unchanged.
    assert config["mcp"]["sweave"]["command"] == ["python", "-m", "user.mcp"]
    assert config["mcp"]["sweave"]["environment"]["USER_TOKEN"] == "secret"
    assert config["mcp"]["sweave"]["_sweave_managed"] is False
    # And the on-disk file is byte-identical to the user's input
    # (ensure_mcp_config didn't write at all).
    on_disk = json.loads(opencode_json.read_text(encoding="utf-8"))
    assert on_disk["mcp"]["sweave"] == user_block


def test_ensure_mcp_config_preserves_other_top_level_keys(tmp_path: Path):
    """The user's existing ``model``/``provider``/etc. config must
    survive a sweave MCP write. ensure_mcp_config merges, doesn't
    replace.
    """
    from sweave.runtime.mcp_config import ensure_mcp_config

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    opencode_json = project_dir / "opencode.json"
    opencode_json.write_text(
        json.dumps(
            {
                "model": "ollama/qwen3:8b",
                "provider": {"ollama": {"baseURL": "http://localhost:11434/v1"}},
                "compaction": {"auto": True},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    config = ensure_mcp_config(project_dir)
    # Other keys are intact.
    assert config["model"] == "ollama/qwen3:8b"
    assert config["provider"]["ollama"]["baseURL"] == "http://localhost:11434/v1"
    assert config["compaction"]["auto"] is True
    # And the sweave block is added.
    assert config["mcp"]["sweave"]["_sweave_managed"] is True


def test_ensure_mcp_config_dry_run_does_not_write(tmp_path: Path, monkeypatch):
    """The ``M1.6_DISABLE_MCP_PLUMBING=1`` env var (or the explicit
    ``dry_run=True`` arg) skips the write -- handy for tests and
    for users who manage their own opencode.json.
    """
    from sweave.runtime.mcp_config import ensure_mcp_config

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    monkeypatch.setenv("M1.6_DISABLE_MCP_PLUMBING", "1")
    ensure_mcp_config(project_dir)
    assert not (project_dir / "opencode.json").exists()


def test_ensure_mcp_config_does_not_touch_home_config(tmp_path: Path, monkeypatch):
    """The plumbing writes ONLY to the project dir. The user's
    global ``~/.config/opencode/opencode.json`` is left alone --
    no global-config injection fallback (per step 0's verified
    path: per-project config is the mechanism).
    """
    from sweave.runtime.mcp_config import ensure_mcp_config

    # The user's home config (AGENTS-style tmp HOME) should not be
    # touched by ensure_mcp_config.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    ensure_mcp_config(project_dir)
    # No global config was created.
    assert not (tmp_path / ".config" / "opencode" / "opencode.json").exists()
    # The project config was created.
    assert (project_dir / "opencode.json").exists()


# ---------------------------------------------------------------------------
# Project activation triggers the MCP config write (smoke)
# ---------------------------------------------------------------------------


def _build_state(monkeypatch, tmp_path: Path):
    """Same AppState stub pattern as the MCP-server tests."""
    from pathlib import Path as PathCls
    from sweave.web import state as state_mod

    monkeypatch.setattr(PathCls, "home", classmethod(lambda cls: tmp_path))

    original_build = state_mod.AppState.build

    @classmethod
    def build_with_stub_agents(cls, config_manager):
        state = original_build.__func__(cls, config_manager)
        state.dynamic_agents_path = tmp_path / "agents.yaml"
        state.traces_dir = tmp_path / "traces"
        state.traces_dir.mkdir(parents=True, exist_ok=True)
        return state

    monkeypatch.setattr(state_mod.AppState, "build", build_with_stub_agents)
    from sweave.web.server import app
    return app


@pytest.fixture
def client(monkeypatch, tmp_path: Path) -> TestClient:
    app = _build_state(monkeypatch, tmp_path)
    with TestClient(app) as c:
        yield c


def test_activate_project_writes_opencode_config(client: TestClient, tmp_path: Path):
    """Activating a project triggers the per-project opencode.json
    write (idempotent). The MCP config is on disk before the
    response returns, so the user could open the project in
    opencode and see the sweave MCP tools."""
    import uuid

    name = f"p-mcp-activate-{uuid.uuid4().hex[:8]}"
    proj_dir = tmp_path / name
    proj_dir.mkdir(parents=True, exist_ok=True)
    r = client.post(
        "/api/projects", json={"name": name, "path": str(proj_dir), "description": ""}
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/api/projects/{name}/active")
    assert r.status_code == 200, r.text
    # The opencode.json was written on activation.
    config_path = proj_dir / "opencode.json"
    assert config_path.exists(), f"opencode.json not written to {config_path}"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["mcp"]["sweave"]["_sweave_managed"] is True


# ---------------------------------------------------------------------------
# Parent gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parent_gating_waits_for_children_to_finish():
    """A parent delegation with one or more children cannot transition
    to ``review`` until every child reaches ``done`` or ``failed``.
    The gate is a polled wait (250ms cadence) bounded by
    ``turn_timeout`` (default 15 min in production; we use a tiny
    timeout here so the test is fast).
    """
    from sweave.runtime.delegation_store import PerProjectDelegationStores
    from sweave.runtime.job_runner import JobRunner
    from sweave.tools import DelegateTaskTool

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-parent-gate-"))

    stores = PerProjectDelegationStores()
    store = await stores.for_project(project_dir)
    runner = JobRunner(
        delegate_tool=DelegateTaskTool.__new__(DelegateTaskTool),  # bypass __init__
        delegation_stores=stores,
        # Tight timeout: 2s. The test takes well under that.
        turn_timeout=2.0,
    )

    # Create a parent + one child.
    parent = Delegation(
        agent="alpha", task="parent", project_name="p",
        parent_task_id=None, depth=0, chain_root_id=None,
    )
    await store.add(parent)
    child = Delegation(
        agent="beta", task="child", project_name="p",
        parent_task_id=parent.delegation_id, depth=1,
        chain_root_id=parent.delegation_id, status="running",
    )
    await store.add(child)

    from sweave.runtime.trace_log import TraceLog

    trace = TraceLog(parent.delegation_id, base_dir=project_dir)
    import time
    t0 = time.monotonic()
    await runner._wait_for_children(parent, store, trace)
    elapsed = time.monotonic() - t0
    assert 1.5 <= elapsed <= 4.0, f"gate took {elapsed:.2f}s; expected ~2.0s"
    events = [
        json.loads(line)
        for line in (trace.path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    timeouts = [e for e in events if e.get("event") == "children_settle_timeout"]
    assert len(timeouts) == 1
    assert timeouts[0]["count"] == 1
    assert timeouts[0]["timeout"] == 2.0


@pytest.mark.asyncio
async def test_parent_gating_resolves_when_all_children_terminal():
    """When all children have status done or failed, the gate
    returns immediately (within the poll interval) -- no timeout
    fired.
    """
    from sweave.runtime.delegation_store import PerProjectDelegationStores
    from sweave.runtime.job_runner import JobRunner
    from sweave.tools import DelegateTaskTool

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-parent-gate-ok-"))

    stores = PerProjectDelegationStores()
    store = await stores.for_project(project_dir)
    runner = JobRunner(
        delegate_tool=DelegateTaskTool.__new__(DelegateTaskTool),
        delegation_stores=stores,
        turn_timeout=10.0,
    )

    parent = Delegation(
        agent="alpha", task="parent", project_name="p",
        parent_task_id=None, depth=0, chain_root_id=None,
    )
    await store.add(parent)
    await store.add(
        Delegation(
            agent="beta", task="c1", project_name="p",
            parent_task_id=parent.delegation_id, depth=1,
            chain_root_id=parent.delegation_id, status="done",
        ),
    )
    await store.add(
        Delegation(
            agent="gamma", task="c2", project_name="p",
            parent_task_id=parent.delegation_id, depth=1,
            chain_root_id=parent.delegation_id, status="failed",
        ),
    )

    from sweave.runtime.trace_log import TraceLog

    trace = TraceLog(parent.delegation_id, base_dir=project_dir)
    await runner._wait_for_children(parent, store, trace)
    events = [
        json.loads(line)
        for line in (trace.path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    settled = [e for e in events if e.get("event") == "children_settled"]
    assert len(settled) == 1
    assert settled[0]["count"] == 2
    assert settled[0]["done"] == 1
    assert settled[0]["failed"] == 1
    timeouts = [e for e in events if e.get("event") == "children_settle_timeout"]
    assert timeouts == []


@pytest.mark.asyncio
async def test_parent_gating_noop_when_no_children():
    """A leaf delegation (no children at all) returns immediately
    from the gate; no trace event is recorded (the gate is a
    no-op)."""
    from sweave.runtime.delegation_store import PerProjectDelegationStores
    from sweave.runtime.job_runner import JobRunner
    from sweave.tools import DelegateTaskTool

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-parent-gate-leaf-"))

    stores = PerProjectDelegationStores()
    store = await stores.for_project(project_dir)
    runner = JobRunner(
        delegate_tool=DelegateTaskTool.__new__(DelegateTaskTool),
        delegation_stores=stores,
        turn_timeout=5.0,
    )

    leaf = Delegation(
        agent="alpha", task="leaf", project_name="p",
        parent_task_id=None, depth=0, chain_root_id=None,
    )
    await store.add(leaf)

    from sweave.runtime.trace_log import TraceLog

    trace = TraceLog(leaf.delegation_id, base_dir=project_dir)
    await runner._wait_for_children(leaf, store, trace)
    # The gate was a no-op; the trace file was never created (no
    # append call). If it does exist, it has no gate events.
    if trace.path.exists():
        events = [
            json.loads(line)
            for line in (trace.path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        gate_events = [e for e in events if e.get("event", "").startswith("children_")]
        assert gate_events == []
