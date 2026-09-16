"""Specialist transcript view (Specialist-view plan Amendment 2026-09-16,
step 2a — engine journal -> per-turn blocks).

Projects the native-engine sidecar journal
(``~/.sweave/engine/sessions.json``, ``SWEAVE_ENGINE_DATA_DIR``
override) into per-turn blocks for the delegation detail fold's
ADDITIVE ``transcript`` key. One block per ``/run`` turn (the
journal's ``user`` message opens the turn; ``user_message_id`` —
protocol v3, carried by the ``done`` SSE event and traced per turn as
``engine_user_message`` — names the prompt unit).

Transparency track owns the payload (custom-engine plan §7: engine
turns render through the same projector; engine work needing a new
field extends the payload, never a second surface).

WHAT EACH BLOCK CARRIES
* ``prompt`` — the prompt the wire actually carried (journal user
  message content: charter render + preamble + task verbatim).
* ``text`` — the assistant's final text (journal assistant messages
  with content; a failed turn carries the honest error string).
* ``tools`` — tool calls with lifecycle: calls declared on the
  assistant message (``toolCalls``) joined with their results
  (journal ``role:"tool"`` messages) by ``toolCallId``.
* ``reasoning`` — journal does NOT persist reasoning; it is joined
  from the delegation trace's ``reasoning`` events attributed to
  turns via the trace's ``engine_user_message`` markers (per-turn
  boundary), capped (sizes bound trace growth on long turns).
* ``tokens`` — the per-turn ``tokens_used`` trace event attributed
  by turn order (the SSE emits one per turn in order).
* ``failed``/``error`` — honest turn failure echo when the journal
  marks the assistant row failed or timed out.

DEGRADE CONTRACT (never raises)
* Missing journal file, unknown engine session, or corrupt JSON ->
  ``None`` (the detail fold projects without the key's content —
  pre-change delegations and opencode turns read exactly as they do
  today; never an empty promise, never a crash).
* Unknown journal shapes (extra/missing keys, non-string fields)
  degrade field-by-field: a shape this projector does not recognize
  becomes an ``unknown`` entry traced raw, never a turn failure.

This module is read-side only: it never mutates the journal, never
writes, never raises on a missing or malformed source.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

#: Anti-bloat caps (trace attribution: reasoning lives in the trace,
#: so long reasoning is bounded here — sizes bound growth, not trust).
MAX_PROMPT_CHARS = 20_000
MAX_TEXT_CHARS = 20_000
MAX_TOOL_RESULT_CHARS = 20_000
MAX_REASONING_CHARS = 8_000


def engine_journal_path(base_dir: Path | None = None) -> Path:
    """Resolve the sidecar journal path.

    ``SWEAVE_ENGINE_DATA_DIR`` (the env override the harness spawn
    honors) wins; the default is the sidecar's ``~/.sweave/engine``
    data dir. A *file* argument is accepted verbatim (tests).
    """
    override = os.environ.get("SWEAVE_ENGINE_DATA_DIR")
    if override:
        base = Path(override)
    elif base_dir is not None:
        base = base_dir
    else:
        base = Path.home() / ".sweave" / "engine"
    if base.suffix == ".json":
        return base
    return base / "sessions.json"


def _load_journal(path: Path) -> dict[str, Any] | None:
    """Load the journal as a dict. Corrupt/missing -> None (degrade)."""
    try:
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — never raise; corrupt journal degrades
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def _clip(text: Any, limit: int) -> str:
    """Stringify + cap one text field with an honest marker."""
    s = text if isinstance(text, str) else ("" if text is None else str(text))
    if len(s) <= limit:
        return s
    return s[:limit] + f" … [truncated {len(s) - limit} chars]"


def _tool_from_journal(
    call: Any,
    results_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """One tool entry: call (from the assistant's ``toolCalls``) +
    result (journal ``role:"tool"`` message) joined by ``toolCallId``.

    Unknown arg/result shapes degrade field-by-field (state the
    projector did not recognize stays ``unknown``, traced raw, never
    assumed).
    """
    if not isinstance(call, dict):
        return None
    call_id = call.get("id")
    tool = call.get("name", "tool")
    args = call.get("args")
    if not isinstance(args, dict):
        args = None
    result = results_by_id.get(str(call_id or "")) if call_id else None
    out: dict[str, Any] = {
        "callID": str(call_id or ""),
        "tool": str(tool or "tool"),
        # Journal semantics: the result message's presence is the
        # terminal state (the loop appends it only after the tool
        # returns; an absent result = the turn never ran it to
        # completion). Status names the honest shape; an unknown
        # result shape degrades to ``unknown``.
        "status": ("completed" if result is not None else "unknown"),
        "args": args,
    }
    if result is None:
        out["result"] = None
        return out
    content = result.get("content", "")
    out["result"] = _clip(content, MAX_TOOL_RESULT_CHARS)
    if result.get("failed"):
        out["status"] = "error"
        out["error"] = str(result.get("error") or "unknown tool error")
    return out


def render_transcript_blocks(
    engine_session_id: str | None,
    trace_events: list[dict[str, Any]] | None = None,
    *,
    journal_path: Path | None = None,
) -> list[dict[str, Any]] | None:
    """Project the engine journal into per-turn transcript blocks.

    One block per ``/run`` turn, chronological (conversation order —
    ruling Q2 keeps transcript order untouched). Returns ``None``
    when there is nothing to project (no engine session id, missing
    journal, unknown session): the detail fold renders absent content
    honestly — pre-change delegations keep today's shape, and never a
    crash. Unknown journal shapes degrade to ``unknown`` rows.
    """
    if not engine_session_id:
        return None
    journal = _load_journal(journal_path or engine_journal_path())
    if journal is None:
        return None
    session = journal.get(engine_session_id)
    if not isinstance(session, dict):
        return None
    messages = session.get("messages")
    if not isinstance(messages, list):
        return None

    # Trace-side turn attribution: `reasoning` chunks ride per-turn
    # (the journal does not persist them), delimited by the traced
    # `engine_user_message` markers; `tokens_used` events arrive one
    # per turn in order. Both only JOIN — never drive (a trace that
    # disagrees with the journal still projects blocks from the
    # journal; missing trace sides are empty, not broken).
    reasoning_turns: list[list[str]] = []
    token_turns: list[dict[str, Any]] = []
    current_reasoning: list[str] | None = None
    for ev in trace_events or []:
        try:
            name = ev.get("event")
        except Exception:  # noqa: BLE001 — never raise on trace shapes
            continue
        if name == "engine_user_message":
            if current_reasoning:
                reasoning_turns.append(current_reasoning)
            current_reasoning = []
        elif name == "reasoning":
            if current_reasoning is None:
                current_reasoning = []
            rtext = ev.get("text")
            if isinstance(rtext, str) and rtext:
                current_reasoning.append(rtext)
        elif name == "tokens_used":
            token_turns.append(
                {
                    "input": ev.get("input", 0),
                    "output": ev.get("output", 0),
                    "reasoning": ev.get("reasoning", 0),
                    "cache_read": ev.get("cache_read", 0),
                    "cache_write": ev.get("cache_write", 0),
                    "context_input": ev.get("context_input", 0),
                }
            )
    if current_reasoning:
        reasoning_turns.append(current_reasoning)

    blocks: list[dict[str, Any]] = []
    # Index tool RESULT messages by toolCallId (the journal separates
    # calls and results).
    results_by_id: dict[str, dict[str, Any]] = {}
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "tool":
            tcid = msg.get("toolCallId")
            if tcid:
                results_by_id[str(tcid)] = msg

    current: dict[str, Any] | None = None
    turn_index = 0  # /run turns, including ones outside this range
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "user":
            # Flush any orphan turn (assistant-only prefix — a session
            # reused by a pre-2a turn projects without a prompt).
            if current is not None:
                blocks.append(current)
                current = None
            current = {
                "user_message_id": str(msg.get("id") or ""),
                "prompt": _clip(msg.get("content"), MAX_PROMPT_CHARS),
                "text": "",
                "reasoning": "",
                "tools": [],
                "tokens": None,
                "failed": False,
                "error": None,
            }
            rid = reasoning_turns[turn_index] if turn_index < len(reasoning_turns) else []
            current["reasoning"] = _clip("".join(rid), MAX_REASONING_CHARS)
            tid = token_turns[turn_index] if turn_index < len(token_turns) else None
            current["tokens"] = dict(tid) if tid else None
            turn_index += 1
        elif current is not None and role == "assistant":
            if msg.get("failed"):
                current["failed"] = True
                current["error"] = str(msg.get("error") or "unknown error")
            content = msg.get("content")
            if isinstance(content, str) and content:
                current["text"] = (
                    current["text"] + content
                    if current["text"]
                    else content
                )
            calls = msg.get("toolCalls")
            if isinstance(calls, list):
                for call in calls:
                    entry = _tool_from_journal(call, results_by_id)
                    if entry is not None:
                        current["tools"].append(entry)
            model = msg.get("model")
            if model:
                current["model"] = str(model)
        elif current is not None and role == "tool":
            # A result message whose toolCallId did not ride an assistant
            # toolCalls entry (drifted shape): trace raw, never assume.
            tcid = msg.get("toolCallId")
            known = any(
                t.get("callID") == str(tcid or "") for t in current["tools"]
            )
            if not known:
                current["tools"].append(
                    {
                        "callID": str(tcid or ""),
                        "tool": str(msg.get("name") or "tool"),
                        "status": "unknown",
                        "args": None,
                        "result": _clip(msg.get("content"), MAX_TOOL_RESULT_CHARS),
                    }
                )
        elif current is None and role in ("assistant", "tool"):
            # An assistant/tool tail before ANY user message (drifted
            # journal): project the row raw in a synthetic unknown
            # block so the user still sees it (never silently dropped).
            current = {
                "user_message_id": "",
                "prompt": None,
                "text": _clip(msg.get("content"), MAX_TEXT_CHARS)
                if role == "assistant"
                else "",
                "reasoning": "",
                "tools": [],
                "tokens": None,
                "failed": bool(msg.get("failed")),
                "error": str(msg.get("error") or "") if msg.get("failed") else None,
            }
            if role == "tool":
                current["tools"].append(
                    {
                        "callID": str(msg.get("toolCallId") or ""),
                        "tool": str(msg.get("name") or "tool"),
                        "status": "unknown",
                        "args": None,
                        "result": _clip(msg.get("content"), MAX_TOOL_RESULT_CHARS),
                    }
                )
            blocks.append(current)
            current = None
        # Any other unknown role: ignored silently (journal is
        # forward-compatible; extra roles ride future features).

    if current is not None:
        blocks.append(current)

    # Cap text now (a turn's text may stream through many assistant
    # parts before the next user message).
    for b in blocks:
        b["text"] = _clip(b["text"], MAX_TEXT_CHARS)
    return blocks
