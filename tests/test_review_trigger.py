"""REVIEW Phase 1 step 3: entry-trigger hardening.

The consumer audit found one production gap the endpoint tests
couldn't see: ``EscalationStore.answer/skip/force_timeout`` clear
``needs_attention`` through the injected flagger, and the production
flagger cleared BLINDLY — wiping the flag on a review-owed
delegation before the router's review-aware loop runs (the router
breaks early without restoring it). The endpoint tests pass only
because their store wires no flagger.

The fix: the production flagger shares the single
``_review_owes_promotion`` rule — a question resolving never clears
while the review is unpromoted; only promote clears. These tests
wire the PRODUCTION factory (``make_attention_flagger``) so the
contract holds at the store boundary, not just the router.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from sweave.runtime.delegation_store import (
    Delegation,
    PerProjectDelegationStores,
)
from sweave.web.routers.delegations import (
    _review_owes_promotion,
    make_attention_flagger,
)


def test_review_owes_promotion_rule():
    assert _review_owes_promotion(Delegation(agent="a", task="t", status="review"))
    assert not _review_owes_promotion(
        Delegation(agent="a", task="t", status="running")
    )
    assert not _review_owes_promotion(
        Delegation(agent="a", task="t", status="done")
    )
    assert not _review_owes_promotion(None)


async def _wired_pair(project_dir: Path, tmp_path: Path):
    """Real stores + production flagger + escalation store."""
    from sweave.runtime.escalation import EscalationStore

    stores = PerProjectDelegationStores()
    store = await stores.for_project(project_dir)
    state = SimpleNamespace(delegation_stores=stores)
    esc = EscalationStore(
        base_dir=tmp_path / "esc",
        delegation_flagger=make_attention_flagger(state),
    )
    return store, esc


async def _add(store, **kw) -> Delegation:
    d = Delegation(agent="backend", task="t", project_name="p", **kw)
    await store.add(d)
    return d


@pytest.mark.asyncio
async def test_wired_answer_keeps_flag_while_review_owed(tmp_path: Path):
    """The production gap, pinned: store-level answer with the real
    flagger keeps the flag on a review-owed delegation."""
    project_dir = Path(tempfile.mkdtemp(prefix="sweave-trig-ans-"))
    store, esc = await _wired_pair(project_dir, tmp_path)
    d = await _add(store, status="review", needs_attention=True)
    await esc.create(delegation_id=d.delegation_id, question="q?",
                     options=None, kind="question", audience="human")

    rec = await esc.answer(delegation_id=d.delegation_id, response="yes")
    assert rec is not None and rec["status"] == "answered"
    got = store.get(d.delegation_id)
    assert got is not None and got.needs_attention is True


@pytest.mark.asyncio
async def test_wired_answer_clears_flag_without_review(tmp_path: Path):
    project_dir = Path(tempfile.mkdtemp(prefix="sweave-trig-ans2-"))
    store, esc = await _wired_pair(project_dir, tmp_path)
    d = await _add(store, status="running", needs_attention=True)
    await esc.create(delegation_id=d.delegation_id, question="q?",
                     options=None, kind="question", audience="human")

    await esc.answer(delegation_id=d.delegation_id, response="yes")
    got = store.get(d.delegation_id)
    assert got is not None and got.needs_attention is False


@pytest.mark.asyncio
async def test_wired_skip_keeps_flag_while_review_owed(tmp_path: Path):
    project_dir = Path(tempfile.mkdtemp(prefix="sweave-trig-skip-"))
    store, esc = await _wired_pair(project_dir, tmp_path)
    d = await _add(store, status="review", needs_attention=True)
    await esc.create(delegation_id=d.delegation_id, question="q?",
                     options=None, kind="question", audience="human")

    rec = await esc.skip(delegation_id=d.delegation_id)
    assert rec is not None and rec["status"] == "skipped"
    got = store.get(d.delegation_id)
    assert got is not None and got.needs_attention is True


@pytest.mark.asyncio
async def test_wired_timeout_keeps_flag_while_review_owed(tmp_path: Path):
    project_dir = Path(tempfile.mkdtemp(prefix="sweave-trig-to-"))
    store, esc = await _wired_pair(project_dir, tmp_path)
    d = await _add(store, status="review", needs_attention=True)
    await esc.create(delegation_id=d.delegation_id, question="q?",
                     options=None, kind="question", audience="human")

    rec = await esc.force_timeout(delegation_id=d.delegation_id)
    assert rec is not None
    got = store.get(d.delegation_id)
    assert got is not None and got.needs_attention is True


@pytest.mark.asyncio
async def test_wired_create_sets_flag(tmp_path: Path):
    """The True path is unchanged: a new question raises attention."""
    project_dir = Path(tempfile.mkdtemp(prefix="sweave-trig-create-"))
    store, esc = await _wired_pair(project_dir, tmp_path)
    d = await _add(store, status="running", needs_attention=False)

    await esc.create(delegation_id=d.delegation_id, question="q?",
                     options=None, kind="question", audience="human")
    got = store.get(d.delegation_id)
    assert got is not None and got.needs_attention is True
