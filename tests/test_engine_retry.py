"""Engine retry tests (user ruling: wait and retry like opencode).

Sidecar retries transient provider failures INSIDE the turn (same
history, no rotation): 429 / 5xx / rate-limit / network-down. Auth,
bad-request, quota-exhausted and context-overflow never retry.

Live half: a scripted stub provider fails N times (429 with
``retry-after-ms``, then 500) before succeeding — the turn recovers
and the attempt count matches. 401 fails after exactly one attempt;
``max_retries=0`` disables.

Contract half: ``turn_retries`` schema bounds, protocol validation,
harness wire body, runtime forwarding, ChatLoop default.
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


# ---------------------------------------------------------------------------
# Scripted stub provider
# ---------------------------------------------------------------------------


STUB: dict = {"script": [], "hits": 0}


class _StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # quiet
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        STUB["hits"] += 1
        if STUB["script"]:
            status, headers, body = STUB["script"].pop(0)
            payload = body.encode()
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (ConnectionResetError, BrokenPipeError):
                pass
            return
        chunks = [
            {"choices": [{"delta": {"content": "recovered"}}]},
            {"choices": [{"delta": {}}], "usage": {"prompt_tokens": 2, "completion_tokens": 1}},
        ]
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for c in chunks:
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
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture(scope="module")
def sidecar(tmp_path_factory, stub_url):
    if node_missing:
        pytest.skip("node not on PATH")
    data_dir = tmp_path_factory.mktemp("engine-retry-data")
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
            "SWEAVE_ENGINE_KEY_OPENROUTER": "stub-key",
        },
    )
    line = read_port_line(proc)
    assert line.startswith("SWEAVE_ENGINE_PORT=")
    port = line.split("=", 1)[1]
    url = f"http://127.0.0.1:{port}"
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


def _spec(tmp_path: Path, **overrides):
    from sweave.harness.base import AgentSpec

    base = dict(
        name="orchestrator",
        role="orchestrator",
        model="openrouter/retry-probe",
        system_prompt="",
        worktree_path=tmp_path,
        memory_bank="",
        tools=[],
        env={},
        harness="sweave-engine",
    )
    base.update(overrides)
    return AgentSpec(**base)


def _message(text="hi", metadata=None):
    from sweave.harness.base import Message

    return Message(type="user", content=text, metadata=metadata or {})


class _FakeTrace:
    def append(self, *a: object) -> None:
        pass


async def _send(proc, text="hi", metadata=None):
    chunks: list[str] = []
    result = await proc.send(
        _message(text, metadata), on_chunk=chunks.append, trace=_FakeTrace()
    )
    return result, chunks


# ---------------------------------------------------------------------------
# Live: recovery + attempt counts
# ---------------------------------------------------------------------------


@needs_node
async def test_recovers_after_transient_failures(sidecar, tmp_path: Path):
    """429 (retry-after-ms) + 500, then success: the turn recovers on
    the third attempt with the default budget."""
    from sweave.harness.engine import SweaveEngineHarness

    STUB["script"] = [
        (429, {"retry-after-ms": "30"}, '{"error": "rate limit, slow down"}'),
        (500, {}, '{"error": "internal error"}'),
    ]
    STUB["hits"] = 0
    proc = await SweaveEngineHarness().attach("eng_retry_1", _spec(tmp_path))
    result, _ = await _send(proc)
    assert result.success, result.error
    assert result.output == "recovered"
    assert STUB["hits"] == 3


@needs_node
async def test_auth_failure_never_retries(sidecar, tmp_path: Path):
    from sweave.harness.engine import SweaveEngineHarness

    STUB["script"] = [(401, {}, '{"error": "invalid api key"}')]
    STUB["hits"] = 0
    proc = await SweaveEngineHarness().attach("eng_retry_2", _spec(tmp_path))
    result, _ = await _send(proc)
    assert not result.success
    assert "401" in (result.error or "")
    assert STUB["hits"] == 1


@needs_node
async def test_max_retries_zero_disables(sidecar, tmp_path: Path):
    from sweave.harness.engine import SweaveEngineHarness

    STUB["script"] = [(429, {"retry-after-ms": "10"}, '{"error": "slow down"}')]
    STUB["hits"] = 0
    proc = await SweaveEngineHarness().attach("eng_retry_3", _spec(tmp_path))
    result, _ = await _send(proc, metadata={"max_retries": 0})
    assert not result.success
    assert STUB["hits"] == 1


@needs_node
async def test_loop_path_retries_too(sidecar, tmp_path: Path):
    """Agentic (tool) turns retry whole provider attempts as well."""
    from sweave.harness.engine import SweaveEngineHarness

    STUB["script"] = [(503, {}, '{"error": "service unavailable"}')]
    STUB["hits"] = 0
    proc = await SweaveEngineHarness().attach(
        "eng_retry_4", _spec(tmp_path, tools=["read"])
    )
    result, _ = await _send(
        proc, metadata={"permission_map": {}, "role": "specialist"}
    )
    assert result.success, result.error
    assert result.output == "recovered"
    assert STUB["hits"] == 2


# ---------------------------------------------------------------------------
# Contract: schema / protocol / wire / forwarding / loop default
# ---------------------------------------------------------------------------


def test_turn_retries_schema_bounds():
    from pydantic import ValidationError

    from sweave.config.schemas import RoutingConfig

    assert RoutingConfig().turn_retries == 3
    assert RoutingConfig(turn_retries=0).turn_retries == 0
    assert RoutingConfig(turn_retries=10).turn_retries == 10
    with pytest.raises(ValidationError):
        RoutingConfig(turn_retries=-1)
    with pytest.raises(ValidationError):
        RoutingConfig(turn_retries=11)


def test_protocol_max_retries_validation():
    from sweave.engine.protocol import validate_run_request

    body = {
        "session_id": "eng_x",
        "composed_prompt": "hi",
        "tools": [],
        "permission_map": {},
        "model": {"provider": "openrouter", "model_id": "m"},
        "turn_timeout": 60.0,
        "cwd": ".",
    }
    assert validate_run_request({**body, "max_retries": 2})["max_retries"] == 2
    assert "max_retries" not in validate_run_request(dict(body))
    with pytest.raises(ValueError, match="bad:max_retries"):
        validate_run_request({**body, "max_retries": -1})
    with pytest.raises(ValueError, match="bad:max_retries"):
        validate_run_request({**body, "max_retries": "many"})


@pytest.mark.asyncio
async def test_harness_sends_max_retries_when_set(tmp_path: Path):
    from sweave.harness.base import AgentSpec
    from sweave.harness.engine import SweaveEngineProcess

    seen: list[dict] = []

    class _FakeResp:
        status_code = 200
        headers = {"X-Sweave-Engine-Protocol": "3"}

        def raise_for_status(self) -> None:
            pass

        async def aread(self) -> bytes:
            return b""

        async def aiter_lines(self):
            yield 'data: {"event": "done", "output": "ok"}'
            yield 'data: {"event": "tokens_used", "input": 1, "output": 1}'

    class _FakeCM:
        async def __aenter__(self):
            return _FakeResp()

        async def __aexit__(self, *a: object) -> bool:
            return False

    class _FakeClient:
        def stream(self, method: str, url: str, json=None, **kw):
            seen.append(json)
            return _FakeCM()

    spec = AgentSpec(
        name="o", role="o", model="", system_prompt="",
        worktree_path=tmp_path, memory_bank="", tools=[],
        env={}, harness="sweave-engine",
    )
    proc = SweaveEngineProcess(spec, "http://127.0.0.1:9", "eng_wire_1")
    proc._client = _FakeClient()  # type: ignore[assignment]
    from sweave.harness.base import Message

    await proc.send(Message(type="user", content="hi", metadata={"max_retries": 2}))
    assert seen and seen[0]["max_retries"] == 2
    await proc.send(Message(type="user", content="hi", metadata={}))
    assert "max_retries" not in seen[1]


@pytest.mark.asyncio
async def test_runtime_forwards_max_retries(tmp_path: Path):
    """``run(max_retries=..)`` lands on the engine message metadata."""
    from sweave.harness.base import AgentResult
    from sweave.harness.engine import ENGINE_HARNESS_NAME
    from sweave.harness.base import harness_registry
    from sweave.runtime.delegation_store import Delegation
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime
    from sweave.runtime.specialist_store import Specialist
    from sweave.runtime.trace_log import TraceLog

    captured: list[dict] = []

    class _FakeProcess:
        _session_id = "eng_fwd_1"

        async def send(
            self, msg, on_chunk=None, trace=None, on_reasoning=None,
            on_tool=None,
        ):
            captured.append(dict(msg.metadata))
            return AgentResult(success=True, output="done")

    class _FakeHarness:
        name = ENGINE_HARNESS_NAME

        async def spawn(self, spec):
            return _FakeProcess()

        async def attach(self, session_id, spec):
            return _FakeProcess()

        def get_default_tools(self):
            return []

    harness_registry._harnesses[ENGINE_HARNESS_NAME] = _FakeHarness()
    try:
        rt = SpecialistRuntime(runners=ServeRunnerRegistry())
        spec = Specialist(name="orchestrator", is_orchestrator=True, harness="sweave-engine")
        d = Delegation(agent="orchestrator", task="hi", project_name="p")
        trace = TraceLog("d-fwd", base_dir=tmp_path)
        out = await rt.run(
            specialist=spec, delegation=d, worktree_path=tmp_path,
            message="hi", trace=trace, max_retries=4,
        )
        assert out == "done"
        assert captured and captured[0].get("max_retries") == 4
    finally:
        from sweave.harness.engine import SweaveEngineHarness

        harness_registry._harnesses[ENGINE_HARNESS_NAME] = SweaveEngineHarness()


def test_chat_loop_retry_default():
    import inspect

    from sweave.chat.loop import ChatLoop

    sig = inspect.signature(ChatLoop.__init__)
    assert sig.parameters["turn_retries"].default == 3
