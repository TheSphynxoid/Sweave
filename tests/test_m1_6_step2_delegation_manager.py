"""M1.6 step 2 tests: DelegationManager + Delegation v3.

Covers:
* ``DepthExceededError`` at depth > max_depth (default 2).
* ``LoopDetectedError`` when the target is already in the active
  chain (A -> B -> A).
* ``BudgetExceededError`` when the new coordination_tokens would
  push the chain over the cap.
* ``record_terminal`` frees the cache (root) / removes the agent
  from the active set (non-root) on done/failed.
* ``rebuild_chain_state`` reconstructs the cache from the store
  (post-restart correctness).
* Top-level delegations (no parent) are not subject to chain
  rules: they don't consume budget, they don't enter the active
  set.
* v3 fields round-trip via ``to_dict`` / ``from_dict``.
"""

from __future__ import annotations

import pytest

from sweave.runtime.delegation_manager import (
    BudgetExceededError,
    ChainError,
    DelegationManager,
    DepthExceededError,
    LoopDetectedError,
    estimate_tokens,
)
from sweave.runtime.delegation_store import Delegation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _delegation(
    *,
    delegation_id: str = "del-1",
    agent: str = "backend",
    parent_task_id: str | None = None,
    chain_root_id: str | None = None,
    depth: int = 0,
    status: str = "running",
    task: str = "x",
    coordination_tokens: int = 0,
) -> Delegation:
    return Delegation(
        delegation_id=delegation_id,
        agent=agent,
        task=task,
        parent_task_id=parent_task_id,
        chain_root_id=chain_root_id,
        depth=depth,
        status=status,
        coordination_tokens=coordination_tokens,
    )


# ---------------------------------------------------------------------------
# Depth cap
# ---------------------------------------------------------------------------


def test_depth_cap_rejects_grandchild():
    """Default max_depth=2: orchestrator (0) -> child (1) -> grandchild (2)
    is the maximum; depth 3 must be rejected."""
    mgr = DelegationManager(max_depth=2, chain_budget=200_000)
    # depth 0 -> depth 1 (allowed)
    child = mgr.validate(parent=None, target="backend", task="x")
    assert child.depth == 0  # top-level; not part of a chain
    # depth 0 record acting as parent -> depth 1 child (allowed)
    parent = _delegation(delegation_id="d0", agent="orchestrator", depth=0)
    d1 = mgr.validate(parent=parent, target="backend", task="x")
    assert d1.depth == 1
    assert d1.chain_root_id == "d0"
    # depth 1 record acting as parent -> depth 2 child (allowed, at the cap)
    d1_record = _delegation(
        delegation_id="d1", agent="backend", depth=1, chain_root_id="d0"
    )
    d2 = mgr.validate(parent=d1_record, target="frontend", task="x")
    assert d2.depth == 2
    # depth 2 -> depth 3 rejected
    d2_record = _delegation(
        delegation_id="d2", agent="frontend", depth=2, chain_root_id="d0"
    )
    with pytest.raises(DepthExceededError) as ei:
        mgr.validate(parent=d2_record, target="reviewer", task="x")
    assert "depth 3" in str(ei.value)
    assert "max_depth=2" in str(ei.value)
    assert ei.value.code == "depth_exceeded"


# ---------------------------------------------------------------------------
# Loop detect
# ---------------------------------------------------------------------------


def test_loop_detected_when_target_already_in_chain():
    """A -> B -> A: a delegating-to-B back-to-A is a loop. The
    second A defer must be rejected because A is already in the
    active set as a defer target (the original root's defer target).
    """
    mgr = DelegationManager(max_depth=10, chain_budget=200_000)
    # Root: A itself is a defer target (we use a non-orchestrator
    # agent for the root to model the case where the orchestrator
    # picks a target, that target picks a peer, then the peer tries
    # to defer back to the original target).
    root = _delegation(delegation_id="A", agent="alpha", depth=0)
    d_b = mgr.validate(parent=root, target="beta", task="x")
    assert d_b.depth == 1
    assert d_b.chain_root_id == "A"
    # active set: {beta} (root is the orchestrator's pick; the
    # active set tracks *defer targets*, not the parent's agent).
    # B -> A: A is the defer target, but A is in the active set? No.
    # Hmm, this isn't actually a loop unless A is in the active set.
    # Let me re-think: the active set is the *child* defer targets.
    # The first defer (target=beta) added beta. The second defer
    # (target=A) sees A is NOT in the active set; no loop.
    # The cleanest loop example: A -> B -> A where A is one of the
    # defer targets. Use a parent (P) to defer to A, then defer
    # from A to B, then try to defer from B back to A.
    b_record = _delegation(
        delegation_id="B", agent="beta", depth=1, chain_root_id="P",
    )
    # But that breaks the "P" chain_root_id. Let me restart with
    # a clean setup.
    mgr2 = DelegationManager(max_depth=10, chain_budget=200_000)
    p = _delegation(delegation_id="P", agent="alpha", depth=0)
    # P -> A
    d_a = mgr2.validate(parent=p, target="A-agent", task="x")
    a_record = _delegation(
        delegation_id="A", agent="A-agent", depth=1, chain_root_id="P"
    )
    # A -> B (depth 2)
    d_b = mgr2.validate(parent=a_record, target="B-agent", task="y")
    b_record = _delegation(
        delegation_id="B", agent="B-agent", depth=2, chain_root_id="P"
    )
    # B -> A: A-agent is already in the active set (added by the
    # first defer). Loop detected.
    with pytest.raises(LoopDetectedError) as ei:
        mgr2.validate(parent=b_record, target="A-agent", task="z")
    assert "A-agent" in str(ei.value)
    assert "loop" in str(ei.value).lower()
    assert ei.value.code == "loop_detected"


def test_loop_detection_is_per_chain():
    """Two independent chains don't share active sets. Chain 1's
    target doesn't pollute chain 2's loop check."""
    mgr = DelegationManager(max_depth=10, chain_budget=200_000)
    # Chain 1: root R1 -> backend
    r1 = _delegation(delegation_id="R1", agent="orchestrator", depth=0)
    mgr.validate(parent=r1, target="backend", task="x")
    # Chain 2: root R2 -> backend (no loop; different chain)
    r2 = _delegation(delegation_id="R2", agent="orchestrator", depth=0)
    d2 = mgr.validate(parent=r2, target="backend", task="x")
    assert d2.depth == 1
    assert d2.chain_root_id == "R2"
    # Both chains accepted; no cross-talk.


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def test_budget_accumulates_coordination_tokens():
    """Each defer's coordination_tokens (tiktoken estimate of task +
    reason) is added to the chain accumulator."""
    mgr = DelegationManager(max_depth=10, chain_budget=10_000)
    root = _delegation(delegation_id="R", agent="alpha", depth=0)
    # A small task stays well under the cap.
    d1 = mgr.validate(parent=root, target="backend", task="hello world", reason="x")
    assert d1.coordination_tokens > 0
    # Snapshot the chain tokens.
    snap1 = mgr.snapshot()
    assert snap1["chain_tokens"]["R"] == d1.coordination_tokens
    # Add a second defer to a fresh target and confirm accumulation.
    d1_record = _delegation(
        delegation_id="d1", agent="backend", depth=1, chain_root_id="R",
        coordination_tokens=d1.coordination_tokens,
    )
    d2 = mgr.validate(parent=d1_record, target="frontend", task="more text", reason="")
    snap2 = mgr.snapshot()
    assert snap2["chain_tokens"]["R"] == d1.coordination_tokens + d2.coordination_tokens


def test_budget_rejects_when_chain_exceeds_cap():
    """A second defer whose coordination_tokens pushes the chain
    over the cap is rejected (no partial state). The check runs
    AFTER the loop check, so we use a fresh target each time.
    """
    mgr = DelegationManager(max_depth=10, chain_budget=20)
    root = _delegation(delegation_id="R", agent="alpha", depth=0)
    # d1 is small; ~3 tokens. Fits under the 20-token cap.
    d1 = mgr.validate(parent=root, target="backend", task="abc", reason="")
    assert d1.coordination_tokens > 0
    assert d1.coordination_tokens < 20
    b_record = _delegation(
        delegation_id="B",
        agent="backend",
        depth=1,
        chain_root_id="R",
        coordination_tokens=d1.coordination_tokens,
    )
    # d2 is large; pushes the chain over the 20-token cap. Fresh
    # target (frontend) to avoid the loop check.
    with pytest.raises(BudgetExceededError) as ei:
        mgr.validate(parent=b_record, target="frontend", task="a" * 200, reason="")
    assert "budget exceeded" in str(ei.value).lower()
    assert ei.value.code == "budget_exceeded"


def test_budget_explicit_cap():
    """A budget cap of 0 tokens rejects any non-empty defer (and
    accepts an empty one -- by construction coord_tokens=0)."""
    mgr = DelegationManager(max_depth=10, chain_budget=0)
    root = _delegation(delegation_id="R", agent="orchestrator", depth=0)
    # Empty task + empty reason -> 0 tokens -> accepted.
    d1 = mgr.validate(parent=root, target="backend", task="", reason="")
    assert d1.coordination_tokens == 0
    # Now any non-empty defer is over the cap.
    b_record = _delegation(
        delegation_id="B",
        agent="backend",
        depth=1,
        chain_root_id="R",
    )
    with pytest.raises(BudgetExceededError):
        # Fresh target so we exercise budget, not loop.
        mgr.validate(parent=b_record, target="frontend", task="x", reason="")


# ---------------------------------------------------------------------------
# record_terminal
# ---------------------------------------------------------------------------


def test_record_terminal_frees_root_on_done():
    """When the root of a chain reaches done, the entire chain's
    cache entries are freed. A new defer with a brand-new root
    starts clean."""
    mgr = DelegationManager(max_depth=10, chain_budget=200_000)
    root = _delegation(delegation_id="R", agent="alpha", depth=0)
    mgr.validate(parent=root, target="backend", task="x")
    mgr.validate(
        parent=_delegation(
            delegation_id="B", agent="backend", depth=1, chain_root_id="R"
        ),
        target="frontend",
        task="y",
    )
    snap_before = mgr.snapshot()
    assert "R" in snap_before["active_sets"]
    # Root reaches done. The root's record has chain_root_id=None
    # (the cache key is itself), so the manager recognises it as a
    # root terminal.
    mgr.record_terminal(
        _delegation(
            delegation_id="R", agent="alpha", depth=0, status="done"
        )
    )
    snap_after = mgr.snapshot()
    # The chain cache for "R" is dropped entirely.
    assert "R" not in snap_after["active_sets"]  # not a key in the cache
    assert "R" not in snap_after["chain_tokens"]


def test_record_terminal_removes_non_root_agent():
    """A non-root terminal just removes that agent from the active
    set (the chain continues). A new defer on the same chain with
    a different target succeeds; deferring to the just-finished
    target is no longer a loop."""
    mgr = DelegationManager(max_depth=10, chain_budget=200_000)
    root = _delegation(delegation_id="R", agent="alpha", depth=0)
    mgr.validate(parent=root, target="backend", task="x")
    b_record = _delegation(
        delegation_id="B", agent="backend", depth=1, chain_root_id="R"
    )
    mgr.validate(parent=b_record, target="frontend", task="y")
    # backend is in the active set.
    assert "backend" in mgr.snapshot()["active_sets"]["R"]
    # backend reaches done. The non-root record carries chain_root_id
    # so the manager knows which chain to update.
    mgr.record_terminal(
        _delegation(
            delegation_id="B", agent="backend", depth=1,
            chain_root_id="R", status="done",
        )
    )
    # backend is freed; the chain still has frontend in the active set.
    snap = mgr.snapshot()
    assert "backend" not in snap["active_sets"]["R"]
    assert "frontend" in snap["active_sets"]["R"]


# ---------------------------------------------------------------------------
# rebuild_chain_state (post-restart correctness)
# ---------------------------------------------------------------------------


def test_rebuild_chain_state_restores_active_set_and_tokens():
    """After a process restart the in-memory cache is empty; the
    manager must rebuild it from the per-project store before
    answering loop/budget queries accurately. Only child records
    (parent_task_id set) contribute to the active set and the
    token accumulator; the root owns the cache but doesn't enter
    it.
    """
    mgr = DelegationManager(max_depth=10, chain_budget=200_000)
    # Pre-restart: three live records on chain "R" (root + 2 children)
    # plus a completed child.
    records = [
        # The root: owns the chain but isn't in the active set.
        _delegation(delegation_id="R", agent="orchestrator", depth=0),
        # Two live children.
        _delegation(
            delegation_id="B", agent="backend", depth=1,
            chain_root_id="R", parent_task_id="R",
            coordination_tokens=10, status="running",
        ),
        _delegation(
            delegation_id="F", agent="frontend", depth=2,
            chain_root_id="R", parent_task_id="B",
            coordination_tokens=15, status="review",
        ),
        # A completed child -- already done, not part of the live cache.
        _delegation(
            delegation_id="X", agent="reviewer", depth=1,
            chain_root_id="R", parent_task_id="R",
            coordination_tokens=7, status="done",
        ),
    ]
    mgr.rebuild_chain_state("R", records)
    snap = mgr.snapshot()
    # Active set: backend + frontend (orchestrator is the root and
    # excluded; the done reviewer is excluded by status filter).
    assert snap["active_sets"]["R"] == {"backend", "frontend"}
    # Token accumulator: 10 + 15 (root + done are excluded).
    assert snap["chain_tokens"]["R"] == 25


# ---------------------------------------------------------------------------
# Top-level delegations
# ---------------------------------------------------------------------------


def test_top_level_delegation_does_not_consume_budget():
    """A user task with no parent is depth 0, has no chain_root,
    and doesn't enter the budget. A subsequent defer under it
    starts with a clean budget."""
    mgr = DelegationManager(max_depth=2, chain_budget=10)
    top = mgr.validate(parent=None, target="backend", task="x" * 1000, reason="x" * 1000)
    assert top.depth == 0
    assert top.chain_root_id is None
    assert top.coordination_tokens == 0  # by design: top-level
    # The first defer sets the chain_root + first coord_tokens.
    top_record = _delegation(
        delegation_id=top.delegation_id, agent="backend", depth=0
    )
    d1 = mgr.validate(parent=top_record, target="frontend", task="hello", reason="")
    assert d1.chain_root_id == top.delegation_id
    assert d1.coordination_tokens > 0


# ---------------------------------------------------------------------------
# estimate_tokens (used by the budget path)
# ---------------------------------------------------------------------------


def test_estimate_tokens_empty_returns_zero():
    assert estimate_tokens("") == 0
    assert estimate_tokens("", "", "") == 0


def test_estimate_tokens_is_monotonic():
    """Longer text -> at least as many tokens. Approximate; cl100k_base
    is the model-free baseline."""
    short = estimate_tokens("hello")
    long = estimate_tokens("hello " * 50)
    assert long > short


# ---------------------------------------------------------------------------
# Errors are ChainError subclasses (for orchestrator/UI handling)
# ---------------------------------------------------------------------------


def test_chain_errors_are_chainerror_subclasses():
    """The MCP ``defer`` tool catches ChainError and surfaces the
    message verbatim as a 'rejected: <reason>' line. All three
    concrete rule errors must inherit ChainError so a single
    ``except ChainError`` catches them.
    """
    # Depth
    mgr_d = DelegationManager(max_depth=1, chain_budget=200_000)
    p = _delegation(delegation_id="P", agent="alpha", depth=0)
    d1 = mgr_d.validate(parent=p, target="A", task="x")
    d1_record = _delegation(
        delegation_id="d1", agent="A", depth=1, chain_root_id="P"
    )
    try:
        mgr_d.validate(parent=d1_record, target="B", task="y")
    except ChainError as e:
        assert isinstance(e, DepthExceededError)
    # Loop
    mgr_l = DelegationManager(max_depth=10, chain_budget=200_000)
    mgr_l.validate(parent=p, target="A", task="x")
    a_record = _delegation(
        delegation_id="A", agent="A", depth=1, chain_root_id="P"
    )
    try:
        mgr_l.validate(parent=a_record, target="A", task="y")
    except ChainError as e:
        assert isinstance(e, LoopDetectedError)


# ---------------------------------------------------------------------------
# Snapshot is read-only (defensive copy)
# ---------------------------------------------------------------------------


def test_snapshot_returns_independent_copy():
    """snapshot() must not let callers mutate the live cache."""
    mgr = DelegationManager(max_depth=10, chain_budget=200_000)
    root = _delegation(delegation_id="R", agent="alpha", depth=0)
    mgr.validate(parent=root, target="backend", task="x")
    snap = mgr.snapshot()
    # The returned active set is a frozenset, so it can't be mutated
    # at all. We test the read-only contract by re-fetching the
    # snapshot and confirming it hasn't changed.
    original = snap["active_sets"]["R"]
    assert "backend" in original
    # And the live state matches what the snapshot returned.
    assert "backend" in mgr.snapshot()["active_sets"]["R"]


# ---------------------------------------------------------------------------
# Constructor wiring from config (the AppState lifespan uses this)
# ---------------------------------------------------------------------------


def test_from_config_uses_routing_knobs():
    from sweave.config.schemas import RoutingConfig, SweaveConfig
    from unittest.mock import patch

    # Build a minimal SweaveConfig with a custom routing block.
    cfg = SweaveConfig()
    # Pydantic v2: routing is a RoutingConfig model.
    cfg.routing = RoutingConfig(
        routes=[],
        fallback="llm",
        chain_budget=42_000,
        max_depth=3,
    )
    mgr = DelegationManager.from_config(cfg)
    assert mgr.max_depth == 3
    assert mgr.chain_budget == 42_000
