from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

import httpx

from .base import (
    Harness,
    AgentSpec,
    AgentProcess,
    Message,
    AgentResult,
    ModelRef,
    model_ref_to_wire,
    harness_registry,
)


def _parse_provider_model(model: str) -> tuple[str | None, str | None]:
    """Split a ``provider/model`` string into ``(providerID, modelID)``.

    Returns ``(None, None)`` for an empty / unqualified string.
    """
    if not model or "/" not in model:
        return None, None
    provider, _, model_id = model.partition("/")
    provider = provider.strip() or None
    model_id = model_id.strip() or None
    return provider, model_id


def _split_json_stream(chunk: str) -> list[str]:
    """Split a chunk from a v2 message stream into individual JSON objects.

    The v2 endpoint emits one JSON object per write; with httpx's text
    streaming they often arrive in a single buffer. We split on the
    ``}`` boundary that closes the outermost object, which is sufficient
    for the flat part-list shape the v2 endpoint uses. A more robust
    parser (incremental JSON, e.g. ijson) can replace this when we
    encounter nested events.
    """
    pieces: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(chunk):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                pieces.append(chunk[start : i + 1])
                start = -1
    return pieces


class OpenCodeProcess:
    """Handle to a running OpenCode server process.

    OpenCode serve exposes a v2 HTTP API (no ``/api/`` prefix) that
    returns JSON for session + message endpoints. We use that, not the
    legacy v1 ``/api/session`` path, which now serves the web UI HTML
    for ``POST /api/session/{id}/message`` (this was the R0 bug fixed
    in M1.0). The serve picks the project via the ``x-opencode-directory``
    HTTP header; the model can be set per-message via
    ``{"model": {"providerID": "...", "modelID": "..."}, ...}``.
    """

    def __init__(
        self,
        spec: AgentSpec,
        process: asyncio.subprocess.Process,
        base_url: str,
        session_id: str,
    ):
        self.spec = spec
        self.process = process
        self.base_url = base_url
        self._session_id = session_id
        self.pid = process.pid
        # 300s default per-request timeout; streaming responses for long
        # LLM calls can take a while. Override via spec.env if needed.
        self._client = httpx.AsyncClient(base_url=base_url, timeout=300.0)
        self._session_created = False

    @property
    def session_id(self) -> str:
        return self._session_id

    @session_id.setter
    def session_id(self, value: str) -> None:
        self._session_id = value

    def _default_headers(self) -> dict[str, str]:
        """Headers applied to every request to the serve.

        ``x-opencode-directory`` tells the serve which project to use for
        this request. One serve can host many projects; each request
        declares which one.
        """
        wd = str(self.spec.worktree_path) if self.spec.worktree_path else ""
        return {"x-opencode-directory": wd} if wd else {}

    async def _ensure_session(self) -> str:
        """Ensure a session exists, create if needed (v2 ``POST /session``)."""
        if self._session_created:
            return self._session_id

        response = await self._client.post(
            "/session", json={}, headers=self._default_headers()
        )
        response.raise_for_status()
        data = response.json()
        self._session_id = data.get("id", str(uuid.uuid4()))
        self._session_created = True
        return self._session_id

    async def send(self, message: Message) -> AgentResult:
        """Send a message via v2 ``POST /session/{id}/message``.

        The response is a chunked JSON stream of the assistant message +
        parts. We concatenate any text parts and return them as the
        output. Streaming errors (e.g. ``AI_APICallError: Cannot connect
        to API``) are surfaced verbatim so the M1.prep trace captures
        the real failure.

        **Model-per-request (M1.4+M1.5 step 1).** When ``message.model``
        is set, it wins over ``self.spec.model`` for this one
        invocation -- the runtime resolves the model at submit time and
        the per-message override is the contract for any caller that
        needs a different model on a single turn (e.g. mid-session
        model switch). The override is a :class:`ModelRef` so
        structured ``{providerID, modelID}`` survives.
        """
        try:
            session_id = await self._ensure_session()

            # v2 body shape: parts[].text, optional agent, optional model.
            body: dict[str, Any] = {
                "parts": [{"type": "text", "text": message.content}],
            }
            # Per-message model override beats spec.model (M1.4+M1.5
            # step 1). Both paths funnel through the same wire builder
            # so the structured-vs-bare fallback stays in one place.
            if message.model is not None:
                wire_model = model_ref_to_wire(message.model)
                if wire_model is not None:
                    body["model"] = wire_model
                elif message.model.get("model_id"):
                    # Structured-but-incomplete ref (provider missing)
                    # falls back to the bare-name path with a warning
                    # emitted at the runtime, not here.
                    body["model"] = message.model["model_id"]
            elif self.spec.model:
                provider_id, model_id = _parse_provider_model(self.spec.model)
                if provider_id and model_id:
                    body["model"] = {"providerID": provider_id, "modelID": model_id}
                elif model_id:
                    # Unqualified; serve resolves from its own default.
                    body["model"] = model_id

            text_parts: list[str] = []
            saw_terminal = False
            last_error: str | None = None
            try:
                async with self._client.stream(
                    "POST",
                    f"/session/{session_id}/message",
                    json=body,
                    headers=self._default_headers(),
                ) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_text():
                        if not chunk:
                            continue
                        for piece in _split_json_stream(chunk):
                            try:
                                obj = json.loads(piece)
                            except json.JSONDecodeError:
                                # Partial chunk; next read will complete it.
                                continue
                            if not isinstance(obj, dict):
                                continue
                            for part in obj.get("parts", []) or []:
                                if not isinstance(part, dict):
                                    continue
                                if part.get("type") == "text":
                                    text_parts.append(part.get("text", ""))
                                elif part.get("type") == "error":
                                    last_error = (
                                        part.get("text")
                                        or part.get("error")
                                        or str(part)
                                    )
                            if (
                                obj.get("info", {}).get("role") == "assistant"
                                and obj.get("parts")
                            ):
                                saw_terminal = True
            except httpx.HTTPStatusError as e:
                upstream = e.response.text.strip() if e.response is not None else ""
                return AgentResult(
                    success=False,
                    output="",
                    error=(
                        f"opencode serve "
                        f"{e.response.status_code if e.response else '?'}: "
                        f"{upstream or str(e)}"
                    ),
                )

            output = "".join(text_parts).strip()
            if last_error and not output:
                return AgentResult(success=False, output="", error=last_error)
            if not output and not saw_terminal:
                return AgentResult(
                    success=False,
                    output="",
                    error=(
                        "opencode serve: empty response "
                        "(provider unreachable or no model configured?)"
                    ),
                )
            return AgentResult(success=True, output=output, metadata={})
        except Exception as e:
            return AgentResult(success=False, output="", error=str(e))
    
    async def terminate(self) -> None:
        """Terminate the OpenCode process."""
        try:
            await self._client.aclose()
        except Exception:
            pass

        process = self.process
        try:
            if process.returncode is None:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            process.kill()
            try:
                await process.wait()
            except Exception:
                pass
        finally:
            log_file = getattr(self, "_log_file", None)
            if log_file:
                try:
                    log_file.close()
                except Exception:
                    pass
                self._log_file = None

    async def wait(self) -> AgentResult:
        """Wait for process to complete."""
        returncode = await self.process.wait()
        return AgentResult(
            success=returncode == 0,
            output="",
            metadata={"returncode": returncode},
        )


class OpenCodeHarness(Harness):
    """OpenCode harness implementation using `opencode serve`.

The serve exposes two parallel HTTP surfaces:

* **v2 (stable, what we use)**: no ``/api/`` prefix. ``POST /session``
  creates a session; ``POST /session/{id}/message`` sends a prompt and
  streams a JSON response containing the assistant message + parts.
* **v1 (legacy, do not use)**: ``/api/session`` etc. ``POST /api/session/{id}/message``
  now serves the web UI HTML (this was the R0 bug fixed in M1.0).

The serve picks the project via the ``x-opencode-directory`` HTTP
header; the model can be set per-message via
``{"model": {"providerID": "ollama", "modelID": "qwen3:8b"}, ...}``.

Streaming response handling: the v2 message endpoint returns a chunked
JSON stream. ``OpenCodeProcess.send`` consumes it incrementally,
concatenates any text parts, and returns the joined text as
``AgentResult.output``. Upstream errors (e.g. ``AI_APICallError:
Cannot connect to API``) are surfaced verbatim so the M1.prep trace
captures the real failure.
"""
    
    name = "opencode"
    
    def __init__(self, command: str = "opencode", serve_args: list[str] | None = None):
        self.command = command
        self.serve_args = serve_args or ["--port", "0"]
        self._processes: dict[str, OpenCodeProcess] = {}
    
    def get_default_tools(self) -> list[str]:
        return [
            "bash", "read", "write", "edit", "glob", "grep",
            "task", "webfetch", "websearch", "todo", "skill",
            "patch", "lsp", "plan",
        ]
    
    async def health_check(self) -> bool:
        """Check if OpenCode is available."""
        try:
            proc = await asyncio.create_subprocess_exec(
                self._resolve_command(), "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            return proc.returncode == 0
        except Exception:
            return False
    
    def _spawn_mock(self, spec: AgentSpec) -> AgentProcess:
        """Build a stub :class:`OpenCodeProcess` for tests.

        The stub uses a canned JSON session id and a canned message
        body that echoes ``spec.name`` (so each specialist's mock
        response is distinct). The v2 wire format is the same as the
        real endpoint. The session id is the specialist name with a
        fixed prefix so the M1.3 session-reuse path is exercised:
        a second delegation to the same specialist hits GET
        /session/{id} -> 200 (reuse), not POST /session (create).
        """
        import asyncio
        import json as _json
        import uuid as _uuid

        # Lazy import of httpx to keep the production import graph
        # minimal (the mock path is test-only).
        try:
            import httpx as _httpx
        except ImportError:  # pragma: no cover
            raise RuntimeError(
                "SWEAVE_MOCK_OPENCODE=1 requires httpx (it's a runtime dep anyway)"
            )

        # Stable per-specialist session id so the GET-reuse path
        # actually fires on a 2nd delegation.
        session_id = f"ses-mock-{spec.name}"
        captured_session_id: list[str] = [session_id]

        class _StubResponse:
            def __init__(self, status_code: int, body: str) -> None:
                self.status_code = status_code
                self._body = body.encode("utf-8")
                self.headers = {"content-type": "application/json"}

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise _httpx.HTTPStatusError(
                        "mock error", request=_httpx.Request("POST", "http://mock"),
                        response=self,
                    )

            def json(self) -> Any:
                import json as _json
                return _json.loads(self._body)

            @property
            def text(self) -> str:
                return self._body.decode("utf-8")

        class _StubStreamResponse:
            def __init__(self, chunks: list[str]) -> None:
                self.status_code = 200
                self.headers = {"content-type": "application/json"}
                self._chunks = [c.encode("utf-8") for c in chunks]

            def raise_for_status(self) -> None:
                pass

            async def __aenter__(self) -> "_StubStreamResponse":
                return self

            async def __aexit__(self, *exc: Any) -> None:
                return None

            async def aiter_text(self) -> Any:
                for c in self._chunks:
                    yield c.decode("utf-8")

            async def aiter_bytes(self) -> Any:
                for c in self._chunks:
                    yield c

        class _StubClient:
            def __init__(self) -> None:
                self.session_id = session_id
                self.sent_session_ids: list[str] = []

            async def post(self, url: str, json: Any = None, headers: Any = None, **kw: Any) -> _StubResponse:
                if url == "/session":
                    return _StubResponse(200, _json.dumps({"id": self.session_id}))
                raise AssertionError(f"unexpected POST {url}")

            async def get(self, url: str, headers: Any = None, **kw: Any) -> _StubResponse:
                if url.startswith("/session/"):
                    return _StubResponse(200, _json.dumps({"id": self.session_id}))
                raise AssertionError(f"unexpected GET {url}")

            def stream(
                self, method: str, url: str, json: Any = None, headers: Any = None, **kw: Any
            ) -> "_StubStreamResponse":
                """Regular (non-async) method returning an async context
                manager -- the same shape as the real httpx
                ``AsyncClient.stream``. An ``async def`` here would
                return a coroutine, and ``async with`` on a coroutine
                fails ('coroutine' object does not support the
                asynchronous context manager protocol).
                """
                if method == "POST" and url.startswith("/session/"):
                    # Echo the task's last word back so tests can assert
                    # the request body round-tripped through the mock.
                    prompt = ""
                    try:
                        parts = (json or {}).get("parts") or []
                        if parts and isinstance(parts[0], dict):
                            prompt = str(parts[0].get("text", ""))
                    except Exception:
                        prompt = ""
                    last_word = prompt.split()[-1] if prompt.split() else "ACK"
                    body = {
                        "info": {"role": "assistant"},
                        "parts": [{
                            "type": "text",
                            "text": f"ACK from mock opencode for {spec.name}: {last_word}",
                        }],
                    }
                    return _StubStreamResponse([_json.dumps(body)])
                raise AssertionError(f"unexpected STREAM {method} {url}")

            async def aclose(self) -> None:
                pass

        class _FakeProcess:
            pid = 99001
            returncode = None

        # Build the OpenCodeProcess using the existing class so the
        # runtime's type expectations match.
        proc = OpenCodeProcess(
            spec=spec,
            process=_FakeProcess(),  # type: ignore[arg-type]
            base_url="http://mock-opencode",
            session_id=self.session_id if False else "",  # placeholder; real one below
        )
        # The OpenCodeProcess.__init__ would normally have set
        # _client via httpx.AsyncClient(base_url=...). We replace it
        # with our stub AFTER construction so the runtime's
        # ``process._client.stream(...)`` calls hit the mock.
        proc._client = _StubClient()  # type: ignore[assignment]
        # Pre-populate the session_created flag so _ensure_session
        # takes the reuse path on subsequent calls.
        proc._session_created = True  # type: ignore[attr-defined]
        # And pretend the system prompt has been sent so the second
        # delegation just reuses the session.
        return proc

    async def spawn(self, spec: AgentSpec) -> AgentProcess:
        """Spawn a new OpenCode server for the agent.

        Test hook: if the environment variable ``SWEAVE_MOCK_OPENCODE=1``
        is set, return a stub :class:`OpenCodeProcess` whose ``_client``
        is a canned httpx mock. The stub serves the same wire format
        the real v2 endpoint emits (POST /session, POST /session/{id}/message
        streaming). This makes end-to-end tests deterministic without a
        live opencode subprocess or an LLM provider. Default behaviour
        (env var unset) is unchanged: real subprocess + real LLM.
        """
        if os.environ.get("SWEAVE_MOCK_OPENCODE") == "1":
            return self._spawn_mock(spec)
        # Prepare environment
        # Prepare environment
        env = os.environ.copy()
        env.update(spec.env)
        env["OPENCODE_MODEL"] = spec.model

        # Create worktree directory
        spec.worktree_path.mkdir(parents=True, exist_ok=True)

        # Resolve command: 'opencode' is often a .cmd shim that CreateProcess
        # cannot execute directly - use the shim's .exe target when present.
        cmd = [self._resolve_command(), "serve", *self.serve_args]

        # Launch with stdout/stderr redirected to a log file. Holding serve
        # output in pipes can deadlock/crash the runtime (Bun illegal
        # instruction observed 2026-08-29) - never pipe serve output.
        log_path = Path(tempfile.gettempdir()) / f"sweave-opencode-{uuid.uuid4().hex[:8]}.log"
        log_file = open(log_path, "ab")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=spec.worktree_path,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        except Exception:
            log_file.close()
            raise

        # Wait for server to be ready and get port (parsed from the log file)
        try:
            port = await self._wait_for_server_ready(process, log_path)
        except Exception:
            try:
                process.terminate()
            except Exception:
                pass
            log_file.close()
            raise
        base_url = f"http://127.0.0.1:{port}"

        # Create session
        session_id = str(uuid.uuid4())
        agent_process = OpenCodeProcess(spec, process, base_url, session_id)
        agent_process._log_file = log_file  # closed on terminate
        agent_process._log_path = log_path
        self._processes[session_id] = agent_process

        # Initialize with system prompt
        await agent_process.send(Message(
            type="system",
            content=spec.system_prompt,
        ))

        return agent_process

    async def attach(self, session_id: str, spec: AgentSpec) -> AgentProcess:
        """Attach to an existing OpenCode session.

        Stub: currently respawns. R1 connects to the shared per-project serve
        and resumes the stored session id.
        """
        return await self.spawn(spec)

    def _resolve_command(self) -> str:
        """Resolve the harness command to a directly executable path."""
        resolved = shutil.which(self.command)
        if resolved and resolved.lower().endswith((".cmd", ".bat")):
            exe = self._exe_from_shim(resolved)
            if exe:
                return exe
            # Fallback: run the shim through cmd.exe
            return resolved
        return resolved or self.command

    @staticmethod
    def _exe_from_shim(shim: str) -> str | None:
        """Extract the real executable targeted by an npm .cmd shim."""
        try:
            text = Path(shim).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        match = re.search(r'"([^"]+\.exe)"', text, re.IGNORECASE)
        if not match:
            return None
        target = match.group(1)
        # Expand npm shim variables (%dp0% / %~dp0% = the shim's directory)
        shim_dir = str(Path(shim).resolve().parent)
        target = re.sub(r"%~?dp0%", lambda _m: shim_dir, target, flags=re.IGNORECASE)
        target = Path(os.path.expandvars(target))
        if target.is_file():
            return str(target)
        return None

    async def _wait_for_server_ready(
        self, process: asyncio.subprocess.Process, log_path: Path
    ) -> int:
        """Poll the serve log file until the listening URL appears."""
        url_re = re.compile(r"http://[\d.]+:(\d+)")
        deadline = asyncio.get_event_loop().time() + 30.0
        while asyncio.get_event_loop().time() < deadline:
            if process.returncode is not None:
                raise RuntimeError(
                    f"OpenCode serve exited early (code {process.returncode}); "
                    f"log: {log_path}"
                )
            try:
                text = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            match = url_re.search(text)
            if match:
                return int(match.group(1))
            await asyncio.sleep(0.25)
        raise RuntimeError(f"OpenCode server failed to start; log: {log_path}")


# Register the harness
harness_registry.register(OpenCodeHarness())