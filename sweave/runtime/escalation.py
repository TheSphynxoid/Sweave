"""Escalation store (M1.9 step 3).

A specialist that's stuck or uncertain calls ``ask_human(question,
options?)`` via MCP. The MCP server escalates the asking delegation:

* The escalation is persisted (one JSON file per asking delegation;
  survives server restarts).
* The store publishes ``specialist.escalated`` with the question,
  options, delegation_id, escalation_id, and a deadline timestamp
  via its callback hooks (the AppState bridges those to WSEventBus).
* The asking delegation's ``needs_attention`` flag is set (the
  Children tab's escalation lane surfaces it inline).

The answer path is ``POST /api/delegations/{id}/answer {response}``
(or the future-facing answer UI). The handler:

* Records the response on the escalation.
* Publishes ``specialist.escalation_resolved``.
* Clears the asking delegation's ``needs_attention`` flag.

If the timeout elapses without an answer, the escalation is auto-
resolved with status=timeout and a "no answer received" string is
returned to the asking session so the LLM can proceed with best
judgment rather than hanging.

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


# Default timeout (matches the chat turn timeout scale; the plan: 15
# minutes, configurable). Lower in tests via the constructor.
DEFAULT_ESCALATION_TIMEOUT_SECONDS = 15 * 60


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
        timeout_seconds: float = DEFAULT_ESCALATION_TIMEOUT_SECONDS,
        event_bus: EventEmitter = None,
    ) -> None:
        self.base_dir = Path(base_dir) / "escalations"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = float(timeout_seconds)
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        # Process-local file lock for the sync load path.
        self._file_lock = threading.Lock()
        self._event_bus = event_bus
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
    ) -> dict[str, Any]:
        """Persist a new escalation. Returns the escalation record.

        Side effects:
        * Sets the asking delegation's ``needs_attention`` flag via
          the WSEventBus (``delegation.needs_attention``).
        * Publishes ``specialist.escalated`` with the question +
          options + delegation_id + escalation_id + deadline.
        """
        escalation_id = f"esc-{uuid.uuid4().hex[:10]}"
        now = _now()
        deadline = now + timedelta(seconds=self.timeout_seconds)
        rec: dict[str, Any] = {
            "escalation_id": escalation_id,
            "delegation_id": delegation_id,
            "question": question,
            "options": list(options) if options else None,
            "status": "pending",
            "created_at": now.isoformat(),
            "deadline_at": deadline.isoformat(),
            "answered_at": None,
            "response": None,
            "escalation_timeout_seconds": self.timeout_seconds,
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
                "deadline_at": rec["deadline_at"],
            },
        )
        return dict(rec)

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
        return dict(rec)

    async def force_timeout(self, *, delegation_id: str) -> dict[str, Any] | None:
        """Mark the escalation as timed out (test seam + a manual
        path; production uses the background sweep)."""
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
        return dict(rec)

    async def get(self, *, delegation_id: str) -> dict[str, Any] | None:
        rec = self._records.get(delegation_id)
        return dict(rec) if rec is not None else None

    async def list_open(self) -> list[dict[str, Any]]:
        """Every pending escalation. The Children tab's escalation
        lane reads from this for the initial render; subsequent
        updates are WS-driven."""
        return [
            dict(r) for r in self._records.values()
            if r.get("status") == "pending"
        ]