"""Delegation records (M1.prep minimal shape, M1.1 will extend).

A :class:`Delegation` is one *implementation work* request to a specialist
agent. The minimal record in M1.prep captures enough state to:

* hand the client a pollable id from ``POST /api/v2/tasks``
* record the status transitions in the trace log
* surface the result via ``GET /api/delegations/{id}``

M1.1 widens this with ``worktree_path``, ``branch``, ``pr_url``,
``parent_task_id``, and a ``manifest`` self-report. The ``schema_version``
field exists from day one so the M1.1 loader can read prep-era records
without guessing.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


SCHEMA_VERSION = 1

# Status transitions (closed set; JobRunner enforces them):
#   queued   -> running
#   running  -> review | done | failed
#   review   -> done | failed
#   done     (terminal)
#   failed   (terminal)
VALID_STATUSES = {"queued", "running", "review", "done", "failed"}


def _now() -> datetime:
    return datetime.now()


@dataclass
class Delegation:
    """One unit of work delegated to a specialist agent.

    schema_version is written on every record. Bump it whenever a
    non-additive change to the field set is made and provide a
    forward-compatible loader in the same module.
    """

    schema_version: int = SCHEMA_VERSION
    delegation_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    agent: str = ""
    model: str = ""
    task: str = ""
    status: str = "queued"
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    parent_session_id: str | None = None
    project_name: str | None = None
    output: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # serialise datetimes
        for k in ("created_at", "updated_at", "started_at", "completed_at"):
            if d[k] is not None:
                d[k] = d[k].isoformat()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Delegation":
        d = dict(data)
        for k in ("created_at", "updated_at", "started_at", "completed_at"):
            v = d.get(k)
            if isinstance(v, str):
                d[k] = datetime.fromisoformat(v)
        # schema_version migration: prep-era records (1) load as-is. Higher
        # versions would be migrated here; M1.1 will add the first one.
        d.setdefault("schema_version", 1)
        # Drop unknown fields silently so a future version with extra
        # fields doesn't poison prep-era code.
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


class DelegationStore:
    """In-memory store of :class:`Delegation` records.

    M1.prep keeps records in process memory only. M1.1 (Record split) will
    add disk persistence (per-project JSON files, same atomic-write
    contract as ProjectManager). The :class:`JobRunner` reads/writes
    through this store.

    A per-store :class:`asyncio.Lock` serialises status transitions; the
    dict-level reads (``get``) are lock-free because Python dict reads of
    a stable key are safe under single-writer-multiple-reader and we
    always copy the record out before mutating.
    """

    def __init__(self) -> None:
        self._records: dict[str, Delegation] = {}
        self._lock = asyncio.Lock()

    async def add(self, delegation: Delegation) -> None:
        async with self._lock:
            self._records[delegation.delegation_id] = delegation

    def get(self, delegation_id: str) -> Delegation | None:
        return self._records.get(delegation_id)

    def list(self) -> list[Delegation]:
        return list(self._records.values())

    async def update(self, delegation_id: str, **fields: Any) -> Delegation | None:
        """Apply *fields* to the matching record. ``updated_at`` is set automatically.

        Returns the updated record, or None if no such id. Raises ``ValueError``
        on an unknown status transition (the JobRunner enforces the closed set).
        """
        async with self._lock:
            rec = self._records.get(delegation_id)
            if rec is None:
                return None
            if "status" in fields and fields["status"] not in VALID_STATUSES:
                raise ValueError(f"invalid status: {fields['status']!r}")
            for k, v in fields.items():
                if hasattr(rec, k):
                    setattr(rec, k, v)
            rec.updated_at = _now()
            return rec
