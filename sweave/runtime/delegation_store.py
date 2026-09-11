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
* **v3** (M1.6): adds ``depth`` (chain depth; 0 for orchestrator's
  own delegation, +1 per defer), ``chain_root_id`` (the root of the
  deferral chain; used for budget accumulation + parent gating), and
  ``coordination_tokens`` (tiktoken estimate of the orchestrator turns
  + defer payloads + result summaries in this delegation; specialist
  internal work is NOT counted). ``from_dict`` migrates v2 records
  forward (defaults fill in).
* **v4** (M1.7): adds ``kind`` (``"task"`` implementation delegation
  vs ``"chat"`` orchestrator conversation turn; pre-M1.7 records
  default to ``"task"``).
* **v5** (M1.9): adds ``needs_attention`` (an ``ask_human`` call is
  currently pending for this delegation; pre-M1.9 records default to
  False).
* **v6** (M1.13): adds ``archived`` / ``archived_at`` (ARCHIVE-not-
  delete sub-state; pre-M1.13 records default to False / None).
* **v7** (M2.0): adds ``estimate`` (caller-supplied
  ``{tokens, seconds}`` or None; record only — no enforcement, no
  calibration; pre-M2.0 records default to None).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from sweave.runtime.locking import atomic_write_json

logger = logging.getLogger(__name__)


SCHEMA_VERSION = 7
SCHEMA_VERSION_PREP = 1  # M1.prep records
SCHEMA_VERSION_V2 = 2  # M1.1 records
SCHEMA_VERSION_V3 = 3  # M1.6 records
SCHEMA_VERSION_V4 = 4  # M1.7 records (chat kind)
SCHEMA_VERSION_V5 = 5  # M1.9 records (needs_attention)
SCHEMA_VERSION_V6 = 6  # M1.13 records (archived / archived_at)

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


class Estimate(TypedDict, total=False):
    """Caller-supplied cost estimate attached to a Delegation (M2.0).

    Both keys optional at write time; absent estimate (None) means
    "no estimate supplied". Record ONLY: nothing enforces or
    calibrates these numbers in M2.0 (calibration is M2.5), and any
    caller-supplied non-negative numbers are accepted — estimate
    gaming is noted, not solved.

    * ``tokens`` — estimated total tokens for the delegation.
    * ``seconds`` — estimated wall-clock seconds for the delegation.
    """

    tokens: int
    seconds: float


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
    # v3 fields (M1.6). All default to sensible values; v2 records
    # load with depth=0 / chain_root_id=None / coordination_tokens=0.
    depth: int = 0  # 0 = orchestrator; +1 per defer
    chain_root_id: str | None = None  # the root of the deferral chain
    coordination_tokens: int = 0  # tiktoken estimate of coordination traffic
    # M1.7 step 2: chat-turn kind. "task" is the implementation
    # delegation (M1.1-M1.6); "chat" is an orchestrator conversation
    # turn. Additive; from_dict falls back to "task" when the field
    # is absent (pre-M1.7 v3 records).
    kind: str = "task"  # "task" | "chat"
    # M1.9 step 3: ask_human escalation lane. True iff an
    # ``ask_human`` call is currently pending for this delegation.
    # The Children tab surfaces needs-attention delegations in the
    # top lane with an inline answer button. The EscalationStore is
    # the source of truth for the question / options / answer; this
    # flag is the cheap read-side indicator the renderer branches on.
    needs_attention: bool = False
    # M1.13 cleanup (ruling 2026-09-11): archive sub-state. ARCHIVE,
    # never delete — records referencing dead projects / deleted
    # sessions (or cascaded on project/session delete) keep their
    # full status + stats; ``archived`` just hides them from the
    # default Children listing while the aggregate surface keeps
    # counting them. ``archived_at`` is the archive timestamp.
    archived: bool = False
    archived_at: datetime | None = None
    # M2.0: caller-supplied estimate. Nullable, no behavior change:
    # task delegations may carry {tokens, seconds} from submit; chat
    # turns never do (non-goal). The estimate-vs-actual projection
    # (detail view) joins this against trace `tokens_used` events +
    # created->completed wall time.
    estimate: Estimate | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("created_at", "updated_at", "started_at", "completed_at", "archived_at"):
            if d[k] is not None:
                d[k] = d[k].isoformat()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Delegation":
        d = dict(data)
        # v1 -> v2 -> v3 -> v4 migration chain. v1 (M1.prep) is
        # missing every v2 field; v2 (M1.1) is missing every v3
        # field; v3 (M1.6) is missing the v4 ``kind`` field. The
        # trace log + status fields are enough to reconstruct what
        # happened; the missing worktree/branch/PR are only meaningful
        # for "review" or later delegations, and any such v1 record is
        # by definition not in that state.
        schema_version = int(d.get("schema_version") or SCHEMA_VERSION_PREP)
        if schema_version < SCHEMA_VERSION_V2:
            d = _migrate_v1_to_v2(d)
        if schema_version < SCHEMA_VERSION_V3:
            d = _migrate_v2_to_v3(d)
        if schema_version < SCHEMA_VERSION_V4:
            d = _migrate_v3_to_v4(d)
        if schema_version < SCHEMA_VERSION_V5:
            d = _migrate_v4_to_v5(d)
        if schema_version < SCHEMA_VERSION_V6:
            d = _migrate_v5_to_v6(d)
        if schema_version < SCHEMA_VERSION:
            d = _migrate_v6_to_v7(d)
        # Always normalise to the current version on the record. The
        # migration step brings the field set up; this stamps the
        # version so the in-memory object matches what a fresh v4 record
        # looks like. (Roundtripping a v1 record should produce a v4.)
        d["schema_version"] = SCHEMA_VERSION
        # Datetime parsing
        for k in ("created_at", "updated_at", "started_at", "completed_at", "archived_at"):
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


def _migrate_v2_to_v3(d: dict[str, Any]) -> dict[str, Any]:
    """Bring a v2 record forward to the v3 field set.

    v2 records predate the deferral chain's structural enforcement
    (M1.6): they have no depth / chain_root_id / coordination_tokens.
    v2 records are by definition *not* part of a deferral chain
    (parent_task_id was unused before the MCP defer tool existed), so
    we set depth=0 (the orchestrator's own delegation) and no chain
    link. coordination_tokens=0 because v2 records never accumulated
    anything (the chain budget is new in v3).
    """
    d.setdefault("depth", 0)
    d.setdefault("chain_root_id", None)
    d.setdefault("coordination_tokens", 0)
    return d


def _migrate_v3_to_v4(d: dict[str, Any]) -> dict[str, Any]:
    """Bring a v3 record forward to the v4 field set (M1.7 step 2).

    v3 records predate the chat vs task distinction (M1.7 step 2):
    they have no ``kind`` field. Every pre-M1.7 delegation was by
    definition a task delegation -- chat delegations only exist from
    the chat loop (kind="chat"). So we default to "task".
    """
    d.setdefault("kind", "task")
    return d


def _migrate_v4_to_v5(d: dict[str, Any]) -> dict[str, Any]:
    """Bring a v4 record forward to the v5 field set (M1.9 step 3).

    v4 records predate ``ask_human``: they have no
    ``needs_attention`` flag. Pre-M1.9 delegations were by definition
    never escalated -- the field defaults to False.
    """
    d.setdefault("needs_attention", False)
    return d


def _migrate_v5_to_v6(d: dict[str, Any]) -> dict[str, Any]:
    """Bring a v5 record forward to the v6 field set (M1.13 cleanup).

    v5 records predate the archive sub-state: they have no
    ``archived`` / ``archived_at`` fields. Every v5 record is by
    definition live (archiving did not exist yet) -- defaults are
    False / None.
    """
    d.setdefault("archived", False)
    d.setdefault("archived_at", None)
    return d


def _migrate_v6_to_v7(d: dict[str, Any]) -> dict[str, Any]:
    """Bring a v6 record forward to the v7 field set (M2.0).

    v6 records predate caller-supplied estimates: they have no
    ``estimate`` field. Every pre-M2.0 delegation was by definition
    submitted without one — the field defaults to None.
    """
    d.setdefault("estimate", None)
    return d


class DelegationStore:
    """In-memory + on-disk store of :class:`Delegation` records for one project.

    M1.1 step 2 adds per-project disk persistence. The store loads from
    ``{project_dir}/.sweave/delegations.json`` on first access; every
    ``add``/``update`` writes the whole file through atomically. Records
    are kept in memory after the initial load (the working set per
    project is small; write-through is the simplest correct contract).

    Locking: callers wrap writes with :class:`ProjectLockRegistry`'s
    per-project lock (already on :class:`AppState`); this class keeps a
    *separate* in-process asyncio lock for status-transition atomicity.
    Two locks is fine: the registry lock serialises *disk writes* (so
    we don't fsync-stomp), the store lock serialises *in-memory state*
    (so :meth:`update` is a compare-and-set under contention).

    Crash recovery: a partial/truncated JSON file is loaded as
    ``{"delegations": []}`` with a warning logged; the API never 500s
    on a bad file. The atomic write makes this rare (a crash mid-write
    leaves the old file intact because we write to ``.tmp`` then
    ``os.replace``).
    """

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir)
        self.file_path = self.project_dir / ".sweave" / "delegations.json"
        self._records: dict[str, Delegation] = {}
        self._lock = asyncio.Lock()
        # Eagerly load on construction; cheap for small record sets.
        # Failures are logged + the store starts empty rather than
        # raising — see _load for the recovery contract.
        self._load()

    # ---- persistence ----------------------------------------------------

    def _load(self) -> None:
        """Load records from disk. Bad file -> empty store + warning."""
        if not self.file_path.exists():
            return
        try:
            text = self.file_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning(
                "DelegationStore: cannot read %s: %s -- starting empty",
                self.file_path, e,
            )
            return
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(
                "DelegationStore: %s is corrupt (%s) -- starting empty",
                self.file_path, e,
            )
            return
        if not isinstance(data, dict) or "delegations" not in data:
            logger.warning(
                "DelegationStore: %s has unexpected shape -- starting empty",
                self.file_path,
            )
            return
        for entry in data["delegations"]:
            if not isinstance(entry, dict):
                continue
            try:
                d = Delegation.from_dict(entry)
            except (TypeError, ValueError) as e:
                logger.warning(
                    "DelegationStore: skipping bad entry in %s: %s",
                    self.file_path, e,
                )
                continue
            self._records[d.delegation_id] = d

    async def _persist(self) -> None:
        """Write the current in-memory state to disk atomically."""
        payload = {"delegations": [d.to_dict() for d in self._records.values()]}
        await atomic_write_json(self.file_path, payload)

    # ---- in-memory API --------------------------------------------------

    async def add(self, delegation: Delegation) -> None:
        async with self._lock:
            self._records[delegation.delegation_id] = delegation
            await self._persist()

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
            await self._persist()
            return rec

    async def archive_many(self, delegation_ids: list[str]) -> int:
        """M1.13 cleanup (ruling 2026-09-11): bulk-archive records.

        ARCHIVE, not delete: sets ``archived=True`` + ``archived_at``
        on each matching record and persists once. Status, output and
        every stat field are preserved (the aggregate surface keeps
        counting archived records). Idempotent: already-archived
        records are skipped and don't count. Returns the number of
        records newly archived.
        """
        async with self._lock:
            n = 0
            for did in delegation_ids:
                rec = self._records.get(did)
                if rec is None or rec.archived:
                    continue
                rec.archived = True
                if rec.archived_at is None:
                    rec.archived_at = _now()
                rec.updated_at = _now()
                n += 1
            if n:
                await self._persist()
            return n

    async def recover_interrupted(self, reason: str = "server restart") -> int:
        """Boot recovery: mark stale non-terminal records failed.

        A server crash/restart orphans every record still in a
        non-terminal status (``running``/``queued``): the coroutine
        driving it died with the process, and nothing will ever pick
        it up again (the JobRunner queue is in-memory). Called from
        the FastAPI lifespan BEFORE any turn can be accepted, so any
        non-terminal record found on disk is by definition stale from
        a previous process run.

        ``running``/``queued`` -> ``failed`` with an explicit
        ``interrupted`` error; chat delegations keep whatever partial
        ``output`` the streaming coalescer had already persisted (the
        durable stream snapshot), so the already-streamed text is not
        lost. ``review``/``done``/``failed`` are untouched (``review``
        is a real user-facing state, not in-flight work).

        Returns the number of records recovered.
        """
        async with self._lock:
            n = 0
            for rec in list(self._records.values()):
                if rec.status not in ("running", "queued"):
                    continue
                rec.status = "failed"
                if not rec.error:
                    rec.error = f"[chat error: interrupted by {reason}]"
                if rec.completed_at is None:
                    rec.completed_at = _now()
                rec.updated_at = _now()
                n += 1
            if n:
                await self._persist()
            return n


class PerProjectDelegationStores:
    """Lazy map of project_name -> :class:`DelegationStore`.

    Holds the per-project in-process state. The :class:`JobRunner` looks
    stores up by ``project_name``; missing projects get a fresh store
    on first delegation. The map is never eagerly populated at startup
    (per the M1.1 plan: a server with N projects doesn't touch N files
    on boot).

    Project deletion hooks: see :meth:`drop`. The lifecycle owner
    (``ProjectManager``) calls this when a project is removed so we
    don't leak in-memory state.

    Trace logs are intentionally NOT moved per-project here — they
    live at ``~/.sweave/traces/{delegation_id}.jsonl`` (keyed by
    delegation_id, not project) and are written by :class:`JobRunner`
    directly via :class:`TraceLog`.
    """

    def __init__(self) -> None:
        self._stores: dict[str, DelegationStore] = {}
        self._meta_lock = asyncio.Lock()

    async def for_project(self, project_dir: Path) -> DelegationStore:
        """Return the store for *project_dir*, creating it on first access."""
        # We key on the resolved path string so the same project always
        # maps to the same store regardless of how the path is spelled.
        key = str(Path(project_dir).resolve())
        existing = self._stores.get(key)
        if existing is not None:
            return existing
        async with self._meta_lock:
            existing = self._stores.get(key)
            if existing is None:
                existing = DelegationStore(project_dir)
                self._stores[key] = existing
            return existing

    def known_projects(self) -> list[str]:
        return list(self._stores.keys())

    def known_projects_stores(self) -> list[DelegationStore]:
        """Return the live store objects (for cross-store lookups)."""
        return list(self._stores.values())

    async def drop(self, project_dir: Path) -> bool:
        """Forget the in-memory store for *project_dir*. Returns True if one was dropped."""
        key = str(Path(project_dir).resolve())
        async with self._meta_lock:
            return self._stores.pop(key, None) is not None
