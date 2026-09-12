"""Delegation detail view (M1.9 step 4).

A read-side rendering of a single delegation's trace JSONL into the
sections the UI detail view patches into place (and the same data the
``sweave log <id>`` CLI prints):

* ``delegation_id`` -- the id.
* ``composed_prompt`` -- the M1.7 step 4 audit payload (memory_chars,
  whats_new_chars, synthesis_chars, transcript_ref_chars, user_chars,
  dropped_* counts). The UI's collapsible "composed prompt" section.
* ``tool_timeline`` -- one entry per callID (snapshot semantics):
  the latest state for each tool the specialist ran, plus the full
  state list so the UI can show lifecycle (pending -> running ->
  completed | error).
* ``tokens`` -- the per-turn ``tokens_used`` payload (input, output,
  reasoning, cost, cache). Aggregates across all step-finish parts.
* ``status_timeline`` -- the status transitions (queued -> running
  -> review | done | failed) with timestamps.
* ``estimate_vs_actual`` -- M2.0: the stored caller-supplied
  ``{tokens, seconds}`` estimate echoed beside actuals (trace
  ``tokens_used`` summed across turns + created->completed wall
  seconds). Missing trace/record degrades to nulls, never raises.
* ``review_request`` -- M2.1: the stored review-request record
  echoed verbatim (None = no review requested: pre-M2.1 records,
  failed delegations, unknown ids). Read side of the M2.0
  detail-fold precedent; no new endpoint.
* ``engine_session_id`` -- M2.1-follow-up: the opencode session id
  that ran this delegation (display + forensics without
  trace-digging). None for pre-change records.
* ``record`` -- review deepening Phase 1 (follow-up spec B,
  subsumed): the record header the modal needs — status, agent,
  task (+140-char snippet, the TurnDelegations card rule),
  output summary (2000 chars, truncated with marker), error,
  created/completed stamps, blocking, needs_attention. None when
  the record is missing (same degrade contract as the trace).
* ``review_bundle`` -- Phase 1: the ``{path, bytes, truncated,
  scope}`` pointer echoed verbatim (None = pre-change record or
  unknown id; ``path`` None = degraded capture, scope says why).

The trace is the source of truth (the JSONL is appended on every
state change). This module is the read-side projector: it never
mutates state, never raises on a missing file. A missing trace
returns a minimal detail view (just the id + empty sections).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _read_trace_events(trace_dir: Path, delegation_id: str) -> list[dict[str, Any]]:
    """Read every line of a trace file. Bad lines are skipped (the
    trace is best-effort detail, not source of truth)."""
    path = trace_dir / f"{delegation_id}.jsonl"
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def _parse_ts(value: Any) -> datetime | None:
    """Best-effort datetime parse (datetime passthrough, ISO string,
    else None — the projection never raises)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def render_estimate_vs_actual(
    *,
    estimate: dict[str, Any] | None,
    events: list[dict[str, Any]],
    created_at: Any = None,
    completed_at: Any = None,
) -> dict[str, Any]:
    """Project estimate-vs-actual for one delegation (M2.0).

    * ``estimate`` — the stored caller-supplied ``{tokens, seconds}``
      (or None = "no estimate supplied"); echoed verbatim.
    * actual tokens — SUMMED across every trace ``tokens_used`` event
      (differs from the ``tokens`` section's last-wins display: that
      one shows the latest turn, this one totals the delegation).
    * actual seconds — created→completed wall time, when both stamps
      exist (a still-running delegation reports null).

    Missing trace / missing record degrades to nulls, never raises.
    """
    total: dict[str, Any] | None = None
    for ev in events:
        if ev.get("event") != "tokens_used":
            continue
        if total is None:
            total = {
                "input": 0, "output": 0, "reasoning": 0,
                "cache_read": 0, "cache_write": 0, "cost": 0,
            }
        for key in ("input", "output", "reasoning", "cache_read", "cache_write"):
            value = ev.get(key, 0)
            total[key] += value if isinstance(value, (int, float)) else 0
        cost = ev.get("cost", 0)
        total["cost"] += cost if isinstance(cost, (int, float)) else 0

    seconds: float | None = None
    start = _parse_ts(created_at)
    end = _parse_ts(completed_at)
    if start is not None and end is not None:
        seconds = (end - start).total_seconds()

    return {
        "estimate": dict(estimate) if estimate else None,
        "actual": {"tokens": total, "seconds": seconds},
    }


#: Task snippet length (Phase 1 record header). Matches the
#: TurnDelegations card rule (``TASK_SNIPPET_CHARS``) so the modal
#: header and the inline card truncate identically.
TASK_SNIPPET_CHARS = 140

#: Output summary length (Phase 1 record header). The modal needs
#: substance (response + verdict context), but the payload stays
#: bounded — longer output truncates with a marker.
OUTPUT_SUMMARY_CHARS = 2000


def _snippet(text: Any, limit: int) -> str | None:
    """Truncate *text* to *limit* chars with a marker. None in →
    None out (missing output is not empty output)."""
    if text is None:
        return None
    s = str(text)
    if len(s) <= limit:
        return s
    return s[:limit] + f"… [truncated {len(s) - limit} chars]"


def render_record_header(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """Project the record header for the detail payload (Phase 1).

    Status/agent/task/output/error/stamps/blocking/attention — what
    the modal needs without a second round-trip. Never raises;
    missing record → None (same degrade contract as a missing
    trace).
    """
    if not record:
        return None
    return {
        "status": record.get("status"),
        "agent": record.get("agent"),
        "task": record.get("task"),
        "task_snippet": _snippet(record.get("task") or "", TASK_SNIPPET_CHARS),
        "output_summary": _snippet(record.get("output"), OUTPUT_SUMMARY_CHARS),
        "error": record.get("error"),
        "created_at": record.get("created_at"),
        "completed_at": record.get("completed_at"),
        "blocking": record.get("blocking"),
        "needs_attention": record.get("needs_attention"),
    }


def render_detail_view(
    delegation_id: str,
    *,
    trace_dir: Path,
    estimate: dict[str, Any] | None = None,
    created_at: Any = None,
    completed_at: Any = None,
    review_request: dict[str, Any] | None = None,
    engine_session_id: str | None = None,
    record: dict[str, Any] | None = None,
    review_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a trace into the detail-view sections.

    Returns the dict the UI / CLI consume. Always returns; a missing
    trace returns a minimal record (id + empty sections).
    Record-side inputs (``estimate`` + stamps) are optional so offline
    readers (``sweave log``, which has the trace but no store) keep
    working: estimate degrades to null while actual tokens still
    project from the trace.
    """
    events = _read_trace_events(trace_dir, delegation_id)

    composed_prompt: dict[str, Any] | None = None
    tool_timeline: dict[str, dict[str, Any]] = {}
    tokens: dict[str, Any] | None = None
    status_timeline: list[dict[str, Any]] = []

    for ev in events:
        name = ev.get("event")
        if name == "composed_prompt":
            composed_prompt = {
                "memory_chars": ev.get("memory_chars", 0),
                "whats_new_chars": ev.get("whats_new_chars", 0),
                "synthesis_chars": ev.get("synthesis_chars", 0),
                "transcript_ref_chars": ev.get("transcript_ref_chars", 0),
                "user_chars": ev.get("user_chars", 0),
                "dropped_memory": ev.get("dropped_memory", 0),
                "dropped_whats_new": ev.get("dropped_whats_new", 0),
                "dropped_synthesis": ev.get("dropped_synthesis", 0),
            }
        elif name in ("tool.started", "tool.updated", "tool.completed", "tool.failed"):
            call_id = ev.get("callID")
            if not call_id:
                continue
            entry = tool_timeline.setdefault(
                call_id,
                {
                    "callID": call_id,
                    "tool": ev.get("tool"),
                    "states": [],
                    "started_at": ev.get("ts"),
                },
            )
            entry["states"].append(ev.get("state") or {})
            # Keep the latest status as the snapshot entry's status
            latest_state = ev.get("state") or {}
            entry["status"] = latest_state.get("status")
            entry["output"] = latest_state.get("output")
            entry["error"] = latest_state.get("error")
            entry["title"] = latest_state.get("title")
            entry["input"] = latest_state.get("input")
            entry["time"] = latest_state.get("time")
        elif name == "tokens_used":
            tokens = {
                "input": ev.get("input", 0),
                "output": ev.get("output", 0),
                "reasoning": ev.get("reasoning", 0),
                "cache_read": ev.get("cache_read", 0),
                "cache_write": ev.get("cache_write", 0),
                "cost": ev.get("cost", 0),
            }
        elif name == "status_changed":
            status_timeline.append(
                {
                    "status": ev.get("status"),
                    "source": ev.get("source"),
                    "ts": ev.get("ts"),
                }
            )

    return {
        "delegation_id": delegation_id,
        "composed_prompt": composed_prompt,
        "tool_timeline": list(tool_timeline.values()),
        "tokens": tokens,
        "status_timeline": status_timeline,
        "estimate_vs_actual": render_estimate_vs_actual(
            estimate=estimate,
            events=events,
            created_at=created_at,
            completed_at=completed_at,
        ),
        "review_request": dict(review_request) if review_request else None,
        "engine_session_id": engine_session_id,
        "record": render_record_header(record),
        "review_bundle": dict(review_bundle) if review_bundle else None,
    }
