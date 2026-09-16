"""Failure-volume trip + raised total ceiling (2026-09-15).

The total iteration ceiling punished successful long work (the
f774d84b class: ≈40 healthy calls/30min → a 3h turn needs ≈240 >
the old 150). Now health is governed by failure guards (doom =
identical, stuckness = consecutive-all-fail, volume = cumulative
failed ≈1/3 of role ceiling) and the total is a pure cost
backstop (specialist 150 → 300; orchestrator 50 → 150 on
2026-09-16 after a healthy 76-call planning turn died at 50).

Shares the sidecar harness of tests/test_engine_tools.py (that
file is another thread's in-flight work — do NOT duplicate its
fixtures here; import them).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.sidecars import (
    read_port_line,
    spawn_sidecar,
    stop_sidecar,
    wait_for_health,
)
from tests.test_engine_tools import (
    _call,
    _FakeTrace,
    _Handler,
    _message,
    _reset_stub,
    _spec,
    needs_node,
    STUB,
)

ENGINE_SRC = Path(__file__).resolve().parent.parent / "sweave-engine" / "src"


def _node_eval(script: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run an inline ESM script against the sidecar sources."""
    return subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(ENGINE_SRC),
    )


NODE_PREAMBLE = (
    f"import {{ detectUnixShell, bashDescriptionFor }} from "
    f"'{ENGINE_SRC.as_uri()}/tools.js';\n"
)


@pytest.fixture(scope="module")
def stub_api(tmp_path_factory):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture(scope="module")
def sidecar(tmp_path_factory, stub_api):
    data_dir = tmp_path_factory.mktemp("engine-failcap-data")
    repo_root = Path(__file__).resolve().parents[1]
    proc = spawn_sidecar(
        ["node", str(repo_root / "sweave-engine" / "src" / "serve.js"), "--port", "0", "--data-dir", str(data_dir)],
        env={
            **os.environ,
            "SWEAVE_ENGINE_BASE_OPENROUTER": stub_api,
            "SWEAVE_API_URL": stub_api,
            "SWEAVE_MCP_TOKEN": "stub-token",
        },
    )
    line = read_port_line(proc)
    assert line.startswith("SWEAVE_ENGINE_PORT=")
    url = f"http://127.0.0.1:{line.split('=', 1)[1]}"
    wait_for_health(url)
    saved = {k: os.environ.get(k) for k in ("SWEAVE_ENGINE_URL",)}
    os.environ["SWEAVE_ENGINE_URL"] = url
    try:
        yield url
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        stop_sidecar(proc)


@pytest.fixture
def worktree(tmp_path):
    (tmp_path / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
    return tmp_path


@needs_node
async def test_slow_bleed_trips_failure_volume(sidecar, worktree):
    """Varying failures with periodic success defeat doom (never
    3 identical) and streak (never 5 straight) — the volume trip
    (50 cumulative) is what catches it, with a handoff."""
    script = []
    for i in range(70):
        if i % 5 == 4:
            script.append({"calls": [_call("bash", {"command": f"echo ok-{i}"})]})
        else:
            script.append({"calls": [_call("bash", {"command": f"exit {(i % 8) + 1}"})]})
    script.append({"text": "NEVER REACHED"})
    _reset_stub()
    STUB["script"] = script
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["bash"]))
    trace = _FakeTrace()
    result = await proc.send(_message("bleed"), trace=trace)
    assert not result.success
    assert "failure_volume" in (result.error or "")
    handoffs = [p for p in trace.of("step.boundary") if "handoff" in p]
    assert len(handoffs) == 1
    assert handoffs[0]["reason"] == "failure_volume"
    assert handoffs[0]["handoff"]["failedIterations"] == 50


@needs_node
async def test_healthy_high_volume_survives_past_old_cap(sidecar, worktree):
    """170 succeeding reads (past the old 150 total) complete —
    the ceiling no longer kills healthy work."""
    reads = [
        {"filePath": "notes.txt", "offset": 1},
        {"filePath": "notes.txt", "offset": 2},
        {"filePath": "notes.txt"},
    ]
    _reset_stub()
    STUB["script"] = [
        {"calls": [_call("read", reads[i % 3])]} for i in range(170)
    ] + [{"text": "HIGH VOLUME DONE"}]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["read"]))
    trace = _FakeTrace()
    result = await proc.send(_message("read a lot"), trace=trace)
    assert result.success, result.error
    assert result.output == "HIGH VOLUME DONE"
    assert trace.of("tool.failed") == []


@needs_node
async def test_orchestrator_failure_volume_at_fifty(sidecar, worktree):
    """Orchestrator scale (≈1/3 of its 150 ceiling — raised with
    the ceiling on 2026-09-16, same headroom rule), role-gated."""
    script = []
    for i in range(65):
        if i % 5 == 4:
            script.append({"calls": [_call("read", {"filePath": "notes.txt", "offset": (i % 3) + 1})]})
        else:
            script.append({"calls": [_call("read", {"filePath": f"missing-{i}.txt"})]})
    script.append({"text": "NEVER REACHED"})
    _reset_stub()
    STUB["script"] = script
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["read"]))
    trace = _FakeTrace()
    result = await proc.send(
        _message("fumble", metadata={"role": "orchestrator"}), trace=trace
    )
    assert not result.success
    assert "failure_volume" in (result.error or "")
    handoffs = [p for p in trace.of("step.boundary") if "handoff" in p]
    assert len(handoffs) == 1
    assert handoffs[0]["handoff"]["failedIterations"] == 50


@needs_node
async def test_slow_streak_survives_burst_rule(sidecar, worktree):
    """Burst rule (incident b8544168fa59): 4 rapid failures, then a
    SLOW failure (tool-timeout at 120s, span >120s window) — the
    5th failure must NOT trip. Slow accumulations belong to the
    volume trip, not the streak.

    Takes ~2.5 minutes by construction (the window is the point).
    """
    _reset_stub()
    STUB["script"] = [
        {"calls": [_call("bash", {"command": "exit 1"})]},
        {"calls": [_call("bash", {"command": "exit 2"})]},
        {"calls": [_call("bash", {"command": "exit 3"})]},
        {"calls": [_call("bash", {"command": "exit 4"})]},
        {"calls": [_call("bash", {"command": "sleep 140"})]},
        {"text": "SLOW FLAIL SURVIVED"},
    ]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["bash"]))
    trace = _FakeTrace()
    result = await proc.send(_message("flail slowly"), trace=trace)
    assert result.success, result.error
    assert result.output == "SLOW FLAIL SURVIVED"
    assert trace.of("tool.failed")


@needs_node
def test_detect_unix_shell_variants():
    """Detector: explicit Git paths first (never PATH order —
    WSL/Store stubs squat `bash`), override honored, off-Windows
    always null."""
    script = NODE_PREAMBLE + """
const t = (name, cond) => { if (!cond) { console.error("FAIL " + name); process.exit(1); } };
const hit = "C:\\\\Program Files\\\\Git\\\\bin\\\\bash.exe";
t("git-present", detectUnixShell({ platform: "win32", env: {}, exists: (p) => p === hit }) === hit);
t("absent", detectUnixShell({ platform: "win32", env: {}, exists: () => false }) === null);
t("override", detectUnixShell({ platform: "win32", env: { SWEAVE_BASH_PATH: "D:\\\\b\\bash.exe" }, exists: (p) => p === "D:\\\\b\\bash.exe" }) === "D:\\\\b\\bash.exe");
t("override-missing-falls-through", detectUnixShell({ platform: "win32", env: { SWEAVE_BASH_PATH: "D:\\\\nope.exe" }, exists: (p) => p === hit }) === hit);
t("linux", detectUnixShell({ platform: "linux", env: {}, exists: () => true }) === null);
t("x86-path", detectUnixShell({ platform: "win32", env: { ProgramFiles: "X:\\\\none", "ProgramFiles(x86)": "X:\\\\86" }, exists: (p) => p === "X:\\\\86\\\\Git\\\\bin\\\\bash.exe" }) === "X:\\\\86\\\\Git\\\\bin\\\\bash.exe");
console.log("DETECTOR OK");
"""
    proc = _node_eval(script)
    assert proc.returncode == 0, proc.stderr
    assert "DETECTOR OK" in proc.stdout


@needs_node
def test_bash_description_names_shell():
    """Both description variants pinnable deterministically."""
    script = NODE_PREAMBLE + """
const t = (name, cond) => { if (!cond) { console.error("FAIL " + name); process.exit(1); } };
const git = bashDescriptionFor("C:\\\\Git\\\\bin\\\\bash.exe", "win32");
const cmd = bashDescriptionFor(null, "win32");
const nix = bashDescriptionFor(null, "linux");
t("git-names-bash", git.includes("Git Bash") && git.includes("pipes"));
t("cmd-names-cmd", cmd.includes("cmd.exe") && cmd.includes("no head/tail"));
t("nix-plain", !nix.includes("Git Bash") && !nix.includes("cmd.exe"));
t("all-keep-tail-note", git.includes("tail") && cmd.includes("tail") && nix.includes("tail"));
console.log("DESCRIPTIONS OK");
"""
    proc = _node_eval(script)
    assert proc.returncode == 0, proc.stderr
    assert "DESCRIPTIONS OK" in proc.stdout


@needs_node
async def test_shell_runs_unix_suffix_when_detected(sidecar, worktree):
    """Self-consistent on any machine: detect, then expect the
    matching behavior (unix-ism iff a bash was found)."""
    probe = NODE_PREAMBLE + "console.log(detectUnixShell() ? 'BASH' : 'CMD');\n"
    detected = _node_eval(probe)
    assert detected.returncode == 0, detected.stderr
    has_bash = "BASH" in detected.stdout
    _reset_stub()
    STUB["script"] = [
        {"calls": [_call("bash", {"command": "echo hello | grep ell"})]},
        {"text": "SHELL PROBED"},
    ]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["bash"]))
    trace = _FakeTrace()
    result = await proc.send(_message("probe shell"), trace=trace)
    if has_bash:
        assert result.success, result.error
        assert result.output == "SHELL PROBED"
    else:
        # cmd.exe: grep unknown → the tool fails (documents the
        # incident class on bash-less machines).
        assert not result.success or trace.of("tool.failed")
