"""Engine step 2 tests: tool executor + permission enforcement + loop.

Hermetic: a localhost stub plays BOTH the provider (scripted
OpenAI-compatible SSE with tool_calls) and the Sweave API
(defer/list/ask/escalate/permission). The REAL sidecar executes
against a tmp worktree; the REAL adapter drives it.

Covers the step-2 done-gate shape: scripted read->edit->bash->grep
turn green with byte-identical trace event names, todo lifecycle,
deny/ask/allow enforcement incl. last-match-wins + external_directory
defaults, orchestrator sweave tools with identical contract strings,
specialist structural gate, doom-loop guard, and the no-loop
single-shot regression.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.sidecars import (
    read_port_line,
    spawn_sidecar,
    stop_sidecar,
    wait_for_health,
)

node_missing = shutil.which("node") is None
needs_node = pytest.mark.skipif(node_missing, reason="node not on PATH")

STUB: dict = {
    "script": [],
    "requests": [],
    "req_headers": [],
    "tasks": [],
    "escalations": [],
    "permissions": [],
    "escalation_status": {"status": "answered", "response": "Yes, proceed"},
    "permission_status": {"status": "answered", "response": "once"},
}


def _sse_chunk(obj: dict) -> bytes:
    return f"data: {json.dumps(obj)}\n\n".encode()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # quiet
        pass

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}

    def _send(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError):
            pass

    def do_GET(self):
        if self.path == "/api/mcp/specialists":
            return self._send(200, {"specialists": [{"name": "backend", "description": "Builds APIs"}]})
        if self.path.startswith("/api/delegations/") and self.path.endswith("/escalation"):
            return self._send(200, dict(STUB["escalation_status"]))
        return self._send(404, {"detail": "stub: unknown GET " + self.path})

    def do_POST(self):
        body = self._read_json()
        if self.path == "/chat/completions":
            STUB["requests"].append(body)
            STUB["req_headers"].append(
                {
                    "user_agent": self.headers.get("User-Agent", ""),
                    "session": self.headers.get("x-opencode-session", ""),
                }
            )
            if not body.get("messages"):
                # Live-provider parity: a request without messages is a
                # 400 (this exact bug shipped once — empty history slice).
                return self._send(400, {"error": {"message": 'Input required: specify "prompt" or "messages"', "code": 400}})
            step = STUB["script"].pop(0) if STUB["script"] else {"text": "DONE"}
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                if step.get("text"):
                    self.wfile.write(_sse_chunk({"choices": [{"delta": {"content": step["text"]}}]}))
                    self.wfile.flush()
                for i, call in enumerate(step.get("calls", [])):
                    self.wfile.write(
                        _sse_chunk(
                            {
                                "choices": [
                                    {
                                        "delta": {
                                            "tool_calls": [
                                                {
                                                    "index": i,
                                                    "id": call.get("id", f"call_{len(STUB['requests'])}_{i}"),
                                                    "type": "function",
                                                    "function": {
                                                        "name": call["name"],
                                                        "arguments": json.dumps(call.get("args", {})),
                                                    },
                                                }
                                            ]
                                        }
                                    }
                                ]
                            }
                        )
                    )
                    self.wfile.flush()
                usage = step.get("usage", {"prompt_tokens": 10, "completion_tokens": 5})
                self.wfile.write(_sse_chunk({"choices": [{"delta": {}}], "usage": usage}))
                self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (ConnectionResetError, BrokenPipeError):
                pass
            return
        if self.path == "/api/v2/tasks":
            STUB["tasks"].append(body)
            if body.get("agent") == "missing-nope":
                return self._send(404, {"detail": "unknown specialist 'missing-nope'"})
            return self._send(200, {"delegation_id": "d-stub-1", "status": "queued"})
        if self.path.endswith("/escalate"):
            STUB["escalations"].append({"path": self.path, "body": body})
            return self._send(200, {"escalation_id": "esc-1"})
        if self.path == "/api/engine/permission":
            STUB["permissions"].append(body)
            status = STUB["permission_status"]
            return self._send(200, {"status": status["status"], "response": status["response"], "delegation_id": body.get("delegation_id")})
        return self._send(404, {"detail": "stub: unknown POST " + self.path})


def _reset_stub():
    STUB["script"] = []
    STUB["requests"] = []
    STUB["req_headers"] = []
    STUB["tasks"] = []
    STUB["escalations"] = []
    STUB["permissions"] = []
    STUB["escalation_status"] = {"status": "answered", "response": "Yes, proceed"}
    STUB["permission_status"] = {"status": "answered", "response": "once"}


@pytest.fixture(scope="module")
def env_urls(tmp_path_factory):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture(scope="module")
def sidecar(tmp_path_factory, env_urls):
    if node_missing:
        pytest.skip("node not on PATH")
    data_dir = tmp_path_factory.mktemp("engine-tools-data")
    repo_root = Path(__file__).resolve().parents[1]
    proc = spawn_sidecar(
        ["node", str(repo_root / "sweave-engine" / "src" / "serve.js"), "--port", "0", "--data-dir", str(data_dir)],
        env={
            **os.environ,
            "SWEAVE_ENGINE_BASE_OPENROUTER": env_urls,
            "SWEAVE_API_URL": env_urls,
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


class _FakeTrace:
    def __init__(self):
        self.events = []

    def append(self, name, payload):
        self.events.append((name, payload))

    def of(self, name):
        return [p for n, p in self.events if n == name]


def _spec(worktree, model="openrouter/test-model", tools=None):
    from sweave.harness.base import AgentSpec

    return AgentSpec(
        name="worker",
        role="specialist",
        model=model,
        system_prompt="",
        worktree_path=worktree,
        memory_bank="session-tools",
        tools=list(tools or []),
        harness="sweave-engine",
    )


def _message(text="do the task", metadata=None, model=None):
    from sweave.harness.base import Message

    return Message(type="user", content=text, metadata=dict(metadata or {}), model=model)


async def _spawn():
    from sweave.harness.engine import SweaveEngineHarness

    return await SweaveEngineHarness().spawn(_spec(Path(".")))


def _call(name, args, call_id=None):
    return {"name": name, "args": args, **({"id": call_id} if call_id else {})}


# ---------------------------------------------------------------------------
# Scripted tool turn (the done-gate fixture)
# ---------------------------------------------------------------------------


@needs_node
async def test_scripted_read_edit_bash_grep_turn(sidecar, worktree):
    _reset_stub()
    STUB["script"] = [
        {"calls": [_call("read", {"filePath": "notes.txt"})]},
        {"calls": [_call("edit", {"filePath": "notes.txt", "oldString": "beta", "newString": "GAMMA"})]},
        {"calls": [_call("bash", {"command": "echo hi"})]},
        {"calls": [_call("grep", {"pattern": "GAMMA"})]},
        {"text": "WORK DONE"},
    ]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(
        _spec(worktree, tools=["read", "edit", "bash", "glob", "grep", "todo"])
    )
    trace = _FakeTrace()
    result = await proc.send(_message("update the notes"), trace=trace)
    assert result.success, result.error
    assert result.output == "WORK DONE"
    # Byte-identical trace event names (the identical-events invariant).
    started = trace.of("tool.started")
    completed = trace.of("tool.completed")
    assert [p["tool"] for p in started] == ["read", "edit", "bash", "grep"]
    assert [p["tool"] for p in completed] == ["read", "edit", "bash", "grep"]
    assert all(set(p) == {"callID", "tool", "state"} for p in started)
    assert trace.of("tool.failed") == []
    assert len(trace.of("step.boundary")) >= 1
    assert len(trace.of("tokens_used")) == 1
    # Ground truth: the edit landed, the grep saw it, bash ran.
    assert "GAMMA" in (worktree / "notes.txt").read_text(encoding="utf-8")
    by_tool = {p["tool"]: p["state"] for p in completed}
    assert "alpha" in by_tool["read"]["output"]
    assert "hi" in by_tool["bash"]["output"]
    assert "notes.txt" in by_tool["grep"]["output"]


@needs_node
async def test_loop_provider_calls_carry_validated_client_headers(sidecar, worktree):
    """Every loop iteration sends UA + the stable session header."""
    _reset_stub()
    STUB["script"] = [
        {"calls": [_call("read", {"filePath": "notes.txt"})]},
        {"text": "HEADERS DONE"},
    ]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(
        _spec(worktree, tools=["read"])
    )
    result = await proc.send(_message("read the notes"), trace=_FakeTrace())
    assert result.success, result.error
    assert result.output == "HEADERS DONE"
    assert len(STUB["req_headers"]) >= 2  # main + auxiliary iteration(s)
    sessions = {h["session"] for h in STUB["req_headers"]}
    assert sessions != {""}
    assert len(sessions) == 1
    assert next(iter(sessions)).startswith("eng_")
    assert all(
        h["user_agent"] == "sweave-engine/0.1.0"
        for h in STUB["req_headers"]
    )


@needs_node
async def test_todo_lifecycle(sidecar, worktree):
    _reset_stub()
    STUB["script"] = [
        {"calls": [_call("todo", {"todos": [{"content": "write code", "status": "in_progress", "priority": "high"}]})]},
        {"calls": [_call("todo", {"todos": [{"content": "write code", "status": "completed", "priority": "high"}]})]},
        {"text": "TASK DONE"},
    ]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["todo"]))
    result = await proc.send(_message("track it"))
    assert result.success, result.error
    assert result.output == "TASK DONE"


@needs_node
async def test_todo_rejects_bad_shapes(sidecar, worktree):
    _reset_stub()
    STUB["script"] = [
        {"calls": [_call("todo", {"todos": [{"content": "", "status": "in_progress", "priority": "high"}]})]},
        {"text": "RECOVERED"},
    ]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["todo"]))
    trace = _FakeTrace()
    result = await proc.send(_message("track it"), trace=trace)
    assert result.success
    failed = trace.of("tool.failed")
    assert len(failed) == 1 and failed[0]["tool"] == "todo"


# ---------------------------------------------------------------------------
# Permission enforcement matrix
# ---------------------------------------------------------------------------


@needs_node
async def test_deny_map_blocks_loudly(sidecar, worktree):
    _reset_stub()
    STUB["script"] = [
        {"calls": [_call("read", {"filePath": "notes.txt"})]},
        {"text": "CANNOT COMPLY"},
    ]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["read"]))
    trace = _FakeTrace()
    result = await proc.send(
        _message("read it", metadata={"permission_map": {"read": "deny"}}), trace=trace
    )
    assert result.success and result.output == "CANNOT COMPLY"
    failed = trace.of("tool.failed")
    assert len(failed) == 1
    assert "permission denied" in failed[0]["state"]["error"]
    assert STUB["permissions"] == []  # deny never asks


@needs_node
async def test_ask_allow_once_round_trip(sidecar, worktree):
    _reset_stub()
    STUB["script"] = [
        {"calls": [_call("bash", {"command": "echo allowed"})]},
        {"text": "RAN IT"},
    ]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["bash"]))
    trace = _FakeTrace()
    result = await proc.send(
        _message(
            "run it",
            metadata={"permission_map": {"bash": "ask"}, "delegation_id": "dlg-ask-1"},
        ),
        trace=trace,
    )
    assert result.success and result.output == "RAN IT"
    asked = trace.of("permission.asked")
    assert len(asked) == 1
    assert asked[0]["permission"] == "bash"
    assert len(STUB["permissions"]) == 1
    assert STUB["permissions"][0]["question"].startswith("Engine asks bash")
    completed = trace.of("tool.completed")
    assert len(completed) == 1 and "allowed" in completed[0]["state"]["output"]


@needs_node
async def test_ask_reject_fails_tool_loudly(sidecar, worktree):
    _reset_stub()
    STUB["permission_status"] = {"status": "answered", "response": "deny"}
    STUB["script"] = [
        {"calls": [_call("bash", {"command": "echo nope"})]},
        {"text": "BLOCKED"},
    ]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["bash"]))
    trace = _FakeTrace()
    result = await proc.send(
        _message(
            "run it",
            metadata={"permission_map": {"bash": "ask"}, "delegation_id": "dlg-ask-2"},
        ),
        trace=trace,
    )
    assert result.success and result.output == "BLOCKED"
    failed = trace.of("tool.failed")
    assert len(failed) == 1
    assert "permission rejected" in failed[0]["state"]["error"]


@needs_node
async def test_last_match_wins(sidecar, worktree):
    from sweave.harness.engine import SweaveEngineHarness

    for permission_map, expect_ok in [
        ({"bash": {"*": "deny", "echo *": "allow"}}, True),
        ({"bash": {"echo *": "allow", "*": "deny"}}, False),
    ]:
        _reset_stub()
        STUB["script"] = [
            {"calls": [_call("bash", {"command": "echo order"})]},
            {"text": "NEXT"},
        ]
        proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["bash"]))
        trace = _FakeTrace()
        result = await proc.send(
            _message("run it", metadata={"permission_map": permission_map}), trace=trace
        )
        assert result.success
        if expect_ok:
            assert trace.of("tool.completed") and not trace.of("tool.failed")
        else:
            assert trace.of("tool.failed") and not trace.of("tool.completed")


@needs_node
async def test_external_directory_default_asks_and_deny_blocks(sidecar, worktree, tmp_path):
    from sweave.harness.engine import SweaveEngineHarness

    outside_dir = tmp_path.parent / (tmp_path.name + "-outside")
    outside_dir.mkdir(exist_ok=True)
    outside = outside_dir / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    # Default (no entry): ask -> stub allows once -> completes.
    _reset_stub()
    STUB["script"] = [
        {"calls": [_call("read", {"filePath": str(outside)})]},
        {"text": "READ IT"},
    ]
    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["read"]))
    trace = _FakeTrace()
    result = await proc.send(
        _message("read outside", metadata={"delegation_id": "dlg-ext-1"}), trace=trace
    )
    assert result.success and result.output == "READ IT"
    asked = trace.of("permission.asked")
    assert len(asked) == 1 and asked[0]["permission"] == "external_directory"
    # Explicit deny: blocked without asking.
    _reset_stub()
    STUB["script"] = [
        {"calls": [_call("read", {"filePath": str(outside)})]},
        {"text": "DENIED PATH"},
    ]
    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["read"]))
    trace = _FakeTrace()
    result = await proc.send(
        _message(
            "read outside",
            metadata={"permission_map": {"external_directory": "deny"}},
        ),
        trace=trace,
    )
    assert result.success and result.output == "DENIED PATH"
    assert trace.of("tool.failed") and STUB["permissions"] == []


@needs_node
async def test_always_allow_grants_folder_per_run_per_specialist(
    sidecar, tmp_path
):
    """Folder grants (2026-09-15 ruling): "always" on a path-keyed
    external_directory ask covers the containing folder + subfolders
    (per-file grants are too trashy); outside-project targets stay
    exact-path (fail closed); grants never cross engine sessions."""
    from sweave.harness.engine import SweaveEngineHarness

    project = tmp_path / "proj"
    wt = project / "wt"
    shared = project / "shared"
    (shared / "sub").mkdir(parents=True)
    (project / "elsewhere").mkdir(parents=True)
    (tmp_path / "far").mkdir(exist_ok=True)
    wt.mkdir(parents=True)
    bodies = {
        shared / "a.txt": "A\n",
        shared / "b.txt": "B\n",
        shared / "sub" / "c.txt": "C\n",
        project / "elsewhere" / "d.txt": "D\n",
        tmp_path / "far" / "f.txt": "F\n",
        tmp_path / "far" / "g.txt": "G\n",
    }
    for path, text in bodies.items():
        path.write_text(text, encoding="utf-8")
    _reset_stub()
    STUB["permission_status"] = {"status": "answered", "response": "always allow"}
    STUB["script"] = [
        {"calls": [_call("read", {"filePath": str(shared / "a.txt")})]},
        {"calls": [_call("read", {"filePath": str(shared / "b.txt")})]},
        {"calls": [_call("read", {"filePath": str(shared / "sub" / "c.txt")})]},
        {"calls": [_call("read", {"filePath": str(project / "elsewhere" / "d.txt")})]},
        {"calls": [_call("read", {"filePath": str(tmp_path / "far" / "f.txt")})]},
        {"calls": [_call("read", {"filePath": str(tmp_path / "far" / "g.txt")})]},
        {"text": "FOLDER DONE"},
    ]
    proc = await SweaveEngineHarness().spawn(_spec(wt, tools=["read"]))
    trace = _FakeTrace()
    result = await proc.send(
        _message(
            "read the files",
            metadata={
                "permission_map": {"external_directory": "ask"},
                "project_dir": str(project),
                "delegation_id": "dlg-folder-1",
            },
        ),
        trace=trace,
    )
    assert result.success and result.output == "FOLDER DONE"
    asked = trace.of("permission.asked")
    # a.txt asks (folder grant shared/); b.txt + sub/c.txt ride it.
    # d.txt asks (folder grant elsewhere/). f.txt asks (exact grant:
    # outside the project) and g.txt asks again.
    assert [a["patterns"] for a in asked] == [
        [str(shared / "a.txt")],
        [str(project / "elsewhere" / "d.txt")],
        [str(tmp_path / "far" / "f.txt")],
        [str(tmp_path / "far" / "g.txt")],
    ]
    # The ask card states the folder scope honestly.
    assert "and everything under it" in STUB["permissions"][0]["question"]
    assert len(STUB["permissions"]) == 4
    assert len(trace.of("tool.completed")) == 6
    # Per-specialist isolation: a second engine session re-asks for a
    # file the first session was granted.
    proc2 = await SweaveEngineHarness().spawn(_spec(wt, tools=["read"]))
    _reset_stub()
    STUB["permission_status"] = {"status": "answered", "response": "always allow"}
    STUB["script"] = [
        {"calls": [_call("read", {"filePath": str(shared / "b.txt")})]},
        {"text": "ISOLATED"},
    ]
    trace2 = _FakeTrace()
    result2 = await proc2.send(
        _message(
            "read again",
            metadata={"project_dir": str(project), "delegation_id": "dlg-folder-2"},
        ),
        trace=trace2,
    )
    assert result2.success and result2.output == "ISOLATED"
    assert len(trace2.of("permission.asked")) == 1


# ---------------------------------------------------------------------------
# Sweave-native tools (identical contract strings)
# ---------------------------------------------------------------------------


@needs_node
async def test_orchestrator_sweave_tools_end_to_end(sidecar, worktree):
    _reset_stub()
    STUB["script"] = [
        {
            "calls": [
                _call("defer", {"target": "backend", "task": "build it"}),
                _call("list_specialists", {}),
            ]
        },
        {"calls": [_call("ask_human", {"question": "Proceed?"})]},
        {"calls": [_call("escalate", {"message": "blocked on schema"})]},
        {"text": "ORCHESTRATED"},
    ]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=[]))
    trace = _FakeTrace()
    result = await proc.send(
        _message(
            "coordinate",
            metadata={"role": "orchestrator", "delegation_id": "dlg-1"},
        ),
        trace=trace,
    )
    assert result.success and result.output == "ORCHESTRATED"
    # defer posted the same body shape the MCP tool posts.
    assert len(STUB["tasks"]) == 1
    assert STUB["tasks"][0]["agent"] == "backend"
    assert STUB["tasks"][0]["parent_task_id"] == "dlg-1"
    # ask_human waited and returned the human answer as the result.
    assert len(STUB["escalations"]) == 2  # ask_human + escalate
    completed = {p["tool"]: p["state"].get("output") for p in trace.of("tool.completed")}
    assert completed["defer"] == "queued: d-stub-1 (target=backend)"
    assert completed["list_specialists"] == "backend -- Builds APIs"
    assert completed["ask_human"] == "Human answer: Yes, proceed"
    assert completed["escalate"].startswith("escalated: esc-1 (to orchestrator;")


@needs_node
async def test_specialist_cannot_defer_structural(sidecar, worktree):
    _reset_stub()
    STUB["script"] = [
        {"calls": [_call("defer", {"target": "backend", "task": "sneaky"})]},
        {"text": "REFUSED PATH"},
    ]
    from sweave.harness.engine import SweaveEngineHarness

    # A real specialist turn offers execution tools (runtime passes
    # them); the structural gate rejects the unoffered sweave tool.
    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["bash"]))
    trace = _FakeTrace()
    result = await proc.send(_message("do it", metadata={"role": "specialist"}), trace=trace)
    assert result.success
    failed = trace.of("tool.failed")
    assert len(failed) == 1
    assert "unknown tool" in failed[0]["state"]["error"]
    assert STUB["tasks"] == []  # never reached the API


@needs_node
async def test_doom_loop_guard(sidecar, worktree):
    _reset_stub()
    same = {"calls": [_call("bash", {"command": "echo loop"})]}
    STUB["script"] = [dict(same), dict(same), dict(same), {"text": "GAVE UP"}]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["bash"]))
    trace = _FakeTrace()
    result = await proc.send(_message("loop me"), trace=trace)
    assert result.success and result.output == "GAVE UP"
    assert len(trace.of("tool.completed")) == 2
    failed = trace.of("tool.failed")
    assert len(failed) == 1
    assert "doom_loop" in failed[0]["state"]["error"]


@needs_node
async def test_toolless_specialist_stays_single_shot(sidecar, worktree):
    _reset_stub()
    STUB["script"] = [{"text": "SIMPLE"}]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=[]))
    result = await proc.send(_message("hi"))
    assert result.success and result.output == "SIMPLE"
    # No tools offered at all (legacy path: no tools key on the wire).
    assert "tools" not in STUB["requests"][-1]


@needs_node
async def test_read_output_truncated_before_history(sidecar, worktree):
    """Oversized tool outputs are capped before entering history.

    Regression (2026-09-14): only ``bash`` truncated (32K); a
    limit-less ``read`` of a ~600KB file dumped 607K chars into
    session history and re-billed it on every remaining iteration
    (~2M of a 5.4M-token turn from ONE read). ``read``/``grep``/
    ``glob`` now share the same cap.
    """
    (worktree / "big.txt").write_text("x" * 100_000 + "\n", encoding="utf-8")
    _reset_stub()
    STUB["script"] = [
        {"calls": [_call("read", {"filePath": "big.txt"})]},
        {"text": "READ DONE"},
    ]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["read"]))
    trace = _FakeTrace()
    result = await proc.send(_message("read the big file"), trace=trace)
    assert result.success, result.error
    assert result.output == "READ DONE"
    completed = trace.of("tool.completed")
    assert len(completed) == 1
    out = completed[0]["state"]["output"]
    assert len(out) <= 32768 + 200
    assert "[truncated" in out and "100001" not in out


@needs_node
async def test_read_defaults_to_paged_window(sidecar, worktree):
    """Omitted read limit pages (opencode parity) and teaches offset.

    Review-hardening step 2 (2026-09-15): the flat head-cut taught
    models to redirect test output to files; a paged default with a
    continuation hint teaches `offset` instead.
    """
    (worktree / "many.txt").write_text(
        "\n".join(f"line-{i}" for i in range(1, 3001)) + "\n", encoding="utf-8"
    )
    _reset_stub()
    STUB["script"] = [
        {"calls": [_call("read", {"filePath": "many.txt"})]},
        {"text": "PAGED DONE"},
    ]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["read"]))
    trace = _FakeTrace()
    result = await proc.send(_message("read the file"), trace=trace)
    assert result.success, result.error
    out = trace.of("tool.completed")[0]["state"]["output"].replace("\r\n", "\n")
    assert "line-1\n" in out and "line-2000\n" in out
    assert "line-2001" not in out
    assert "Use offset=2001 to continue" in out


@needs_node
async def test_read_explicit_limit_and_offset_win(sidecar, worktree):
    """Explicit offset/limit page anywhere; final page has no hint."""
    (worktree / "many.txt").write_text(
        "\n".join(f"line-{i}" for i in range(1, 3001)) + "\n", encoding="utf-8"
    )
    _reset_stub()
    STUB["script"] = [
        {"calls": [_call("read", {"filePath": "many.txt", "offset": 2001, "limit": 500})]},
        {"calls": [_call("read", {"filePath": "many.txt", "offset": 2501, "limit": 600})]},
        {"text": "OFFSET DONE"},
    ]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["read"]))
    trace = _FakeTrace()
    result = await proc.send(_message("page the file"), trace=trace)
    assert result.success, result.error
    first, second = (
        p["state"]["output"].replace("\r\n", "\n") for p in trace.of("tool.completed")
    )
    assert "line-2001\n" in first and "line-2500\n" in first
    assert "line-2000\n" not in first and "line-2501" not in first
    assert "Use offset=2501 to continue" in first
    assert "line-2501\n" in second and "line-3000" in second
    assert "to continue" not in second


@needs_node
async def test_bash_oversized_output_keeps_tail(sidecar, worktree):
    """Oversized bash output keeps the tail (failures live at the end)."""
    _reset_stub()
    STUB["script"] = [
        {"calls": [_call("bash", {"command": "node -e \"for(let i=1;i<=5000;i++) console.log('line-'+i)\""})]},
        {"text": "TAIL DONE"},
    ]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["bash"]))
    trace = _FakeTrace()
    result = await proc.send(_message("run it"), trace=trace)
    assert result.success, result.error
    out = trace.of("tool.completed")[0]["state"]["output"]
    assert len(out) <= 32768 + 200
    assert "showing the tail" in out
    assert "line-5000" in out
    assert "\nline-1\n" not in out


@needs_node
async def test_orchestrator_md_write_allowed_code_denied(sidecar, worktree):
    """Orchestrator `.md` widening (2026-09-15): `.md` writes land,
    code writes die at the map gate (last-match-wins deny-then-allow).

    The test passes the REAL orchestrator map shape the runtime
    renders (not a hand copy) — a map-shape regression fails here,
    not in production.
    """
    from sweave.runtime.specialist_runtime import ORCHESTRATOR_MD_WRITE_MAP

    orch_map = {
        "edit": dict(ORCHESTRATOR_MD_WRITE_MAP),
        "write": dict(ORCHESTRATOR_MD_WRITE_MAP),
    }
    _reset_stub()
    STUB["script"] = [
        {"calls": [_call("write", {"filePath": "docs/note.md", "content": "plan text"})]},
        {"calls": [_call("write", {"filePath": "evil.py", "content": "print(1)"})]},
        {"calls": [_call("edit", {"filePath": "docs/note.md", "oldString": "plan", "newString": "PLAN"})]},
        {"calls": [_call("bash", {"command": "echo no"})]},
        {"text": "ORCHESTRATED WRITES"},
    ]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(
        _spec(worktree, tools=["read", "edit", "write", "todo"])
    )
    trace = _FakeTrace()
    result = await proc.send(
        _message(
            "write the plan",
            metadata={
                "role": "orchestrator",
                "permission_map": orch_map,
                "delegation_id": "dlg-orch-md",
            },
        ),
        trace=trace,
    )
    assert result.success, result.error
    completed = {p["tool"]: p["state"].get("output") for p in trace.of("tool.completed")}
    failed = {p["tool"]: p["state"].get("error") for p in trace.of("tool.failed")}
    assert "docs/note.md" in completed["write"]
    assert "permission denied" in failed.get("write", "")
    assert "docs/note.md" in completed["edit"]
    # bash is unoffered for the orchestrator (structural, not map).
    assert "unknown tool" in failed.get("bash", "")
    # Ground truth: the .md landed edited, the .py never existed.
    assert (worktree / "docs" / "note.md").read_text(encoding="utf-8") == "PLAN text"
    assert not (worktree / "evil.py").exists()


@needs_node
async def test_orchestrator_todo_tracks_plan(sidecar, worktree):
    """Orchestrator `todo` (2026-09-15): plan tracking rides the
    turn like any exec tool (offered-set + session state)."""
    _reset_stub()
    STUB["script"] = [
        {"calls": [_call("todo", {"todos": [
            {"content": "defer backend", "status": "in_progress", "priority": "high"},
            {"content": "synthesize", "status": "pending", "priority": "medium"},
        ]})]},
        {"text": "TRACKED"},
    ]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(
        _spec(worktree, tools=["read", "edit", "write", "todo"])
    )
    trace = _FakeTrace()
    result = await proc.send(
        _message("track it", metadata={"role": "orchestrator"}), trace=trace
    )
    assert result.success, result.error
    out = trace.of("tool.completed")[0]["state"]["output"]
    assert "defer backend" in out and "in_progress" in out


@needs_node
async def test_tokens_used_carries_cache_read(sidecar, worktree):
    """The terminal ``tokens_used`` anchor reports real cache numbers
    (the loop-turn emit hardcoded zeros while per-step boundaries
    already carried them)."""
    _reset_stub()
    STUB["script"] = [
        {
            "calls": [_call("read", {"filePath": "notes.txt"})],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 5,
                "prompt_tokens_details": {"cached_tokens": 60},
            },
        },
        {
            "text": "CACHE DONE",
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 3,
                "prompt_tokens_details": {"cached_tokens": 70},
            },
        },
    ]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["read"]))
    trace = _FakeTrace()
    result = await proc.send(_message("read the notes"), trace=trace)
    assert result.success, result.error
    anchors = trace.of("tokens_used")
    assert len(anchors) == 1
    assert anchors[0]["input"] == 220
    assert anchors[0]["cache_read"] == 130
    # Billed input sums cumulative per-step prompts (100+120);
    # context_input is the peak live context (max, not sum).
    assert anchors[0]["context_input"] == 120
    assert "cache_write" in anchors[0]


@needs_node
async def test_specialist_ceiling_beyond_fifty(sidecar, worktree):
    """Role-aware ceiling: a healthy 55-iteration specialist turn
    completes (the flat 50 killed succeeding read loops). No handoff
    is recorded on success."""
    _reset_stub()
    reads = [
        {"filePath": "notes.txt", "offset": 1},
        {"filePath": "notes.txt", "offset": 2},
        {"filePath": "src/main.py"},
    ]
    STUB["script"] = (
        [{"calls": [_call("read", reads[i % 3])]} for i in range(55)]
        + [{"text": "LONG DONE"}]
    )
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["read"]))
    trace = _FakeTrace()
    result = await proc.send(_message("keep reading"), trace=trace)
    assert result.success, result.error
    assert result.output == "LONG DONE"
    assert all("handoff" not in p for p in trace.of("step.boundary"))


@needs_node
async def test_orchestrator_ceiling_stays_fifty(sidecar, worktree):
    """The orchestrator keeps the 50-iteration ceiling (its turns
    never needed more observed) and trips loud with a handoff
    record naming what ran."""
    _reset_stub()
    reads = [
        {"filePath": "notes.txt", "offset": 1},
        {"filePath": "notes.txt", "offset": 2},
        {"filePath": "src/main.py"},
    ]
    STUB["script"] = (
        [{"calls": [_call("read", reads[i % 3])]} for i in range(55)]
        + [{"text": "NEVER REACHED"}]
    )
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["read"]))
    trace = _FakeTrace()
    result = await proc.send(
        _message("keep reading", metadata={"role": "orchestrator"}), trace=trace
    )
    assert not result.success
    assert "max_steps" in (result.error or "")
    handoffs = [p for p in trace.of("step.boundary") if "handoff" in p]
    assert len(handoffs) == 1
    hand = handoffs[0]["handoff"]
    assert hand["iterations"] == 50
    assert hand["toolCalls"] == 50
    assert any("notes.txt" in f for f in hand["filesTouched"])


@needs_node
async def test_no_progress_trips_early(sidecar, worktree):
    """Stuckness trip: alternating failing calls dodge the identical-
    call doom guard but five straight all-failed iterations fail fast
    with ``no_progress`` instead of burning all 50."""
    _reset_stub()
    cmds = ["exit 1", "exit 2"]
    STUB["script"] = (
        [{"calls": [_call("bash", {"command": cmds[i % 2]})]} for i in range(8)]
        + [{"text": "NEVER REACHED"}]
    )
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["bash"]))
    trace = _FakeTrace()
    result = await proc.send(_message("run failing commands"), trace=trace)
    assert not result.success
    assert "no_progress" in (result.error or "")
    assert len(STUB["requests"]) == 5
    handoffs = [p for p in trace.of("step.boundary") if "handoff" in p]
    assert len(handoffs) == 1
    assert handoffs[0]["reason"] == "no_progress"


@needs_node
async def test_doom_rejections_feed_the_streak(sidecar, worktree):
    """Identical repeats hit the doom guard (rejected without
    executing), and those rejections count as failures: a model that
    ignores five straight 'try a different approach' tells trips
    ``no_progress`` instead of rattling to the ceiling."""
    _reset_stub()
    same = {"calls": [_call("read", {"filePath": "notes.txt"})]}
    STUB["script"] = [dict(same) for _ in range(10)] + [{"text": "NEVER REACHED"}]
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(worktree, tools=["read"]))
    trace = _FakeTrace()
    result = await proc.send(_message("read the same file forever"), trace=trace)
    assert not result.success
    assert "no_progress" in (result.error or "")
    assert len(STUB["requests"]) < 10
