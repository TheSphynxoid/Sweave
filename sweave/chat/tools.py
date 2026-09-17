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

#: TOOL_CARDS step 1 (2026-09-16): the additive per-row `detail` blob --
#: bounded so the persisted row (and thus the chat history itself) never
#: bloats (the 2026-09-14 607K-read lesson). bash `output_excerpt` rides
#: its own cap so a 32K tool result stays 2K in the chat thread; the full
#: text already lands in the trace / detail endpoint (offline-readable).
DETAIL_JSON_CHARS = 2_000
OUTPUT_EXCERPT_CHARS = 2_000
#: Write old-capture cap (plan F4): small files only; a bigger file
#: degrades to preview+stats (never fails the turn).
WRITE_OLD_CAPTURE_CHARS = 200_000

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

# TOOL_CARDS step 1: structured-window regex for the *engine read*
# footer. The plan says: the projector NEVER parses footer text on
# either path — the footer parser here is only for the pre-structured
# window back-compat (engine turns emitted before the sidecar gains
# state.window ride the start-point value, marked `from_footer: true`
# so the UI knows it is derived, not carried). Once the sidecar emits
# the structured window these rows stop matching the regex.
_READ_WINDOW_RE = re.compile(
    r"\(Showing lines (\d+)-(\d+) of (\d+)\. Use offset=(\d+) to continue\.\)"
)
_READ_WINDOW_RE_ACC = re.compile(
    r"\(Showing lines (\d+)-(\d+) of (\d+)\. Use offset=(\d+)\)"
)
_EXIT_RE = re.compile(r"\bexit (-?\d+)[:\s]")


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


def _clip(text: Any, limit: int, marker: str = "…[truncated]") -> str:
    """Caps *text* at *limit* chars with an honest truncation marker."""
    s = text if isinstance(text, str) else ("" if text is None else str(text))
    if len(s) <= limit:
        return s
    return s[:limit] + marker


# ---------------------------------------------------------------------------
# Tool-cards detail (TOOL_CARDS plan step 1, 2026-09-16): one additive
# `detail` blob per row, generic over input + output so BOTH harnesses
# benefit. Not engine-only: opencode rows derive from the parts they
# carry; missing fields stay ABSENT (never guessed, never footer-parsed
# — one explicit back-compat derivation is the legacy-window exception,
# marked `from_footer`).
# ---------------------------------------------------------------------------


def _first_str(d: dict, *keys: str) -> Any:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def _read_window_from_output(output: Any) -> dict[str, Any] | None:
    """Derive the read window from the pre-structured engine footer.

    EXPLICITLY a back-compat deviation from ruling F3 ("never parse the
    footer"): rows persist BEFORE the sidecar gains the structured
    window emit; the marker says derived. The engine's own structured
    `state.window` (once shipped) rides first and this parser never
    fires (the marker will be absent). Opencode rows: absent if their
    footer text does not match — never guessed.
    """
    try:
        if not isinstance(output, str) or not output:
            return None
        for rx in (_READ_WINDOW_RE, _READ_WINDOW_RE_ACC):
            m = rx.search(output)
            if m:
                shown_from, shown_to, total, next_off = m.groups()
                return {
                    "shownFrom": int(shown_from),
                    "shownTo": int(shown_to),
                    "total": int(total),
                    "nextOffset": int(next_off),
                    "from_footer": True,
                }
    except Exception:  # noqa: BLE001 — never raises
        return None
    return None


def _derive_window(state: dict, tool: str, output: Any, tool_input: dict) -> dict | None:
    window = state.get("window")
    if isinstance(window, dict) and window.get("shownFrom") is not None:
        return {
            "shownFrom": window.get("shownFrom"),
            "shownTo": window.get("shownTo"),
            "total": window.get("total"),
            "from_footer": False,
        }
    # Pre-structured engine rows: derivable from the footers only.
    derived = _read_window_from_output(output)
    return derived


def _derive_exit(state: dict, tool: str, output: Any, err: Any) -> int | None:
    """exit code: structured where the engine carries it; otherwise the
    failure string only (opencode parses NOTHING — absent stays absent).
    Bash COMMAND also rides even on failure (the plan's primary ask)."""
    if tool.strip().lower() != "bash":
        return None
    v = state.get("exit")
    if isinstance(v, (int, float)):
        return int(v)
    src = err if isinstance(err, str) else ("" if output is None else str(output))
    m = _EXIT_RE.search(src or "")
    if m:
        try:
            return int(m.group(1))
        except Exception:  # noqa: BLE001
            return None
    return None


def _grep_match_count(state: dict) -> int | None:
    """True when *state/output* carries a match count for grep-like rows."""
    v = state.get("matchCount", state.get("count"))
    if isinstance(v, (int, float)):
        return int(v)
    return None


def build_tool_detail(tool: Any, tool_input: Any, state: dict, output: Any, error: Any) -> dict:
    """Per-tool additive `detail` blob (plan step 1 — §3.1 matrix).

    NEVER match content in grep rows; NEVER parse footer text except the
    explicit back-compat `from_footer` window dev; garbage in returns a
    best-effort {} (composer isolation holds; hot-stream safe).
    """
    name = str(tool or "").strip().lower()
    inp = tool_input if isinstance(tool_input, dict) else {}
    out: dict[str, Any] = {}
    try:
        if name == "read":
            ow = _derive_window(state, name, output, inp)
            if ow is not None:
                out["window"] = ow
            # filePath is the summary's job; only the window is additive.
        elif name == "write":
            out["mode"] = "create" if not inp.get("old") else "overwrite"
            out["linesAdded"] = (
                len(str(inp.get("content") or "").splitlines())
                if isinstance(inp.get("content"), str) else None
            )
            out["preview"] = (
                _clip(inp.get("content"), 200) if isinstance(inp.get("content"), str) else None
            )
            if inp.get("old"):
                out["linesRemoved"] = len(str(inp.get("old")).splitlines())
                out["old_capture"] = _clip(inp.get("old"), WRITE_OLD_CAPTURE_CHARS)
        elif name == "edit":
            # Edit keeps full old/new in `input` already (the
            # _capped_input edit-like rule); detail carries the churn
            # stats so the card shows +N/-N lines without the blob.
            old = inp.get("oldString")
            new = inp.get("newString")
            if isinstance(old, str) and isinstance(new, str):
                out["linesAdded"] = len(new.splitlines())
                out["linesRemoved"] = len(old.splitlines())
        elif name == "bash":
            out["command"] = inp.get("command") or inp.get("cmd")
            out["exit"] = _derive_exit(state, name, output, error)
            if isinstance(output, str) and output:
                out["output_excerpt"] = _clip(output, OUTPUT_EXCERPT_CHARS)
                # `truncated`: honest signal — the sidecar's inner cap
                # (32K) rode the row's output verbatim, this 2K excerpt
                # rides the DETAIL cap. Either way the UI shows it.
                out["truncated"] = (
                    "truncated" in output
                    or len(output) > OUTPUT_EXCERPT_CHARS
                )
        elif name == "grep":
            out["pattern"] = inp.get("pattern") or inp.get("regex")
            out["path"] = inp.get("path")
            out["include"] = inp.get("include")
            mc = _grep_match_count(state)
            out["matchCount"] = mc  # None when neither path carries it
        elif name == "glob":
            out["pattern"] = inp.get("pattern")
            out["path"] = inp.get("path")
            cnt = state.get("count", state.get("matchCount"))
            out["count"] = int(cnt) if isinstance(cnt, (int, float)) else None
        elif name == "git":
            out["verb"] = inp.get("verb") or inp.get("subcommand")
            out["args"] = inp.get("args")
        elif name == "todo":
            todos = inp.get("todos")
            if isinstance(todos, list):
                out["titles"] = [
                    str(t.get("content")) for t in todos if isinstance(t, dict) and t.get("content")
                ]
    except Exception:  # noqa: BLE001 — hot path, garbage never raises
        return out
    return out


def _capped_ks(d: dict, keys: tuple[str, ...]) -> bool:
    for k in keys:
        if k in d:
            return True
    return False


def output_excerpt_for(text: Any, limit: int = OUTPUT_EXCERPT_CHARS) -> str | None:
    """The 2K inline tail for bash rows (plan F2). Absent stays absent."""
    if not isinstance(text, str) or not text:
        return None
    return _clip(text, limit)


def tool_event(call_id: Any, tool: Any, state: Any) -> dict[str, Any]:
    """Normalize one tool transition into the ``on_tool`` event shape.

    Both harnesses report ``{callID, tool, state: {status, input,
    output?, error?, title?}}`` (opencode v2 parts, engine SSE), so one
    normalizer serves both. Never raises.

    TOOL_CARDS step 1: the state rides verbatim (including the engine's
    additive extras — window/exit/count/old-capture) so the detail
    builder sees what the wire carried; projection stays one consumer.
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

    TOOL_CARDS step 1: the row gains the additive `detail` blob
    (per-tool capped) — chat + detail + specialist surfaces all ride
    the same row, so one builder serves both renderers. Garbage in
    still produces a best-effort row (hot-stream safe).
    """
    try:
        ev = event if isinstance(event, dict) else {}
        # TOOL_CARDS step 1: the additive extras (window / exit /
        # count / old-capture) live on the harness STATE. Two
        # callers, two shapes: the raw wire gives state; the
        # normalized event already flattens them (see tool_event).
        # Detail derives from whichever carries them, so both
        # harnesses and both call paths share one builder.
        raw_state = ev.get("state") if isinstance(ev.get("state"), dict) else {}
        extras_state = {
            k: raw_state[k] for k in raw_state if k not in ("status", "input", "output", "error", "title")
        }
        extras_state.update({
            k: v for k, v in ev.items()
            if k not in ("status", "input", "output", "error", "title", "tool", "callID", "round", "state", "summary")
        })
        call_id = str(ev.get("callID") or ev.get("call_id") or "")
        tool = str(ev.get("tool") or "tool")
        status = str(ev.get("state", ev.get("status", "")) or "")
        if isinstance(ev.get("state"), dict):
            status = str(ev["state"].get("status", "") or status)
        state = ev.get("state") if isinstance(ev.get("state"), dict) else {}
        tool_input = ev.get("input", state.get("input", {}))
        title = ev.get("title", state.get("title"))
        row = {
            "callID": call_id,
            "tool": tool,
            "status": status or "unknown",
            "summary": summarize_tool_call(tool, tool_input),
            "title": str(title)[:SUMMARY_CHARS] if title else None,
            "input": _capped_input(tool, tool_input),
            # Additive (plan F1/F6): the per-tool detail blob + the 2K
            # bash excerpt. Caps pin row bloat; absent fields stay
            # absent so legacy rows degrade to today's one-liners.
            "detail": _capped_detail(tool, tool_input, extras_state, ev.get("output"), ev.get("error", raw_state.get("error"))),
            "round": int(round),
        }
        if state.get("output") or ev.get("output"):
            src = ev.get("output") or state.get("output")
            if isinstance(src, str):
                exc = output_excerpt_for(src)
                if exc is not None:
                    row["output_excerpt"] = exc
        # `input`/`round` always ride (v1 row contract); the additive
        # keys ride only when present so legacy UIs degrade cleanly.
        payload = {
            "callID": row["callID"],
            "tool": row["tool"],
            "status": row["status"],
            "summary": row["summary"],
            "title": row["title"],
            "input": row["input"],
            "round": row["round"],
        }
        for key in ("detail", "output_excerpt"):
            if row.get(key) is not None:
                payload[key] = row[key]
        return payload
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


def _capped_detail(tool: Any, tool_input: Any, state: dict, output: Any, error: Any) -> dict:
    """build_tool_detail with the DETAIL_JSON_CHARS cap (the row's
    additive blob rides the row everywhere, so the largest per-row
    growth IS the detail; caps in the matrix tests)."""
    raw = build_tool_detail(tool, tool_input, state, output, error)
    if not isinstance(raw, dict):
        return {}
    try:
        text = json.dumps(raw, default=str, ensure_ascii=False)
        if len(text) <= DETAIL_JSON_CHARS:
            return raw
        # Drop the biggest field first (prefer preview/old_capture/titles),
        # then clip to the cap honestly.
        for big in ("old_capture", "preview", "command", "args", "titles",
                    "pattern", "path", "include", "verb", "output_excerpt"):
            if big in raw and raw[big] is not None:
                raw[big] = _clip(raw[big], DETAIL_JSON_CHARS // 4)
                text = json.dumps(raw, default=str, ensure_ascii=False)
                if len(text) <= DETAIL_JSON_CHARS:
                    return raw
        return {
            "clipped": True,
            "detail_chars": len(text),
        }
    except Exception:  # noqa: BLE001
        return {}
