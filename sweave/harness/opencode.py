from __future__ import annotations

import asyncio
import json
import os
import subprocess
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
        process: subprocess.Popen,
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
        
        if self.process.poll() is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self.process.wait),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                self.process.kill()
                await asyncio.to_thread(self.process.wait)
    
    async def wait(self) -> AgentResult:
        """Wait for process to complete."""
        await asyncio.to_thread(self.process.wait)
        return AgentResult(
            success=self.process.returncode == 0,
            output="",
            metadata={"returncode": self.process.returncode},
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
                self.command, "--version",
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
        
        # Start OpenCode server
        cmd = [self.command, "serve", *self.serve_args]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=spec.worktree_path,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        # Wait for server to be ready and get port
        port = await self._wait_for_server_ready(process)
        base_url = f"http://127.0.0.1:{port}"
        
        # Create session
        session_id = str(uuid.uuid4())
        agent_process = OpenCodeProcess(spec, process, base_url, session_id)
        self._processes[session_id] = agent_process
        
        # Initialize with system prompt
        await agent_process.send(Message(
            type="system",
            content=spec.system_prompt,
        ))
        
        return agent_process
    
    async def attach(self, session_id: str, spec: AgentSpec) -> AgentProcess:
        """Attach to an existing OpenCode session."""
        # For now, spawn new - in future, connect to existing server
        return await self.spawn(spec)
    
    async def _wait_for_server_ready(self, process: subprocess.Popen) -> int:
        """Wait for OpenCode server to be ready and return port."""
        # OpenCode outputs port to stdout when using --port 0
        # Read stdout until we see the port
        for _ in range(50):  # 5 second timeout
            if process.stdout is None:
                await asyncio.sleep(0.1)
                continue
            
            line = await asyncio.to_thread(process.stdout.readline)
            if not line:
                await asyncio.sleep(0.1)
                continue
            
            line = line.decode().strip()
            # OpenCode outputs something like "Server running on http://127.0.0.1:XXXXX"
            if "http://" in line and ":" in line:
                try:
                    port = int(line.split(":")[-1].split("/")[0])
                    return port
                except (ValueError, IndexError):
                    pass
            
            await asyncio.sleep(0.1)
        
        # Fallback: try common ports
        for port in range(4096, 4200):
            try:
                async with httpx.AsyncClient(timeout=1.0) as client:
                    await client.get(f"http://127.0.0.1:{port}/health")
                    return port
            except Exception:
                continue
        
        raise RuntimeError("OpenCode server failed to start")


# Register the harness
harness_registry.register(OpenCodeHarness())