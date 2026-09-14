"""Per-turn tool-call records for chat transparency (opencode-style).

The chat thread shows *what the turn did*, not just what it said:
compact one-liner rows (``Read src/foo.ts``) with an expandable diff
for edits. This module is the shared shape + summarizer behind that:

* harnesses (opencode / engine) report raw tool transitions via the
  ``on_tool`` callback (``{"callID", "tool", "status", "input",
  "output", "error", "title"}``),
* :func:`compact_tool_record` turns one transition into the small
  persisted row (``metadata.tools[]`` on the assistant message),
* :func:`summarize_tool_call` derives the one-liner from the tool
  name + its input (``read`` + ``{filePath: ...}`` -> the path).

Design notes:

* UI-only projection. The composer (``transcript.py``) reads only
  ``role`` / ``content`` / ``metadata.superseded`` off past messages,
  so ``metadata.tools`` can never leak into the model context. The
  provider-visible tool history stays where it belongs: the harness
  session (opencode sqlite / engine session file) via session resume.
  This mirrors opencode: its UI renders the session's tool parts, it
  does not paste them into a prompt.
* Delegation-generic (not orchestrator-specific): rows are keyed by
  (delegation, round), so future specialist chats reuse the same
  pipeline + WS event.
* Never raises: harnesses call this on a hot stream path with
  provider-shaped payloads; garbage in means a best-effort row out.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: One-liner budget per tool row (opencode shows the path/command, not
#: the whole input blob).
SUMMARY_CHARS = 160

#: Persisted input budget (edit/write rows carry old/new strings so the
#: expandable diff works offline; the full output stays in the trace /
#: detail endpoint, never on the message).
INPUT_JSON_CHARS = 6_000

#: Pathological-turn guard: a wedged tool loop must not blow up the
#: session JSON. Oldest rows win (the turn's first actions are usually
#: the informative ones); extras still stream live + land in the trace.
MAX_TOOLS_PER_MESSAGE = 100

#: Input keys consulted for the one-liner, in priority order. Covers
#: the engine sidecar shapes (filePath/command/pattern) and opencode
#: native shapes (filePath/file_path/command) plus generic fallbacks.
_SUMMARY_KEYS: tuple[str, ...] = (
    "filePath",
    "file_path",
    "path",
    "file",
    "filename",
    "command",
    "cmd",
    "pattern",
    "regex",
    "query",
    "text",
    "task",
    "description",
    "title",
)

_EDIT_LIKE = re.compile(r"edit|write|patch|create|apply", re.IGNORECASE)


def _as_text(value: Any) -> str | None:
    """Best-effort scalar render of one input value (None when empty)."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (list, tuple)):
        bits = [_as_text(v) for v in value[:3]]
        bits = [b for b in bits if b]
        return ", ".join(bits) or None
    return None


def summarize_tool_call(tool: Any, input: Any) -> str:
    """One-liner detail for a tool row (the path / command / pattern).

    ``Read`` + ``{"filePath": "src/foo.ts"}`` -> ``"src/foo.ts"``.
    Never raises; unknown shapes degrade to ``""`` (the UI then shows
    just the tool verb).
    """
    try:
        if isinstance(input, dict):
            lowered = {str(k).lower(): v for k, v in input.items()}
            for key in _SUMMARY_KEYS:
                if key in input:
                    text = _as_text(input[key])
                    if text:
                        return text[:SUMMARY_CHARS]
                low = lowered.get(key.lower())
                if low is not None and key not in input:
                    text = _as_text(low)
                    if text:
                        return text[:SUMMARY_CHARS]
        elif isinstance(input, str):
            text = input.strip()
            if text:
                return text[:SUMMARY_CHARS]
        return ""
    except Exception:  # noqa: BLE001 — hot path, never fail a turn
        return ""


def _capped_input(tool_name: str, input: Any) -> Any | None:
    """Persisted input for expandable rows (edit/write diffs only).

    Reads stay one-liners (their summary is the whole story); edit-like
    tools keep their input JSON capped so the diff renders offline.
    Returns None when there is nothing worth persisting.
    """
    try:
        if not isinstance(input, dict) or not input:
            return None
        if not _EDIT_LIKE.search(str(tool_name or "")):
            return None
        text = json.dumps(input, default=str)
        if len(text) > INPUT_JSON_CHARS:
            text = text[:INPUT_JSON_CHARS] + "…[truncated]"
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return None


def tool_event(call_id: Any, tool: Any, state: Any) -> dict[str, Any]:
    """Normalize one tool transition into the ``on_tool`` event shape.

    Both harnesses report ``{callID, tool, state: {status, input,
    output?, error?, title?}}`` (opencode v2 parts, engine SSE), so one
    normalizer serves both. Never raises.
    """
    try:
        st = state if isinstance(state, dict) else {}
        return {
            "callID": str(call_id or ""),
            "tool": str(tool or "tool"),
            "status": str(st.get("status") or "unknown"),
            "input": st.get("input", {}),
            "output": st.get("output"),
            "error": st.get("error"),
            "title": st.get("title"),
        }
    except Exception:  # noqa: BLE001
        return {
            "callID": "",
            "tool": "tool",
            "status": "unknown",
            "input": {},
            "output": None,
            "error": None,
            "title": None,
        }


def compact_tool_record(event: Any, *, round: int = 0) -> dict[str, Any]:
    """Build one persisted tool row from a harness ``on_tool`` event.

    Never raises; missing fields degrade to empty strings (the row
    still marks that *something* ran under that callID).
    """
    try:
        ev = event if isinstance(event, dict) else {}
        call_id = str(ev.get("callID") or ev.get("call_id") or "")
        tool = str(ev.get("tool") or "tool")
        status = str(ev.get("state", ev.get("status", "")) or "")
        if isinstance(ev.get("state"), dict):
            status = str(ev["state"].get("status", "") or status)
        state = ev.get("state") if isinstance(ev.get("state"), dict) else {}
        tool_input = ev.get("input", state.get("input", {}))
        title = ev.get("title", state.get("title"))
        return {
            "callID": call_id,
            "tool": tool,
            "status": status or "unknown",
            "summary": summarize_tool_call(tool, tool_input),
            "title": str(title)[:SUMMARY_CHARS] if title else None,
            "input": _capped_input(tool, tool_input),
            "round": int(round),
        }
    except Exception:  # noqa: BLE001
        return {
            "callID": "",
            "tool": "tool",
            "status": "unknown",
            "summary": "",
            "title": None,
            "input": None,
            "round": int(round) if isinstance(round, int) else 0,
        }
