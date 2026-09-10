"""EscalationStore ``delegation_flagger`` contract (M1.12 fix 2026-09-10).

``create()`` documented "sets the asking delegation's needs_attention
flag" since M1.9, but the flip lived only in the ask_human router —
the permission bridge + stall branch create records directly, so the
flag stayed False and every answer surface keyed on it stayed dark
(the stuck-reviewer incident, escalation ``esc-47fdd816e8``). The
flag lifecycle now lives in the store, injected as a callback.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sweave.runtime.escalation import EscalationStore


def _store(tmp_path: Path, flagger) -> EscalationStore:
    return EscalationStore(
        base_dir=tmp_path,
        timeout_seconds=None,
        event_bus=None,
        delegation_flagger=flagger,
    )


def test_create_flips_needs_attention_on(tmp_path: Path) -> None:
    calls: list[tuple[str, bool]] = []
    store = _store(tmp_path, lambda did, v: calls.append((did, v)))
    asyncio.run(
        store.create(
            delegation_id="d1",
            question="Permission required: ...",
            options=["allow once", "always allow", "deny"],
            kind="permission",
            audience="human",
            timeout_seconds=None,
        )
    )
    assert calls == [("d1", True)]


async def _resolve_cycle(store: EscalationStore, calls: list) -> None:
    await store.create(delegation_id="d2", question="q")
    await store.answer(delegation_id="d2", response="allow once")
    await store.create(delegation_id="d3", question="q")
    await store.skip(delegation_id="d3")
    await store.create(delegation_id="d4", question="q", timeout_seconds=50)
    await store.force_timeout(delegation_id="d4")


def test_every_resolution_path_flips_needs_attention_off(tmp_path: Path) -> None:
    calls: list[tuple[str, bool]] = []
    store = _store(tmp_path, lambda did, v: calls.append((did, v)))
    asyncio.run(_resolve_cycle(store, calls))
    assert ("d2", True) in calls and ("d2", False) in calls
    assert ("d3", True) in calls and ("d3", False) in calls
    assert ("d4", True) in calls and ("d4", False) in calls


def test_no_flagger_still_works(tmp_path: Path) -> None:
    store = EscalationStore(
        base_dir=tmp_path, timeout_seconds=None, event_bus=None
    )
    rec = asyncio.run(store.create(delegation_id="d5", question="q"))
    assert rec["status"] == "pending"


def test_flagger_failure_never_breaks_the_lifecycle(tmp_path: Path) -> None:
    def boom(did: str, v: bool) -> None:
        raise RuntimeError("delegation store unreachable")

    store = _store(tmp_path, boom)
    rec = asyncio.run(store.create(delegation_id="d6", question="q"))
    assert rec["status"] == "pending"
    answered = asyncio.run(store.answer(delegation_id="d6", response="deny"))
    assert answered is not None and answered["status"] == "answered"
