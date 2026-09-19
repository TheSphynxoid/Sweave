"""Engine step 1 tests: skeleton chat path, no tools (hermetic).

A localhost stub stands in for the provider (OpenAI-compatible SSE);
the REAL sidecar (`sweave-engine/src/serve.js`) and the REAL Python
adapter (`sweave/harness/engine.py`) speak the frozen protocol across
it. No network, no keys — provider auth is env-redirected at the
sidecar (`SWEAVE_ENGINE_BASE_OPENROUTER=http://127.0.0.1:<stub>`).

Covers the step-1 done-gate shape: incremental multi-delta streaming
(>1 on_chunk per turn), output join, tokens_used parity with the stub
usage, request validation (400s), busy-guard 409, abort
(acknowledged + idle-409), revert pointer truncation, version-header
check, and model mapping (spec string + per-message override).

Skipped when node is absent (the sidecar is a Node process).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from tests.sidecars import (
    read_port_line,
    spawn_sidecar,
    stop_sidecar,
    wait_for_health,
)

node_missing = shutil.which("node") is None
needs_node = pytest.mark.skipif(node_missing, reason="node not on PATH")


# ---------------------------------------------------------------------------
# Stub provider (OpenAI-compatible SSE)
# ---------------------------------------------------------------------------

STUB: dict = {"bodies": [], "hold_second_chunk": threading.Event(), "delay": 0.0, "reasoning_mode": None}


class _StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # quiet
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            STUB["bodies"].append(json.loads(raw.decode("utf-8")))
        except ValueError:
            STUB["bodies"].append({})
        if STUB["delay"]:
            time.sleep(STUB["delay"])
        # True streaming: headers + chunk 0 flush immediately; chunk 1
        # waits on the test-controlled event (abort/busy scenarios).
        # Close-delimited (no Content-Length) so flushes hit the wire.
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunks = [
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {"content": " world"}}]},
            {"choices": [{"delta": {}}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}},
        ]
        # Thinking-inclusion probe (protocol v2): when the mode is
        # set, chunk 0 carries reasoning deltas in a real provider
        # shape instead of text — the sidecar must forward them as
        # `reasoning` SSE (never as token text).
        mode = STUB.get("reasoning_mode")
        if mode == "deepseek":
            chunks = [
                {"choices": [{"delta": {"reasoning_content": "Let me "}}]},
                {"choices": [{"delta": {"reasoning_content": "think"}}]},
                {"choices": [{"delta": {"content": "Hello"}}]},
                {"choices": [{"delta": {}}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}},
            ]
        elif mode == "openrouter":
            chunks = [
                {"choices": [{"delta": {
                    "reasoning": "We",
                    "reasoning_details": [
                        {"type": "reasoning.text", "text": "We", "format": "unknown", "index": 0},
                    ],
                }}]},
                {"choices": [{"delta": {"content": "done"}}]},
                {"choices": [{"delta": {}}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}},
            ]
        for i, c in enumerate(chunks):
            if i == 1 and not STUB["hold_second_chunk"].is_set():
                STUB["hold_second_chunk"].wait(timeout=15)
            try:
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
                self.wfile.flush()
            except (ConnectionResetError, BrokenPipeError):
                return
        try:
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (ConnectionResetError, BrokenPipeError):
            pass


@pytest.fixture(scope="module")
def stub_url():
    STUB["bodies"].clear()
    STUB["hold_second_chunk"].set()
    STUB["delay"] = 0.0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def sidecar(tmp_path_factory, stub_url):
    if node_missing:
        pytest.skip("node not on PATH")
    data_dir = tmp_path_factory.mktemp("engine-data")
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
            "SWEAVE_ENGINE_BASE_OPENROUTER": stub_url,
        },
    )
    line = read_port_line(proc)
    assert line.startswith("SWEAVE_ENGINE_PORT=")
    port = line.split("=", 1)[1]
    url = f"http://127.0.0.1:{port}"
    # Wait for health (bounded; a dead sidecar fails loudly on the turn).
    wait_for_health(url)
    monkeypatch_vars = {
        "SWEAVE_ENGINE_URL": url,
        "SWEAVE_ENGINE_BASE_OPENROUTER": stub_url,
    }
    saved = {k: os.environ.get(k) for k in monkeypatch_vars}
    os.environ.update(monkeypatch_vars)
    try:
        yield url
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        stop_sidecar(proc)


class _FakeTrace:
    def __init__(self):
        self.events = []

    def append(self, name, payload):
        self.events.append((name, payload))


def _spec(**overrides):
    from sweave.harness.base import AgentSpec

    base = dict(
        name="orchestrator",
        role="orchestrator",
        model="openrouter/google/gemma-4-26b-a4b-it:free",
        system_prompt="",
        worktree_path=Path("."),
        memory_bank="session-x",
        tools=[],
        harness="sweave-engine",
    )
    base.update(overrides)
    return AgentSpec(**base)


def _message(text="hi", **overrides):
    from sweave.harness.base import Message

    return Message(type="user", content=text, metadata={}, **overrides)


# ---------------------------------------------------------------------------
# Happy path + streaming shape
# ---------------------------------------------------------------------------


@needs_node
async def test_chat_turn_streams_incremental_deltas(sidecar):
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec())
    chunks: list[str] = []
    trace = _FakeTrace()
    result = await proc.send(
        _message("say hello"), on_chunk=chunks.append, trace=trace
    )
    assert result.success, result.error
    assert result.output == "Hello world"
    assert len(chunks) >= 2, f"expected true token streaming, got {chunks!r}"
    assert "".join(chunks) == "Hello world"


@needs_node
async def test_tokens_used_matches_stub_usage(sidecar):
    from sweave.harness.engine import SweaveEngineHarness

    proc = await SweaveEngineHarness().spawn(_spec())
    trace = _FakeTrace()
    result = await proc.send(_message("tokens"), trace=trace)
    assert result.success, result.error
    anchored = [p for n, p in trace.events if n == "tokens_used"]
    assert len(anchored) == 1
    assert anchored[0]["input"] == 5
    assert anchored[0]["output"] == 3
    # Single-shot turn: one request, so peak context == billed input.
    assert anchored[0]["context_input"] == 5


@needs_node
async def test_reasoning_content_forwarded_not_output(sidecar):
    """DeepSeek-native shape (`reasoning_content`): thinking text
    reaches on_reasoning + the trace, never the turn output."""
    from sweave.harness.engine import SweaveEngineHarness

    STUB["reasoning_mode"] = "deepseek"
    try:
        proc = await SweaveEngineHarness().spawn(_spec())
        chunks: list[str] = []
        thinkings: list[str] = []
        trace = _FakeTrace()
        result = await proc.send(
            _message("think"),
            on_chunk=chunks.append,
            on_reasoning=thinkings.append,
            trace=trace,
        )
    finally:
        STUB["reasoning_mode"] = None
    assert result.success, result.error
    assert "".join(thinkings) == "Let me think"
    assert result.output == "Hello"
    assert "".join(chunks) == "Hello"
    reasoned = [p for n, p in trace.events if n == "reasoning"]
    assert [p["text"] for p in reasoned] == ["Let me ", "think"]


@needs_node
async def test_openrouter_reasoning_not_doubled(sidecar):
    """OpenRouter shape (`reasoning` + `reasoning_details` carrying
    the same text): forwarded exactly once per delta."""
    from sweave.harness.engine import SweaveEngineHarness

    STUB["reasoning_mode"] = "openrouter"
    try:
        proc = await SweaveEngineHarness().spawn(_spec())
        thinkings: list[str] = []
        trace = _FakeTrace()
        result = await proc.send(
            _message("think"), on_reasoning=thinkings.append, trace=trace
        )
    finally:
        STUB["reasoning_mode"] = None
    assert result.success, result.error
    assert "".join(thinkings) == "We"
    assert result.output == "done"


@needs_node
async def test_single_shot_path_forwards_reasoning(sidecar):
    """Tool-less specialist turn (single-shot pump in serve.js, not
    the loop): same reasoning contract."""
    from sweave.harness.engine import SweaveEngineHarness

    STUB["reasoning_mode"] = "deepseek"
    try:
        proc = await SweaveEngineHarness().spawn(
            _spec(role="specialist", name="worker")
        )
        thinkings: list[str] = []
        result = await proc.send(
            _message("think"), on_reasoning=thinkings.append
        )
    finally:
        STUB["reasoning_mode"] = None
    assert result.success, result.error
    assert "".join(thinkings) == "Let me think"
    assert result.output == "Hello"


@needs_node
async def test_model_mapping_and_per_message_override(sidecar):
    from sweave.harness.base import Message
    from sweave.harness.engine import SweaveEngineHarness

    STUB["bodies"].clear()
    proc = await SweaveEngineHarness().spawn(_spec())
    result = await proc.send(_message("m1"))
    assert result.success, result.error
    assert STUB["bodies"][-1]["model"] == "google/gemma-4-26b-a4b-it:free"
    STUB["bodies"].clear()
    result = await proc.send(
        Message(
            type="user",
            content="m2",
            metadata={},
            model={"provider": "openrouter", "model_id": "other-model"},
        )
    )
    assert result.success, result.error
    assert STUB["bodies"][-1]["model"] == "other-model"


@needs_node
async def test_health_carries_protocol_version(sidecar):
    from sweave.engine.protocol import PROTOCOL_VERSION

    async with httpx.AsyncClient(base_url=sidecar, timeout=5.0) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.headers["X-Sweave-Engine-Protocol"] == PROTOCOL_VERSION
    assert resp.json()["protocol_version"] == PROTOCOL_VERSION


@needs_node
async def test_version_mismatch_refuses_loudly():
    from sweave.harness.engine import _check_version
    from sweave.engine.protocol import ProtocolMismatch

    with pytest.raises(ProtocolMismatch):
        _check_version({"X-Sweave-Engine-Protocol": "999"})
    with pytest.raises(ProtocolMismatch):
        _check_version({})


# ---------------------------------------------------------------------------
# Validation + guards
# ---------------------------------------------------------------------------


@needs_node
async def test_run_validation_400s(sidecar):
    good = {
        "session_id": "v1",
        "composed_prompt": "hi",
        "tools": [],
        "permission_map": {},
        "model": {"provider": "openrouter", "model_id": "m"},
        "turn_timeout": 30,
        "cwd": ".",
    }
    async with httpx.AsyncClient(base_url=sidecar, timeout=10.0) as client:
        for missing in ("session_id", "composed_prompt", "model", "turn_timeout"):
            bad = dict(good)
            del bad[missing]
            resp = await client.post("/run", json=bad)
            assert resp.status_code == 400
            assert f"missing:{missing}" in resp.json()["reason"]
        bad = dict(good, tools=["teleport"])
        resp = await client.post("/run", json=bad)
        assert resp.status_code == 400
        assert "bad:tools" in resp.json()["reason"]
        bad = dict(good, model={"model_id": "m"})
        resp = await client.post("/run", json=bad)
        assert resp.status_code == 400
        assert "bad:model" in resp.json()["reason"]
        # Variant must be a string when present (an object would
        # serialize garbage into the provider body).
        bad = dict(
            good, model={"provider": "p", "model_id": "m", "variant": {"x": 1}}
        )
        resp = await client.post("/run", json=bad)
        assert resp.status_code == 400
        assert "bad:model" in resp.json()["reason"]
        # String variants (incl. thinking-off) validate clean.
        ok = dict(
            good,
            session_id="v2",
            model={"provider": "p", "model_id": "m", "variant": "high"},
        )
        resp = await client.post("/run", json=ok)
        assert resp.status_code != 400


@needs_node
async def test_busy_guard_409_and_abort_flow(sidecar):
    STUB["hold_second_chunk"].clear()
    events: list[dict] = []

    async def collect():
        async with httpx.AsyncClient(base_url=sidecar, timeout=30.0) as client:
            body = {
                "session_id": "busy-1",
                "composed_prompt": "slow please",
                "tools": [],
                "permission_map": {},
                "model": {"provider": "openrouter", "model_id": "m"},
                "turn_timeout": 30,
                "cwd": ".",
            }
            async with client.stream("POST", "/run", json=body) as resp:
                assert resp.status_code == 200
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        events.append(json.loads(line[5:]))

    task = asyncio.create_task(collect())
    try:
        for _ in range(100):
            if any(e.get("event") == "token" for e in events):
                break
            await asyncio.sleep(0.1)
        assert any(e.get("event") == "token" for e in events)
        async with httpx.AsyncClient(base_url=sidecar, timeout=10.0) as client:
            resp = await client.post("/run", json={
                "session_id": "busy-1",
                "composed_prompt": "intrude",
                "tools": [],
                "permission_map": {},
                "model": {"provider": "openrouter", "model_id": "m"},
                "turn_timeout": 30,
                "cwd": ".",
            })
            assert resp.status_code == 409
            resp = await client.post("/abort", json={"session_id": "busy-1"})
            assert resp.status_code == 200
            assert resp.json()["outcome"] == "acknowledged"
            resp = await client.post("/abort", json={"session_id": "busy-1"})
            assert resp.status_code in (200, 409)
    finally:
        STUB["hold_second_chunk"].set()
        await asyncio.wait_for(task, timeout=30)


@needs_node
async def test_abort_idle_is_409(sidecar):
    async with httpx.AsyncClient(base_url=sidecar, timeout=10.0) as client:
        resp = await client.post("/abort", json={"session_id": "idle-zzz"})
        assert resp.status_code == 409


@needs_node
async def test_revert_truncates_next_prompt(sidecar):
    STUB["bodies"].clear()
    base = {
        "tools": [],
        "permission_map": {},
        "model": {"provider": "openrouter", "model_id": "m"},
        "turn_timeout": 30,
        "cwd": ".",
    }

    async def run(session_id, prompt):
        seen = []

        async with httpx.AsyncClient(base_url=sidecar, timeout=30.0) as client:
            async with client.stream(
                "POST", "/run", json={**base, "session_id": session_id, "composed_prompt": prompt}
            ) as resp:
                assert resp.status_code == 200
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        seen.append(json.loads(line[5:]))
        return seen

    first = await run("rev-1", "first question")
    done1 = next(e for e in first if e.get("event") == "done")
    await run("rev-1", "second question")
    n_before = len(STUB["bodies"])
    async with httpx.AsyncClient(base_url=sidecar, timeout=10.0) as client:
        resp = await client.post(
            "/revert",
            json={"session_id": "rev-1", "to_message": "msg_nope"},
        )
        assert resp.status_code == 400
        # Revert to the first turn's assistant message: the next
        # prompt must replace the reverted tail (no "second question").
        resp = await client.post(
            "/revert",
            json={"session_id": "rev-1", "to_message": done1["message_id"]},
        )
        assert resp.status_code == 200
    assert len(STUB["bodies"]) == n_before  # revert itself calls no provider
    _ = await run("rev-1", "third question")
    last_body = STUB["bodies"][-1]
    prompts = [m["content"] for m in last_body["messages"]]
    assert prompts == ["first question", "Hello world", "third question"]


@needs_node
async def test_auth_missing_fails_loud_at_turn_start(sidecar):
    from sweave.harness.engine import SweaveEngineHarness

    # github-copilot has no OpenAI-compatible surface mapped: the
    # turn must fail at start with the named code, never mid-stream.
    proc = await SweaveEngineHarness().spawn(
        _spec(model="github-copilot/anything")
    )
    result = await proc.send(_message("hi"))
    assert not result.success
    assert "auth_missing" in result.error
