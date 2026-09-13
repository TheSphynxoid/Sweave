"""Engine Zen-slice tests: opencode provider on the native engine.

Hermetic twin of the Go file: a localhost stub plays the Zen gateway
(OpenAI-compatible `/chat/completions`). Same gateway family as Go,
different paths (`/zen/v1/*`); gpt → responses, claude → messages,
gemini → google-gated, compatible attempted.
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
            HITS.append(
                {
                    "auth": self.headers.get("Authorization", ""),
                    "model": body.get("model", ""),
                }
            )
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                self.wfile.write(
                    _sse_chunk({"choices": [{"delta": {"content": "ZEN OK"}}]})
                )
                self.wfile.flush()
                self.wfile.write(
                    _sse_chunk(
                        {
                            "choices": [{"delta": {}}],
                            "usage": {"prompt_tokens": 7, "completion_tokens": 2},
                        }
                    )
                )
                self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (ConnectionResetError, BrokenPipeError):
                pass
            return
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
    home = tmp_path_factory.mktemp("zen-fake-home")
    data_dir = tmp_path_factory.mktemp("zen-data")
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
            "SWEAVE_ENGINE_BASE_OPENCODE": stub_url,
            "SWEAVE_ENGINE_KEY_OPENCODE": "test-zen-key",
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


def _spec(model: str):
    from sweave.harness.base import AgentSpec

    return AgentSpec(
        name="worker",
        role="specialist",
        model=model,
        system_prompt="",
        worktree_path=Path("."),
        memory_bank="session-zen",
        tools=[],
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


@needs_node
async def test_zen_free_model_turn(sidecar, stub_url):
    """deepseek-v4-flash-free: Bearer key + model id reach Zen."""
    HITS.clear()
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(
        _spec("opencode/deepseek-v4-flash-free")
    )
    trace = _Trace()
    result = await proc.send(_message("say ZEN OK"), trace=trace)
    assert result.success, result.error
    assert result.output == "ZEN OK"
    assert len(HITS) == 1
    assert HITS[0]["auth"] == "Bearer test-zen-key"
    assert HITS[0]["model"] == "deepseek-v4-flash-free"
    assert len([p for n, p in trace.events if n == "tokens_used"]) == 1


@needs_node
async def test_zen_gpt_flavor_rejected(sidecar, stub_url):
    HITS.clear()
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec("opencode/gpt-5.4-mini"))
    result = await proc.send(_message("hi"), trace=_Trace())
    assert not result.success
    assert "responses" in (result.error or "")
    assert HITS == []


@needs_node
async def test_zen_claude_flavor_rejected(sidecar, stub_url):
    HITS.clear()
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec("opencode/claude-sonnet-4-5"))
    result = await proc.send(_message("hi"), trace=_Trace())
    assert not result.success
    assert "messages" in (result.error or "")
    assert HITS == []


@needs_node
async def test_zen_gemini_flavor_rejected(sidecar, stub_url):
    HITS.clear()
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec("opencode/gemini-3.5-flash"))
    result = await proc.send(_message("hi"), trace=_Trace())
    assert not result.success
    assert "google" in (result.error or "")
    assert HITS == []
