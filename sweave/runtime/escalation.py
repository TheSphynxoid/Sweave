"""Escalation store (M1.9 step 3; M1.11 roles + no-timeout questions).

Two record kinds (M1.11):

* ``question`` (audience ``human``): orchestrator -> human blocking
  question via the ``ask_human`` MCP tool. No deadline — the asking
  turn (ChatLoop) waits indefinitely until ``answered`` or
  ``skipped``. Skip is the opencode-Esc equivalent, guarded by a
  system-issued confirm (the UI dialog, not LLM text).
* ``escalation`` (audience ``orchestrator``): specialist ->
  orchestrator non-blocking notice via the ``escalate`` MCP tool.
  The specialist finishes its turn; the record stays pending in the
  global audit log until a human acknowledges/answers it.

The MCP server escalates the asking delegation:

* The escalation is persisted (one JSON file per asking delegation;
  survives server restarts).
* The store publishes ``specialist.escalated`` with the question,
  options, delegation_id, escalation_id, kind, audience, and a
  deadline timestamp (``None`` for no-timeout questions) via its
  callback hooks (the AppState bridges those to WSEventBus).
* The asking delegation's ``needs_attention`` flag is set (the
  Children audit lane surfaces it inline).

The answer path is ``POST /api/delegations/{id}/answer {response}``
(and ``POST /api/delegations/{id}/skip {confirmed: true}`` for the
explicit skip). The handler:

* Records the response on the escalation.
* Publishes ``specialist.escalation_resolved``.
* Clears the asking delegation's ``needs_attention`` flag.

Legacy timeout records (``status=timeout``, ``response="no answer
received"``) are still readable; new questions are created without
a deadline so the LLM proceeds only on an explicit human answer or
skip rather than a timer.

Persistence: ``<base_dir>/escalations/{delegation_id}.json``. Same
shape as ``trace_log``: small JSON files keyed by delegation_id,
process-local in-memory map mirrors them. Per-delegation, not per-
project (escalations are an LLM-call surface, not a project artefact).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Union

logger = logging.getLogger(__name__)


# Default timeout (M1.9 legacy: 15 minutes). M1.11: questions have
# NO timeout by user ruling — ``None`` means "wait indefinitely".
# The constructor accepts ``float | None``; ``None`` (the new
# default) creates records with ``deadline_at=None``. A numeric
# value preserves the old deadline behaviour for callers that
# still want it (tests, manual timeouts).
DEFAULT_ESCALATION_TIMEOUT_SECONDS: float | None = None


# An emitter is either:
# - a callable (sync or async) accepting (event_name, data), OR
# - an object with a ``publish(event, data)`` method (async).
EventEmitter = Union[
    Callable[[str, dict[str, Any]], Union[None, Awaitable[None]]],
    Any,
]


def _now() -> datetime:
    return datetime.now()


def _now_iso() -> str:
    return _now().isoformat()


async def _emit_via(emitter: EventEmitter | None, event: str, data: dict[str, Any]) -> None:
    """Best-effort event emission. Accepts a sync callable, an async
    callable, or an object with ``.publish`` (WSEventBus-shaped).

    Errors are logged + swallowed; the escalation lifecycle must
    never crash on a transport hiccup.
    """
    if emitter is None:
        return
    try:
        if hasattr(emitter, "publish"):
            result = emitter.publish(event, data)
            if inspect.isawaitable(result):
                await result
            return
        result = emitter(event, data)
        if inspect.isawaitable(result):
            await result
    except Exception as e:  # noqa: BLE001
        logger.warning("EscalationStore: emit failed for %s: %s", event, e)


class EscalationStore:
    """Persistence + event surface for ask_human escalations.

    The MCP stdio server is stateless; every ``ask_human`` call hits
    the sweave HTTP API (``/api/delegations/{id}/escalate``), which
    delegates here. The store is process-local but backed by a JSON
    file so a server restart doesn't lose open escalations (the LLM
    can resume waiting on the same escalation_id).
    """

    def __init__(
        self,
        *,
        base_dir: Path,
        timeout_seconds: float | None = DEFAULT_ESCALATION_TIMEOUT_SECONDS,
        event_bus: EventEmitter = None,
        delegation_flagger: Any = None,
    ) -> None:
        self.base_dir = Path(base_dir) / "escalations"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = (
            float(timeout_seconds) if timeout_seconds is not None else None
        )
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        # Process-local file lock for the sync load path.
        self._file_lock = threading.Lock()
        self._event_bus = event_bus
        # M1.12 fix (2026-09-10): async callable ``(delegation_id,
        # needs_attention)`` that flips the asking delegation's flag.
        # Fulfils create()'s documented contract for ALL creators:
        # the ask_human router flipped the flag itself, but the
        # permission-bridge + stall-branch creators called create()
        # directly, so the flag stayed False and every answer
        # surface keyed on it (Children escalation lane, Turn
        # delegation badge) never lit up.
        self._delegation_flagger = delegation_flagger
        self._load_all()

    # ---- file IO -------------------------------------------------------

    def _path_for(self, delegation_id: str) -> Path:
        return self.base_dir / f"{delegation_id}.json"

    def _load_all(self) -> None:
        """Eagerly load every escalation file. The set is small
        (typically <10 open at a time); cheap and removes a per-
        request load path."""
        with self._file_lock:
            for f in self.base_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as e:
                    logger.warning("EscalationStore: bad file %s: %s", f, e)
                    continue
                if not isinstance(data, dict) or "delegation_id" not in data:
                    continue
                self._records[data["delegation_id"]] = data

    async def _persist(self, delegation_id: str) -> None:
        rec = self._records.get(delegation_id)
        if rec is None:
            return
        path = self._path_for(delegation_id)
        # Atomic write: tmp + os.replace
        import tempfile
        fd, tmp = tempfile.mkstemp(
            prefix=".esc.", suffix=".json.tmp", dir=str(self.base_dir)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(rec, f, indent=2)
                f.write("\n")
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ---- helpers -------------------------------------------------------

    async def _emit(self, event: str, data: dict[str, Any]) -> None:
        await _emit_via(self._event_bus, event, data)

    # ---- CRUD ----------------------------------------------------------

    async def create(
        self,
        *,
        delegation_id: str,
        question: str,
        options: list[str] | None = None,
        kind: str = "question",
        audience: str = "human",
        timeout_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist a new escalation. Returns the escalation record.

        ``kind`` is ``question`` (orchestrator -> human, blocking)
        or ``escalation`` (specialist -> orchestrator, notice).
        ``audience`` mirrors it (``human`` | ``orchestrator``).
        ``timeout_seconds`` overrides the store default for this
        record only; ``None`` (default) means no deadline — the
        record waits until answered or skipped (M1.11 ruling).
        ``metadata`` (M1.12) carries structured detail (e.g. the
        opencode permission request id + patterns for
        ``kind="permission"`` records); optional, persisted
        additively (records load key-tolerantly, no schema bump —
        escalation records are not Delegations).

        Side effects:
        * Sets the asking delegation's ``needs_attention`` flag via
          the WSEventBus (``delegation.needs_attention``).
        * Publishes ``specialist.escalated`` with the question +
          options + delegation_id + escalation_id + kind +
          audience + deadline (``None`` when no timeout).
        """
        escalation_id = f"esc-{uuid.uuid4().hex[:10]}"
        now = _now()
        # Per-record override wins; otherwise the store default;
        # None throughout means no deadline (M1.11 questions).
        effective_timeout = (
            timeout_seconds if timeout_seconds is not None
            else self.timeout_seconds
        )
        deadline = (
            now + timedelta(seconds=effective_timeout)
            if effective_timeout is not None
            else None
        )
        rec: dict[str, Any] = {
            "escalation_id": escalation_id,
            "delegation_id": delegation_id,
            "question": question,
            "options": list(options) if options else None,
            "kind": kind,
            "audience": audience,
            "status": "pending",
            "created_at": now.isoformat(),
            "deadline_at": deadline.isoformat() if deadline else None,
            "answered_at": None,
            "response": None,
            "escalation_timeout_seconds": effective_timeout,
            "metadata": metadata if isinstance(metadata, dict) else None,
        }
        async with self._lock:
            self._records[delegation_id] = rec
            await self._persist(delegation_id)
        await self._emit(
            "specialist.escalated",
            {
                "escalation_id": escalation_id,
                "delegation_id": delegation_id,
                "question": question,
                "options": list(options) if options else None,
                "kind": kind,
                "audience": audience,
                "deadline_at": rec["deadline_at"],
                "metadata": rec.get("metadata"),
            },
        )
        await self._flag(delegation_id, True)
        return dict(rec)

    async def _flag(self, delegation_id: str, value: bool) -> None:
        """Best-effort ``needs_attention`` flip via the injected callback.

        Never raises: the escalation lifecycle must not depend on
        the delegation store being reachable (mirrors ``_emit``).
        """
        flagger = self._delegation_flagger
        if flagger is None:
            return
        try:
            result = flagger(delegation_id, value)
            if inspect.isawaitable(result):
                await result
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "EscalationStore: delegation flag flip failed for %s: %s",
                delegation_id,
                e,
            )

    async def answer(
        self,
        *,
        delegation_id: str,
        response: str,
    ) -> dict[str, Any] | None:
        """Record the user's answer. Returns the updated escalation
        or None if no such escalation exists.

        Side effects:
        * Clears the asking delegation's ``needs_attention`` flag
          (via the WSEventBus).
        * Publishes ``specialist.escalation_resolved`` with the
          response + status.
        """
        async with self._lock:
            rec = self._records.get(delegation_id)
            if rec is None:
                return None
            if rec["status"] != "pending":
                # Already answered or timed out; treat the second answer
                # as a no-op (the MCP path is the only caller and it
                # holds the lock; this guard is for safety).
                return dict(rec)
            rec["status"] = "answered"
            rec["response"] = response
            rec["answered_at"] = _now_iso()
            await self._persist(delegation_id)
        await self._emit(
            "specialist.escalation_resolved",
            {
                "escalation_id": rec["escalation_id"],
                "delegation_id": delegation_id,
                "status": "answered",
                "response": response,
            },
        )
        await self._flag(delegation_id, False)
        return dict(rec)

    async def force_timeout(self, *, delegation_id: str) -> dict[str, Any] | None:
        """Mark the escalation as timed out (manual path + test seam).

        M1.11: questions no longer auto-time-out; this stays for
        legacy records and callers that opted into a deadline."""
        async with self._lock:
            rec = self._records.get(delegation_id)
            if rec is None:
                return None
            if rec["status"] != "pending":
                return dict(rec)
            rec["status"] = "timeout"
            rec["response"] = "no answer received"
            rec["answered_at"] = _now_iso()
            await self._persist(delegation_id)
        await self._emit(
            "specialist.escalation_resolved",
            {
                "escalation_id": rec["escalation_id"],
                "delegation_id": delegation_id,
                "status": "timeout",
                "response": "no answer received",
            },
        )
        await self._flag(delegation_id, False)
        return dict(rec)

    async def get(self, *, delegation_id: str) -> dict[str, Any] | None:
        rec = self._records.get(delegation_id)
        if rec is None:
            return None
        # Backward compat: pre-M1.11 records lack kind/audience.
        out = dict(rec)
        out.setdefault("kind", "question")
        out.setdefault("audience", "human")
        return out

    async def skip(
        self,
        *,
        delegation_id: str,
    ) -> dict[str, Any] | None:
        """Record an explicit human skip (the opencode-Esc equivalent).

        The UI guards this with a system-issued "are you sure?"
        confirm before calling; the store itself just needs the
        pending record. Emits ``specialist.escalation_resolved``
        with ``status=skipped`` so waiters unblock and the audit
        lane clears. Returns the updated record or None.
        """
        async with self._lock:
            rec = self._records.get(delegation_id)
            if rec is None:
                return None
            if rec["status"] != "pending":
                return dict(rec)
            rec["status"] = "skipped"
            rec["response"] = "skipped by user — proceed with best judgment"
            rec["answered_at"] = _now_iso()
            await self._persist(delegation_id)
        await self._emit(
            "specialist.escalation_resolved",
            {
                "escalation_id": rec["escalation_id"],
                "delegation_id": delegation_id,
                "status": "skipped",
                "response": rec["response"],
            },
        )
        await self._flag(delegation_id, False)
        return dict(rec)

    async def list_open(self) -> list[dict[str, Any]]:
        """Every pending escalation. The Children tab's escalation
        lane reads from this for the initial render; subsequent
        updates are WS-driven."""
        return [
            dict(r) for r in self._records.values()
            if r.get("status") == "pending"
        ]