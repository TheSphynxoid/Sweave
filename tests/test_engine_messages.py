"""Engine messages-transport tests (Anthropic Messages API wire).

Hermetic: a localhost stub plays the gateway `/messages` endpoint
(text/tool deltas, usage, 400s) while the REAL sidecar executes.
Proves flavor routing (messages models hit /messages, never
/chat/completions), the block mapping (text/tool_use/tool_result
with role alternation), the max_tokens strip-retry, and the
identical downstream trace vocabulary for tool turns.

Model under test: opencode-go/minimax-m3 (messages flavor per the
Go docs table). The `opencode` Claude rows share the flavor gate;
google rows still fail loud (pending transport, documented).
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

MSG_MODEL = "opencode-go/minimax-m3"

HITS: list[dict] = []
SCRIPT: list[dict] = []


def _sse_chunk(obj: dict) -> bytes:
    return f"data: {json.dumps(obj)}\n\n".encode()


def _completed(input_tokens=7, output_tokens=2):
    return {
        "type": "message_delta",
        "delta": {},
        "usage": {"output_tokens": output_tokens},
    }


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
        if self.path == "/messages":
            body = self._read_json()
            HITS.append(
                {
                    "auth": self.headers.get("Authorization", ""),
                    "model": body.get("model", ""),
                    "max_tokens": body.get("max_tokens", "ABSENT"),
                    "user_agent": self.headers.get("User-Agent", ""),
                    "session": self.headers.get("x-opencode-session", ""),
                    "tools": body.get("tools", "ABSENT"),
                    "messages": body.get("messages", []),
                }
            )
            step = SCRIPT.pop(0) if SCRIPT else {"text": "DONE"}
            if "fail_status" in step:
                payload = json.dumps({"error": step.get("fail_body", {})}).encode()
                self.send_response(step["fail_status"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (ConnectionResetError, BrokenPipeError):
                    pass
                return
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                self.wfile.write(
                    _sse_chunk(
                        {
                            "type": "message_start",
                            "message": {
                                "id": "msg_stub",
                                "usage": {
                                    "input_tokens": step.get("in_tokens", 7),
                                    "output_tokens": 0,
                                },
                            },
                        }
                    )
                )
                self.wfile.flush()
                if step.get("text"):
                    self.wfile.write(
                        _sse_chunk(
                            {
                                "type": "content_block_start",
                                "index": 0,
                                "content_block": {"type": "text"},
                            }
                        )
                    )
                    self.wfile.write(
                        _sse_chunk(
                            {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {"type": "text_delta", "text": step["text"]},
                            }
                        )
                    )
                    self.wfile.flush()
                for i, call in enumerate(step.get("calls", []), start=1):
                    self.wfile.write(
                        _sse_chunk(
                            {
                                "type": "content_block_start",
                                "index": i,
                                "content_block": {
                                    "type": "tool_use",
                                    "id": call.get("id", f"toolu_{i}"),
                                    "name": call["name"],
                                },
                            }
                        )
                    )
                    self.wfile.write(
                        _sse_chunk(
                            {
                                "type": "content_block_delta",
                                "index": i,
                                "delta": {
                                    "type": "input_json_delta",
                                    "partial_json": json.dumps(
                                        call.get("args", {})
                                    ),
                                },
                            }
                        )
                    )
                    self.wfile.write(
                        _sse_chunk({"type": "content_block_stop", "index": i})
                    )
                    self.wfile.flush()
                self.wfile.write(_sse_chunk(_completed()))
                self.wfile.write(
                    _sse_chunk({"type": "message_stop"})
                )
                self.wfile.flush()
            except (ConnectionResetError, BrokenPipeError):
                pass
            return
        if self.path == "/chat/completions":
            HITS.append({"chat_hit": True})
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
    home = tmp_path_factory.mktemp("msg-fake-home")
    data_dir = tmp_path_factory.mktemp("msg-data")
    repo_root = Path(__file__).resolve().parents[1]
    proc = spawn_sidecar(
        ["node", str(repo_root / "sweave-engine" / "src" / "serve.js"), "--port", "0", "--data-dir", str(data_dir)],
        env={
            **os.environ,
            "HOME": str(home),
            "USERPROFILE": str(home),
            "SWEAVE_ENGINE_BASE_OPENCODE_GO": stub_url,
            "SWEAVE_ENGINE_KEY_OPENCODE_GO": "test-msg-key",
            # The google-gap test needs the `opencode` entry keyed so
            # it reaches the flavor gate (not auth_missing).
            "SWEAVE_ENGINE_BASE_OPENCODE": stub_url,
            "SWEAVE_ENGINE_KEY_OPENCODE": "test-msg-key",
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


def _spec(model: str, tools=None, worktree: Path = Path(".")):
    from sweave.harness.base import AgentSpec

    return AgentSpec(
        name="worker",
        role="specialist",
        model=model,
        system_prompt="",
        worktree_path=worktree,
        memory_bank="session-msg",
        tools=list(tools or []),
        harness="sweave-engine",
    )


def _message(text: str):
    from sweave.harness.base import Message

    return Message(type="user", content=text, metadata={})


class _Trace:
    def __init__(self):
        self.events = []

    def append(self, name, payload):
        self.events.append((name, payload))

    def of(self, name):
        return [p for n, p in self.events if n == name]


@needs_node
async def test_messages_single_shot_text(sidecar, stub_url):
    """Messages model, no tools: /messages only, text + tokens."""
    HITS.clear()
    SCRIPT.clear()
    SCRIPT.append({"text": "MSG OK"})
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(MSG_MODEL))
    trace = _Trace()
    result = await proc.send(_message("say MSG OK"), trace=trace)
    assert result.success, result.error
    assert result.output == "MSG OK"
    assert len(HITS) == 1
    assert HITS[0]["auth"] == "Bearer test-msg-key"
    assert HITS[0]["model"] == "minimax-m3"
    assert HITS[0]["user_agent"] == "sweave-engine/0.1.0"
    assert HITS[0]["session"].startswith("eng_")
    assert HITS[0]["max_tokens"] == 32000
    assert "chat_hit" not in HITS[0]
    assert len(trace.of("tokens_used")) == 1


@needs_node
async def test_messages_loop_tool_turn(sidecar, stub_url, tmp_path):
    """Messages + tools: tool_use executes, same trace vocabulary."""
    (tmp_path / "notes.txt").write_text("alpha\n", encoding="utf-8")
    HITS.clear()
    SCRIPT.clear()
    SCRIPT.append(
        {"calls": [{"name": "read", "args": {"filePath": "notes.txt"}}]}
    )
    SCRIPT.append({"text": "READ DONE"})
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(
        _spec(MSG_MODEL, tools=["read"], worktree=tmp_path)
    )
    trace = _Trace()
    result = await proc.send(_message("read the notes"), trace=trace)
    assert result.success, result.error
    assert result.output == "READ DONE"
    assert len(HITS) == 2
    assert all(h["user_agent"] == "sweave-engine/0.1.0" for h in HITS)
    assert HITS[0]["session"] == HITS[1]["session"]
    # Anthropic tool shape on the wire.
    tools = HITS[0]["tools"]
    assert isinstance(tools, list) and "input_schema" in tools[0]
    assert tools[0]["name"] == "read"
    # Second iteration carries the prior tool_use + its tool_result.
    second = HITS[1]["messages"]
    kinds = [
        (m.get("role"), b.get("type"))
        for m in second
        if isinstance(m, dict)
        for b in (m.get("content") or [])
        if isinstance(b, dict)
    ]
    assert ("assistant", "tool_use") in kinds
    assert ("user", "tool_result") in kinds
    results = [
        b
        for m in second
        if isinstance(m, dict)
        for b in (m.get("content") or [])
        if isinstance(b, dict) and b.get("type") == "tool_result"
    ]
    assert "alpha" in results[0].get("content", "")
    # Roles strictly alternate (gateway 400s otherwise).
    roles = [m.get("role") for m in second if isinstance(m, dict)]
    assert all(a != b for a, b in zip(roles, roles[1:]))
    assert roles[0] == "user"
    # Identical downstream vocabulary.
    assert [p["tool"] for p in trace.of("tool.started")] == ["read"]
    assert [p["tool"] for p in trace.of("tool.completed")] == ["read"]
    assert len(trace.of("tokens_used")) == 1


@needs_node
async def test_messages_max_tokens_fallback(sidecar, stub_url):
    """max_tokens above the model ceiling 400s once; the turn halves
    and retries (2 hits, second at 16000) and still succeeds."""
    HITS.clear()
    SCRIPT.clear()
    SCRIPT.append(
        {"fail_status": 400, "fail_body": {"error": {"type": "invalid_request_error", "message": "max_tokens exceeds maximum"}}}
    )
    SCRIPT.append({"text": "RECOVERED"})
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(MSG_MODEL))
    result = await proc.send(_message("hi"), trace=_Trace())
    assert result.success, result.error
    assert result.output == "RECOVERED"
    assert len(HITS) == 2
    assert HITS[0]["max_tokens"] == 32000
    assert HITS[1]["max_tokens"] == 16000


@needs_node
async def test_google_flavor_still_fails_loud(sidecar, stub_url):
    """Documents the remaining gap: google-flavor models fail at turn
    start naming the pending transport (never a mid-turn 400)."""
    HITS.clear()
    SCRIPT.clear()
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(
        _spec("opencode/gemini-3-flash")
    )
    result = await proc.send(_message("hi"), trace=_Trace())
    assert not result.success
    assert "google" in (result.error or "")
    assert HITS == []
