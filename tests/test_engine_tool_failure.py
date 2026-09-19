"""Engine tool-failure tests (wrong-path errors are tool errors, never turn kills).

Incident 2026-09-19 follow-up: a wrong `bash` (and, same class,
`read`/`write`) could kill the whole session — e.g. using a
directory as a file ended the entire turn instead of returning a
normal tool error the model adjusts to. Root cause: fs throws past
the narrow pre-checks (`writeFile` EISDIR on a directory, TOCTOU
deletes, EACCES) escaped `executeTool` as rejections, and loop.js
treats a rejected tool as turn-fatal — which ALSO left an
unanswered assistant call in the journal (the session-poison class
behind `test_engine_history_hygiene.py`).

Fix: `fsFail()` errno mapping in `sweave-engine/src/tools.js`
(EISDIR/EACCES/EPERM/ENOENT/ENOSPC/ENAMETOOLONG + raw first line),
applied at every fallible fs site, plus a totality guard in
`executeTool` so no tool can ever reject (abort control signals
still propagate).

Hermetic: localhost stub plays the provider (scripted SSE), the
REAL sidecar executes against a tmp worktree. Each test scripts one
failing call + a text follow-up and proves the turn SUCCEEDS past
the failure with the typed error on `tool.failed`.
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

STUB: dict = {"script": [], "requests": []}


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

    def do_POST(self):
        if self.path == "/chat/completions":
            body = self._read_json()
            STUB["requests"].append(body)
            step = STUB["script"].pop(0) if STUB["script"] else {"text": "DONE"}
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                if step.get("text"):
                    self.wfile.write(
                        _sse_chunk({"choices": [{"delta": {"content": step["text"]}}]})
                    )
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
                self.wfile.write(
                    _sse_chunk(
                        {
                            "choices": [{"delta": {}}],
                            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                        }
                    )
                )
                self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (ConnectionResetError, BrokenPipeError):
                pass
            return
        payload = json.dumps({"error": "stub: unknown POST " + self.path}).encode()
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (ConnectionResetError, BrokenPipeError):
            pass


@pytest.fixture(scope="module")
def stub_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture(scope="module")
def sidecar(stub_url, tmp_path_factory):
    if node_missing:
        pytest.skip("node not on PATH")
    fake_home = tmp_path_factory.mktemp("toolfail-home")
    data_dir = tmp_path_factory.mktemp("toolfail-data")
    repo_root = Path(__file__).resolve().parents[1]
    proc = spawn_sidecar(
        [
            "node",
            str(repo_root / "sweave-engine" / "src" / "serve.js"),
            "--port",
            "0",
            "--data-dir",
            str(data_dir),
        ],
        env={
            **os.environ,
            "HOME": str(fake_home),
            "USERPROFILE": str(fake_home),
            "SWEAVE_ENGINE_BASE_OPENROUTER": stub_url,
            "SWEAVE_ENGINE_KEY_OPENROUTER": "test-toolfail-key",
            "SWEAVE_API_URL": stub_url,
            "SWEAVE_MCP_TOKEN": "stub-token",
        },
    )
    line = read_port_line(proc)
    assert line.startswith("SWEAVE_ENGINE_PORT="), line
    url = f"http://127.0.0.1:{line.split('=', 1)[1]}"
    wait_for_health(url)
    saved = os.environ.get("SWEAVE_ENGINE_URL")
    os.environ["SWEAVE_ENGINE_URL"] = url
    try:
        yield url
    finally:
        if saved is None:
            os.environ.pop("SWEAVE_ENGINE_URL", None)
        else:
            os.environ["SWEAVE_ENGINE_URL"] = saved
        stop_sidecar(proc)


class _Trace:
    def __init__(self):
        self.events = []

    def append(self, name, payload):
        self.events.append((name, payload))

    def of(self, name):
        return [p for n, p in self.events if n == name]


def _spec(worktree, tools):
    from sweave.harness.base import AgentSpec

    return AgentSpec(
        name="worker",
        role="specialist",
        model="openrouter/test-model",
        system_prompt="",
        worktree_path=worktree,
        memory_bank="session-toolfail",
        tools=list(tools),
        harness="sweave-engine",
    )


def _message(text="do the task"):
    from sweave.harness.base import Message

    return Message(type="user", content=text, metadata={})


def _call(name, args):
    return {"name": name, "args": args}


@needs_node
async def test_write_directory_is_tool_error(sidecar, tmp_path):
    """`write` to a directory: typed failure, turn continues and succeeds."""
    (tmp_path / "adir").mkdir()
    STUB["script"] = [
        {"calls": [_call("write", {"filePath": "adir", "content": "x"})]},
        {"text": "RECOVERED"},
    ]
    STUB["requests"] = []
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(tmp_path, ["write"]))
    trace = _Trace()
    result = await proc.send(_message(), trace=trace)
    assert result.success, result.error
    assert result.output == "RECOVERED"
    # The turn survived the failure: a second provider iteration ran.
    assert len(STUB["requests"]) == 2
    failed = {p["tool"]: p["state"].get("error", "") for p in trace.of("tool.failed")}
    assert "write" in failed
    assert "is a directory" in failed["write"]
    assert "adir" in failed["write"]


@needs_node
async def test_edit_directory_names_it(sidecar, tmp_path):
    """`edit` on a directory: names it as a directory, turn succeeds."""
    (tmp_path / "adir").mkdir()
    STUB["script"] = [
        {
            "calls": [
                _call(
                    "edit",
                    {"filePath": "adir", "oldString": "a", "newString": "b"},
                )
            ]
        },
        {"text": "RECOVERED"},
    ]
    STUB["requests"] = []
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(tmp_path, ["edit"]))
    trace = _Trace()
    result = await proc.send(_message(), trace=trace)
    assert result.success, result.error
    assert result.output == "RECOVERED"
    assert len(STUB["requests"]) == 2
    failed = {p["tool"]: p["state"].get("error", "") for p in trace.of("tool.failed")}
    assert "is a directory" in failed.get("edit", "")


@needs_node
async def test_read_missing_is_tool_error(sidecar, tmp_path):
    """`read` of a missing file stays a tool error (pins the contract)."""
    STUB["script"] = [
        {"calls": [_call("read", {"filePath": "nope.txt"})]},
        {"text": "RECOVERED"},
    ]
    STUB["requests"] = []
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(tmp_path, ["read"]))
    trace = _Trace()
    result = await proc.send(_message(), trace=trace)
    assert result.success, result.error
    assert result.output == "RECOVERED"
    assert len(STUB["requests"]) == 2
    failed = {p["tool"]: p["state"].get("error", "") for p in trace.of("tool.failed")}
    assert "no such file" in failed.get("read", "")


@needs_node
async def test_bash_exit_code_is_tool_error(sidecar, tmp_path):
    """Non-zero `bash` exit stays a tool error (pins the contract)."""
    STUB["script"] = [
        {"calls": [_call("bash", {"command": "exit 3"})]},
        {"text": "RECOVERED"},
    ]
    STUB["requests"] = []
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(tmp_path, ["bash"]))
    trace = _Trace()
    result = await proc.send(_message(), trace=trace)
    assert result.success, result.error
    assert result.output == "RECOVERED"
    assert len(STUB["requests"]) == 2
    failed = {p["tool"]: p["state"].get("error", "") for p in trace.of("tool.failed")}
    assert "exit 3" in failed.get("bash", "")
