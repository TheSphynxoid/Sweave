"""M1.4+M1.5 step 1 tests: ModelRef into the harness contract.

Covers:
* message.model precedence over spec.model in OpenCodeProcess.send
* Bare (provider=None) Message.model falls back to the unqualified-name path
* Contract type import stability: ModelRef + model_ref_to_wire live in
  sweave.harness.base; specialist_store re-exports the same object
* SpecialistRuntime records model_used on the trace
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from sweave.harness.base import (
    AgentSpec,
    Message,
    ModelRef,
    model_ref_to_wire,
)
from sweave.harness.opencode import OpenCodeProcess


# ---------------------------------------------------------------------------
# Hermeticity: same fixture as test_m1_3_step3_job_runner_integration.py
# (module-scoped, autouse). The runtime tests at the bottom of this file
# drive ``SpecialistRuntime.run`` end-to-end; without the env var the
# ServeRunner would try to spawn a real ``opencode serve`` subprocess.
# ---------------------------------------------------------------------------
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
# Helpers (mirrors the test_opencode_v2_harness.py pattern)
# ---------------------------------------------------------------------------


def _make_spec(
    worktree: Path | None = None,
    model: str = "",
) -> AgentSpec:
    return AgentSpec(
        name="test-agent",
        role="backend",
        model=model,
        system_prompt="you are a test agent",
        worktree_path=worktree or Path("/tmp/worktree"),
        memory_bank="project-test",
        tools=[],
        harness="opencode",
    )


def _make_process_with_transport(
    spec: AgentSpec,
    handler,
) -> OpenCodeProcess:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        base_url="http://test", transport=transport, timeout=10.0
    )
    proc = OpenCodeProcess(
        spec=spec,
        process=MagicMock(),  # never used by send()
        base_url="http://test",
        session_id="ses_init",
    )
    proc._client = client
    return proc


# ---------------------------------------------------------------------------
# OpenCodeProcess.send: message.model beats spec.model
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_model_overrides_spec_model(tmp_path: Path):
    """M1.4+M1.5 step 1: ``Message.model`` wins over ``AgentSpec.model``
    when both are set. The structured ``{providerID, modelID}`` from
    the per-message override is what the wire sees.
    """
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path == "/session" and request.method == "POST":
            return httpx.Response(200, json={"id": "ses_x"})
        if request.url.path == "/session/ses_x/message":
            return httpx.Response(
                200,
                content=json.dumps({
                    "info": {"role": "assistant", "time": {"created": 0, "completed": 1}, "finish": "stop"},
                    "parts": [{"type": "text", "text": "ok"}],
                }),
            )
        return httpx.Response(404)

    # spec.model is one thing; Message.model is a different provider/model.
    spec = _make_spec(tmp_path, model="gmi/MiniMaxAI/MiniMax-M3")
    process = _make_process_with_transport(spec, handler)

    result = await process.send(
        Message(
            type="user",
            content="hi",
            model=ModelRef(provider="ollama", model_id="qwen3:8b"),
        )
    )
    assert result.success is True
    body = json.loads(captured[1].content)
    # The per-message ModelRef wins; spec.model is ignored for this call.
    assert body["model"] == {"providerID": "ollama", "modelID": "qwen3:8b"}


@pytest.mark.asyncio
async def test_send_message_model_bare_falls_back_to_unqualified_name(
    tmp_path: Path,
):
    """A Message.model with provider=None (legacy/bare ref) emits just
    the model_id on the wire -- the v2 protocol then resolves the
    provider from its own default. Matches the SpecialistRuntime path
    for v1 records.
    """
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path == "/session" and request.method == "POST":
            return httpx.Response(200, json={"id": "ses_b"})
        if request.url.path == "/session/ses_b/message":
            return httpx.Response(
                200,
                content=json.dumps({
                    "info": {"role": "assistant", "time": {"created": 0, "completed": 1}, "finish": "stop"},
                    "parts": [{"type": "text", "text": "ok"}],
                }),
            )
        return httpx.Response(404)

    process = _make_process_with_transport(_make_spec(tmp_path), handler)
    result = await process.send(
        Message(
            type="user",
            content="x",
            model=ModelRef(provider=None, model_id="qwen3:8b"),
        )
    )
    assert result.success is True
    body = json.loads(captured[1].content)
    # Bare id path: serve resolves the provider itself.
    assert body["model"] == "qwen3:8b"


@pytest.mark.asyncio
async def test_send_no_message_model_uses_spec_model(tmp_path: Path):
    """When Message.model is None, the legacy spec.model path is used
    (regression guard: per-message override must not break the default).
    """
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path == "/session" and request.method == "POST":
            return httpx.Response(200, json={"id": "ses_n"})
        if request.url.path == "/session/ses_n/message":
            return httpx.Response(
                200,
                content=json.dumps({
                    "info": {"role": "assistant", "time": {"created": 0, "completed": 1}, "finish": "stop"},
                    "parts": [{"type": "text", "text": "ok"}],
                }),
            )
        return httpx.Response(404)

    spec = _make_spec(tmp_path, model="zhipu/glm-5")
    process = _make_process_with_transport(spec, handler)
    result = await process.send(Message(type="user", content="hi"))
    assert result.success is True
    body = json.loads(captured[1].content)
    assert body["model"] == {"providerID": "zhipu", "modelID": "glm-5"}


# ---------------------------------------------------------------------------
# Contract type import stability
# ---------------------------------------------------------------------------


def test_model_ref_canonical_home_is_harness_base():
    """M1.4+M1.5 step 1: ModelRef is owned by sweave.harness.base.
    The specialist_store re-export is the same object (not a copy).
    """
    from sweave.harness.base import ModelRef as CanonicalModelRef
    from sweave.harness.base import model_ref_to_wire as CanonicalToWire
    from sweave.runtime.specialist_store import (
        ModelRef as CompatModelRef,
        model_ref_to_wire as CompatToWire,
    )
    # Same object identity -- the re-export is a name binding, not a copy.
    assert CompatModelRef is CanonicalModelRef
    assert CompatToWire is CanonicalToWire


def test_message_field_is_optional_modelref():
    """M1.4+M1.5 step 1: Message gains an optional ``model: ModelRef | None``
    field. Default is None (backward compatible with code that builds
    Message(type=..., content=...) without naming the model).
    """
    m = Message(type="user", content="hi")
    assert m.model is None
    # And the type annotation is permissive.
    m2 = Message(type="user", content="hi", model={"provider": "gmi", "model_id": "m"})
    assert m2.model == ModelRef(provider="gmi", model_id="m")


# ---------------------------------------------------------------------------
# model_ref_to_wire: unchanged behavior under its new home
# ---------------------------------------------------------------------------


def test_model_ref_to_wire_canonical_home():
    assert model_ref_to_wire(None) is None
    assert model_ref_to_wire(ModelRef()) is None
    assert model_ref_to_wire(ModelRef(provider="gmi", model_id="m")) == {
        "providerID": "gmi",
        "modelID": "m",
    }
    # Incomplete ref returns None (runtime emits a warning in production).
    assert model_ref_to_wire(ModelRef(provider="gmi")) is None
    assert model_ref_to_wire(ModelRef(model_id="m")) is None


# ---------------------------------------------------------------------------
# SpecialistRuntime: model_used trace event
# ---------------------------------------------------------------------------
#
# We reuse the same MockOpenCodeProcess + _build_runner_with_mock_process
# scaffolding from tests/test_specialist_runtime.py (kept in sync by hand)
# rather than importing it, to keep this test file self-contained for the
# M1.4+M1.5 step 1 contract. The runtime only needs the runner's HTTP
# client and an _ensure_session path; the send mock is per-instance.


import json as _json
import httpx as _httpx

from sweave.runtime.specialist_runtime import SpecialistRuntime
from sweave.runtime.serve_runner import ServeRunnerRegistry
from sweave.runtime.trace_log import TraceLog
from sweave.runtime.delegation_store import Delegation
from sweave.runtime.specialist_store import Specialist as _Specialist


class _Step1MockProcess:
    """Stand-in OpenCodeProcess; the runtime uses _client + _session_id."""

    def __init__(self, client: _httpx.AsyncClient, session_id: str) -> None:
        self._client = client
        self._session_id = session_id
        self._send_calls: list[dict] = []

    async def send(self, message: Any) -> Any:
        from sweave.harness.base import AgentResult
        self._send_calls.append(
            {"type": getattr(message, "type", None), "content": getattr(message, "content", None)}
        )
        return AgentResult(success=True, output="", metadata={"mock": True})


@pytest.mark.asyncio
async def test_runtime_records_model_used_on_trace(tmp_path: Path):
    """M1.4+M1.5 step 1: after a delegation, the trace contains a
    ``model_used`` event with the resolved ModelRef, the wire shape,
    and the source level (task_override / specialist.current_model / none).
    """
    import os

    from sweave.runtime.serve_runner import ServeRunner as _SR

    assert os.environ.get("SWEAVE_MOCK_OPENCODE") == "1", (
        "This test depends on the SWEAVE_MOCK_OPENCODE=1 fixture; "
        "the runtime otherwise tries to start a real subprocess."
    )

    worktree = tmp_path / "wt"
    worktree.mkdir(parents=True, exist_ok=True)

    # Stub the runner: avoid subprocess start; provide a mock process.
    async def _fake_start(self) -> None:
        self.port = 0
        self.base_url = "http://mock-opencode"
        self.process = None
        self.log_path = None
        self.touch()

    orig_start = _SR.start
    _SR.start = _fake_start  # type: ignore[assignment]
    try:
        # Build the runtime. Use the stub `_build_process` to attach a
        # mock process to a real httpx transport.
        class _T(_httpx.MockTransport):
            def __init__(self) -> None:
                def handler(request: _httpx.Request) -> _httpx.Response:
                    if request.url.path == "/session" and request.method == "POST":
                        return _httpx.Response(200, content=_json.dumps({"id": "ses_trace"}))
                    return _httpx.Response(200, content=_json.dumps({
                        "info": {"role": "assistant", "time": {"created": 0, "completed": 1}, "finish": "stop"},
                        "parts": [{"type": "text", "text": "ok"}],
                    }))
                super().__init__(handler)

        client = _httpx.AsyncClient(transport=_T(), base_url="http://test")

        runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
        spec = _Specialist(
            name="alpha", system_prompt="", current_model="ollama/qwen3:8b"
        )
        # task_override: a structured ModelRef passed to runtime.run.
        model_ref = ModelRef(provider="zai", model_id="glm-5.3")
        trace = TraceLog("d-trace", base_dir=tmp_path)
        delegation = Delegation(agent="alpha", task="x", model="")

        async def fake_build(self_runner, d, *_args, **_kwargs):
            return _Step1MockProcess(client=client, session_id="ses_trace")

        import sweave.runtime.specialist_runtime as sr_mod

        orig_build = sr_mod.SpecialistRuntime._build_process
        sr_mod.SpecialistRuntime._build_process = fake_build  # type: ignore[assignment]
        try:
            await runtime.run(
                specialist=spec,
                delegation=delegation,
                worktree_path=worktree,
                message="x",
                trace=trace,
                model_ref=model_ref,
            )
        finally:
            sr_mod.SpecialistRuntime._build_process = orig_build  # type: ignore[assignment]
            await client.aclose()

        # Read the trace and assert the model_used event is present.
        events = [
            _json.loads(line)
            for line in (trace.path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        used_events = [e for e in events if e.get("event") == "model_used"]
        assert len(used_events) == 1
        used = used_events[0]
        # task_override wins; specialist.current_model is not consulted.
        assert used["model_ref"] == {"provider": "zai", "model_id": "glm-5.3"}
        assert used["model_wire"] == {"providerID": "zai", "modelID": "glm-5.3"}
        assert used["source"] == "task_override"
    finally:
        _SR.start = orig_start  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_runtime_records_model_used_source_specialist(tmp_path: Path):
    """When no task_override is passed, the trace's ``model_used.source``
    is ``specialist.current_model`` (the second level of the chain).
    """
    import os

    from sweave.runtime.serve_runner import ServeRunner as _SR

    assert os.environ.get("SWEAVE_MOCK_OPENCODE") == "1"

    worktree = tmp_path / "wt"
    worktree.mkdir(parents=True, exist_ok=True)

    async def _fake_start(self) -> None:
        self.port = 0
        self.base_url = "http://mock-opencode"
        self.process = None
        self.log_path = None
        self.touch()

    orig_start = _SR.start
    _SR.start = _fake_start  # type: ignore[assignment]
    try:
        class _T(_httpx.MockTransport):
            def __init__(self) -> None:
                def handler(request: _httpx.Request) -> _httpx.Response:
                    if request.url.path == "/session" and request.method == "POST":
                        return _httpx.Response(200, content=_json.dumps({"id": "ses_spec"}))
                    return _httpx.Response(200, content=_json.dumps({
                        "info": {"role": "assistant", "time": {"created": 0, "completed": 1}, "finish": "stop"},
                        "parts": [{"type": "text", "text": "ok"}],
                    }))
                super().__init__(handler)

        client = _httpx.AsyncClient(transport=_T(), base_url="http://test")

        runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
        # Set current_model to a structured-pair string so model_ref
        # resolves to a real {provider, model_id} dict.
        spec = _Specialist(
            name="alpha", system_prompt="", current_model="ollama/qwen3:8b"
        )
        trace = TraceLog("d-trace-spec", base_dir=tmp_path)
        delegation = Delegation(agent="alpha", task="x", model="")

        async def fake_build(self_runner, d, *_args, **_kwargs):
            return _Step1MockProcess(client=client, session_id="ses_spec")

        import sweave.runtime.specialist_runtime as sr_mod

        orig_build = sr_mod.SpecialistRuntime._build_process
        sr_mod.SpecialistRuntime._build_process = fake_build  # type: ignore[assignment]
        try:
            await runtime.run(
                specialist=spec,
                delegation=delegation,
                worktree_path=worktree,
                message="x",
                trace=trace,
                # No model_ref: fall through to specialist.current_model.
            )
        finally:
            sr_mod.SpecialistRuntime._build_process = orig_build  # type: ignore[assignment]
            await client.aclose()

        events = [
            _json.loads(line)
            for line in (trace.path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        used_events = [e for e in events if e.get("event") == "model_used"]
        assert len(used_events) == 1
        used = used_events[0]
        assert used["model_ref"] == {"provider": "ollama", "model_id": "qwen3:8b"}
        assert used["model_wire"] == {
            "providerID": "ollama",
            "modelID": "qwen3:8b",
        }
        assert used["source"] == "specialist.current_model"
    finally:
        _SR.start = orig_start  # type: ignore[assignment]
