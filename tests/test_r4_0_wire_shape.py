"""R4.0 wire-shape regression: chat session-id resolution.

The user-visible bug (2026-09-05): chat turn -> ``HTTPStatusError 500``
posting to ``/session/chat-{delegation_id}/message``. The per-Session
orchestrator binding seam (``Session.orchestrator_session_id``) held
the right id; the runtime's ``SpecialistRuntime._ensure_session``
wrote the id through the setter and *never propagated it into
``process._session_id``*. ``_build_process`` seeded the placeholder
(``chat-{hex}``); ``_send_message`` posted to that placeholder on the
wire -> opencode serve returned 500 (unknown session).

Root cause: two sources of truth (binding + process), only the binding
was updated. Fix: ``_ensure_session`` now writes ``process._session_id``
in all three paths (create / 404-recreate / reuse); ``_send_message``
asserts the resolved id starts with ``ses_`` before posting.

These tests pin the wire-shape contract the M1.7 + M1.8 + M1.9 tests
left as a coverage gap (they asserted ``spec.session_id`` / callback
state but never the actual URL the runtime POSTed to).

R4.0 ruling (locked 2026-09-05): the chat-{hex} prefix is *internal*
(``delegation_id``); it must never appear in any wire URL. The runtime
must post to a serve-issued ``ses_*`` id at all times.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from sweave.config.schemas import AgentSpec
from sweave.harness.opencode import OpenCodeProcess
from sweave.runtime.serve_runner import ServeRunner
from sweave.runtime.specialist_runtime import SpecialistRuntime
from sweave.runtime.specialist_store import Specialist
from sweave.runtime.trace_log import TraceLog, read_trace


# ---------------------------------------------------------------------------
# Recording httpx transport + handler
# ---------------------------------------------------------------------------


class _RecordingTransport(httpx.MockTransport):
    """httpx.MockTransport subclass that records every request URL so
    the wire-shape assertions can pin the exact bytes the runtime put
    on the wire.

    The default handler records then 404s; tests replace
    ``transport.handler`` to drive the routes they care about.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

        def default_handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(
                404, json={"error": "no_route", "path": request.url.path}
            )

        super().__init__(default_handler)


class _SubprocessStandin:
    """Minimal stand-in for the asyncio.subprocess.Process the harness
    holds; the runtime + tests don't talk to it directly."""

    pid = 99001
    returncode = None


def _stream_response(*chunks: str) -> httpx.Response:
    """Build a streaming JSON response (httpx 0.28+ ByteStream)."""
    import httpx as _httpx

    r = _httpx.Response(200, content=b"")
    r.stream = _httpx.ByteStream(b"".join(c.encode("utf-8") for c in chunks))
    return r


def _build_process_with_transport(
    tmp_path: Path,
    transport: _RecordingTransport,
    *,
    session_id: str = "",
) -> OpenCodeProcess:
    """Build an OpenCodeProcess bound to the recording transport.

    The ``session_id`` arg mirrors what ``_build_process`` does in
    production (R4.0 fix): empty string -- the id is resolved by
    ``_ensure_session``, never seeded by the builder. Wire-shape
    tests that simulate the pre-R4.0 bug pass the chat-{hex}
    placeholder here.
    """
    spec = AgentSpec(
        name="orchestrator",
        role="orchestrator",
        model="",
        system_prompt="",
        worktree_path=tmp_path,
        memory_bank="",
        tools=[],
        env={},
        harness="opencode",
    )
    client = httpx.AsyncClient(
        base_url="http://test", transport=transport, timeout=10.0
    )
    proc = OpenCodeProcess(
        spec=spec,
        process=_SubprocessStandin(),  # type: ignore[arg-type]
        base_url="http://test",
        session_id=session_id,
    )
    proc._client = client
    return proc


def _build_runner(tmp_path: Path) -> ServeRunner:
    """A never-started ServeRunner; the tests only need the object as
    a positional arg to ``_ensure_session``. The runtime's R4.0 code
    doesn't touch the runner inside ``_ensure_session``.
    """
    return ServeRunner(
        specialist_name="orchestrator",
        worktree_path=tmp_path,
    )


def _install_handler(
    transport: _RecordingTransport, handler: Any
) -> None:
    """Replace the recording transport's handler with one that
    records + delegates. The transport's ``handler`` attribute is
    what httpx.MockTransport calls.
    """

    def combined(request: httpx.Request) -> httpx.Response:
        transport.requests.append(request)
        return handler(request)

    transport.handler = combined  # type: ignore[assignment]


def _handler_factory(
    *,
    issue: str = "ses_real_001",
    reuse_id: str | None = None,
    accepted_issued: str | None = None,
) -> Any:
    """Build a request handler that emulates the opencode v2 wire.

    - ``POST /session`` -> 200 ``{id: issue}``.
    - ``GET /session/{reuse_id}`` -> 200; any other id -> 404.
    - ``POST /session/{accepted_issued}/message`` -> 200 (terminal);
      any other id -> 404. ``accepted_issued`` defaults to ``issue``.

    Returns a 1-arg callable (httpx.Request -> httpx.Response).
    """
    if accepted_issued is None:
        accepted_issued = issue

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/session":
            return httpx.Response(200, json={"id": issue})
        if request.method == "GET" and path.startswith("/session/"):
            sid = path[len("/session/"):].rsplit("/", 1)[0]
            if reuse_id is not None and sid == reuse_id:
                return httpx.Response(200, json={"id": sid})
            return httpx.Response(
                404, json={"error": "not_found", "session_id": sid}
            )
        if (
            request.method == "POST"
            and path.startswith("/session/")
            and path.endswith("/message")
        ):
            sid = path[len("/session/"):].rsplit("/", 1)[0]
            if sid != accepted_issued:
                return httpx.Response(
                    404, json={"error": "not_found", "session_id": sid}
                )
            body = {
                "info": {
                    "role": "assistant",
                    "time": {"created": 0, "completed": 1},
                    "finish": "stop",
                },
                "parts": [{"type": "text", "text": "ACK"}],
            }
            return _stream_response(json.dumps(body))
        return httpx.Response(404, json={"error": "no_route", "path": path})

    return handler


def _make_runtime() -> SpecialistRuntime:
    from sweave.runtime.serve_runner import ServeRunnerRegistry

    return SpecialistRuntime(runners=ServeRunnerRegistry())


# ---------------------------------------------------------------------------
# Tests: the four invariants from docs/R4_PLAN.md R4.0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_turn_posts_to_resolved_ses_id_not_chat_hex(tmp_path: Path):
    """Invariant 1: the chat-{hex} placeholder MUST NEVER appear in
    any wire URL. The runtime must post the message to the
    serve-issued ``ses_*`` id resolved by ``_ensure_session``.

    The test seeds ``process._session_id`` with the chat-{hex}
    placeholder (what ``_build_process`` used to do pre-R4.0) so
    the regression would surface if ``_ensure_session`` ever
    forgot to overwrite it. R4.0 itself removes the placeholder
    seed in ``_build_process`` -- but this test pins the runtime's
    *behavior* (always propagate the resolved id into the
    process), independent of how the placeholder got there. The
    defensive ``ses_`` assertion in ``_send_message`` is the
    second line of defence.
    """
    transport = _RecordingTransport()
    # Seed the pre-R4.0 placeholder. After R4.0, _build_process no
    # longer does this; the test pins the runtime contract.
    proc = _build_process_with_transport(
        tmp_path, transport, session_id="chat-abc123def456"
    )
    runner = _build_runner(tmp_path)
    runtime = _make_runtime()

    # Simulate the chat-loop callback seam (M1.7 step 1): the
    # binding is external (Session.orchestrator_session_id).
    bound: dict[str, str | None] = {"id": None}

    _install_handler(transport, _handler_factory(issue="ses_real_001"))

    spec = Specialist(name="orchestrator", system_prompt="", session_id="")
    trace = TraceLog("chat-abc123def456", base_dir=tmp_path)
    proc = await runtime._ensure_session(
        runner=runner,
        process=proc,
        specialist=spec,
        trace=trace,
        fresh=True,
        session_id_getter=lambda: bound["id"],
        session_id_setter=lambda v: bound.__setitem__("id", v),
    )

    # The binding was written through the callback (M1.7 contract).
    assert bound["id"] == "ses_real_001"
    # The process's session_id is the resolved id, NOT the chat-{hex}
    # placeholder -- this is the R4.0 invariant.
    assert proc._session_id == "ses_real_001"
    assert proc._session_id.startswith("ses_")
    # The chat-{hex} prefix is nowhere on the wire.
    chat_hex = "chat-abc123def456"
    for req in transport.requests:
        assert chat_hex not in req.url.path, (
            f"chat-{{hex}} leaked onto the wire: {req.url.path}"
        )


@pytest.mark.asyncio
async def test_chat_second_turn_reuses_same_ses_id(tmp_path: Path):
    """Invariant 2: a second turn for the same session reuses the
    resolved id. The runtime must NOT create a new session on every
    turn; ``GET /session/{stored}`` confirms the id is still valid
    and the reuse path fires.
    """
    transport = _RecordingTransport()
    proc = _build_process_with_transport(tmp_path, transport, session_id="")
    runner = _build_runner(tmp_path)
    runtime = _make_runtime()

    bound: dict[str, str | None] = {"id": "ses_reuse_42"}
    _install_handler(
        transport,
        _handler_factory(issue="ses_reuse_42", reuse_id="ses_reuse_42"),
    )

    spec = Specialist(name="orchestrator", system_prompt="", session_id="")
    trace = TraceLog("d2", base_dir=tmp_path)

    proc = await runtime._ensure_session(
        runner=runner,
        process=proc,
        specialist=spec,
        trace=trace,
        fresh=False,
        session_id_getter=lambda: bound["id"],
        session_id_setter=lambda v: bound.__setitem__("id", v),
    )

    # Reuse path: GET /session/{stored} fired (no POST /session).
    methods = [r.method for r in transport.requests]
    assert methods.count("GET") == 1
    assert methods.count("POST") == 0
    # The stored id is the one on the wire (and on the process).
    assert proc._session_id == "ses_reuse_42"
    assert bound["id"] == "ses_reuse_42"
    # The stored id was the one in the GET URL -- no placeholder.
    get_path = transport.requests[0].url.path
    assert get_path == "/session/ses_reuse_42"


@pytest.mark.asyncio
async def test_stale_binding_recreates_and_rebinds(tmp_path: Path):
    """Invariant 3: a stale binding (GET returns 404) recreates the
    session, persists the new id, AND propagates the new id into
    the process so the subsequent message POST uses the freshly
    issued id (not the stale one).
    """
    transport = _RecordingTransport()
    proc = _build_process_with_transport(tmp_path, transport, session_id="")
    runner = _build_runner(tmp_path)
    runtime = _make_runtime()

    bound: dict[str, str | None] = {"id": "ses_stale_99"}
    # GET /session/ses_stale_99 -> 404 (reuse_id=None means no id matches).
    _install_handler(
        transport, _handler_factory(issue="ses_fresh_99", reuse_id=None)
    )

    spec = Specialist(name="orchestrator", system_prompt="", session_id="")
    trace = TraceLog("d3", base_dir=tmp_path)

    proc = await runtime._ensure_session(
        runner=runner,
        process=proc,
        specialist=spec,
        trace=trace,
        fresh=False,
        session_id_getter=lambda: bound["id"],
        session_id_setter=lambda v: bound.__setitem__("id", v),
    )

    # The runtime took the recreate-on-404 path.
    methods_paths = [(r.method, r.url.path) for r in transport.requests]
    assert ("GET", "/session/ses_stale_99") in methods_paths
    assert ("POST", "/session") in methods_paths
    # The new id is on the binding AND on the process.
    assert bound["id"] == "ses_fresh_99"
    assert proc._session_id == "ses_fresh_99"
    # The trace records the recreate event.
    events = [e["event"] for e in read_trace(trace.delegation_id, base_dir=tmp_path)]
    assert "session_recreated_after_404" in events
    assert "session_recreated" in events


@pytest.mark.asyncio
async def test_chat_hex_never_appears_in_any_wire_url(tmp_path: Path):
    """Invariant 4 (the negative-space guarantee): across the full
    runtime flow (create + reuse + 404-recreate), the ``chat-{hex}``
    placeholder (the value ``_build_process`` used to seed
    ``process._session_id`` pre-R4.0) NEVER appears in any wire
    URL. The runtime's wire IDs are exclusively serve-issued
    ``ses_*`` ids.
    """
    transport = _RecordingTransport()
    proc = _build_process_with_transport(tmp_path, transport, session_id="")
    runner = _build_runner(tmp_path)
    runtime = _make_runtime()

    # 1. Create path: fresh=True, no stored id -> POST /session -> ses_b
    bound: dict[str, str | None] = {"id": None}
    _install_handler(transport, _handler_factory(issue="ses_b", reuse_id="ses_b"))
    spec = Specialist(name="orchestrator", system_prompt="", session_id="")
    trace = TraceLog("d4a", base_dir=tmp_path)
    proc = await runtime._ensure_session(
        runner=runner,
        process=proc,
        specialist=spec,
        trace=trace,
        fresh=True,
        session_id_getter=lambda: bound["id"],
        session_id_setter=lambda v: bound.__setitem__("id", v),
    )
    assert proc._session_id == "ses_b"

    # 2. Reuse path: stored id valid -> GET /session/ses_b
    trace = TraceLog("d4b", base_dir=tmp_path)
    proc = await runtime._ensure_session(
        runner=runner,
        process=proc,
        specialist=spec,
        trace=trace,
        fresh=False,
        session_id_getter=lambda: bound["id"],
        session_id_setter=lambda v: bound.__setitem__("id", v),
    )
    assert proc._session_id == "ses_b"

    # 3. Recreate path: stored id stale -> 404 -> POST /session -> ses_b
    bound["id"] = "ses_stale_z"
    _install_handler(transport, _handler_factory(issue="ses_b", reuse_id=None))
    trace = TraceLog("d4c", base_dir=tmp_path)
    proc = await runtime._ensure_session(
        runner=runner,
        process=proc,
        specialist=spec,
        trace=trace,
        fresh=False,
        session_id_getter=lambda: bound["id"],
        session_id_setter=lambda v: bound.__setitem__("id", v),
    )
    assert proc._session_id == "ses_b"

    # The chat-{hex} placeholder must NEVER appear on the wire.
    # The delegation ids in this test all start with "d4" so we
    # use a concrete "chat-" prefix to assert against (the
    # production pattern is ``chat-{uuid4().hex[:12]}``).
    wire_paths = [r.url.path for r in transport.requests]
    chat_paths = [p for p in wire_paths if p.startswith("/session/chat-")]
    assert chat_paths == [], (
        f"chat-{{hex}} leaked onto the wire: {chat_paths}"
    )
    # Every wire id is a serve-issued ses_*.
    for path in wire_paths:
        if path == "/session":
            continue
        if path.startswith("/session/"):
            sid = path[len("/session/"):].rsplit("/", 1)[0]
            assert sid.startswith("ses_"), (
                f"non-ses_ id on the wire: {sid} (path={path})"
            )


@pytest.mark.asyncio
async def test_send_message_refuses_non_ses_process_session_id(tmp_path: Path):
    """The defensive assertion in ``_send_message`` must reject any
    process whose ``_session_id`` does not start with ``ses_``.
    Pre-R4.0 this guard did not exist; the placeholder reached the
    wire and the real serve returned a generic 500.
    """
    transport = _RecordingTransport()
    # Seed the process with the chat-{hex} placeholder (the pre-R4.0
    # bug). The runtime's defensive assertion must catch it before
    # the wire is touched.
    proc = _build_process_with_transport(
        tmp_path, transport, session_id="chat-deadbeef1234"
    )
    runtime = _make_runtime()
    spec = Specialist(name="orchestrator", system_prompt="", session_id="")
    trace = TraceLog("d5", base_dir=tmp_path)

    with pytest.raises(RuntimeError, match="refusing to send"):
        await runtime._send_message(
            process=proc,
            body={"parts": [{"type": "text", "text": "hi"}]},
            trace=trace,
        )

    # The wire was NEVER touched -- no requests went out.
    assert transport.requests == [], (
        f"_send_message reached the wire with a non-ses_ id: "
        f"{[r.url.path for r in transport.requests]}"
    )