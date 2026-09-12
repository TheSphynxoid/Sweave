"""Windows-no-flash platform helper + MCP dispatcher routing.

Pins the 2026-09-09 fixes:

* every subprocess spawn goes through ``sweave.platform`` so no
  console-subsystem child (git, python, opencode, gh, docker) ever
  flashes a CMD window;
* the MCP ``tools/call`` dispatcher routes on the *params* model
  (the -32602 incident: handlers registered against the full
  request models rejected every call).
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


def test_creationflags_is_int_and_no_window_on_windows():
    from sweave.platform import creationflags_no_window

    flags = creationflags_no_window()
    assert isinstance(flags, int)
    if os.name == "nt":
        assert flags == subprocess.CREATE_NO_WINDOW
        assert flags != 0


def test_creationflags_is_zero_off_windows(monkeypatch):
    import sweave.platform as plat

    monkeypatch.setattr(plat.os, "name", "posix")
    assert plat.creationflags_no_window() == 0


def test_run_no_window_injects_creationflags(monkeypatch):
    import sweave.platform as plat

    seen: dict = {}

    def fake_run(*args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(plat.subprocess, "run", fake_run)
    plat.run_no_window(["git", "--version"], capture_output=True)
    assert seen.get("creationflags") == plat.creationflags_no_window()


def test_run_no_window_respects_explicit_flags(monkeypatch):
    import sweave.platform as plat

    seen: dict = {}

    def fake_run(*args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(plat.subprocess, "run", fake_run)
    plat.run_no_window(["git", "--version"], creationflags=1234)
    assert seen.get("creationflags") == 1234


def test_spawn_scratch_serve_is_windowless(monkeypatch):
    """The models-sync scratch serve must not flash/park a visible
    console for the whole sync (user-visible CMD on `sweave models
    sync` and the Settings Sync button)."""
    import sweave.models_sync as msync
    import sweave.platform as plat

    seen: dict = {}

    class _FakeProc:
        pid = 99999

    def fake_popen(*args, **kwargs):
        seen.update(kwargs)
        return _FakeProc()

    monkeypatch.setattr(msync.subprocess, "Popen", fake_popen)
    msync.spawn_scratch_serve("opencode", 18792)
    assert seen.get("creationflags") == plat.creationflags_no_window()


@pytest.mark.skipif(os.name != "nt", reason="taskkill tree-kill is Windows-only")
def test_stop_scratch_serve_tree_kill_is_windowless(monkeypatch):
    """The taskkill reaper flashes too without the discipline."""
    import sweave.models_sync as msync
    import sweave.platform as plat

    seen: dict = {}

    class _FakeProc:
        pid = 99999

    def fake_run(*args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(msync.subprocess, "run", fake_run)
    msync.stop_scratch_serve(_FakeProc())
    assert seen.get("creationflags") == plat.creationflags_no_window()


def test_check_output_no_window_injects_creationflags(monkeypatch):
    import sweave.platform as plat

    seen: dict = {}

    def fake_check_output(*args, **kwargs):
        seen.update(kwargs)
        return b""

    monkeypatch.setattr(plat.subprocess, "check_output", fake_check_output)
    plat.check_output_no_window(["git", "rev-parse", "HEAD"])
    assert seen.get("creationflags") == plat.creationflags_no_window()


def test_pythonw_executable_prefers_windowless_on_windows(monkeypatch):
    import sweave.platform as plat

    if os.name != "nt":
        assert plat.pythonw_executable() == sys.executable
        return
    # Standard install: pythonw.exe sits next to python.exe.
    exe = plat.pythonw_executable()
    assert exe.lower().endswith("pythonw.exe") or exe == sys.executable
    # Absent pythonw falls back to the running interpreter.
    monkeypatch.setattr(plat.os.path, "exists", lambda p: False)
    assert plat.pythonw_executable() == sys.executable


@pytest.mark.asyncio
async def test_dispatcher_routes_unknown_tool_to_text_error(monkeypatch):
    # Managed session (provisioned env token — unprovisioned calls
    # are rejected before routing; see test_m1_6_step1_mcp_server).
    monkeypatch.setenv("SWEAVE_MCP_TOKEN", "test-token")
    from mcp.types import CallToolRequestParams

    from sweave.mcp import _call_tool_dispatcher

    result = await _call_tool_dispatcher(
        None, CallToolRequestParams(name="nope", arguments={})
    )
    assert result.is_error is True
    assert "unknown tool" in result.content[0].text


@pytest.mark.asyncio
async def test_dispatcher_routes_list_specialists(monkeypatch):
    from mcp.types import CallToolRequestParams

    import sweave.mcp as mcp_mod

    async def fake_get(path, token):
        assert path == "/api/mcp/specialists"
        return {"specialists": [{"name": "backend", "description": "d"}]}

    monkeypatch.setattr(mcp_mod, "_http_get", fake_get)
    monkeypatch.setenv("SWEAVE_MCP_TOKEN", "test-token")

    result = await mcp_mod._call_tool_dispatcher(
        None, CallToolRequestParams(name="list_specialists", arguments={})
    )
    assert result.is_error is False
    assert "backend" in result.content[0].text


@pytest.mark.asyncio
async def test_dispatcher_defer_validates_before_http(monkeypatch):
    # Managed session (provisioned env token — see above).
    monkeypatch.setenv("SWEAVE_MCP_TOKEN", "test-token")
    from mcp.types import CallToolRequestParams

    import sweave.mcp as mcp_mod

    # No arguments at all (opencode omits them for some calls) ->
    # tool-level rejection, not a protocol error.
    result = await mcp_mod._call_tool_dispatcher(
        None, CallToolRequestParams(name="defer", arguments=None)
    )
    assert result.is_error is True
    assert result.content[0].text.startswith("rejected:")
