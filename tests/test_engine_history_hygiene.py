"""Engine history-hygiene tests (dangling tool-call sanitize).

Incident 2026-09-19: two specialist delegations failed loud with
``engine_failed_before_work: provider_error: provider 400`` on BOTH
flavors. Full upstream errors (recovered from
``~/.sweave/engine/sessions.json`` — the trace truncates at 200
chars, which read as a model outage):
- responses (muse-spark-1.3-contributor): "No tool output found for
  function call call_01a0..."
- chat (deepseek-v4.1-flash): "assistant message with 'tool_calls'
  must be followed by tool messages..."

Root cause: a turn dying mid-tool-loop (abort, timeout, crash /
restart after the assistant entry was appended but before every
tool output landed) leaves an unanswered function_call in the
journal. Sessions resume across delegations, so EVERY later turn on
that session replays the poison and 400s before any work (a live
scan found 17/358 journals carrying at least one dangling call,
zero orphans).

Fix: ``sanitizeHistory()`` in ``sweave-engine/src/sessions.js``,
applied in both history mappers (loop.js chat + responses.js).
Hermetic: a localhost stub plays both gateway endpoints while the
REAL sidecar boots against a pre-seeded POISONED journal. Proves
both flavors serve the next turn (poison stripped from the wire
input) and answered pairs survive (no over-strip).
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from contextlib import contextmanager
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

RESP_MODEL = "opencode-go/muse-spark-1.3-contributor"  # responses flavor
CHAT_MODEL = "opencode-go/glm-5.3-flash"  # chat flavor

POISON_CALL_ID = "call_poison_dangling_1"
ANSWERED_CALL_ID = "call_answered_kept_1"

HITS: list[dict] = []


def _poisoned_journal() -> dict:
    """One dangling assistant call (no tool output) + one answered pair."""
    now = int(time.time() * 1000)
    return {
        "eng_poisoned": {
            "id": "eng_poisoned",
            "created": now,
            "revert": None,
            "messages": [
                {
                    "id": "msg_seed_user_1",
                    "role": "user",
                    "content": "do the work",
                    "at": now,
                },
                {
                    "id": "msg_seed_asst_dangle",
                    "role": "assistant",
                    "content": "",
                    "toolCalls": [
                        {
                            "id": POISON_CALL_ID,
                            "name": "bash",
                            "args": {"command": "sleep 30"},
                        }
                    ],
                    "model": "opencode-go/glm-5.3-flash",
                    "at": now + 1,
                },
                {
                    "id": "msg_seed_asst_ok",
                    "role": "assistant",
                    "content": "",
                    "toolCalls": [
                        {
                            "id": ANSWERED_CALL_ID,
                            "name": "read",
                            "args": {"filePath": "notes.txt"},
                        }
                    ],
                    "model": "opencode-go/glm-5.3-flash",
                    "at": now + 2,
                },
                {
                    "id": "msg_seed_tool_ok",
                    "role": "tool",
                    "toolCallId": ANSWERED_CALL_ID,
                    "name": "read",
                    "content": "alpha",
                    "at": now + 3,
                },
            ],
        }
    }


def _sse(obj: dict) -> bytes:
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

    def _send_sse(self, chunks: list[bytes]):
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for chunk in chunks:
                self.wfile.write(chunk)
                self.wfile.flush()
        except (ConnectionResetError, BrokenPipeError):
            pass

    def do_POST(self):
        body = self._read_json()
        if self.path == "/responses":
            HITS.append({"flavor": "responses", "input": body.get("input", [])})
            self._send_sse(
                [
                    _sse({"type": "response.output_text.delta", "delta": "RESP OK"}),
                    _sse(
                        {
                            "type": "response.completed",
                            "response": {
                                "usage": {"input_tokens": 7, "output_tokens": 2}
                            },
                        }
                    ),
                ]
            )
            return
        if self.path == "/chat/completions":
            HITS.append({"flavor": "chat", "messages": body.get("messages", [])})
            self._send_sse(
                [
                    _sse({"choices": [{"delta": {"content": "CHAT OK"}}]}),
                    b"data: [DONE]\n\n",
                ]
            )
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


@pytest.fixture()
def stub_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@contextmanager
def _sidecar_with_journal(stub_url: str, tmp_path_factory, journal: dict):
    """Boot the REAL sidecar against a pre-seeded journal (yields its URL)."""
    home = tmp_path_factory.mktemp("hyg-fake-home")
    data_dir = tmp_path_factory.mktemp("hyg-data")
    (data_dir / "sessions.json").write_text(json.dumps(journal), encoding="utf-8")
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
            "HOME": str(home),
            "USERPROFILE": str(home),
            "SWEAVE_ENGINE_BASE_OPENCODE_GO": stub_url,
            "SWEAVE_ENGINE_KEY_OPENCODE_GO": "test-hyg-key",
            "SWEAVE_API_URL": stub_url,
            "SWEAVE_MCP_TOKEN": "stub-token",
        },
    )
    try:
        line = read_port_line(proc)
        assert line.startswith("SWEAVE_ENGINE_PORT="), line
        url = f"http://127.0.0.1:{line.split('=', 1)[1]}"
        assert wait_for_health(url), "sidecar never healthy"
        saved = os.environ.get("SWEAVE_ENGINE_URL")
        os.environ["SWEAVE_ENGINE_URL"] = url
        try:
            yield url
        finally:
            if saved is None:
                os.environ.pop("SWEAVE_ENGINE_URL", None)
            else:
                os.environ["SWEAVE_ENGINE_URL"] = saved
    finally:
        stop_sidecar(proc)


def _spec(model: str, worktree: Path):
    from sweave.harness.base import AgentSpec

    return AgentSpec(
        name="worker",
        role="specialist",
        model=model,
        system_prompt="",
        worktree_path=worktree,
        memory_bank="session-hyg",
        tools=["read"],
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
async def test_responses_poisoned_session_serves(stub_url, tmp_path_factory, tmp_path):
    """Responses flavor: dangling call stripped, answered pair kept, turn serves."""
    HITS.clear()
    from sweave.harness.engine import SweaveEngineHarness

    with _sidecar_with_journal(stub_url, tmp_path_factory, _poisoned_journal()):
        proc = await SweaveEngineHarness().attach(
            "eng_poisoned", _spec(RESP_MODEL, tmp_path)
        )
        result = await proc.send(_message("continue"), trace=_Trace())
    assert result.success, result.error
    assert result.output == "RESP OK"
    assert len(HITS) >= 1
    first_input = HITS[0]["input"]
    call_ids = [
        i.get("call_id")
        for i in first_input
        if isinstance(i, dict) and i.get("type") == "function_call"
    ]
    # The dangling call never reaches the wire; the answered one does.
    assert POISON_CALL_ID not in call_ids
    assert ANSWERED_CALL_ID in call_ids
    outputs = [
        i for i in first_input if isinstance(i, dict) and i.get("type") == "function_call_output"
    ]
    assert any("alpha" in o.get("output", "") for o in outputs)


@needs_node
async def test_chat_poisoned_session_serves(stub_url, tmp_path_factory, tmp_path):
    """Chat flavor: dangling tool_calls stripped, answered pair kept, turn serves."""
    HITS.clear()
    from sweave.harness.engine import SweaveEngineHarness

    with _sidecar_with_journal(stub_url, tmp_path_factory, _poisoned_journal()):
        proc = await SweaveEngineHarness().attach(
            "eng_poisoned", _spec(CHAT_MODEL, tmp_path)
        )
        result = await proc.send(_message("continue"), trace=_Trace())
    assert result.success, result.error
    assert result.output == "CHAT OK"
    assert len(HITS) >= 1
    first_messages = HITS[0]["messages"]
    dangling = [
        m
        for m in first_messages
        if isinstance(m, dict)
        and any(
            tc.get("id") == POISON_CALL_ID for tc in (m.get("tool_calls") or [])
        )
    ]
    assert dangling == []
    kept = [
        m
        for m in first_messages
        if isinstance(m, dict)
        and any(
            tc.get("id") == ANSWERED_CALL_ID for tc in (m.get("tool_calls") or [])
        )
    ]
    assert len(kept) == 1
    tool_msgs = [m for m in first_messages if m.get("role") == "tool"]
    assert any(t.get("tool_call_id") == ANSWERED_CALL_ID for t in tool_msgs)
