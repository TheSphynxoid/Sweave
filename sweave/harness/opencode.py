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


class OpenCodeProcess:
    """Handle to a running OpenCode server process."""
    
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
        self.session_id = session_id
        self.pid = process.pid
        self._client = httpx.AsyncClient(base_url=base_url, timeout=300.0)
        self._session_created = False
    
    @property
    def session_id(self) -> str:
        return self._session_id
    
    @session_id.setter
    def session_id(self, value: str):
        self._session_id = value
    
    async def _ensure_session(self) -> str:
        """Ensure a session exists, create if needed."""
        if self._session_created:
            return self._session_id
        
        # Create session via OpenCode API
        response = await self._client.post("/api/session", json={})
        response.raise_for_status()
        data = response.json()
        self._session_id = data.get("id", str(uuid.uuid4()))
        self._session_created = True
        return self._session_id
    
    async def send(self, message: Message) -> AgentResult:
        """Send a message to the OpenCode session."""
        try:
            session_id = await self._ensure_session()
            
            # Send message via OpenCode API
            response = await self._client.post(
                f"/api/session/{session_id}/message",
                json={
                    "content": message.content,
                    "role": message.type,
                },
            )
            response.raise_for_status()
            data = response.json()
            
            return AgentResult(
                success=True,
                output=data.get("content", ""),
                metadata=data,
            )
        except Exception as e:
            return AgentResult(
                success=False,
                output="",
                error=str(e),
            )
    
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
    """OpenCode harness implementation using `opencode serve`."""
    
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