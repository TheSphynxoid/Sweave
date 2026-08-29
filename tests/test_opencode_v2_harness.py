"""Tests for the OpenCode harness v2 path fix (M1.0)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from sweave.harness.opencode import (
    OpenCodeProcess,
    _parse_provider_model,
    _split_json_stream,
)
from sweave.harness.base import AgentSpec, Message, AgentResult


# --- _parse_provider_model ------------------------------------------------


def test_parse_provider_model_full():
    assert _parse_provider_model("ollama/qwen3:8b") == ("ollama", "qwen3:8b")


def test_parse_provider_model_no_slash():
    assert _parse_provider_model("qwen3:8b") == (None, None)


def test_parse_provider_model_empty():
    assert _parse_provider_model("") == (None, None)
    assert _parse_provider_model("   ") == (None, None)


def test_parse_provider_model_whitespace():
    assert _parse_provider_model(" ollama / qwen3:8b ") == ("ollama", "qwen3:8b")


# --- _split_json_stream ---------------------------------------------------


def test_split_stream_two_objects():
    chunk = '{"a":1}{"b":2}'
    assert _split_json_stream(chunk) == ['{"a":1}', '{"b":2}']


def test_split_stream_nested_object():
    chunk = '{"info":{"role":"assistant"},"parts":[{"type":"text","text":"hi"}]}'
    out = _split_json_stream(chunk)
    assert out == [chunk]


def test_split_stream_string_with_brace():
    chunk = '{"text":"hello { world }"}'
    out = _split_json_stream(chunk)
    assert out == [chunk]


def test_split_stream_garbage_around():
    chunk = 'garbage{"x":1}more'
    assert _split_json_stream(chunk) == ['{"x":1}']


def test_split_stream_empty():
    assert _split_json_stream("") == []


def test_split_stream_partial_is_dropped():
    # A trailing "{" without a close is dropped (no complete object)
    chunk = '{"a":1}{"b"'
    assert _split_json_stream(chunk) == ['{"a":1}']


# --- OpenCodeProcess: v2 send with mocked httpx ---------------------------


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
    handler,  # callable(httpx.Request) -> httpx.Response
) -> OpenCodeProcess:
    """Create an OpenCodeProcess whose underlying httpx client uses a
    MockTransport so we can intercept the v2 requests without a real serve.
    """
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="http://test", transport=transport, timeout=10.0)
    proc = OpenCodeProcess(
        spec=spec,
        process=MagicMock(),  # never used by send()
        base_url="http://test",
        session_id="ses_init",  # overwritten by _ensure_session
    )
    proc._client = client
    return proc


@pytest.mark.asyncio
async def test_ensure_session_uses_v2_path(tmp_path: Path):
    """POST /session (v2), not POST /api/session (v1 HTML)."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path == "/session" and request.method == "POST":
            return httpx.Response(200, json={"id": "ses_abc"})
        return httpx.Response(404)

    process = _make_process_with_transport(_make_spec(tmp_path), handler)
    sid = await process._ensure_session()
    assert sid == "ses_abc"
    assert process._session_created is True
    assert len(captured) == 1
    assert captured[0].url.path == "/session"
    # x-opencode-directory header must point at the worktree
    assert captured[0].headers.get("x-opencode-directory") == str(tmp_path)


@pytest.mark.asyncio
async def test_send_uses_v2_message_path_and_x_opencode_directory(tmp_path: Path):
    """POST /session/{id}/message with parts body and x-opencode-directory header."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path == "/session" and request.method == "POST":
            return httpx.Response(200, json={"id": "ses_xyz"})
        if (
            request.url.path == "/session/ses_xyz/message"
            and request.method == "POST"
        ):
            # Simulate the v2 streaming response: a chunked JSON object
            # containing the assistant message + a text part.
            body = {
                "info": {"role": "assistant"},
                "parts": [{"type": "text", "text": "ACK"}],
            }
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=json.dumps(body),
            )
        return httpx.Response(404)

    spec = _make_spec(tmp_path, model="ollama/qwen3:8b")
    process = _make_process_with_transport(spec, handler)
    result = await process.send(Message(type="user", content="say ACK"))

    assert result.success is True
    assert result.output == "ACK"
    assert result.error is None
    assert len(captured) == 2
    msg_req = captured[1]
    assert msg_req.url.path == "/session/ses_xyz/message"
    assert msg_req.headers.get("x-opencode-directory") == str(tmp_path)
    body = json.loads(msg_req.content)
    assert body["parts"] == [{"type": "text", "text": "say ACK"}]
    assert body["model"] == {"providerID": "ollama", "modelID": "qwen3:8b"}


@pytest.mark.asyncio
async def test_send_omits_model_when_spec_has_none(tmp_path: Path):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path == "/session" and request.method == "POST":
            return httpx.Response(200, json={"id": "ses_q"})
        if request.url.path == "/session/ses_q/message":
            return httpx.Response(
                200,
                content=json.dumps({
                    "info": {"role": "assistant"},
                    "parts": [{"type": "text", "text": "ok"}],
                }),
            )
        return httpx.Response(404)

    process = _make_process_with_transport(_make_spec(tmp_path, model=""), handler)
    result = await process.send(Message(type="user", content="hi"))
    assert result.success is True
    body = json.loads(captured[1].content)
    assert "model" not in body


@pytest.mark.asyncio
async def test_send_concatenates_multiple_text_parts(tmp_path: Path):
    """A v2 stream may have multiple text parts (e.g. delta updates)."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path == "/session":
            return httpx.Response(200, json={"id": "ses_m"})
        if request.url.path == "/session/ses_m/message":
            # Two objects concatenated in the response body (the v2 stream
            # format when the chunks happen to arrive in one buffer).
            chunk = (
                '{"info":{"role":"assistant"},"parts":[{"type":"text","text":"A"}]}'
                '{"info":{"role":"assistant"},"parts":[{"type":"text","text":"CK"}]}'
            )
            return httpx.Response(200, content=chunk)
        return httpx.Response(404)

    process = _make_process_with_transport(_make_spec(tmp_path), handler)
    result = await process.send(Message(type="user", content="say ACK"))
    assert result.success is True
    assert result.output == "ACK"


@pytest.mark.asyncio
async def test_send_returns_empty_response_error_when_stream_has_no_text(
    tmp_path: Path,
):
    """If the stream has no assistant text and no terminal marker, surface
    a clear error so the trace captures the real failure (e.g. no model)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/session":
            return httpx.Response(200, json={"id": "ses_e"})
        return httpx.Response(200, content="")

    process = _make_process_with_transport(_make_spec(tmp_path), handler)
    result = await process.send(Message(type="user", content="hello"))
    assert result.success is False
    assert "empty response" in (result.error or "")


@pytest.mark.asyncio
async def test_send_surfaces_upstream_error_part(tmp_path: Path):
    """An AI_APICallError in a 'type: error' part should appear in result.error."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/session":
            return httpx.Response(200, json={"id": "ses_x"})
        body = {
            "info": {"role": "assistant"},
            "parts": [
                {
                    "type": "error",
                    "text": "AI_APICallError: Cannot connect to API: Unable to connect.",
                }
            ],
        }
        return httpx.Response(200, content=json.dumps(body))

    process = _make_process_with_transport(_make_spec(tmp_path), handler)
    result = await process.send(Message(type="user", content="x"))
    assert result.success is False
    assert "AI_APICallError" in (result.error or "")
    assert "Cannot connect" in (result.error or "")


@pytest.mark.asyncio
async def test_send_surfaces_http_error(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/session":
            return httpx.Response(200, json={"id": "ses_y"})
        return httpx.Response(500, text="internal error: bad model id")

    process = _make_process_with_transport(_make_spec(tmp_path), handler)
    result = await process.send(Message(type="user", content="x"))
    assert result.success is False
    assert "500" in (result.error or "")
    assert "bad model id" in (result.error or "")


@pytest.mark.asyncio
async def test_send_reuses_session_id_across_calls(tmp_path: Path):
    """Second send should NOT re-create the session."""
    session_calls = 0
    message_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal session_calls, message_calls
        if request.url.path == "/session":
            session_calls += 1
            return httpx.Response(200, json={"id": "ses_one"})
        if request.url.path == "/session/ses_one/message":
            message_calls += 1
            return httpx.Response(
                200,
                content=json.dumps({
                    "info": {"role": "assistant"},
                    "parts": [{"type": "text", "text": "ok"}],
                }),
            )
        return httpx.Response(404)

    process = _make_process_with_transport(_make_spec(tmp_path), handler)
    await process.send(Message(type="user", content="one"))
    await process.send(Message(type="user", content="two"))
    assert session_calls == 1
    assert message_calls == 2
