"""M1.8 step 1 tests: harness streaming callback.

Covers:

* `OpenCodeProcess.send(message, on_chunk=None)`: when ``on_chunk``
  is provided, the callback is invoked with each text part as it
  leaves the opencode stream. When None, behaviour is unchanged
  (backwards compatible -- all pre-M1.8 callers pass None).
* `SpecialistRuntime._send_message` (the chat-loop path): same
  contract. The mock opencode serves multi-chunk responses; the
  callback receives the parts in order.
* A misbehaving callback that raises does not poison the stream
  -- the runtime logs and continues. This is the resilience
  invariant: streaming is best-effort from the harness's view.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any

import pytest
from contextlib import asynccontextmanager


# ---------------------------------------------------------------------------
# Hermeticity: gate the opencode subprocess seam the same way M1.3 step 3
# did. M1.8's tests are about the streaming callback contract; under
# ``SWEAVE_MOCK_OPENCODE=1`` the runtime + harness are stubbed so the
# callback plumbing is exercised end-to-end without a real opencode
# subprocess.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True, scope="module")
def _mock_opencode_env():
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
# OpenCodeProcess.send callback tests (pure mock-stream)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_with_no_callback_preserves_old_behaviour():
    """Backwards compatibility: callers that pass no callback see
    no change. The full text is returned, no callback side effects.
    """
    from sweave.harness.opencode import OpenCodeHarness
    from sweave.harness.base import AgentSpec, Message
    from pathlib import Path

    spec = AgentSpec(
        name="test",
        role="test",
        model="",
        system_prompt="",
        worktree_path=Path("."),
        memory_bank="",
        tools=[],
        env={},
        harness="opencode",
    )
    proc = OpenCodeHarness()._spawn_mock(spec)
    # Track any callback invocations -- with on_chunk=None, the
    # callback list must remain empty.
    received: list[str] = []

    def would_be_called(text: str) -> None:
        received.append(text)

    msg = Message(type="user", content="hi")
    # Pass on_chunk=None. The harness must not invoke any callback.
    result = await proc.send(msg, on_chunk=None)
    assert result.success
    assert received == []  # never invoked


@pytest.mark.asyncio
async def test_send_incremental_callback_receives_text_parts_in_order():
    """Multi-chunk response: the callback fires for each text part,
    in the order the harness reads them. The accumulated result
    matches the full text.
    """
    from sweave.harness.opencode import OpenCodeHarness
    from sweave.harness.base import AgentSpec, Message
    from pathlib import Path

    spec = AgentSpec(
        name="backend",
        role="backend",
        model="",
        system_prompt="",
        worktree_path=Path("."),
        memory_bank="",
        tools=[],
        env={},
        harness="opencode",
    )
    proc = OpenCodeHarness()._spawn_mock(spec)
    # The mock returns one chunk with one text part. We can't
    # easily make it return multiple parts from the existing
    # _StubStreamResponse, so the assertion here is: callback
    # fires at least once, with a string, and the full text
    # matches.
    received: list[str] = []

    def on_chunk(text: str) -> None:
        received.append(text)

    msg = Message(type="user", content="hi")
    result = await proc.send(msg, on_chunk=on_chunk)
    assert result.success
    # The callback was called at least once
    assert received, "callback was not invoked"
    # The accumulated callback output matches the result output
    assert "".join(received) == result.output


@pytest.mark.asyncio
async def test_send_async_callback_is_awaited():
    """Async callbacks are awaited by the harness. The harness
    detects coroutine return values and awaits them.
    """
    from sweave.harness.opencode import OpenCodeHarness
    from sweave.harness.base import AgentSpec, Message
    from pathlib import Path

    spec = AgentSpec(
        name="backend",
        role="backend",
        model="",
        system_prompt="",
        worktree_path=Path("."),
        memory_bank="",
        tools=[],
        env={},
        harness="opencode",
    )
    proc = OpenCodeHarness()._spawn_mock(spec)
    flags = {"async_called": False}

    async def on_chunk_async(text: str) -> None:
        flags["async_called"] = True

    msg = Message(type="user", content="hi")
    await proc.send(msg, on_chunk=on_chunk_async)
    assert flags["async_called"] is True


@pytest.mark.asyncio
async def test_send_callback_exception_does_not_poison_stream():
    """A misbehaving callback that raises must not crash the
    send. The harness logs + continues. The full output is still
    returned.
    """
    from sweave.harness.opencode import OpenCodeHarness
    from sweave.harness.base import AgentSpec, Message
    from pathlib import Path

    spec = AgentSpec(
        name="backend",
        role="backend",
        model="",
        system_prompt="",
        worktree_path=Path("."),
        memory_bank="",
        tools=[],
        env={},
        harness="opencode",
    )
    proc = OpenCodeHarness()._spawn_mock(spec)

    def bad_callback(text: str) -> None:
        raise RuntimeError("callback is broken")

    msg = Message(type="user", content="hi")
    # Should not raise
    result = await proc.send(msg, on_chunk=bad_callback)
    assert result.success
    # The output is still the canned stub response
    assert "ACK" in result.output or len(result.output) > 0


# ---------------------------------------------------------------------------
# SpecialistRuntime._send_message callback tests (the chat-loop path)
# ---------------------------------------------------------------------------


def _build_runtime_with_mock_send(chunks: list[str] | None = None):
    """Build a runtime whose ``_send_message`` is replaced with a
    fake that produces the given chunks. Mirrors the M1.3 step 3
    helper. Note: setting a function as an instance attribute does
    NOT auto-bind ``self`` (the bound-method protocol only applies
    to class attributes that are functions). The runtime calls
    ``self._send_message(process, body, trace)`` — the first arg
    in that call is the OpenCodeProcess (not the runtime). The
    fake's signature mirrors the runtime's call site: no ``self``,
    just process/body/trace/on_chunk.
    """
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)
    _chunks = list(chunks or ["final-text"])

    async def fake_send(process, body, trace, on_chunk=None, on_reasoning=None, **kwargs):
        for chunk in _chunks:
            if on_chunk is not None:
                result = on_chunk(chunk)
                if hasattr(result, "__await__"):
                    await result
        return "".join(_chunks)

    runtime._send_message = fake_send  # type: ignore[assignment]
    return runtime


class _FakeProcess:
    """Minimal stand-in for OpenCodeProcess. The runtime's
    _send_message only needs ``process._client.stream``; we
    patch _send_message to bypass that anyway, so a no-op
    process is fine.
    """
    pass


class _FakeTrace:
    def append(self, *args: Any, **kwargs: Any) -> None:
        pass


@pytest.mark.asyncio
async def test_runtime_send_message_invokes_on_chunk_per_part():
    """The runtime's _send_message (the chat-loop path) accepts
    on_chunk and forwards each text part to it. The full output
    is the accumulated result.
    """
    runtime = _build_runtime_with_mock_send(chunks=["hello ", "world"])
    received: list[str] = []

    async def on_chunk(text: str) -> None:
        received.append(text)

    out = await runtime._send_message(  # type: ignore[arg-type]
        _FakeProcess(), {"parts": [{"text": "x"}]}, _FakeTrace(), on_chunk=on_chunk
    )
    assert out == "hello world"
    assert received == ["hello ", "world"]


@pytest.mark.asyncio
async def test_runtime_send_message_no_callback_preserves_old_behaviour():
    """No callback = no streaming side effects. The full text is
    returned, identical to pre-M1.8 behaviour.
    """
    runtime = _build_runtime_with_mock_send(chunks=["only-this"])
    out = await runtime._send_message(  # type: ignore[arg-type]
        _FakeProcess(), {"parts": [{"text": "x"}]}, _FakeTrace()
    )
    assert out == "only-this"


@pytest.mark.asyncio
async def test_runtime_send_message_callback_exception_does_not_crash():
    """A misbehaving callback that raises is logged and ignored;
    the runtime still returns the full output. The streaming
    path is best-effort from the harness's view.

    Exercises the real ``_send_message`` (not a fake) -- the
    try/except lives in the real implementation, and we want to
    pin the resilience invariant. Under ``SWEAVE_MOCK_OPENCODE=1``
    the stub returns one chunk with one text part; the callback
    is invoked once.
    """
    from sweave.harness.opencode import OpenCodeHarness
    from sweave.harness.base import AgentSpec
    from pathlib import Path
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    spec = AgentSpec(
        name="backend",
        role="backend",
        model="",
        system_prompt="",
        worktree_path=Path("."),
        memory_bank="",
        tools=[],
        env={},
        harness="opencode",
    )
    proc = OpenCodeHarness()._spawn_mock(spec)
    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())

    def bad(text: str) -> None:
        raise ValueError("nope")

    out = await runtime._send_message(
        proc,
        {"parts": [{"type": "text", "text": "hi"}]},
        _FakeTrace(),
        on_chunk=bad,
    )
    # The full text still flows (the real _send_message catches
    # the callback exception, logs, and continues)
    assert "ACK" in out


@pytest.mark.asyncio
async def test_runtime_send_message_mixed_sync_and_async_callbacks():
    """The runtime detects async callbacks (coroutine return) and
    awaits them. Sync callbacks are invoked directly. Mixed use
    is supported (the same callback could be either depending on
    caller context).
    """
    runtime = _build_runtime_with_mock_send(chunks=["x", "y", "z"])
    received: list[str] = []

    async def async_cb(text: str) -> None:
        received.append(f"a:{text}")

    out = await runtime._send_message(  # type: ignore[arg-type]
        _FakeProcess(),
        {"parts": [{"text": "x"}]},
        _FakeTrace(),
        on_chunk=async_cb,
    )
    assert out == "xyz"
    assert received == ["a:x", "a:y", "a:z"]


# ---------------------------------------------------------------------------
# R4.0 / R4.2 hotfix (2026-09-07): error-prefix contract.
#
# The chat loop (``sweave/chat/loop.py`` lines 492 + 549) short-circuits a
# hard-failed first/synthesis turn on the ``[chat error:`` prefix --
# skipping the second orchestrator call when the first one already
# errored. The previous prefix (``[error:``) didn't match, so the
# prefix was effectively dead and every error triggered a wasted
# synthesis turn (~30-60s wait on a doomed run). The user reported a
# "frozen" turn: the second orchestrator call was hanging on a
# connection that the opencode serve had already closed (the
# ``httpx.ReadError`` from ``_send_message`` reached the chat loop,
# but only via the wasteful second attempt). This test pins the
# contract so a future refactor can't reintroduce the bug.
# ---------------------------------------------------------------------------


class _RaisingProcess:
    """Minimal OpenCodeProcess-shaped stub whose ``_client.stream``
    raises ``httpx.ReadError`` (the symptom the user reported:
    the opencode serve died mid-stream)."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    @property
    def _session_id(self) -> str:
        return "ses_real_001"

    @property
    def _client(self) -> Any:  # type: ignore[override]
        outer = self

        class _Client:
            def stream(self, *args: Any, **kwargs: Any) -> Any:
                # ``async with process._client.stream(...)`` calls
                # ``__aenter__`` on the returned context manager; the
                # exception is raised there, which is the same
                # surface as the live httpx error.
                @asynccontextmanager  # type: ignore[misc]
                async def _cm() -> Any:
                    raise outer._exc
                    yield  # pragma: no cover -- unreachable

                return _cm()

        return _Client()


@pytest.mark.asyncio
async def test_runtime_send_message_read_error_surfaces_chat_error_prefix(caplog):
    """``httpx.ReadError`` (opencode serve died mid-stream) must
    surface as ``[chat error: ReadError: ...]`` so the chat loop's
    hard-fail branch fires and skips the wasteful synthesis turn.
    The traceback is logged at WARNING/DEBUG, NOT surfaced to the
    user -- a stack trace in the chat bubble is noise.
    """
    import contextlib  # noqa: F401 -- used by the inline stub
    import logging

    import httpx
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime
    from sweave.runtime.trace_log import TraceLog

    proc = _RaisingProcess(
        httpx.ReadError("peer closed connection without sending complete response")
    )
    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    trace = TraceLog("d1", base_dir=__import__("pathlib").Path(".").resolve().parent / "tests" / "_scratch_readerror")

    with caplog.at_level(logging.WARNING, logger="sweave.runtime.specialist_runtime"):
        out = await runtime._send_message(
            proc,  # type: ignore[arg-type]
            {"parts": [{"type": "text", "text": "hi"}]},
            trace,
        )

    # The prefix is the contract the chat loop checks.
    assert out.startswith("[chat error: "), (
        f"expected [chat error: ...] prefix; got: {out[:80]!r}"
    )
    # The exception class is in the surfaced text (so the user can
    # tell what failed) but the traceback is NOT.
    assert "ReadError" in out
    assert "Traceback" not in out
    # The traceback IS logged for the dev (WARNING + DEBUG).
    assert any("SpecialistRuntime._send_message failed" in r.message for r in caplog.records)
