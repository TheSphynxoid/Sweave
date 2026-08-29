"""Ephemeral sub-agent runs (M1.1 step 3).

A :class:`SubAgentRun` is the record for a *read-only, disposable*
specialist task (Polly's ``/investigate`` pattern, R2's
``SubAgentRun`` consumer). Sub-agent runs are intentionally NOT
persisted to disk and are NOT subject to schema migrations: they
die with the process. M1.1 only delivers the type + store + the
lifecycle primitives; the API endpoints arrive in step 4.

What is ephemeral here
----------------------
* **Identity.** A ``run_id`` (UUID) is enough; nothing in v2 (deferral
  chains, manifests) references a sub-agent run.
* **Lifetime.** The store caps its size at :data:`MAX_RUNS`; the
  oldest entry is dropped on every insert once the cap is hit.
  Read-heavy traffic (e.g. an investigation that triggers dozens of
  probes) does not grow memory unbounded.
* **Scope.** :class:`SubAgentRunStore` is per-process. There is no
  per-project split (R2's ``/investigate`` runs against the active
  project, but the *ephemeral* nature of these records means a
  per-project split adds bookkeeping without persistence benefit).
"""

from __future__ import annotations

import asyncio
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


MAX_RUNS = 500

# Closed sets (kept as Literal types so static checkers catch typos).
SubAgentPurpose = Literal["explore", "investigate", "custom"]
SubAgentStatus = Literal["running", "done", "failed"]


def _now() -> datetime:
    return datetime.now()


@dataclass
class SubAgentRun:
    """One ephemeral sub-agent run.

    Distinct from :class:`~sweave.runtime.delegation_store.Delegation`:
    no worktree, no PR, no deferral chain, no manifest. Lives only as
    long as the server does.
    """

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    agent: str = ""
    purpose: str = "custom"  # one of SubAgentPurpose
    status: str = "running"  # one of SubAgentStatus
    parent_session_id: str | None = None
    project_name: str | None = None
    started_at: datetime = field(default_factory=_now)
    finished_at: datetime | None = None
    output_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "run_id": self.run_id,
            "agent": self.agent,
            "purpose": self.purpose,
            "status": self.status,
            "parent_session_id": self.parent_session_id,
            "project_name": self.project_name,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "output_summary": self.output_summary,
        }
        return d


class SubAgentRunStore:
    """FIFO-capped in-memory store of :class:`SubAgentRun` records.

    Order is by insertion (which equals ``started_at`` order in
    practice). The cap is enforced on every insert; once :data:`MAX_RUNS`
    is hit, the oldest entry is dropped. The cap is generous
    (500) because each entry is a few hundred bytes.

    Concurrency: a single :class:`asyncio.Lock` serialises mutations.
    Reads (``get``, ``list``) are lock-free against the OrderedDict
    because CPython's GIL makes short reads atomic, and we never
    mutate the dict structure without the lock held.
    """

    def __init__(self, max_runs: int = MAX_RUNS) -> None:
        if max_runs <= 0:
            raise ValueError("max_runs must be positive")
        self._records: "OrderedDict[str, SubAgentRun]" = OrderedDict()
        self._max = max_runs
        self._lock = asyncio.Lock()

    @property
    def max_runs(self) -> int:
        return self._max

    @property
    def size(self) -> int:
        return len(self._records)

    async def add(self, run: SubAgentRun) -> None:
        async with self._lock:
            self._records[run.run_id] = run
            # Cap: drop oldest (FIFO) until we're under the limit.
            while len(self._records) > self._max:
                self._records.popitem(last=False)

    def get(self, run_id: str) -> SubAgentRun | None:
        return self._records.get(run_id)

    def list(self) -> list[SubAgentRun]:
        # Newest first; OrderedDict preserves insertion order so we
        # reverse the natural FIFO view.
        return list(reversed(self._records.values()))

    async def update(self, run_id: str, **fields: Any) -> SubAgentRun | None:
        async with self._lock:
            rec = self._records.get(run_id)
            if rec is None:
                return None
            if "status" in fields and fields["status"] not in ("running", "done", "failed"):
                raise ValueError(f"invalid status: {fields['status']!r}")
            for k, v in fields.items():
                if hasattr(rec, k):
                    setattr(rec, k, v)
            return rec

    def clear(self) -> None:
        """Drop all records. For tests."""
        self._records.clear()
