"""Delegation records.

A :class:`Delegation` is one *implementation work* request to a specialist
agent. The state machine (queued → running → review → done/failed) and the
core identification fields are stable across schema versions; the
:data:`SCHEMA_VERSION` on every record is what lets us widen the field
set without losing old data.

Schema history
--------------
* **v1** (M1.prep): identity + status + timings + parent_session_id +
  project_name + output/error. In-memory only; no worktree / PR / chain
  fields.
* **v2** (M1.1): adds ``worktree_path``, ``branch``, ``pr_url``,
  ``parent_task_id`` (deferral chain — None means orchestrator-initiated),
  and ``manifest`` (a structured self-report from the specialist). Per-
  project disk persistence lives one level up (M1.1 step 2) — this module
  is just the record + the store API.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, TypedDict


SCHEMA_VERSION = 2
SCHEMA_VERSION_PREP = 1  # M1.prep records

# Status transitions (closed set; JobRunner enforces them):
#   queued   -> running
#   running  -> review | done | failed
#   review   -> done | failed
#   done     (terminal)
#   failed   (terminal)
VALID_STATUSES = {"queued", "running", "review", "done", "failed"}


def _now() -> datetime:
    return datetime.now()


class Manifest(TypedDict, total=False):
    """Specialist self-report attached to a Delegation (M1.1).

    All fields are optional at write time. The specialist is expected to
    produce this via a prompt convention; generation itself is M1.6
    scope — M1.1 only stores what it's given.

    * ``files_touched`` — list of repo-relative paths the specialist
      created or modified.
    * ``intent`` — free-text summary of what the work was supposed to do
      (for the R2 resolution skill's diff3 + manifest payload).
    * ``confidence`` — 0.0–1.0 self-rated confidence in the result. None
      means the specialist declined to rate.
    * ``breaking_change`` — True if the work should be reviewed extra
      carefully (API rename, schema migration, etc.). Drives the R2
      "needs-review" mediation head.
    """
    files_touched: list[str]
    intent: str
    confidence: float | None
    breaking_change: bool


@dataclass
class Delegation:
    """One unit of work delegated to a specialist agent.

    ``schema_version`` is written on every record. The :meth:`from_dict`
    loader migrates older records forward (v1 → v2) so a project on disk
    after the M1.1 upgrade reads cleanly with the new fields present.
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
    # v2 fields (M1.1). All optional at write time; filled as the
    # delegation progresses (worktree created → worktree_path; PR opened
    # → pr_url; specialist reports → manifest).
    worktree_path: str | None = None
    branch: str | None = None
    pr_url: str | None = None
    parent_task_id: str | None = None  # deferral chain; None = orchestrator-initiated
    manifest: Manifest | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("created_at", "updated_at", "started_at", "completed_at"):
            if d[k] is not None:
                d[k] = d[k].isoformat()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Delegation":
        d = dict(data)
        # v1 -> v2 migration: M1.prep records are missing every v2 field.
        # Their values are unknown (we never persisted them), so all
        # default to None. The trace log + status fields are enough to
        # reconstruct what happened; the missing worktree/branch/PR are
        # only meaningful for "review" or later delegations, and any such
        # v1 record is by definition not in that state (the v1 -> v2 bump
        # ships before any specialist ever wrote those fields).
        schema_version = int(d.get("schema_version") or SCHEMA_VERSION_PREP)
        if schema_version < SCHEMA_VERSION:
            d = _migrate_v1_to_v2(d)
        # Always normalise to the current version on the record. The
        # migration step brings the field set up; this stamps the
        # version so the in-memory object matches what a fresh v2 record
        # looks like. (Roundtripping a v1 record should produce a v2.)
        d["schema_version"] = SCHEMA_VERSION
        # Datetime parsing
        for k in ("created_at", "updated_at", "started_at", "completed_at"):
            v = d.get(k)
            if isinstance(v, str):
                d[k] = datetime.fromisoformat(v)
        # Drop unknown fields silently so a future version with extra
        # fields doesn't poison current code.
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


def _migrate_v1_to_v2(d: dict[str, Any]) -> dict[str, Any]:
    """Bring a v1 record forward to the v2 field set.

    All v2-only fields default to ``None``; the v1 record didn't carry
    them, so we don't fabricate. ``parent_task_id`` was not in v1, but
    M1.1's deferral chain is a *new* mechanism — old orchestrator-
    initiated delegations (the only kind v1 ever produced) are by
    definition ``parent_task_id=None``.
    """
    d.setdefault("worktree_path", None)
    d.setdefault("branch", None)
    d.setdefault("pr_url", None)
    d.setdefault("parent_task_id", None)
    d.setdefault("manifest", None)
    return d


class DelegationStore:
    """In-memory store of :class:`Delegation` records.

    M1.prep keeps records in process memory only. M1.1 step 2 will add
    per-project disk persistence (per-project JSON file, atomic writes
    via :func:`runtime.locking.atomic_write_json_sync`, write-through).
    This class is the single source of truth for the store API; the
    :class:`JobRunner` reads/writes through it.

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
