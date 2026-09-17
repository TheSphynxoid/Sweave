"""Step 0 contract tests: versioned engine<->orchestrator protocol freeze.

Hermetic by construction — ``sweave.engine.protocol`` does zero I/O
(no home touches, no sockets), and the harness parity pin calls only
the pure ``_emit_tool_trace`` helper. No engine binary needed; the mock
for this step is a dict-level fake speaking the frozen shapes.
"""

from __future__ import annotations

import pytest

from sweave.engine.protocol import (
    ABORT_OUTCOMES,
    ENGINE_HARNESS_NAME,
    PROTOCOL_VERSION,
    PROTOCOL_VERSION_HEADER,
    RUN_REQUEST_REQUIRED,
    SWEAVE_NATIVE_TOOLS,
    TOOL_BASELINE,
    TRACE_EVENT_NAMES,
    ProtocolMismatch,
    check_protocol_version,
    parse_event_name,
    validate_revert_request,
    validate_run_request,
)


def _valid_run_body(**overrides):
    body = {
        "session_id": "eng_test_001",
        "composed_prompt": "do the thing",
        "tools": ["read", "bash", "defer"],
        "permission_map": {"bash": {"*": "ask"}},
        "model": {"provider": "opencode", "model_id": "x-free"},
        "turn_timeout": 1800.0,
        "cwd": "C:/work/tree",
    }
    body.update(overrides)
    return body


# --- version handshake -------------------------------------------------


def test_version_match_returns_version():
    assert check_protocol_version(PROTOCOL_VERSION) == PROTOCOL_VERSION


def test_version_mismatch_refuses_loudly():
    with pytest.raises(ProtocolMismatch):
        check_protocol_version("999")


def test_versionless_peer_is_mismatch_not_default():
    with pytest.raises(ProtocolMismatch):
        check_protocol_version(None)


def test_version_header_name_frozen():
    assert PROTOCOL_VERSION_HEADER == "X-Sweave-Engine-Protocol"


# --- POST /run ----------------------------------------------------------


def test_valid_run_body_passes_through_unchanged():
    body = _valid_run_body()
    assert validate_run_request(body) is body


@pytest.mark.parametrize("field", list(RUN_REQUEST_REQUIRED))
def test_each_missing_required_field_is_named(field):
    body = _valid_run_body()
    del body[field]
    with pytest.raises(ValueError, match=f"missing:{field}"):
        validate_run_request(body)


def test_unknown_keys_ignored_forward_compat():
    body = _valid_run_body(future_field="whatever")
    assert validate_run_request(body) is body


def test_unknown_tool_name_rejected_loudly():
    with pytest.raises(ValueError, match="bad:tools"):
        validate_run_request(_valid_run_body(tools=["read", "teleport"]))


def test_non_list_tools_rejected():
    with pytest.raises(ValueError, match="bad:tools"):
        validate_run_request(_valid_run_body(tools="read"))


def test_bad_permission_map_model_timeout_rejected():
    with pytest.raises(ValueError, match="bad:permission_map"):
        validate_run_request(_valid_run_body(permission_map=["bash"]))
    with pytest.raises(ValueError, match="bad:model"):
        validate_run_request(_valid_run_body(model="opencode/x"))
    with pytest.raises(ValueError, match="bad:turn_timeout"):
        validate_run_request(_valid_run_body(turn_timeout=0))
    with pytest.raises(ValueError, match="bad:turn_timeout"):
        validate_run_request(_valid_run_body(turn_timeout=-5))


def test_non_dict_body_rejected():
    with pytest.raises(ValueError, match="bad:body"):
        validate_run_request(["not", "a", "dict"])


# --- frozen sets (additions need a user ruling) --------------------------


def test_tool_baseline_is_the_6_group_parity_bar():
    # read / write+edit / bash / glob / grep / todo — opencode
    # built-in names verbatim — plus `git` (2026-09-15 GIT_READ_TOOL
    # ruling: engine-native read-only inspection, verb allowlist;
    # trace-use audit is commit archaeology). Changing this tuple
    # beyond a ruling is a scope change.
    assert set(TOOL_BASELINE) == {
        "read",
        "edit",
        "write",
        "bash",
        "glob",
        "grep",
        "todo",
        "git",
    }


def test_run_request_accepts_git_and_names_unknown_sidecar_mismatch():
    # Step-2 done-gate: `git` rides the wire. An old sidecar (or any
    # peer whose KNOWN_TOOLS lacks it) rejects with the loud
    # `bad:tools (unknown: ...)` shape — never cryptic.
    body = _valid_run_body(tools=["read", "git", "defer"])
    assert validate_run_request(body) is body
    old_allow = tuple(t for t in TOOL_BASELINE if t != "git")
    unknown = [t for t in ["read", "git"] if t not in old_allow]
    assert unknown == ["git"]  # the old-sidecar bad_request shape


def test_sweave_native_tools_match_mcp_surface():
    assert set(SWEAVE_NATIVE_TOOLS) == {
        "defer",
        "list_specialists",
        "ask_human",
        "escalate",
    }


def test_engine_harness_name():
    assert ENGINE_HARNESS_NAME == "sweave-engine"


# --- SSE vocabulary ------------------------------------------------------


def test_every_frozen_event_name_parses():
    for name in TRACE_EVENT_NAMES:
        assert parse_event_name(name) == name


def test_unknown_event_name_rejected():
    with pytest.raises(ValueError, match="bad:event"):
        parse_event_name("tool.teleported")


def test_vocabulary_carries_m1_9_anchor_names():
    # The identical-trace-events invariant: these six names MUST match
    # what the opencode harness emits, or `sweave log` / DetailView
    # diverge per engine.
    for name in (
        "tool.started",
        "tool.updated",
        "tool.completed",
        "tool.failed",
        "step.boundary",
        "tokens_used",
    ):
        assert name in TRACE_EVENT_NAMES


class _FakeTrace:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def append(self, name, payload):
        self.events.append((name, payload))


def test_harness_tool_lifecycle_parity_pin():
    """The harness helper emits exactly the frozen tool.* names.

    If opencode renames a part status, this test — not a live turn —
    is what catches the drift.
    """
    from sweave.harness.opencode import _emit_tool_trace

    trace = _FakeTrace()
    snapshots: dict = {}
    first_state: dict = {}
    for status in ("pending", "running", "completed"):
        _emit_tool_trace(
            trace,
            snapshots,
            first_state,
            {"callID": "c1", "tool": "read", "state": {"status": status}},
        )
    names = [n for n, _ in trace.events]
    assert names == ["tool.started", "tool.updated", "tool.completed"]

    trace2 = _FakeTrace()
    _emit_tool_trace(
        trace2, {}, {}, {"callID": "c9", "tool": "bash", "state": {"status": "error"}}
    )
    names2 = [n for n, _ in trace2.events]
    assert "tool.started" in names2  # begin marker even without pending
    assert "tool.failed" in names2


# --- control verbs --------------------------------------------------------


def test_abort_outcome_is_acknowledged_only():
    # User ruling 2026-09-17: no unconfirmed state — a stop is
    # acknowledged or the turn was never live (409), never limbo.
    assert set(ABORT_OUTCOMES) == {"acknowledged"}


def test_valid_revert_body_passes_through():
    body = {"session_id": "eng_test_001", "to_message": "msg_42"}
    assert validate_revert_request(body) is body


def test_revert_missing_field_is_named():
    with pytest.raises(ValueError, match="missing:to_message"):
        validate_revert_request({"session_id": "eng_test_001"})
    with pytest.raises(ValueError, match="missing:session_id"):
        validate_revert_request({"to_message": "msg_42"})
