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
* Clears the asking delegation's ``needs_attention`` flag — unless
  the delegation still owes attention elsewhere (M2.1 follow-up §A
  step 1: an unpromoted ``review`` keeps the flag; the answer only
  resolves the question).

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


# Ask-batch cap (TOOL_CARDS_PLAN §3 step 3, F5): ask_human accepts
# at most 5 questions per call. Legacy single ``question`` normalizes
# to a 1-elem batch server-side.
MAX_QUESTIONS_PER_ESCALATION = 5


def _projected_questions(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """Batch questions for a record (legacy-safe projection).

    Pre-batch records (and legacy single-question paths) carry only
    ``question``/``options`` — they read as a 1-elem batch. New
    records always carry ``questions[]``.
    """
    qs = rec.get("questions")
    if isinstance(qs, list) and qs:
        return qs
    return [
        {
            "question": str(rec.get("question", "") or ""),
            "options": rec.get("options") if isinstance(rec.get("options"), list) else None,
        }
    ]


def _joined_response(
    answers: list[str], questions: list[dict[str, Any]]
) -> str:
    """Joined ``response`` for a fully-answered batch.

    1-elem batch (or legacy single) → the bare answer string
    (byte-identical to the pre-batch ``response``). Multi-elem →
    "1) a  2) b" so the synthesis note quotes every pair.
    """
    parts = [str(a or "").strip() for a in answers]
    if len(questions) <= 1:
        return parts[0] if parts else ""
    return "  ".join(f"{i + 1}) {p}" for i, p in enumerate(parts))


def _normalize_questions(
    question: str,
    options: list[str] | None,
    questions: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Project the create-time input into the normalized batch shape.

    ``questions[{"question", "options"?}]`` wins when present; the
    legacy ``question``/``options`` pair otherwise projects as a
    1-elem batch. Every item always carries both keys (``options``
    may be None) so consumers never index-guard.
    """
    if questions:
        return [
            {
                "question": str(item.get("question", "") or ""),
                "options": (
                    [str(o) for o in item.get("options")]
                    if item.get("options")
                    else None
                ),
            }
            for item in questions
        ]
    return [
        {
            "question": str(question or ""),
            "options": [str(o) for o in options] if options else None,
        }
    ]


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


async def read_record_resilient(
    store: Any,
    delegation_id: str,
    *,
    attempts: int = 3,
    delay_s: float = 0.1,
) -> tuple[dict[str, Any] | None, BaseException | None]:
    """Read one escalation record, tolerating transient store errors.

    File-lock contention makes single reads flaky at millisecond
    scale; a one-shot ``except -> None`` at a timer-suspension check
    reads a live human question as "no question" and the timer kills
    a turn that was correctly awaiting the human (hygiene B6).
    Retries boundedly (default 3×100ms — never a wedge), then
    returns ``(None, last_error)`` so the caller can distinguish
    "store unreadable" (warn + attribute the kill honestly) from
    "no record" instead of conflating both as None.
    """
    last: BaseException | None = None
    for _ in range(max(1, attempts)):
        try:
            rec = await store.get(delegation_id=delegation_id)
            return (rec if isinstance(rec, dict) else None), None
        except Exception as e:  # noqa: BLE001
            last = e
            await asyncio.sleep(delay_s)
    logger.warning(
        "EscalationStore: record unreadable for %s after %d tries: %s",
        delegation_id,
        attempts,
        last,
    )
    return None, last


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

    async def create_or_reuse(
        self,
        *,
        delegation_id: str,
        question: str,
        options: list[str] | None = None,
        kind: str = "question",
        audience: str = "human",
        timeout_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
        reuse_request_id: str | None = None,
        questions: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Persist a new escalation, or reuse the live one for the same ask.

        Batch (TOOL_CARDS_PLAN §3 step 3): ``questions`` is an
        optional list of ``{"question", "options"?}`` dicts (≤5).
        When present it wins over the legacy ``question``/``options``
        pair; when absent the legacy pair projects as a 1-elem batch
        so every new record carries ``questions[]`` + ``answers[]``
        (additive keys; single-question records are 1-elem).

        Atomic under the store lock (incident 2026-09-11: the in-band
        bridge and the stall branch both created unconditionally, and
        create() overwrites per delegation_id — the second finder
        destroyed the first's live record or its recorded answer).
        When ``reuse_request_id`` is set and a PENDING record for this
        delegation already carries that request id in its metadata, the
        existing record returns untouched with ``created=False`` (no
        new event, no flag flip — the UI already shows it). Otherwise
        a new record is created exactly as before (``created=True``).
        Param shapes mirror :meth:`create`.
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
            "questions": _normalize_questions(question, options, questions),
            "answers": [],
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
            if reuse_request_id is not None:
                existing = self._records.get(delegation_id)
                if (
                    existing is not None
                    and existing.get("status") == "pending"
                    and (existing.get("metadata") or {}).get("requestID")
                    == reuse_request_id
                ):
                    return dict(existing), False
            self._records[delegation_id] = rec
            await self._persist(delegation_id)
        await self._emit(
            "specialist.escalated",
            {
                "escalation_id": escalation_id,
                "delegation_id": delegation_id,
                "question": question,
                "options": list(options) if options else None,
                "questions": rec["questions"],
                "kind": kind,
                "audience": audience,
                "deadline_at": rec["deadline_at"],
                "metadata": rec.get("metadata"),
            },
        )
        await self._flag(delegation_id, True)
        return dict(rec), True

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
        questions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Persist a new escalation. Returns the escalation record.

        Thin wrapper over :meth:`create_or_reuse` (no reuse key, so it
        always creates). Callers racing another finder on the SAME ask
        (permission bridge, stall branch) must use ``create_or_reuse``
        with the ask's request id instead.
        """
        rec, _ = await self.create_or_reuse(
            delegation_id=delegation_id,
            question=question,
            options=options,
            kind=kind,
            audience=audience,
            timeout_seconds=timeout_seconds,
            metadata=metadata,
            questions=questions,
        )
        return rec

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
        answers: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Record the human's answer to the escalation.

        Batch semantics (TOOL_CARDS_PLAN §3 step 3, locked by test):

        * ``answers`` (optional list) answers the batch positionally.
          A partial post PERSISTS on ``rec["answers"]`` WITHOUT
          resolving — status stays ``pending``, no resolved event
          and no flag clear fire, so the UI can post per-question
          answers as they land.
        * RESOLVE RULE (emptiness rule, 2026-09-17 bug fix): the
          record resolves to ``answered`` ONLY when the stored
          answers array is FULL-LENGTH **AND every slot is
          non-empty** (strip()). A full-length array with "" empty
          slots (the wizard preview posting ["A", "", ""] for
          unfilled inputs) is a PARTIAL post: it persists what is
          non-empty and stays pending, so ``waitEscalation`` /
          ``_wait_for_escalation`` can never be unblocked by an
          unfilled slot.
        * OVERWRITE RULE (2026-09-17): an incoming non-empty slot
          value OVERWRITES the stored one (re-answering works); an
          incoming EMPTY slot PRESERVES the stored value (the
          wizard's "" placeholders never erase a prior partial
          answer).
        * When the record resolves, ``response`` becomes the
          joined answers ("1) ... 2) ...") and the resolved event
          + flag clear fire (all-at-once ruling).
        * Legacy ``response`` (no ``answers``) is the 1-element
          batch's answer — resolves exactly as before.

        Returns the updated escalation (with additive ``resolved``
        bool) or None if no such escalation exists.
        """
        async with self._lock:
            rec = self._records.get(delegation_id)
            if rec is None:
                return None
            if rec["status"] != "pending":
                # Already answered or timed out; treat the second answer
                # as a no-op (the MCP path is the only caller and it
                # holds the lock; this guard is for safety). Additive
                # resolved flags (hygiene B4) so the caller can tell a
                # live steer from a late no-op instead of reading a
                # 200 as "steered".
                out = dict(rec)
                out["resolved"] = False
                out["resolved_reason"] = f"already_{rec['status']}"
                return out
            questions = _projected_questions(rec)
            if answers:
                # OVERWRITE RULE (2026-09-17 fix): an incoming NON-
                # empty slot value OVERWRITES the stored one (re-
                # answering works); an incoming EMPTY slot PRESERVES
                # the stored value (the wizard's "" placeholders
                # never erase an earlier partial answer).
                stored = [str(a) for a in (rec.get("answers") or [])]
                for i, incoming in enumerate(answers[: len(questions)]):
                    incoming = str(incoming or "").strip()
                    if not incoming:
                        continue  # wizard "" -> preserve stored[i]
                    while len(stored) <= i:
                        stored.append("")  # lazily pad skipped slots
                    stored[i] = incoming
                # Fillup is NOT stored (the record stays short until a
                # later slot is non-empty) - matches the locked
                # unpadded partial shape.
                rec["answers"] = stored
            else:
                rec["answers"] = [str(response)]
            # RESOLVE RULE (2026-09-17 emptiness fix): full length AND
            # every slot non-empty (stripped). A full-length array with
            # "" placeholders (the wizard preview posting ["A", "",
            # ""]) is a PARTIAL post: persist what is non-empty and
            # stay pending, never unblocking waitEscalation /
            # _wait_for_escalation on an unfilled slot.
            if len(questions) <= 1:
                # Legacy 1-elem batch: the bare answer resolves exactly
                # as before (byte-identical).
                partial = not any(
                    str(a or "").strip() for a in rec["answers"]
                )
            else:
                # Batch: full length AND every slot non-empty.
                partial = len(rec["answers"]) < len(questions) or any(
                    not str(a or "").strip() for a in rec["answers"][: len(questions)]
                )
            if partial:
                # Partial post: persist WITHOUT resolving (all-at-once
                # ruling; per-question posts stay incremental).
                await self._persist(delegation_id)
                partial_rec = dict(rec)
                partial_rec["resolved"] = False
                return partial_rec
            rec["status"] = "answered"
            rec["response"] = _joined_response(rec["answers"], questions)
            rec["answered_at"] = _now_iso()
            await self._persist(delegation_id)
        await self._emit(
            "specialist.escalation_resolved",
            {
                "escalation_id": rec["escalation_id"],
                "delegation_id": delegation_id,
                "status": "answered",
                "response": rec["response"],
            },
        )
        await self._flag(delegation_id, False)
        out = dict(rec)
        out["resolved"] = True
        return out

    async def force_timeout(self, *, delegation_id: str) -> dict[str, Any] | None:
        """Mark the escalation as timed out (manual path + test seam).

        M1.11: questions no longer auto-time-out; this stays for
        legacy records and callers that opted into a deadline."""
        async with self._lock:
            rec = self._records.get(delegation_id)
            if rec is None:
                return None
            if rec["status"] != "pending":
                out = dict(rec)
                out["resolved"] = False
                out["resolved_reason"] = f"already_{rec['status']}"
                return out
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
        # Batch projection: legacy records without ``questions[]``
        # read as a 1-elem batch (additive, never crashes a consumer
        # that indexes questions[]).
        out = dict(rec)
        out.setdefault("kind", "question")
        out.setdefault("audience", "human")
        out["questions"] = _projected_questions(out)
        out["answers"] = list(out.get("answers") or [])
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
                out = dict(rec)
                out["resolved"] = False
                out["resolved_reason"] = f"already_{rec['status']}"
                return out
            rec["status"] = "skipped"
            # Batch: skip = the WHOLE batch -> best judgment.
            _qs = _projected_questions(rec)
            rec["answers"] = [
                "(skipped: best judgment)" for _ in _qs
            ]
            rec["response"] = (
                "skipped by user — proceed with best judgment"
            )
            if len(_qs) > 1:
                rec["response"] = (
                    "all questions skipped by user — "
                    "proceed with best judgment"
                )
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

    async def mark_seen(
        self,
        *,
        delegation_id: str,
    ) -> dict[str, Any] | None:
        """Record orchestrator consumption of a notice (mailbox rule).

        Called by the chat loop after a synthesis turn that incorporated
        the notice: a specialist's ``escalate`` record (kind
        ``escalation``, audience ``orchestrator``) resolves as ``seen``
        instead of waiting for a human ack that was never its audience.
        Only pending records transition (anything else returns as-is);
        callers filter by kind — the store does not second-guess which
        records are notices. Emits ``specialist.escalation_resolved``
        (status ``seen``) so lanes clear, and clears
        ``needs_attention`` through the same review-aware flagger as
        every other resolution path. Returns the updated record or
        None when no record exists.
        """
        async with self._lock:
            rec = self._records.get(delegation_id)
            if rec is None:
                return None
            if rec["status"] != "pending":
                out = dict(rec)
                out["resolved"] = False
                out["resolved_reason"] = f"already_{rec['status']}"
                return out
            rec["status"] = "seen"
            rec["response"] = "seen by orchestrator at synthesis — no action needed"
            rec["answered_at"] = _now_iso()
            await self._persist(delegation_id)
        await self._emit(
            "specialist.escalation_resolved",
            {
                "escalation_id": rec["escalation_id"],
                "delegation_id": delegation_id,
                "status": "seen",
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