"""Per-specialist worktree policy (user toggle, never an LLM parameter).

``Specialist.worktree_policy`` steers isolation per task at dispatch —
the orchestrator's defer contract is unchanged:
* ``isolated`` (default): fresh sweave/{task}/{agent} tree (today).
* ``inherit``: the parent delegation's tree (reviewers); parentless
  or treeless parents fall back to the project root.
* ``none``: project root, no tree at all.

Ownership: only the creator retires a tree (``worktree_owned``,
schema v11) — a shared child settling never removes another
delegation's tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sweave.runtime.delegation_store import PerProjectDelegationStores
from sweave.runtime.job_runner import JobRunner
from sweave.runtime.specialist_store import (
    DEFAULT_WORKTREE_POLICY,
    Specialist,
    validate_worktree_policy,
)
from tests.conftest import fake_worktree_manager_factory


class _StubRuntime:
    def __init__(self, calls: list) -> None:
        self.calls = calls

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return "ok"


def _factory_for(policies: dict, **kw):
    def _factory(agent: str, project=None):
        policy = policies.get(agent)
        if policy is None:
            return None  # transient fallback (isolated default)
        return Specialist(
            name=agent, scope="project", is_orchestrator=False,
            system_prompt="", harness="sweave-engine",
            worktree_policy=policy,
        )

    return _factory


def _runner(tmp_path: Path, run_calls: list, policies: dict, **kw):
    kw.setdefault("delegate_tool", None)
    kw.setdefault("delegation_stores", PerProjectDelegationStores())
    kw.setdefault("project_dir_resolver", lambda name: tmp_path)
    kw.setdefault("traces_dir", tmp_path / "traces")
    kw.setdefault("specialist_runtime", _StubRuntime(run_calls))
    kw.setdefault("specialist_factory", _factory_for(policies))
    kw.setdefault("turn_timeout", 30.0)
    kw.setdefault(
        "worktree_manager_factory",
        fake_worktree_manager_factory(tmp_path / "wt-root")[0],
    )
    return JobRunner(**kw)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_inherit_uses_parent_tree_and_never_retires_it(tmp_path: Path):
    from sweave.runtime.trace_log import TraceLog

    factory, calls = fake_worktree_manager_factory(tmp_path / "wt-root")
    run_calls: list = []
    runner = _runner(
        tmp_path, run_calls, {"backend": "isolated", "reviewer": "inherit"},
        worktree_manager_factory=factory,
    )
    parent = await runner.submit(agent="backend", task="impl", project_name="p1")
    await runner.wait(parent.delegation_id, timeout=10)
    assert len(calls["created"]) == 1

    child = await runner.submit(
        agent="reviewer", task="review", project_name="p1",
        parent_task_id=parent.delegation_id,
    )
    terminal = await runner.wait(child.delegation_id, timeout=10)
    assert terminal is not None
    # No second tree created; the child points at the parent's tree,
    # unowned; the runtime ran inside it.
    assert len(calls["created"]) == 1
    assert terminal.worktree_path == parent.worktree_path
    assert terminal.worktree_owned is False
    assert Path(run_calls[-1]["worktree_path"]) == Path(parent.worktree_path or "")
    assert terminal.branch == parent.branch
    # Settling the child (done) retires NOTHING — the parent owns it.
    assert calls["removed"] == []
    # The owner settling still retires.
    store = await runner.stores.for_project(tmp_path)
    prec = store.get(parent.delegation_id)
    assert prec is not None
    await runner._transition(
        prec, store, TraceLog(parent.delegation_id, base_dir=tmp_path), "done"
    )
    assert calls["removed"] == [(prec.task_id, "backend")]


@pytest.mark.asyncio
async def test_inherit_parentless_falls_back_to_project_root(tmp_path: Path):
    factory, calls = fake_worktree_manager_factory(tmp_path / "wt-root")
    run_calls: list = []
    runner = _runner(
        tmp_path, run_calls, {"reviewer": "inherit"},
        worktree_manager_factory=factory,
    )
    d = await runner.submit(agent="reviewer", task="review", project_name="p1")
    terminal = await runner.wait(d.delegation_id, timeout=10)
    assert terminal is not None
    assert calls["created"] == []
    assert terminal.worktree_path is None
    assert terminal.worktree_owned is False
    assert Path(run_calls[0]["worktree_path"]) == tmp_path


@pytest.mark.asyncio
async def test_none_runs_in_project_root_without_tree(tmp_path: Path):
    factory, calls = fake_worktree_manager_factory(tmp_path / "wt-root")
    run_calls: list = []
    runner = _runner(
        tmp_path, run_calls, {"reviewer": "none"},
        worktree_manager_factory=factory,
    )
    d = await runner.submit(agent="reviewer", task="review", project_name="p1")
    terminal = await runner.wait(d.delegation_id, timeout=10)
    assert terminal is not None
    assert calls["created"] == []
    assert terminal.worktree_path is None
    assert terminal.worktree_owned is False
    assert Path(run_calls[0]["worktree_path"]) == tmp_path
    assert calls["removed"] == []


@pytest.mark.asyncio
async def test_unknown_policy_degrades_to_isolated(tmp_path: Path):
    factory, calls = fake_worktree_manager_factory(tmp_path / "wt-root")
    run_calls: list = []

    def _factory(agent: str, project=None):
        rec = Specialist(
            name=agent, scope="project", is_orchestrator=False,
            system_prompt="", harness="sweave-engine",
        )
        rec.worktree_policy = "teleport"  # bypasses API validation
        return rec

    runner = _runner(tmp_path, run_calls, {}, worktree_manager_factory=factory)
    runner.specialist_factory = _factory  # type: ignore[assignment]
    d = await runner.submit(agent="backend", task="t", project_name="p1")
    terminal = await runner.wait(d.delegation_id, timeout=10)
    assert terminal is not None
    assert len(calls["created"]) == 1
    assert terminal.worktree_owned is True


def test_policy_defaults_and_validation():
    assert DEFAULT_WORKTREE_POLICY == "isolated"
    assert Specialist(name="x").worktree_policy == "isolated"
    assert validate_worktree_policy("inherit") == "inherit"
    for bad in ("", "ISOLATED", "shared", None, 42):
        try:
            validate_worktree_policy(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_seed_override_merge_and_preservation(tmp_path: Path, monkeypatch):
    from pathlib import Path as _Path

    monkeypatch.setattr(_Path, "home", classmethod(lambda cls: tmp_path))
    from sweave.runtime.specialist_store import SpecialistResolver

    r = SpecialistResolver()
    assert r.resolve("reviewer-specialist").worktree_policy == "isolated"
    r.set_seed_worktree_policy("reviewer-specialist", "inherit")
    assert r.resolve("reviewer-specialist").worktree_policy == "inherit"
    # Sibling setters preserve the choice both directions.
    r.set_seed_harness("reviewer-specialist", "opencode")
    assert r.resolve("reviewer-specialist").worktree_policy == "inherit"
    from sweave.runtime.specialist_store import parse_model_ref

    r.set_seed_model("reviewer-specialist", parse_model_ref("opencode/m"))
    assert r.resolve("reviewer-specialist").worktree_policy == "inherit"
    # Clearing restores the default.
    r.set_seed_worktree_policy("reviewer-specialist", None)
    assert r.resolve("reviewer-specialist").worktree_policy == "isolated"
    try:
        r.set_seed_worktree_policy("reviewer-specialist", "teleport")
    except ValueError:
        pass
    else:
        raise AssertionError("accepted unknown policy")
    try:
        r.set_seed_worktree_policy("no-such-seed", "inherit")
    except ValueError:
        pass
    else:
        raise AssertionError("accepted unknown seed")


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


def _client(monkeypatch, tmp_path):
    from pathlib import Path as _Path

    monkeypatch.setattr(_Path, "home", classmethod(lambda cls: tmp_path))
    from sweave.web import state as state_mod

    original_build = state_mod.AppState.build

    @classmethod
    def build_with_stub_agents(cls, config_manager):  # type: ignore[no-untyped-def]
        state = original_build.__func__(cls, config_manager)  # type: ignore[attr-defined]
        state.dynamic_agents_path = tmp_path / "agents.yaml"
        return state

    monkeypatch.setattr(state_mod.AppState, "build", build_with_stub_agents)

    from fastapi.testclient import TestClient

    from sweave.web.server import app

    with TestClient(app) as c:
        yield c


def test_api_policy_crud(monkeypatch, tmp_path: Path):
    for client in _client(monkeypatch, tmp_path):
        r = client.post(
            "/api/specialists",
            json={"name": "policy-probe", "scope": "global",
                  "worktree_policy": "inherit"},
        )
        assert r.status_code == 201, r.text
        assert r.json()["worktree_policy"] == "inherit"

        r = client.put(
            "/api/specialists/policy-probe?scope=global",
            json={"worktree_policy": "none"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["worktree_policy"] == "none"

        r = client.put(
            "/api/specialists/policy-probe?scope=global",
            json={"worktree_policy": "teleport"},
        )
        assert r.status_code == 400

        r = client.post(
            "/api/specialists",
            json={"name": "policy-bad", "scope": "global",
                  "worktree_policy": "teleport"},
        )
        assert r.status_code == 400


def test_api_seed_policy_override(monkeypatch, tmp_path: Path):
    for client in _client(monkeypatch, tmp_path):
        r = client.put(
            "/api/specialists/reviewer-specialist",
            json={"worktree_policy": "inherit"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["worktree_policy"] == "inherit"

        r = client.get("/api/specialists/reviewer-specialist")
        assert r.status_code == 200, r.text
        assert r.json()["worktree_policy"] == "inherit"

        # Unknown policy on seeds is a 400, not a silent default.
        r = client.put(
            "/api/specialists/reviewer-specialist",
            json={"worktree_policy": "teleport"},
        )
        assert r.status_code == 400
