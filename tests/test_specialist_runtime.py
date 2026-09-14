"""M1.3 step 2 tests: SpecialistRuntime.

All paths use a mocked ServeRunner (so no real opencode serve
starts). The mocked OpenCodeProcess speaks the same httpx stream
shape the real one does.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from sweave.runtime.delegation_store import Delegation
from sweave.runtime.serve_runner import ServeRunner, ServeRunnerRegistry
from sweave.runtime.specialist_runtime import SpecialistRuntime
from sweave.runtime.specialist_store import (
    ModelRef,
    Specialist,
)
from sweave.runtime.trace_log import TraceLog, read_trace


# Step 4: this file drives SpecialistRuntime.run end-to-end on the
# opencode path. The step-4 default flip would otherwise route every
# unpinned Specialist at the REAL engine sidecar (node + LLM);
# the mock seam pins opencode — the path under test — exactly as
# before the flip (GOTCHAS: runtime-path tests must set this).
@pytest.fixture(autouse=True, scope="module")
def _mock_opencode_env():
    import os

    old = os.environ.get("SWEAVE_MOCK_OPENCODE")
    os.environ["SWEAVE_MOCK_OPENCODE"] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("SWEAVE_MOCK_OPENCODE", None)
        else:
            os.environ["SWEAVE_MOCK_OPENCODE"] = old


# ---------------------------------------------------------------------------
# Mock OpenCodeProcess
# ---------------------------------------------------------------------------


class MockOpenCodeProcess:
    """Stands in for OpenCodeProcess; the runtime uses
    ``process._client`` (httpx.AsyncClient) directly + reads
    ``process._session_id`` to build the URL. The runtime also calls
    ``process.send(Message(...))`` for the system prompt; we mimic
    that with a thin ``send`` shim that routes through the same
    mock transport.
    """

    def __init__(self, client: httpx.AsyncClient, session_id: str = "sid-init") -> None:
        self._client = client
        self._session_id = session_id
        self._send_calls: list[dict] = []

    async def send(self, message: Any) -> Any:
        # Record the call and return a stub result. The runtime only
        # cares that the call doesn't raise.
        self._send_calls.append({
            "type": getattr(message, "type", None),
            "content": getattr(message, "content", None),
        })
        from sweave.harness.base import AgentResult

        return AgentResult(
            success=True,
            output="",
            metadata={"mock": True},
        )


# ---------------------------------------------------------------------------
# Mock ServeRunner
# ---------------------------------------------------------------------------


class _FakeProc:
    pid = 12345
    returncode = None


def _build_runner_with_mock_process(
    tmp_path: Path,
    *,
    worktree: Path | None = None,
    session_id: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> tuple[ServeRunner, Any]:
    """Build a ServeRunner with a fake subprocess + the given httpx
    client. The test's MockOpenCodeProcess is constructed separately
    by the runtime (it owns the client)."""
    worktree = worktree or (tmp_path / "wt")
    worktree.mkdir(parents=True, exist_ok=True)

    runner = ServeRunner(
        specialist_name="alpha",
        worktree_path=worktree,
    )
    runner.process = _FakeProc()
    runner.port = 9999
    runner.base_url = "http://127.0.0.1:9999"
    runner.log_path = tmp_path / "serve.log"
    runner.touch()
    if session_id is not None:
        runner.sessions[session_id] = True
    mock_proc = MockOpenCodeProcess(
        client=http_client, session_id=session_id or "sid-init"
    )
    return runner, mock_proc


# ---------------------------------------------------------------------------
# Mock httpx transport
# ---------------------------------------------------------------------------


def _mock_transport(routes: dict[tuple[str, str], Any]) -> httpx.MockTransport:
    """Build a MockTransport that maps (method, path) -> response builder.

    Each route is a function ``(request: httpx.Request) -> httpx.Response``.
    Unmatched routes return 404. The handler is built first and passed
    positionally (httpx 0.28+ signature).
    """
    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        builder = routes.get(key)
        if builder is None:
            return httpx.Response(404, json={"error": "not_found", "path": request.url.path})
        return builder(request)
    return httpx.MockTransport(handler)


def _session_id_response(sid: str) -> httpx.Response:
    return httpx.Response(200, json={"id": sid})


def _session_404(_request: httpx.Request | None = None) -> httpx.Response:
    return httpx.Response(404, json={"error": "not_found"})


def _stream_response(*chunks: str) -> httpx.Response:
    """Build a streaming JSON response from one or more pre-rendered chunks.

    httpx 0.28+ requires ``response.stream`` to be an
    ``AsyncByteStream`` (or its base ``ByteStream``). Passing
    ``content=list(...)`` wraps it in an ``IteratorByteStream``
    which fails the streaming send's ``isinstance`` check, so we
    construct a real ``ByteStream`` and assign it.
    """
    import httpx as _httpx

    r = _httpx.Response(200, content=b"")
    r.stream = _httpx.ByteStream(b"".join(c.encode("utf-8") for c in chunks))
    return r


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_session_creates_when_no_stored_id(tmp_path: Path):
    """fresh=True OR no stored session id -> POST /session + persist id."""
    transport = _mock_transport({
        ("POST", "/session"): lambda r: _session_id_response("sid-fresh"),
    })
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        runner, proc = _build_runner_with_mock_process(
            tmp_path, http_client=client, session_id=None
        )
        spec = Specialist(name="alpha", system_prompt="you are alpha")
        trace = TraceLog("d1", base_dir=tmp_path)
        runtime = SpecialistRuntime(runners=ServeRunnerRegistry())

        result_proc = await runtime._ensure_session(
            runner, proc, spec, trace, fresh=True
        )
        assert result_proc is proc
        assert spec.session_id == "sid-fresh"


@pytest.mark.asyncio
async def test_ensure_session_reuses_when_stored_id_valid(tmp_path: Path):
    """GET /session/{id} 200 -> reuse; no POST /session."""
    called: list[tuple[str, str]] = []
    def t_post(r: httpx.Request) -> httpx.Response:
        called.append((r.method, r.url.path))
        return httpx.Response(500, text="POST /session should not be called on reuse")
    def t_get(r: httpx.Request) -> httpx.Response:
        called.append((r.method, r.url.path))
        return httpx.Response(200, json={"id": "ses_stored"})
    transport = _mock_transport({
        ("GET", "/session/ses_stored"): t_get,
        ("POST", "/session"): t_post,
    })
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        runner, proc = _build_runner_with_mock_process(
            tmp_path, http_client=client, session_id="ses_stored"
        )
        spec = Specialist(name="alpha", system_prompt="", session_id="ses_stored")
        trace = TraceLog("d2", base_dir=tmp_path)
        runtime = SpecialistRuntime(runners=ServeRunnerRegistry())

        await runtime._ensure_session(runner, proc, spec, trace, fresh=False)
        assert spec.session_id == "ses_stored"
        # The GET path is the only one called; POST must NOT be invoked.
        assert all(c[0] == "GET" for c in called), f"unexpected calls: {called}"


@pytest.mark.asyncio
async def test_ensure_session_recreates_on_404(tmp_path: Path):
    """GET /session/{id} 404 -> POST /session + persist new id; warn-trace."""
    transport = _mock_transport({
        ("GET", "/session/ses_stale"): _session_404,
        ("POST", "/session"): lambda r: _session_id_response("sid-recreated"),
    })
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        runner, proc = _build_runner_with_mock_process(
            tmp_path, http_client=client, session_id="ses_stale"
        )
        spec = Specialist(name="alpha", system_prompt="", session_id="ses_stale")
        trace = TraceLog("d3", base_dir=tmp_path)
        runtime = SpecialistRuntime(runners=ServeRunnerRegistry())

        await runtime._ensure_session(runner, proc, spec, trace, fresh=False)
        assert spec.session_id == "sid-recreated"
        events = [e["event"] for e in read_trace(trace.delegation_id, base_dir=tmp_path)]
        assert "session_recreated_after_404" in events
        assert "session_recreated" in events


@pytest.mark.asyncio
async def test_ensure_session_ignores_foreign_engine_id(tmp_path: Path):
    """Stored `eng_*` id (prior engine turn) -> NO verify GET; create fresh.

    Live 2026-09-14: the engine→opencode fallback carried an eng_
    binding into the opencode path, the verify GET answered non-404,
    and the turn died in _send_message's ses_ guard. Mirror of
    _engine_session_resume (which refuses ses_* the same way).
    """
    called: list[tuple[str, str]] = []
    get_paths: list[str] = []
    def t_post(r: httpx.Request) -> httpx.Response:
        called.append((r.method, r.url.path))
        return _session_id_response("ses-fresh-after-eng")
    def t_get(r: httpx.Request) -> httpx.Response:
        get_paths.append(r.url.path)
        return httpx.Response(500, text="must not be verified")
    transport = _mock_transport({
        ("POST", "/session"): t_post,
        ("GET", "/session/eng_9dd37cb9ff1e"): t_get,
    })
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        runner, proc = _build_runner_with_mock_process(
            tmp_path, http_client=client, session_id="sid-init"
        )
        spec = Specialist(name="alpha", system_prompt="", session_id="eng_9dd37cb9ff1e")
        trace = TraceLog("d-foreign", base_dir=tmp_path)
        runtime = SpecialistRuntime(runners=ServeRunnerRegistry())

        await runtime._ensure_session(runner, proc, spec, trace, fresh=False)
        assert spec.session_id == "ses-fresh-after-eng"
        assert proc._session_id == "ses-fresh-after-eng"
        # No verify GET for the foreign id — straight to create.
        assert get_paths == []
        events = [e["event"] for e in read_trace(trace.delegation_id, base_dir=tmp_path)]
        assert "session_foreign_id_ignored" in events
        assert "session_created" in events


@pytest.mark.asyncio
async def test_ensure_session_recreates_on_verify_500(tmp_path: Path):
    """Verify GET 500 (not 404) -> recreate; only 200 reuses."""
    transport = _mock_transport({
        ("GET", "/session/ses_old"): lambda r: httpx.Response(500, text="boom"),
        ("POST", "/session"): lambda r: _session_id_response("ses-new2"),
    })
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        runner, proc = _build_runner_with_mock_process(
            tmp_path, http_client=client, session_id="sid-init"
        )
        spec = Specialist(name="alpha", system_prompt="", session_id="ses_old")
        trace = TraceLog("d-verify500", base_dir=tmp_path)
        runtime = SpecialistRuntime(runners=ServeRunnerRegistry())

        await runtime._ensure_session(runner, proc, spec, trace, fresh=False)
        assert spec.session_id == "ses-new2"
        events = [e["event"] for e in read_trace(trace.delegation_id, base_dir=tmp_path)]
        assert "session_recreated_after_verify" in events
        assert "session_recreated" in events


@pytest.mark.asyncio
async def test_ensure_session_sends_system_prompt_on_create(tmp_path: Path):
    """On session create, the system prompt is sent ONCE per session."""
    sent_paths: list[str] = []

    class _T(httpx.MockTransport):
        def __init__(self, handler=None):
            super().__init__(handler or self.handle_request)

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            sent_paths.append(request.url.path)
            if (request.method, request.url.path) == ("POST", "/session"):
                return _session_id_response("sid-1")
            if request.method == "POST" and request.url.path.endswith("/message"):
                return _stream_response(json.dumps({
                    "info": {"role": "assistant", "time": {"created": 0, "completed": 1}, "finish": "stop"},
                    "parts": [{"type": "text", "text": "ack"}],
                }))
            return httpx.Response(404)

    client = httpx.AsyncClient(transport=_T(), base_url="http://test")
    runner, proc = _build_runner_with_mock_process(
        tmp_path, http_client=client, session_id=None
    )
    spec = Specialist(name="alpha", system_prompt="you are alpha")
    trace = TraceLog("d4", base_dir=tmp_path)
    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    await runtime._ensure_session(runner, proc, spec, trace, fresh=True)
    # POST /session observed (session create)
    assert any(p == "/session" for p in sent_paths)
    # The system prompt is sent via process.send (not the transport);
    # the mock records the call.
    assert any(
        c.get("type") == "system" and c.get("content") == "you are alpha"
        for c in proc._send_calls
    ), f"system prompt not sent; calls: {proc._send_calls}"


# ---------------------------------------------------------------------------
# Model body construction
# ---------------------------------------------------------------------------


def test_model_body_emits_structured_pair():
    ref = ModelRef(provider="gmi", model_id="MiniMaxAI/MiniMax-M3")
    body = SpecialistRuntime._model_body(ref)
    assert body == {"providerID": "gmi", "modelID": "MiniMaxAI/MiniMax-M3"}


def test_model_body_returns_none_for_legacy_bare_ref():
    ref = ModelRef(provider=None, model_id="qwen3:8b")
    assert SpecialistRuntime._model_body(ref) is None


def test_model_body_returns_none_for_incomplete_ref():
    assert SpecialistRuntime._model_body(ModelRef(provider="ollama", model_id=None)) is None


# ---------------------------------------------------------------------------
# Integration: end-to-end with a mock transport
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_emits_structured_model_with_k_revised(tmp_path: Path):
    """When a ModelRef is provided, the v2 body carries {providerID,
    modelID}. The wire shape is what opencode v2 requires (probe 5b
    proved bare names get a 400).

    The streaming response is hard to mock with httpx 0.28+
    MockTransport (see probe_5* tests for the end-to-end real-serve
    probe). This test uses a non-streaming POST path: the runtime
    delegates to ``process._client.post`` (not ``.stream``) when the
    transport advertises non-streaming, OR we patch the runtime's
    ``_send_message`` to use a non-streaming call. We take the
    simpler route: assert the model_body construction
    independently, and verify the runtime call path with a
    non-streaming shim.
    """
    sent_to_message: list[dict] = []

    class _T(httpx.MockTransport):
        def __init__(self, handler=None):
            super().__init__(handler or self.handle_request)

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            if (request.method, request.url.path) == ("POST", "/session"):
                return _session_id_response("sid-e2e")
            if request.method == "POST" and request.url.path.endswith("/message"):
                body = json.loads(request.content)
                sent_to_message.append(body)
                # Use a non-streaming response (the runtime's
                # _send_message catches general Exception and formats
                # the error; for a non-streaming call we POST and
                # parse the response as a single JSON object).
                return httpx.Response(200, json={
                    "info": {"role": "assistant", "time": {"created": 0, "completed": 1}, "finish": "stop"},
                    "parts": [{"type": "text", "text": "hello back"}],
                })
            return httpx.Response(404)

    runner_reg = ServeRunnerRegistry()

    async def fake_start(self):
        self.process = _FakeProc()
        self.port = 9999
        self.base_url = "http://127.0.0.1:9999"
        self.log_path = tmp_path / "serve.log"
        self.touch()

    from sweave.runtime.serve_runner import ServeRunner as SR
    from sweave.runtime import specialist_runtime as sr_mod
    orig_start = SR.start
    orig_send = sr_mod.SpecialistRuntime._send_message
    SR.start = fake_start  # type: ignore[assignment]

    async def non_streaming_send(self, process, body, trace, *args, **kwargs):
        # The runtime normally does
        # ``process._client.stream(POST, /session/{id}/message, json=body)``
        # and consumes the stream. For this test we want a single
        # JSON response (matches the live v2 response on a non-streaming
        # call; the wire format is the same). The ModelRef is built
        # the same way.
        client = process._client
        resp = await client.post(
            f"/session/{process._session_id}/message", json=body
        )
        resp.raise_for_status()
        data = resp.json()
        text_parts: list[str] = []
        for part in data.get("parts", []) or []:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(part.get("text", ""))
        trace.append("output_text", {"chunks": len(text_parts), "length": sum(len(t) for t in text_parts)})
        return "".join(text_parts)

    sr_mod.SpecialistRuntime._send_message = non_streaming_send  # type: ignore[assignment]
    try:
        worktree = tmp_path / "wt"
        worktree.mkdir(parents=True, exist_ok=True)
        spec = Specialist(name="alpha", system_prompt="you are alpha")
        trace = TraceLog("d-e2e", base_dir=tmp_path)
        delegation = Delegation(agent="alpha", task="do the thing", model="")
        client = httpx.AsyncClient(transport=_T(), base_url="http://test")

        async def fake_build(self_runner, d, *_args, **_kwargs):
            return MockOpenCodeProcess(client=client, session_id="sid-init")

        orig_build = sr_mod.SpecialistRuntime._build_process
        sr_mod.SpecialistRuntime._build_process = fake_build  # type: ignore[assignment]
        try:
            runtime = SpecialistRuntime(runners=runner_reg)
            model_ref = ModelRef(provider="zai", model_id="glm-5.3")
            output = await runtime.run(
                specialist=spec,
                delegation=delegation,
                worktree_path=worktree,
                message="do the thing",
                trace=trace,
                model_ref=model_ref,
            )
            assert output == "hello back"
            assert spec.session_id == "sid-e2e"
            assert len(sent_to_message) == 1
            body = sent_to_message[0]
            assert body["model"] == {
                "providerID": "zai", "modelID": "glm-5.3"
            }
            assert body["parts"][0]["text"].startswith("Task working directory: ")
            assert "do the thing" in body["parts"][0]["text"]
        finally:
            sr_mod.SpecialistRuntime._build_process = orig_build  # type: ignore[assignment]
    finally:
        SR.start = orig_start  # type: ignore[assignment]
        sr_mod.SpecialistRuntime._send_message = orig_send  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_run_legacy_bare_model_emits_no_structured_body(tmp_path: Path):
    """A v1 specialist (bare string) -> body has no 'model' key;
    the harness falls back to the unqualified-name path with a warning.

    M1.3 K-revised: v1 path survives with a warning (not a hard
    error) for backward compatibility. The bare name is still sent
    so default-provider models work; non-default providers will 400
    on the serve end, which the harness surfaces verbatim."""
    sent_to_message: list[dict] = []

    class _T(httpx.MockTransport):
        def __init__(self, handler=None):
            super().__init__(handler or self.handle_request)

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            if (request.method, request.url.path) == ("POST", "/session"):
                return _session_id_response("sid-legacy")
            if request.method == "POST" and request.url.path.endswith("/message"):
                body = json.loads(request.content)
                sent_to_message.append(body)
                return httpx.Response(200, json={
                    "info": {"role": "assistant", "time": {"created": 0, "completed": 1}, "finish": "stop"},
                    "parts": [{"type": "text", "text": "ok"}],
                })
            return httpx.Response(404)

    runner_reg = ServeRunnerRegistry()

    async def fake_start(self):
        self.process = _FakeProc()
        self.port = 9999
        self.base_url = "http://127.0.0.1:9999"
        self.log_path = tmp_path / "serve.log"
        self.touch()

    from sweave.runtime.serve_runner import ServeRunner as SR
    from sweave.runtime import specialist_runtime as sr_mod
    orig_start = SR.start
    orig_send = sr_mod.SpecialistRuntime._send_message
    SR.start = fake_start  # type: ignore[assignment]

    async def non_streaming_send(self, process, body, trace, *args, **kwargs):
        client = process._client
        resp = await client.post(
            f"/session/{process._session_id}/message", json=body
        )
        resp.raise_for_status()
        data = resp.json()
        text_parts: list[str] = []
        for part in data.get("parts", []) or []:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(part.get("text", ""))
        trace.append("output_text", {"chunks": len(text_parts), "length": sum(len(t) for t in text_parts)})
        return "".join(text_parts)

    sr_mod.SpecialistRuntime._send_message = non_streaming_send  # type: ignore[assignment]
    try:
        worktree = tmp_path / "wt"
        worktree.mkdir(parents=True, exist_ok=True)
        spec = Specialist(
            name="alpha", system_prompt="", current_model="qwen3:8b"
        )
        trace = TraceLog("d-legacy", base_dir=tmp_path)
        delegation = Delegation(agent="alpha", task="x", model="")
        client = httpx.AsyncClient(transport=_T(), base_url="http://test")

        async def fake_build(self_runner, d, *_args, **_kwargs):
            return MockOpenCodeProcess(client=client, session_id="sid-init")

        orig_build = sr_mod.SpecialistRuntime._build_process
        sr_mod.SpecialistRuntime._build_process = fake_build  # type: ignore[assignment]
        try:
            runtime = SpecialistRuntime(runners=runner_reg)
            await runtime.run(
                specialist=spec,
                delegation=delegation,
                worktree_path=worktree,
                message="x",
                trace=trace,
            )
            assert len(sent_to_message) == 1
            body = sent_to_message[0]
            # The structured pair is absent: model key is missing.
            assert "model" not in body
        finally:
            sr_mod.SpecialistRuntime._build_process = orig_build  # type: ignore[assignment]
    finally:
        SR.start = orig_start  # type: ignore[assignment]
        sr_mod.SpecialistRuntime._send_message = orig_send  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_run_with_fresh_creates_new_session_each_time(tmp_path: Path):
    session_create_count = 0

    class _T(httpx.MockTransport):
        def __init__(self, handler=None):
            super().__init__(handler or self.handle_request)

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal session_create_count
            if (request.method, request.url.path) == ("POST", "/session"):
                session_create_count += 1
                return _session_id_response(f"sid-{session_create_count}")
            if request.method == "POST" and request.url.path.endswith("/message"):
                return httpx.Response(200, json={
                    "info": {"role": "assistant", "time": {"created": 0, "completed": 1}, "finish": "stop"},
                    "parts": [{"type": "text", "text": "ok"}],
                })
            return httpx.Response(404)

    runner_reg = ServeRunnerRegistry()

    async def fake_start(self):
        self.process = _FakeProc()
        self.port = 9999
        self.base_url = "http://127.0.0.1:9999"
        self.log_path = tmp_path / "serve.log"
        self.touch()

    from sweave.runtime.serve_runner import ServeRunner as SR
    from sweave.runtime import specialist_runtime as sr_mod
    orig_start = SR.start
    orig_send = sr_mod.SpecialistRuntime._send_message
    SR.start = fake_start  # type: ignore[assignment]

    async def non_streaming_send(self, process, body, trace, *args, **kwargs):
        client = process._client
        resp = await client.post(
            f"/session/{process._session_id}/message", json=body
        )
        resp.raise_for_status()
        data = resp.json()
        text_parts: list[str] = []
        for part in data.get("parts", []) or []:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(part.get("text", ""))
        trace.append("output_text", {"chunks": len(text_parts), "length": sum(len(t) for t in text_parts)})
        return "".join(text_parts)

    sr_mod.SpecialistRuntime._send_message = non_streaming_send  # type: ignore[assignment]
    try:
        worktree = tmp_path / "wt"
        worktree.mkdir(parents=True, exist_ok=True)
        spec = Specialist(
            name="alpha", system_prompt="", session_id="sid-pre-existing"
        )
        trace = TraceLog("d-fresh", base_dir=tmp_path)
        delegation = Delegation(agent="alpha", task="x", model="")
        client = httpx.AsyncClient(transport=_T(), base_url="http://test")

        async def fake_build(self_runner, d, *_args, **_kwargs):
            return MockOpenCodeProcess(client=client, session_id="sid-init")

        orig_build = sr_mod.SpecialistRuntime._build_process
        sr_mod.SpecialistRuntime._build_process = fake_build  # type: ignore[assignment]
        try:
            runtime = SpecialistRuntime(runners=runner_reg)
            await runtime.run(
                specialist=spec,
                delegation=delegation,
                worktree_path=worktree,
                message="x",
                trace=trace,
                fresh=True,
            )
            assert spec.session_id == "sid-1"
            assert session_create_count == 1
        finally:
            sr_mod.SpecialistRuntime._build_process = orig_build  # type: ignore[assignment]
    finally:
        SR.start = orig_start  # type: ignore[assignment]
        sr_mod.SpecialistRuntime._send_message = orig_send  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Single-active-task queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_runs_for_same_key_serialise(tmp_path: Path):
    """Two concurrent run() calls for the same (specialist, worktree)
    key serialise (one runs after the other) per the design's
    'one active task per specialist' rule."""
    in_flight = 0
    max_in_flight = 0

    class _T(httpx.MockTransport):
        def __init__(self, handler=None):
            super().__init__(handler or self.handle_request)

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            if (request.method, request.url.path) == ("POST", "/session"):
                return _session_id_response("sid-q")
            return httpx.Response(200, json={
                "info": {"role": "assistant", "time": {"created": 0, "completed": 1}, "finish": "stop"},
                "parts": [{"type": "text", "text": "x"}],
            })

    runner_reg = ServeRunnerRegistry()

    async def fake_start(self):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        self.process = _FakeProc()
        self.port = 9999
        self.base_url = "http://127.0.0.1:9999"
        self.log_path = tmp_path / "serve.log"
        self.touch()

    async def fake_shutdown(self):
        nonlocal in_flight
        in_flight -= 1

    from sweave.runtime.serve_runner import ServeRunner as SR
    orig_start = SR.start
    orig_shutdown = SR.shutdown
    SR.start = fake_start  # type: ignore[assignment]
    SR.shutdown = fake_shutdown  # type: ignore[assignment]
    try:
        worktree = tmp_path / "wt"
        worktree.mkdir(parents=True, exist_ok=True)
        spec = Specialist(name="alpha", system_prompt="")
        trace1 = TraceLog("d-q1", base_dir=tmp_path)
        trace2 = TraceLog("d-q2", base_dir=tmp_path)
        client = httpx.AsyncClient(transport=_T(), base_url="http://test")

        from sweave.runtime import specialist_runtime as sr_mod

        async def fake_build(self_runner, d, *_args, **_kwargs):
            return MockOpenCodeProcess(client=client, session_id="sid-init")

        orig_build = sr_mod.SpecialistRuntime._build_process
        orig_send = sr_mod.SpecialistRuntime._send_message
        sr_mod.SpecialistRuntime._build_process = fake_build  # type: ignore[assignment]

        async def slow_send(self, process, body, trace, *args, **kwargs):
            # Non-streaming shim (httpx 0.28+ MockTransport + ByteStream
            # don't play well together). The runtime's _send_message
            # contract is: POST to /session/{id}/message, parse the
            # JSON response, return concatenated text.
            if "a" in str(body):
                await asyncio.sleep(0.1)
            client = process._client
            resp = await client.post(
                f"/session/{process._session_id}/message", json=body
            )
            resp.raise_for_status()
            data = resp.json()
            text_parts = [
                p.get("text", "")
                for p in (data.get("parts") or [])
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            return "".join(text_parts)

        sr_mod.SpecialistRuntime._send_message = slow_send  # type: ignore[assignment]
        try:
            runtime = SpecialistRuntime(runners=runner_reg)
            d1 = Delegation(agent="alpha", task="a", model="")
            d2 = Delegation(agent="alpha", task="b", model="")
            results = await asyncio.gather(
                runtime.run(
                    specialist=spec, delegation=d1, worktree_path=worktree,
                    message="a", trace=trace1,
                ),
                runtime.run(
                    specialist=spec, delegation=d2, worktree_path=worktree,
                    message="b", trace=trace2,
                ),
            )
            assert all(r == "x" for r in results)
            assert max_in_flight == 1, (
                f"queue failed: {max_in_flight} concurrent runs observed"
            )
        finally:
            sr_mod.SpecialistRuntime._build_process = orig_build  # type: ignore[assignment]
            sr_mod.SpecialistRuntime._send_message = orig_send  # type: ignore[assignment]
    finally:
        SR.start = orig_start  # type: ignore[assignment]
        SR.shutdown = orig_shutdown  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_different_keys_run_in_parallel(tmp_path: Path):
    """Different (specialist, worktree) keys run concurrently. We
    verify the absence of cross-key blocking by running two keys
    and asserting the total wall-time is < the sum of their
    per-call sleeps."""
    class _T(httpx.MockTransport):
        def __init__(self, handler=None):
            super().__init__(handler or self.handle_request)

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            if (request.method, request.url.path) == ("POST", "/session"):
                return _session_id_response("sid-p")
            return httpx.Response(200, json={
                "info": {"role": "assistant", "time": {"created": 0, "completed": 1}, "finish": "stop"},
                "parts": [{"type": "text", "text": "x"}],
            })

    runner_reg = ServeRunnerRegistry()

    async def fake_start(self):
        self.process = _FakeProc()
        self.port = 9999
        self.base_url = "http://127.0.0.1:9999"
        self.log_path = tmp_path / "serve.log"
        self.touch()

    async def fake_shutdown(self):
        pass

    from sweave.runtime.serve_runner import ServeRunner as SR
    orig_start = SR.start
    orig_shutdown = SR.shutdown
    SR.start = fake_start  # type: ignore[assignment]
    SR.shutdown = fake_shutdown  # type: ignore[assignment]
    try:
        wt_a = tmp_path / "wt_a"
        wt_b = tmp_path / "wt_b"
        wt_a.mkdir()
        wt_b.mkdir()
        client = httpx.AsyncClient(transport=_T(), base_url="http://test")

        from sweave.runtime import specialist_runtime as sr_mod

        async def fake_build(self_runner, d, *_args, **_kwargs):
            return MockOpenCodeProcess(client=client, session_id="sid-init")

        orig_build = sr_mod.SpecialistRuntime._build_process
        sr_mod.SpecialistRuntime._build_process = fake_build  # type: ignore[assignment]
        try:
            runtime = SpecialistRuntime(runners=runner_reg)
            spec_a = Specialist(name="alpha", system_prompt="")
            spec_b = Specialist(name="bravo", system_prompt="")
            d_a = Delegation(agent="alpha", task="a", model="")
            d_b = Delegation(agent="bravo", task="b", model="")
            trace_a = TraceLog("d-pa", base_dir=tmp_path)
            trace_b = TraceLog("d-pb", base_dir=tmp_path)
            real_send = sr_mod.SpecialistRuntime._send_message

            async def slow_send(self, process, body, trace, *args, **kwargs):
                await asyncio.sleep(0.1)
                # Non-streaming shim (see test_concurrent_runs_for_same_key_serialise
                # for the rationale).
                client = process._client
                resp = await client.post(
                    f"/session/{process._session_id}/message", json=body
                )
                resp.raise_for_status()
                data = resp.json()
                text_parts = [
                    p.get("text", "")
                    for p in (data.get("parts") or [])
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                return "".join(text_parts)

            sr_mod.SpecialistRuntime._send_message = slow_send  # type: ignore[assignment]
            try:
                t0 = time.monotonic()
                await asyncio.gather(
                    runtime.run(specialist=spec_a, delegation=d_a, worktree_path=wt_a, message="a", trace=trace_a),
                    runtime.run(specialist=spec_b, delegation=d_b, worktree_path=wt_b, message="b", trace=trace_b),
                )
                elapsed = time.monotonic() - t0
                # If the keys were serialised, the wall time would be
                # ~0.2s (two 0.1s sleeps). Parallel: ~0.1s. Allow a
                # generous fudge for CI.
                assert elapsed < 0.18, (
                    f"different keys appear to be serialised: {elapsed:.3f}s"
                )
            finally:
                sr_mod.SpecialistRuntime._send_message = real_send  # type: ignore[assignment]
        finally:
            sr_mod.SpecialistRuntime._build_process = orig_build  # type: ignore[assignment]
    finally:
        SR.start = orig_start  # type: ignore[assignment]
        SR.shutdown = orig_shutdown  # type: ignore[assignment]


# (imports used by the parallel-timing test above)
import asyncio
import time

# ---------------------------------------------------------------------------
# info.error surfacing (2026-09-10 rerun incident)
# ---------------------------------------------------------------------------


def _send_message_proc(tmp_path: Path, *chunks: str) -> tuple[Any, TraceLog]:
    """Mock process whose /message stream replays the given chunks.

    Note: httpx 0.28+ MockTransport does not deliver a manually
    assigned ByteStream through ``client.stream()`` (aiter_text
    yields nothing), so we fake the stream CM directly. This still
    exercises the real ``_send_message`` parsing/branching -- the
    layer the incident bit through.
    """

    class _FakeStreamClient:
        def stream(self, method: str, url: str, **kwargs: Any) -> Any:
            class _Resp:
                async def __aenter__(self) -> _Resp:
                    return self

                async def __aexit__(self, *args: Any) -> bool:
                    return False

                def raise_for_status(self) -> None:
                    pass

                async def aiter_text(self) -> Any:
                    for c in chunks:
                        yield c

            return _Resp()

    proc = MockOpenCodeProcess(client=_FakeStreamClient(), session_id="ses_errtest")
    return proc, TraceLog("d-errtest", base_dir=tmp_path)


@pytest.mark.asyncio
async def test_send_message_surfaces_info_error(tmp_path: Path):
    """A 200 stream carrying info.error (e.g. 401 CreditsError) with zero
    text parts must come back as a "[chat error:" failure, never "".

    Regression: two rerun turns hit an upstream 401, the runtime
    returned "", and the chat loop persisted empty assistant messages
    (turns that "did nothing"; the error was only in opencode.db).
    """
    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    proc, trace = _send_message_proc(
        tmp_path,
        json.dumps({
            "info": {
                "role": "assistant",
                "time": {"created": 1, "completed": 2},
                "finish": "stop",
                "error": {
                    "name": "APIError",
                    "data": {"message": "Insufficient balance.", "statusCode": 401},
                },
            },
            "parts": [],
        }),
    )
    out = await runtime._send_message(
        proc, {"parts": [{"type": "text", "text": "hi"}]}, trace
    )
    assert out == "[chat error: APIError: Insufficient balance.]"
    events = [e["event"] for e in read_trace(trace.delegation_id, base_dir=tmp_path)]
    assert "info_error" in events
    assert "output_text" not in events


@pytest.mark.asyncio
async def test_send_message_flags_terminal_less_stream(tmp_path: Path):
    """A stream that ends with no text parts and no terminal flag is an
    incomplete turn, not an empty answer."""
    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    proc, trace = _send_message_proc(
        tmp_path,
        json.dumps({
            "info": {"role": "assistant", "time": {"created": 1}},
            "parts": [{"type": "reasoning", "text": "hmm"}],
        }),
    )
    out = await runtime._send_message(
        proc, {"parts": [{"type": "text", "text": "hi"}]}, trace
    )
    assert out.startswith("[chat error: opencode serve: incomplete turn")
    events = [e["event"] for e in read_trace(trace.delegation_id, base_dir=tmp_path)]
    assert "incomplete_turn" in events


@pytest.mark.asyncio
async def test_send_message_terminal_empty_text_still_success(tmp_path: Path):
    """A terminal turn with no text (e.g. tool-only) keeps the old
    success contract -- only error and unterminated streams change."""
    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    proc, trace = _send_message_proc(
        tmp_path,
        json.dumps({
            "info": {"role": "assistant", "time": {"created": 1, "completed": 2}, "finish": "stop"},
            "parts": [],
        }),
    )
    out = await runtime._send_message(
        proc, {"parts": [{"type": "text", "text": "hi"}]}, trace
    )
    assert out == ""

@pytest.mark.asyncio
async def test_send_message_stalls_on_wire_silence(tmp_path: Path):
    """No bytes for stall_seconds -> truthful stall error (never a
    bare ReadTimeout, never an empty success). Regression for the
    stream-probe hangs (hung tool approval, zero bytes, 300s of
    nothing, then a cryptic ReadTimeout)."""
    import asyncio as _asyncio

    class _HangingClient:
        def stream(self, method: str, url: str, **kwargs: Any) -> Any:
            class _Resp:
                async def __aenter__(self) -> Any:
                    return self

                async def __aexit__(self, *args: Any) -> bool:
                    return False

                def raise_for_status(self) -> None:
                    pass

                async def aiter_text(self) -> Any:
                    await _asyncio.sleep(10.0)
                    yield "{}"
                    return

            return _Resp()

    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    proc = MockOpenCodeProcess(client=_HangingClient(), session_id="ses_stalltest")
    trace = TraceLog("d-stalltest", base_dir=tmp_path)
    out = await runtime._send_message(
        proc, {"parts": [{"type": "text", "text": "hi"}]}, trace,
        stall_seconds=0.2,
    )
    assert out.startswith("[chat error: stalled after ")
    assert "without data" in out
    events = [e["event"] for e in read_trace(trace.delegation_id, base_dir=tmp_path)]
    assert "stalled" in events
    assert "output_text" not in events


@pytest.mark.asyncio
async def test_send_message_glues_split_objects(tmp_path: Path):
    """A wire object split across chunks is glued via carry (was:
    silently dropped, losing the object)."""
    import json as _json

    seen: list[str] = []

    class _SplitClient:
        def stream(self, method: str, url: str, **kwargs: Any) -> Any:
            full = _json.dumps({
                "info": {"role": "assistant", "time": {"created": 1, "completed": 2}, "finish": "stop"},
                "parts": [{"type": "text", "text": "glued!"}],
            })
            half = len(full) // 2

            class _Resp:
                async def __aenter__(self) -> Any:
                    return self

                async def __aexit__(self, *args: Any) -> bool:
                    return False

                def raise_for_status(self) -> None:
                    pass

                async def aiter_text(self) -> Any:
                    yield full[:half]
                    yield full[half:]

            return _Resp()

    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    proc = MockOpenCodeProcess(client=_SplitClient(), session_id="ses_splittest")
    trace = TraceLog("d-splittest", base_dir=tmp_path)
    out = await runtime._send_message(
        proc, {"parts": [{"type": "text", "text": "hi"}]}, trace
    )
    assert out == "glued!"
