"""Sweave-native execution engine (custom harness) — protocol layer.

Step 0 (protocol freeze): versioned engine<->orchestrator contract with
NO implementation. The engine binary (a TS sidecar, later steps) speaks
this protocol; the Python orchestrator keeps ALL knowledge (prompt
composition, tool schemas + permission roots, model/budget, traces) and
the engine owns execution only (LLM call, token stream, tool execution
in the worktree cwd).

Baselines are adopted verbatim, never re-designed here:
* 6-tool executor + permission triples + once/always/reject outcomes:
  opencode built-ins + ``permission`` docs (allow/ask/deny,
  last-match-wins, per-agent override, ``external_directory``).
* Revert verb: ``POST /session/{id}/revert {messageID}`` pointer
  semantics + shadow-git file restore, per
  ``docs/M2_1_FOLLOWUP_PLAN.md`` section C probes (2026-09-13).
* Trace vocabulary: ``tool.*`` / ``step.boundary`` / ``tokens_used``
  shapes identical to the M1.9 audit anchor
  (``sweave/harness/opencode.py``); the transparency track owns the
  vocabulary, this module adopts it (identical-trace-events invariant).
* Sweave-tool contract strings: ``queued:`` / ``rejected:`` /
  ``escalated:`` lines from ``sweave/mcp/__init__.py``.
"""

from __future__ import annotations

from sweave.engine.protocol import (
    PROTOCOL_VERSION,
    PROTOCOL_VERSION_HEADER,
    ENGINE_HARNESS_NAME,
    ROLE_ORCHESTRATOR,
    ROLE_SPECIALIST,
    RUN_REQUEST_OPTIONAL,
    TOOL_BASELINE,
    SWEAVE_NATIVE_TOOLS,
    TRACE_EVENT_NAMES,
    ProtocolMismatch,
    check_protocol_version,
    parse_event_name,
    validate_run_request,
)

__all__ = [
    "PROTOCOL_VERSION",
    "PROTOCOL_VERSION_HEADER",
    "ENGINE_HARNESS_NAME",
    "ROLE_ORCHESTRATOR",
    "ROLE_SPECIALIST",
    "RUN_REQUEST_OPTIONAL",
    "TOOL_BASELINE",
    "SWEAVE_NATIVE_TOOLS",
    "TRACE_EVENT_NAMES",
    "ProtocolMismatch",
    "check_protocol_version",
    "parse_event_name",
    "validate_run_request",
]
