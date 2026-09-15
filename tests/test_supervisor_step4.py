"""Supervisor step 4: opencode activity ferry (tool-started pulses).

The SSE bus is silent mid-tool, so the island plugin's
``tool.execute.before`` hook ferries starts to
``POST /api/activity/tool-started``, attributed via the session
registry into ``tool.started`` trace pulses — which the
supervisor already counts (zero supervisor code change by
design). Pins: endpoint attribution shapes, plugin source
contract (hook + path + token + never-break-host), and the
pulse-layer integration (a ferried start moves ``_last_pulse``).
"""

from __future__ import annotations

import json
from pathlib import Path

from sweave.runtime import permission_bridge as bridge


def _trace_events(traces_dir: Path, delegation_id: str) -> list[dict]:
    from sweave.runtime.trace_log import TraceLog

    path = TraceLog(delegation_id, base_dir=traces_dir).path
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_ferry_notes_registered_session(tmp_path: Path):
    bridge.register_session("ses-ferry-1", "http://127.0.0.1:9", tmp_path, "dlg-ferry-1")
    try:
        out = bridge.record_ferried_tool_started(
            {"session_id": "ses-ferry-1", "tool": "bash", "target": "sleep 60"},
            traces_dir=tmp_path / "traces",
        )
        assert out == {"status": "ok", "noted": True, "delegation_id": "dlg-ferry-1"}
        events = _trace_events(tmp_path / "traces", "dlg-ferry-1")
        starts = [e for e in events if e.get("event") == "tool.started"]
        assert len(starts) == 1
        assert starts[0]["tool"] == "bash"
        assert starts[0]["source"] == "ferry"
    finally:
        bridge._SESSIONS.pop("ses-ferry-1", None)


def test_ferry_unknown_session_noops(tmp_path: Path):
    out = bridge.record_ferried_tool_started(
        {"session_id": "ses-ghost", "tool": "bash", "target": "x"},
        traces_dir=tmp_path / "traces",
    )
    assert out["status"] == "ok" and out["noted"] is False
    assert not (tmp_path / "traces").exists()


def test_ferry_missing_session_noops(tmp_path: Path):
    out = bridge.record_ferried_tool_started(
        {"tool": "bash"}, traces_dir=tmp_path / "traces"
    )
    assert out["status"] == "ok" and out["noted"] is False


def test_ferry_accepts_sessionID_spellings(tmp_path: Path):
    bridge.register_session("ses-ferry-2", "http://127.0.0.1:9", tmp_path, "dlg-ferry-2")
    try:
        out = bridge.record_ferried_tool_started(
            {"sessionID": "ses-ferry-2", "tool": "read"},
            traces_dir=tmp_path / "traces",
        )
        assert out["noted"] is True
    finally:
        bridge._SESSIONS.pop("ses-ferry-2", None)


def test_ferried_start_counts_as_pulse(tmp_path: Path):
    """Zero-code supervisor consumption: the ferried start moves
    ``_last_pulse`` (the incident class that motivated the ferry
    would have pulsed)."""
    from sweave.runtime.job_runner import JobRunner

    bridge.register_session("ses-ferry-3", "http://127.0.0.1:9", tmp_path, "dlg-ferry-3")
    try:
        traces = tmp_path / "traces"
        bridge.record_ferried_tool_started(
            {"session_id": "ses-ferry-3", "tool": "bash", "target": "sleep 60"},
            traces_dir=traces,
        )
        from sweave.runtime.delegation_store import Delegation
        from sweave.runtime.trace_log import TraceLog

        runner = JobRunner(
            delegate_tool=None,  # type: ignore[arg-type]
            delegation_stores=None,  # type: ignore[arg-type]
            turn_timeout=30.0,
        )
        pulsed = runner._last_pulse(TraceLog("dlg-ferry-3", base_dir=traces))
        assert pulsed is not None
        assert pulsed[1] == "tool.started bash"
    finally:
        bridge._SESSIONS.pop("ses-ferry-3", None)


def test_plugin_source_contract():
    src = bridge.bundled_plugin_source()
    # Hook + endpoint + token guard (the ferry contract).
    assert '"tool.execute.before"' in src
    assert "/api/activity/tool-started" in src
    assert "X-Sweave-MCP-Token" in src
    # Never-break-host posture (both handlers swallow).
    assert src.count("never break the host") >= 2
    # Minimal payload: name + compact target, never file contents.
    assert "slice(0, 200)" in src
    assert "permission.asked" in src  # original ferry intact
