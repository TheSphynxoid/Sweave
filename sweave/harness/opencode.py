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
        """
        try:
            session_id = await self._ensure_session()

            # v2 body shape: parts[].text, optional agent, optional model.
            body: dict[str, Any] = {
                "parts": [{"type": "text", "text": message.content}],
            }
            if self.spec.model:
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
    
    async def spawn(self, spec: AgentSpec) -> AgentProcess:
        """Spawn a new OpenCode server for the agent."""
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