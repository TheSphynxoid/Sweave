"""Versioned engine<->orchestrator protocol (Step 0 freeze).

No implementation here: pure constants + validation helpers with zero
I/O (no home touches, no sockets), so contract tests run hermetic.

Transport: HTTP on localhost. ``POST /run`` streams Server-Sent Events;
``GET /health`` is the version handshake; ``POST /abort`` and
``POST /revert`` are the control verbs. The Python side (a future
``Harness`` adapter) refuses loudly on version mismatch at connect —
never fails turns cryptically (wire-drift ruling 2026-09-11).
"""

from __future__ import annotations

from typing import Any


#: Protocol version. Bumped only with a user-locked ruling; mismatches
#: refuse loudly at connect (``ProtocolMismatch``), never fail turns
#: cryptically. Carried as ``GET /health`` ``protocol_version`` AND the
#: ``X-Sweave-Engine-Protocol`` header on every request/response.
PROTOCOL_VERSION = "1"

#: Header carrying :data:`PROTOCOL_VERSION` on every protocol message.
PROTOCOL_VERSION_HEADER = "X-Sweave-Engine-Protocol"

#: Harness registry name for the native engine. ``Specialist.harness``
#: already carries this field (default ``"opencode"``); step 4 flips
#: selection + fallback, never the schema.
ENGINE_HARNESS_NAME = "sweave-engine"
OPENCODE_HARNESS_NAME = "opencode"

#: v1 tool baseline — the parity bar. Names mirror opencode's built-ins
#: verbatim (opencode tools docs, 2026-09): ``read`` reads files/dirs;
#: ``edit`` does exact-string replacement; ``write`` creates/overwrites
#: (both gated by the single ``edit`` permission key); ``bash`` runs
#: shell commands; ``glob``/``grep`` search; ``todo`` is the task list
#: (opencode ``todowrite``). Everything else (``lsp``, ``skill``
#: tool-execution, ``plan``, ``webfetch``, ``websearch``, ``patch``)
#: stays opencode-only until demand proves otherwise — each addition
#: needs a user ruling with a trace-use audit first.
TOOL_BASELINE: tuple[str, ...] = (
    "read",
    "edit",
    "write",
    "bash",
    "glob",
    "grep",
    "todo",
)

#: Sweave tools, native calls on this harness (same args + same
#: contract strings as the MCP surface — no ``sweave_`` prefix
#: mangling, no MCP hop). The MCP server stays solely as the opencode
#: adapter.
SWEAVE_NATIVE_TOOLS: tuple[str, ...] = (
    "defer",
    "list_specialists",
    "ask_human",
    "escalate",
)

#: Exact SSE event vocabulary. ``token`` is the only engine-native
#: addition (true token streaming — the liveness the opencode wire
#: lacks); every other name is adopted verbatim from the M1.9 harness
#: vocabulary (``sweave/harness/opencode.py``) per the
#: identical-trace-events invariant. Vocabulary owner is the
#: transparency track — new shapes are proposed there first, never
#: invented here.
TRACE_EVENT_NAMES: tuple[str, ...] = (
    "token",
    "tool.started",
    "tool.updated",
    "tool.completed",
    "tool.failed",
    "step.boundary",
    "permission.asked",
    "done",
    "error",
    "tokens_used",
)

#: ``POST /run`` required fields. ``session_id`` is the durable engine
#: session (attach/resume across engine restarts — ruling 3); the
#: orchestrator still binds it per Sweave Session/Specialist exactly
#: like the opencode ``ses_*`` binding. ``cwd`` is the delegation
#: worktree (one cwd per specialist); ``permission_map`` is the
#: orchestrator-rendered map the engine enforces blindly.
RUN_REQUEST_REQUIRED: tuple[str, ...] = (
    "session_id",
    "composed_prompt",
    "tools",
    "permission_map",
    "model",
    "turn_timeout",
    "cwd",
)

#: Named turn-start failures. ``auth_missing``: a catalog provider with
#: no usable credential fails loudly here (step-1 constraint — config
#: keys -> env -> opencode auth-store bootstrap -> engine OAuth),
#: never as a mid-turn cryptic error.
TURN_START_ERRORS: tuple[str, ...] = ("auth_missing", "protocol_mismatch", "bad_request")


class ProtocolMismatch(Exception):
    """Raised when the engine's protocol version != ours at connect."""


def check_protocol_version(remote_version: str | None) -> str:
    """Accept the handshake version or refuse loudly.

    Returns :data:`PROTOCOL_VERSION` on match; raises
    :class:`ProtocolMismatch` otherwise (including ``None`` — a
    versionless peer is a mismatch, not a default).
    """
    if remote_version != PROTOCOL_VERSION:
        raise ProtocolMismatch(
            f"engine protocol mismatch: local={PROTOCOL_VERSION!r} "
            f"remote={remote_version!r} (refusing; bump together by ruling)"
        )
    return PROTOCOL_VERSION


def validate_run_request(body: dict[str, Any]) -> dict[str, Any]:
    """Validate a ``POST /run`` body; return it unchanged when valid.

    Raises ``ValueError`` with a named reason (``missing:<field>``,
    ``bad:<field>``) so callers surface ``error: bad_request`` without
    spending a model token. Unknown keys are ignored (forward
    compatibility — same rule as the ``defer`` estimate surface).
    """
    if not isinstance(body, dict):
        raise ValueError("bad:body (must be an object)")
    for field_name in RUN_REQUEST_REQUIRED:
        if field_name not in body or body[field_name] is None:
            raise ValueError(f"missing:{field_name}")
    tools = body["tools"]
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        raise ValueError("bad:tools (must be a list of tool names)")
    unknown_tools = [t for t in tools if t not in TOOL_BASELINE + SWEAVE_NATIVE_TOOLS]
    if unknown_tools:
        raise ValueError(f"bad:tools (unknown: {','.join(sorted(unknown_tools))})")
    permission_map = body["permission_map"]
    if not isinstance(permission_map, dict):
        raise ValueError("bad:permission_map (must be an object)")
    model = body["model"]
    if not isinstance(model, dict):
        raise ValueError("bad:model (must be a ModelRef object)")
    turn_timeout = body["turn_timeout"]
    if not isinstance(turn_timeout, (int, float)) or not turn_timeout > 0:
        raise ValueError("bad:turn_timeout (must be > 0 seconds)")
    return body


def parse_event_name(raw: str) -> str:
    """Validate one SSE event name against the frozen vocabulary."""
    if raw not in TRACE_EVENT_NAMES:
        raise ValueError(f"bad:event (unknown {raw!r}; vocabulary is frozen)")
    return raw


#: ``POST /abort {session_id}`` outcomes. Consented engine-stop for the
#: view track's abort endpoint: ``acknowledged`` = the engine stopped
#: the turn; ``UNCONFIRMED`` = stop was attempted but the turn may
#: still be running server-side — LOUD either way, never silent. NOT
#: gated by ``KILL_ON_SILENCE`` (user-initiated kill != watchdog
#: auto-kill). 409 when no live turn (busy-guard semantics match
#: opencode's ``assertNotBusy`` + our ``TurnActiveError``).
ABORT_OUTCOMES: tuple[str, ...] = ("acknowledged", "UNCONFIRMED")


#: ``POST /revert {session_id, to_message}`` — engine rewind for
#: supersede-via-revert (``docs/M2_1_FOLLOWUP_PLAN.md`` section C).
#: Adopts opencode's probed pointer + shadow-git semantics verbatim:
#: revert sets a pointer (message listing still returns everything —
#: truncation is view-level); the NEXT prompt replaces the reverted
#: tail; file-state restore is engine-owned (turn-scoped journal or
#: shadow-tree, decided at the step-2 build) and git-gated (snapshots
#: enable only in git repos — Sweave projects always are, but the
#: capability probe verifies vcs before promising file-undo).
#: Whole-message granularity in v1; busy-guard 409 mid-turn.
REVERT_REQUIRED: tuple[str, ...] = ("session_id", "to_message")


def validate_revert_request(body: dict[str, Any]) -> dict[str, Any]:
    """Validate a ``POST /revert`` body; return it unchanged when valid."""
    if not isinstance(body, dict):
        raise ValueError("bad:body (must be an object)")
    for field_name in REVERT_REQUIRED:
        if field_name not in body or body[field_name] is None:
            raise ValueError(f"missing:{field_name}")
    return body
