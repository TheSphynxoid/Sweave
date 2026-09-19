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

Hygiene B5 additions (same file, same harness):
* pre-flight history ceiling (``capHistory``): a 405-message journal
  serves with the wire input capped at 400 + an honest truncation
  note — immortal sessions can no longer 400 on context length;
* corrupt journal entries (non-object / missing messages array) are
  dropped at boot; the sidecar still serves;
* the ``done`` event names freshly-created sessions
  (``session_fresh``) so the orchestrator can tell silent journal
  amnesia from a true resume.
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


def _straddling_journal() -> dict:
    """Review fix (cap/sever interaction): an answered pair sitting
    exactly on the ceiling cut — the assistant half is dropped as
    oldest while the tool half survives. The mapper must drop the
    orphan output too; replaying it alone 400s both flavors."""
    now = int(time.time() * 1000)
    msgs = [
        {
            "id": "msg_old_asst",
            "role": "assistant",
            "content": "",
            "toolCalls": [{"id": "call_straddle", "name": "bash", "args": {}}],
            "model": "m/m",
            "at": now,
        },
        {
            "id": "msg_old_tool",
            "role": "tool",
            "toolCallId": "call_straddle",
            "name": "bash",
            "content": "old output",
            "at": now + 1,
        },
    ]
    # 398 fillers + live prompt = 401 total -> the ceiling drops
    # exactly the assistant half, stranding its tool output.
    for i in range(398):
        msgs.append(
            {"id": f"msg_f_{i}", "role": "user", "content": f"f{i}", "at": now + 2 + i}
        )
    return {
        "eng_straddle": {
            "id": "eng_straddle",
            "created": now,
            "revert": None,
            "messages": msgs,
        }
    }


def _fat_journal(n_pairs: int = 205) -> dict:
    """A journal past the pre-flight ceiling (400 msgs): tiny text pairs."""
    now = int(time.time() * 1000)
    msgs = []
    for i in range(n_pairs):
        msgs.append(
            {"id": f"msg_fat_u_{i}", "role": "user", "content": f"u{i}", "at": now + i}
        )
        msgs.append(
            {
                "id": f"msg_fat_a_{i}",
                "role": "assistant",
                "content": f"a{i}",
                "at": now + i,
            }
        )
    return {
        "eng_fat": {"id": "eng_fat", "created": now, "revert": None, "messages": msgs}
    }


def _spec(model: str, worktree: Path, tools=None):
    from sweave.harness.base import AgentSpec

    return AgentSpec(
        name="worker",
        role="specialist",
        model=model,
        system_prompt="",
        worktree_path=worktree,
        memory_bank="session-hyg",
        tools=list(tools) if tools is not None else ["read"],
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


@needs_node
async def test_chat_history_ceiling_caps_wire(stub_url, tmp_path_factory, tmp_path):
    """410-msg journal on the single-shot chat path: wire capped at
    400 + honest note first, live prompt last, turn serves."""
    HITS.clear()
    from sweave.harness.engine import SweaveEngineHarness

    with _sidecar_with_journal(stub_url, tmp_path_factory, _fat_journal()):
        proc = await SweaveEngineHarness().attach(
            "eng_fat", _spec(CHAT_MODEL, tmp_path, tools=[])
        )
        result = await proc.send(_message("continue"), trace=_Trace())
    assert result.success, result.error
    assert result.output == "CHAT OK"
    assert len(HITS) == 1
    wired = HITS[0]["messages"]
    # 400 capped (prompt included, newest survives) + truncation note.
    assert len(wired) == 401
    assert "sweave history note" in wired[0]["content"]
    assert wired[-1]["content"] == "continue"  # live prompt survives


@needs_node
async def test_responses_history_ceiling_caps_wire(
    stub_url, tmp_path_factory, tmp_path
):
    """Same ceiling on the single-shot responses path."""
    HITS.clear()
    from sweave.harness.engine import SweaveEngineHarness

    with _sidecar_with_journal(stub_url, tmp_path_factory, _fat_journal()):
        proc = await SweaveEngineHarness().attach(
            "eng_fat", _spec(RESP_MODEL, tmp_path, tools=[])
        )
        result = await proc.send(_message("continue"), trace=_Trace())
    assert result.success, result.error
    assert result.output == "RESP OK"
    assert len(HITS) == 1
    wired = HITS[0]["input"]
    assert len(wired) == 401  # same shape as the chat path
    assert wired[0].get("role") == "user"
    assert "sweave history note" in wired[0].get("content", "")
    assert wired[-1].get("content") == "continue"


@needs_node
async def test_corrupt_journal_entry_dropped(stub_url, tmp_path_factory, tmp_path):
    """Non-object / messages-less journal entries are dropped at boot;
    the sidecar still serves (on both the healthy and the healed id)."""
    HITS.clear()
    from sweave.harness.engine import SweaveEngineHarness

    now = int(time.time() * 1000)
    journal = {
        "eng_ok": {
            "id": "eng_ok",
            "created": now,
            "revert": None,
            "messages": [
                {
                    "id": "m1",
                    "role": "user",
                    "content": "hi",
                    "at": now,
                }
            ],
        },
        "eng_bad_str": "torn bytes",
        "eng_bad_obj": {"id": "eng_bad_obj"},
    }
    with _sidecar_with_journal(stub_url, tmp_path_factory, journal):
        for sid in ("eng_ok", "eng_bad_str"):
            proc = await SweaveEngineHarness().attach(
                sid, _spec(CHAT_MODEL, tmp_path, tools=[])
            )
            result = await proc.send(_message("continue"), trace=_Trace())
            assert result.success, (sid, result.error)
            assert result.output == "CHAT OK"


@needs_node
async def test_session_fresh_named_on_done(stub_url, tmp_path_factory, tmp_path):
    """First turn on a fresh id carries session_fresh; the second does
    not — the orchestrator can tell journal amnesia from a resume."""
    from sweave.harness.engine import SweaveEngineHarness

    with _sidecar_with_journal(stub_url, tmp_path_factory, {}):
        proc = await SweaveEngineHarness().attach(
            "eng_brand_new", _spec(CHAT_MODEL, tmp_path, tools=[])
        )
        first = await proc.send(_message("one"), trace=_Trace())
        assert first.success, first.error
        assert first.metadata.get("session_fresh") is True
        second = await proc.send(_message("two"), trace=_Trace())
        assert second.success, second.error
        assert "session_fresh" not in second.metadata


@needs_node
async def test_straddling_pair_drops_orphan_output_chat(
    stub_url, tmp_path_factory, tmp_path
):
    """Review fix: ceiling severs an answered pair (assistant dropped,
    tool survives) — the orphan output must not reach the chat wire."""
    HITS.clear()
    from sweave.harness.engine import SweaveEngineHarness

    with _sidecar_with_journal(stub_url, tmp_path_factory, _straddling_journal()):
        proc = await SweaveEngineHarness().attach(
            "eng_straddle", _spec(CHAT_MODEL, tmp_path, tools=[])
        )
        result = await proc.send(_message("continue"), trace=_Trace())
    assert result.success, result.error
    tools = [
        m for m in HITS[0]["messages"] if isinstance(m, dict) and m.get("role") == "tool"
    ]
    assert all(t.get("tool_call_id") != "call_straddle" for t in tools)


@needs_node
async def test_straddling_pair_drops_orphan_output_responses(
    stub_url, tmp_path_factory, tmp_path
):
    """Same severed pair on the responses wire: no unattributed
    function_call_output."""
    HITS.clear()
    from sweave.harness.engine import SweaveEngineHarness

    with _sidecar_with_journal(stub_url, tmp_path_factory, _straddling_journal()):
        proc = await SweaveEngineHarness().attach(
            "eng_straddle", _spec(RESP_MODEL, tmp_path, tools=[])
        )
        result = await proc.send(_message("continue"), trace=_Trace())
    assert result.success, result.error
    outputs = [
        i
        for i in HITS[0]["input"]
        if isinstance(i, dict) and i.get("type") == "function_call_output"
    ]
    assert all(o.get("call_id") != "call_straddle" for o in outputs)
