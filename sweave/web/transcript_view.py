"""Specialist transcript view (Specialist-view plan Amendment 2026-09-16,
step 2a — engine journal -> per-turn blocks).

Projects the native-engine sidecar journal
(``~/.sweave/engine`` — sharded ``sessions/<id>.json`` since the
2026-09-20 journal surgery, legacy single-file ``sessions.json``
before that; ``SWEAVE_ENGINE_DATA_DIR`` override) into per-turn
blocks for the delegation detail fold's ADDITIVE ``transcript`` key. One block per ``/run`` turn (the
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
from urllib.parse import quote

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


def _read_session_file(path: Path) -> dict[str, Any] | None:
    """Load one sharded session file. Corrupt/missing -> None (degrade)."""
    try:
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — never raise; corrupt journal degrades
        return None
    return raw if isinstance(raw, dict) else None


def _load_session(journal_path: Path, session_id: str) -> dict[str, Any] | None:
    """Load one engine session: sharded file first, legacy journal second.

    Shard layout (2026-09-20 journal surgery): ``<dir>/sessions/<id>.json``
    where ``<dir>`` is the data dir (``journal_path`` itself when it is a
    dir, else its parent). Legacy single-file ``sessions.json`` (explicit
    file fixtures, pre-change sidecars) stays readable. Both degrade to
    ``None`` the same way.
    """
    try:
        base = journal_path if journal_path.is_dir() else journal_path.parent
        shard = base / "sessions" / (quote(session_id, safe="") + ".json")
        session = _read_session_file(shard)
        if session is not None:
            return session
        if journal_path.is_dir():
            return None
        journal = _load_journal(journal_path)
        if journal is None:
            return None
        session = journal.get(session_id)
        return session if isinstance(session, dict) else None
    except Exception:  # noqa: BLE001 — never raise; corrupt journal degrades
        return None


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
        # result shape degrades to ``unknown``. The additive
        # ``result_missing`` flag (hygiene B6) lets renderers tell a
        # never-completed call (abort/timeout/crash poison — the wire
        # now sanitizes these, the journal still shows them) from an
        # unrecognized shape: both stay ``unknown`` for
        # back-compat, the flag names the difference.
        "status": ("completed" if result is not None else "unknown"),
        "result_missing": result is None,
        "args": args,
        # Timeline shape (2026-09-18: bare-"bash" fix). The renderer
        # (ToolTimelineRow) reads input/output/detail — the journal
        # carries both, but this projector used to emit only
        # args/result, so every transcript tool row degraded to a
        # bare verb with no info. Map them onto the same keys the
        # tool_timeline fold uses.
        "input": args,
    }
    if result is None:
        out["result"] = None
        return out
    content = result.get("content", "")
    clipped = _clip(content, MAX_TOOL_RESULT_CHARS)
    out["result"] = clipped
    out["output"] = clipped
    if result.get("failed"):
        out["status"] = "error"
        out["error"] = str(result.get("error") or "unknown tool error")
    # Additive detail via the shared chat builder (the same blob
    # the timeline/chat rows use — one builder, both renderers).
    # Extras are empty on the journal path (no exit/window journaled);
    # the builder degrades those to absent, never raises.
    try:
        from sweave.chat.tools import build_tool_detail

        detail = build_tool_detail(
            out["tool"], args or {}, {},
            content if isinstance(content, str) else "",
            out.get("error"),
        )
        if detail:
            out["detail"] = detail
    except Exception:  # noqa: BLE001 — the fold must never raise
        pass
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
    session = _load_session(journal_path or engine_journal_path(), engine_session_id)
    if session is None:
        return None
    messages = session.get("messages")
    if not isinstance(messages, list):
        return None

    # Trace-side turn attribution. `reasoning` chunks ride per-turn
    # (the journal does not persist them). Preferred join: the traced
    # `engine_user_message` marker id == the journal user message id
    # (protocol v3). Legacy fallback: positional order (markers and
    # `tokens_used` events arrived one per turn in order). The
    # positional join LIES whenever a turn is missing its anchor —
    # an empty-reasoning turn was dropped from the list entirely and
    # a failed turn emits no `tokens_used`, shifting every later
    # turn's attribution onto its neighbour (hygiene B6). So: when
    # ANY id-carrying anchor exists, join by id; positional only for
    # id-less (pre-v3) traces. Both only JOIN — never drive (a trace
    # that disagrees with the journal still projects blocks from the
    # journal; missing trace sides are empty, not broken).
    reasoning_by_id: dict[str, list[str]] = {}
    reasoning_turns: list[list[str]] = []
    token_by_id: dict[str, dict[str, Any]] = {}
    token_turns: list[dict[str, Any]] = []
    current_id: str | None = None
    current_reasoning: list[str] | None = None
    for ev in trace_events or []:
        try:
            name = ev.get("event")
        except Exception:  # noqa: BLE001 — never raise on trace shapes
            continue
        if name == "engine_user_message":
            # Review fix: anchor EVERY marker unconditionally (even an
            # empty buffer), not just truthy ones — an empty-reasoning
            # turn must still occupy its positional slot or every
            # later neighbour shifts. The `is not None` guard keeps
            # the pre-first-marker prefix out of the list (there is no
            # turn before the first marker to attribute it to).
            if current_reasoning is not None:
                reasoning_turns.append(current_reasoning)
                if current_id is not None:
                    reasoning_by_id.setdefault(current_id, []).extend(
                        current_reasoning
                    )
            mid = ev.get("id")
            current_id = str(mid) if isinstance(mid, str) and mid else None
            if current_id is not None:
                # Every marked turn owns an entry (possibly empty) so
                # an empty-reasoning turn still anchors its neighbours.
                reasoning_by_id.setdefault(current_id, [])
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
            # The sidecar names the turn's prompt unit on tokens_used
            # (protocol v3, hygiene B6); older anchors lack it.
            tid = ev.get("user_message_id")
            if isinstance(tid, str) and tid:
                token_by_id[tid] = {
                    "input": ev.get("input", 0),
                    "output": ev.get("output", 0),
                    "reasoning": ev.get("reasoning", 0),
                    "cache_read": ev.get("cache_read", 0),
                    "cache_write": ev.get("cache_write", 0),
                    "context_input": ev.get("context_input", 0),
                }
    if current_reasoning:
        reasoning_turns.append(current_reasoning)
        if current_id is not None:
            reasoning_by_id.setdefault(current_id, []).extend(current_reasoning)
    # NOTE: reasoning that predates every marker (id-less prefix with
    # marked turns later) stays positional-only and is dropped when
    # the id join wins — unattributable is unattributable; the
    # alternative (positional) misattributes with confidence.

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
            _uid = current["user_message_id"]
            # Review fix: per-turn fallback, not a global switch. A
            # turn WITH an id anchor joins by id; a turn without one
            # (pre-v3 journals, synthetic orphan blocks) falls back to
            # positional. The old global switch discarded positional
            # data for every unanchored turn the moment ANY anchor
            # existed.
            if _uid and _uid in reasoning_by_id:
                rid = reasoning_by_id[_uid]
            else:
                rid = (
                    reasoning_turns[turn_index]
                    if turn_index < len(reasoning_turns)
                    else []
                )
            current["reasoning"] = _clip("".join(rid), MAX_REASONING_CHARS)
            if _uid and _uid in token_by_id:
                tid = token_by_id[_uid]
            else:
                tid = (
                    token_turns[turn_index]
                    if turn_index < len(token_turns)
                    else None
                )
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
                        "output": _clip(msg.get("content"), MAX_TOOL_RESULT_CHARS),
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
                        "output": _clip(msg.get("content"), MAX_TOOL_RESULT_CHARS),
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
