"""Engine responses-transport tests (Responses API wire).

Hermetic: a localhost stub plays the gateway `/responses` endpoint
(text deltas, function_call items, usage). The REAL sidecar
executes; the REAL adapter drives it. Proves the flavor routing
(responses models hit /responses, never /chat/completions), the
input-item mapping (message/function_call/function_call_output),
and the identical downstream trace vocabulary for tool turns.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

node_missing = shutil.which("node") is None
needs_node = pytest.mark.skipif(node_missing, reason="node not on PATH")

HITS: list[dict] = []
SCRIPT: list[dict] = []


def _sse_chunk(obj: dict) -> bytes:
    return f"data: {json.dumps(obj)}\n\n".encode()


def _completed(input_tokens=7, output_tokens=2):
    return {
        "type": "response.completed",
        "response": {
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}
        },
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
        if self.path == "/responses":
            body = self._read_json()
            HITS.append(
                {
                    "auth": self.headers.get("Authorization", ""),
                    "model": (body.get("model", "")),
                    "user_agent": self.headers.get("User-Agent", ""),
                    "session": self.headers.get("x-opencode-session", ""),
                    "tools": body.get("tools", "ABSENT"),
                    "input": body.get("input", []),
                }
            )
            step = SCRIPT.pop(0) if SCRIPT else {"text": "DONE"}
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                if step.get("text"):
                    self.wfile.write(
                        _sse_chunk(
                            {
                                "type": "response.output_text.delta",
                                "delta": step["text"],
                            }
                        )
                    )
                    self.wfile.flush()
                for call in step.get("calls", []):
                    self.wfile.write(
                        _sse_chunk(
                            {
                                "type": "response.output_item.done",
                                "item": {
                                    "type": "function_call",
                                    "id": call.get("id", "item_1"),
                                    "call_id": call.get("call_id", "call_1"),
                                    "name": call["name"],
                                    "arguments": json.dumps(call.get("args", {})),
                                },
                            }
                        )
                    )
                    self.wfile.flush()
                self.wfile.write(_sse_chunk(_completed()))
                self.wfile.flush()
            except (ConnectionResetError, BrokenPipeError):
                pass
            return
        if self.path == "/chat/completions":
            HITS.append({"chat_hit": True})
        body = json.dumps({"error": "stub: unknown POST " + self.path}).encode()
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
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
    home = tmp_path_factory.mktemp("resp-fake-home")
    data_dir = tmp_path_factory.mktemp("resp-data")
    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.Popen(
        ["node", str(repo_root / "sweave-engine" / "src" / "serve.js"), "--port", "0", "--data-dir", str(data_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env={
            **os.environ,
            "HOME": str(home),
            "USERPROFILE": str(home),
            "SWEAVE_ENGINE_BASE_OPENCODE_GO": stub_url,
            "SWEAVE_ENGINE_KEY_OPENCODE_GO": "test-resp-key",
            "SWEAVE_API_URL": stub_url,
            "SWEAVE_MCP_TOKEN": "stub-token",
        },
    )
    assert proc.stdout is not None
    line = proc.stdout.readline().strip()
    assert line.startswith("SWEAVE_ENGINE_PORT="), line
    url = f"http://127.0.0.1:{line.split('=', 1)[1]}"
    for _ in range(50):
        try:
            if httpx.get(f"{url}/health", timeout=2.0).status_code == 200:
                break
        except httpx.ConnectError:
            time.sleep(0.1)
    saved = os.environ.get("SWEAVE_ENGINE_URL")
    os.environ["SWEAVE_ENGINE_URL"] = url
    try:
        yield url
    finally:
        if saved is None:
            os.environ.pop("SWEAVE_ENGINE_URL", None)
        else:
            os.environ["SWEAVE_ENGINE_URL"] = saved
        proc.kill()


RESP_MODEL = "opencode-go/muse-spark-1.3-contributor"


def _spec(model: str, tools=None, worktree: Path = Path(".")):
    from sweave.harness.base import AgentSpec

    return AgentSpec(
        name="worker",
        role="specialist",
        model=model,
        system_prompt="",
        worktree_path=worktree,
        memory_bank="session-resp",
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
async def test_responses_single_shot_text(sidecar, stub_url):
    """Responses model, no tools: /responses only, text + tokens."""
    HITS.clear()
    SCRIPT.clear()
    SCRIPT.append({"text": "RESP OK"})
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec(RESP_MODEL))
    trace = _Trace()
    result = await proc.send(_message("say RESP OK"), trace=trace)
    assert result.success, result.error
    assert result.output == "RESP OK"
    assert len(HITS) == 1
    assert HITS[0]["auth"] == "Bearer test-resp-key"
    assert HITS[0]["model"] == "muse-spark-1.3-contributor"
    assert HITS[0]["user_agent"] == "sweave-engine/0.1.0"
    assert HITS[0]["session"].startswith("eng_")
    assert "chat_hit" not in HITS[0]
    assert len(trace.of("tokens_used")) == 1


@needs_node
async def test_responses_loop_tool_turn(sidecar, stub_url, tmp_path):
    """Responses + tools: function_call executes, same trace vocabulary."""
    (tmp_path / "notes.txt").write_text("alpha\n", encoding="utf-8")
    HITS.clear()
    SCRIPT.clear()
    SCRIPT.append(
        {"calls": [{"name": "read", "args": {"filePath": "notes.txt"}}]}
    )
    SCRIPT.append({"text": "READ DONE"})
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(
        _spec(RESP_MODEL, tools=["read"], worktree=tmp_path)
    )
    trace = _Trace()
    result = await proc.send(_message("read the notes"), trace=trace)
    assert result.success, result.error
    assert result.output == "READ DONE"
    # Both iterations hit /responses with the validated headers.
    assert len(HITS) == 2
    assert all(h["user_agent"] == "sweave-engine/0.1.0" for h in HITS)
    assert HITS[0]["session"] == HITS[1]["session"]
    # Function tool defs ride the first request (Responses shape).
    tools = HITS[0]["tools"]
    assert isinstance(tools, list) and tools[0]["type"] == "function"
    assert tools[0]["name"] == "read"
    # Second iteration carries the prior call + its output as input items.
    second_input = HITS[1]["input"]
    kinds = [
        i.get("type", i.get("role")) for i in second_input if isinstance(i, dict)
    ]
    assert "function_call" in kinds
    assert "function_call_output" in kinds
    outputs = [
        i for i in second_input if i.get("type") == "function_call_output"
    ]
    assert "alpha" in outputs[0].get("output", "")
    # Identical downstream vocabulary.
    assert [p["tool"] for p in trace.of("tool.started")] == ["read"]
    assert [p["tool"] for p in trace.of("tool.completed")] == ["read"]
    assert len(trace.of("tokens_used")) == 1


@needs_node
async def test_chat_model_never_hits_responses(sidecar, stub_url):
    """Flavor routing: chat models stay on /chat/completions."""
    HITS.clear()
    SCRIPT.clear()
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec("opencode-go/glm-5.3-flash"))
    result = await proc.send(_message("hi"), trace=_Trace())
    # The stub 404s /chat/completions — the turn fails, but the point
    # stands: zero /responses hits for a chat-flavor model.
    assert not result.success
    assert not [h for h in HITS if "model" in h and h.get("auth")]
    assert all("chat_hit" in h for h in HITS)
