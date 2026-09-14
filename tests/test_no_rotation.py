"""No-rotation invariant tests (user ruling: sessions are immortal).

Sessions are user data — only the user ends one (new session). Stops,
stalls and edits kill or rewrite the WORK, never the conversation:

* ``abort_live_turn``: eng_ → sidecar abort; ses_ → serve abort with
  a serve-restart (OS-level) fallback; anything else is a no-op.
  Every outcome keeps the binding (the caller never clears it).
* ``rewrite_history_before``: engine ``/revert before_message``,
  opencode native revert to the predecessor, honest
  ``preamble_fallback`` when no id mapping exists.
* Harness ``send`` surfaces the turn's ``user_message_id`` (protocol
  v3 ``done``) in ``AgentResult.metadata`` for per-turn tracing.
* Protocol v3 accepts ``before_message`` as the ``to_message``
  alternative on ``/revert``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


def _runtime(tmp_path: Path):
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    return SpecialistRuntime(runners=ServeRunnerRegistry())


# ---------------------------------------------------------------------------
# abort_live_turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abort_live_turn_engine_acknowledged(monkeypatch, tmp_path: Path):
    import sweave.harness.engine as eng

    seen: list[str] = []

    class _FakeEngineHarness:
        name = eng.ENGINE_HARNESS_NAME

        async def abort_turn(self, session_id: str) -> bool:
            seen.append(session_id)
            return True

    monkeypatch.setattr(
        eng.harness_registry, "get", lambda name: _FakeEngineHarness()
    )
    rt = _runtime(tmp_path)
    out = await rt.abort_live_turn(
        specialist_name="orchestrator",
        worktree_path=tmp_path,
        engine_session_id="eng_live_1",
    )
    assert out == "acknowledged"
    assert seen == ["eng_live_1"]


@pytest.mark.asyncio
async def test_abort_live_turn_unknown_id_is_noop(tmp_path: Path):
    rt = _runtime(tmp_path)
    assert await rt.abort_live_turn(
        specialist_name="orchestrator",
        worktree_path=tmp_path,
        engine_session_id=None,
    ) == "no_live_turn"
    assert await rt.abort_live_turn(
        specialist_name="orchestrator",
        worktree_path=tmp_path,
        engine_session_id="junk",
    ) == "no_live_turn"


@pytest.mark.asyncio
async def test_abort_live_turn_opencode_restart_fallback(
    monkeypatch, tmp_path: Path
):
    """Serve abort fails → the serve is restarted (OS-level kill);
    the session persists in sqlite and the same id resumes."""
    import httpx

    import sweave.runtime.specialist_runtime as rt_mod

    restarted: list[str] = []

    async def _boom_post(self, url: str, **kwargs: Any):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(httpx.AsyncClient, "post", _boom_post)

    rt = _runtime(tmp_path)
    runner = SimpleNamespace(
        base_url="http://127.0.0.1:9",
        restart=None,
    )

    async def _restart() -> None:
        restarted.append("yes")

    runner.restart = _restart  # type: ignore[attr-defined]
    key = ("orchestrator", str(tmp_path.resolve()))
    rt.runners._runners[key] = runner  # type: ignore[assignment]

    out = await rt.abort_live_turn(
        specialist_name="orchestrator",
        worktree_path=tmp_path,
        engine_session_id="ses_orphan_1",
    )
    assert out == "serve_restarted"
    assert restarted == ["yes"]
    assert key in rt.runners._runners  # binding untouched by us


@pytest.mark.asyncio
async def test_abort_live_turn_opencode_acknowledged(
    monkeypatch, tmp_path: Path
):
    import httpx

    class _Resp:
        status_code = 200

    async def _ok_post(self, url: str, **kwargs: Any):
        assert url.endswith("/session/ses_busy_1/abort")
        return _Resp()

    monkeypatch.setattr(httpx.AsyncClient, "post", _ok_post)

    rt = _runtime(tmp_path)
    rt.runners._runners[("orchestrator", str(tmp_path.resolve()))] = (  # type: ignore[assignment]
        SimpleNamespace(base_url="http://127.0.0.1:9")
    )
    out = await rt.abort_live_turn(
        specialist_name="orchestrator",
        worktree_path=tmp_path,
        engine_session_id="ses_busy_1",
    )
    assert out == "acknowledged"


# ---------------------------------------------------------------------------
# rewrite_history_before
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_engine_revert(monkeypatch, tmp_path: Path):
    import sweave.harness.engine as eng

    posted: list[dict] = []

    async def _fake_ensure_sidecar():
        return SimpleNamespace(base_url="http://127.0.0.1:9")

    class _Resp:
        status_code = 200
        text = '{"ok": true}'

    class _FakeClient:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a: Any) -> bool:
            return False

        async def post(self, url: str, json: Any = None):
            posted.append({"url": url, "json": json})
            return _Resp()

    monkeypatch.setattr(eng, "_ensure_sidecar", _fake_ensure_sidecar)
    monkeypatch.setattr(eng.httpx, "AsyncClient", _FakeClient)

    rt = _runtime(tmp_path)
    out = await rt.rewrite_history_before(
        specialist_name="orchestrator",
        worktree_path=tmp_path,
        session_id="eng_hist_1",
        before_ids=["msg_oldest", "msg_newer"],
    )
    assert out == "reverted"
    assert posted and posted[0]["url"] == "/revert"
    # Earliest first: the rewrite drops the oldest superseded prompt
    # and everything after it.
    assert posted[0]["json"]["before_message"] == "msg_oldest"


@pytest.mark.asyncio
async def test_rewrite_no_mapping_falls_back_loud(tmp_path: Path):
    rt = _runtime(tmp_path)
    out = await rt.rewrite_history_before(
        specialist_name="orchestrator",
        worktree_path=tmp_path,
        session_id="eng_hist_1",
        before_ids=[],
    )
    assert out == "preamble_fallback:no_mapping"


@pytest.mark.asyncio
async def test_rewrite_opencode_predecessor(monkeypatch, tmp_path: Path):
    """Opencode native revert targets the predecessor of the earliest
    superseded prompt (revert keeps the named message)."""
    import httpx

    calls: list[tuple[str, Any]] = []

    class _Resp:
        def __init__(self, status_code: int, payload: Any) -> None:
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    listing = {
        "messages": [
            {"info": {"id": "m1", "role": "user"}},
            {"info": {"id": "m2", "role": "assistant"}},
            {"info": {"id": "m3", "role": "user"}},
            {"info": {"id": "m4", "role": "assistant"}},
        ]
    }

    async def _fake_get(self, url: str, **kwargs: Any):
        return _Resp(200, listing)

    async def _fake_post(self, url: str, **kwargs: Any):
        calls.append((url, kwargs.get("json")))
        return _Resp(200, {})

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    rt = _runtime(tmp_path)
    rt.runners._runners[("orchestrator", str(tmp_path.resolve()))] = (  # type: ignore[assignment]
        SimpleNamespace(base_url="http://127.0.0.1:9")
    )
    out = await rt.rewrite_history_before(
        specialist_name="orchestrator",
        worktree_path=tmp_path,
        session_id="ses_hist_1",
        before_ids=["m3"],
    )
    assert out == "reverted"
    assert calls and calls[0][0].endswith("/session/ses_hist_1/revert")
    # Predecessor of m3 is m2 — kept, everything after dropped.
    assert calls[0][1] == {"messageID": "m2"}


@pytest.mark.asyncio
async def test_rewrite_opencode_first_prompt_preamble(
    monkeypatch, tmp_path: Path
):
    """Editing the very first prompt has no predecessor to revert to:
    honest preamble fallback, binding kept."""
    import httpx

    class _Resp:
        status_code = 200

        def json(self):
            return {"messages": [{"info": {"id": "m1", "role": "user"}}]}

    async def _fake_get(self, url: str, **kwargs: Any):
        return _Resp()

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    rt = _runtime(tmp_path)
    rt.runners._runners[("orchestrator", str(tmp_path.resolve()))] = (  # type: ignore[assignment]
        SimpleNamespace(base_url="http://127.0.0.1:9")
    )
    out = await rt.rewrite_history_before(
        specialist_name="orchestrator",
        worktree_path=tmp_path,
        session_id="ses_hist_1",
        before_ids=["m1"],
    )
    assert out == "preamble_fallback:first_prompt"


# ---------------------------------------------------------------------------
# Harness: done user_message_id -> metadata (protocol v3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_send_captures_user_message_id(tmp_path: Path):
    import json as _json

    from sweave.engine.protocol import PROTOCOL_VERSION
    from sweave.harness.base import AgentSpec, Message
    from sweave.harness.engine import SweaveEngineProcess

    lines = [
        'data: {"event": "token", "text": "hi"}',
        'data: {"event": "done", "output": "hi", "user_message_id": "msg_prompt_9"}',
        'data: {"event": "tokens_used", "input": 1, "output": 1}',
    ]

    class _FakeResp:
        status_code = 200
        headers = {"X-Sweave-Engine-Protocol": PROTOCOL_VERSION}

        def raise_for_status(self) -> None:
            pass

        async def aread(self) -> bytes:
            return b""

        async def aiter_lines(self):
            for line in lines:
                yield line

    class _FakeStreamCM:
        async def __aenter__(self):
            return _FakeResp()

        async def __aexit__(self, *a: Any) -> bool:
            return False

    class _FakeClient:
        def stream(self, *a: Any, **k: Any):
            return _FakeStreamCM()

    spec = AgentSpec(
        name="orchestrator",
        role="orchestrator",
        model="",
        system_prompt="",
        worktree_path=tmp_path,
        memory_bank="",
        tools=[],
        env={},
        harness="sweave-engine",
    )
    proc = SweaveEngineProcess(
        spec=spec, base_url="http://127.0.0.1:9", session_id="eng_meta_1"
    )
    proc._client = _FakeClient()  # type: ignore[assignment]
    result = await proc.send(Message(type="user", content="hi"))
    assert result.success is True
    assert result.output == "hi"
    assert result.metadata.get("user_message_id") == "msg_prompt_9"


# ---------------------------------------------------------------------------
# Protocol v3: /revert before_message
# ---------------------------------------------------------------------------


def test_revert_before_message_accepted():
    from sweave.engine.protocol import validate_revert_request

    body = {"session_id": "eng_1", "before_message": "msg_7"}
    assert validate_revert_request(body) is body


def test_revert_needs_one_target():
    from sweave.engine.protocol import validate_revert_request

    with pytest.raises(ValueError):
        validate_revert_request({"session_id": "eng_1"})
