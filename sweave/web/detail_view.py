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

The trace is the source of truth (the JSONL is appended on every
state change). This module is the read-side projector: it never
mutates state, never raises on a missing file. A missing trace
returns a minimal detail view (just the id + empty sections).
"""

from __future__ import annotations

import json
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


def render_detail_view(
    delegation_id: str,
    *,
    trace_dir: Path,
) -> dict[str, Any]:
    """Project a trace into the detail-view sections.

    Returns the dict the UI / CLI consume. Always returns; a missing
    trace returns a minimal record (id + empty sections).
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
    }