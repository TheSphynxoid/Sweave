from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, Any
import asyncio
import os
import subprocess

from sweave.platform import creationflags_no_window


@dataclass
class MemoryEntry:
    """A single memory entry.

    M1.7 step 4: ``ts`` (timestamp) is the runtime's signal for the
    multi-source "what's new" delta. Entries written before M1.7
    carry ``ts=None``; the runtime treats them as legacy and
    includes them in recall output but not in the "what's new"
    section (which is filtered by ``ts > session.last_memory_recall_ts``).
    """
    content: str
    tags: list[str] = None
    metadata: dict[str, Any] = None
    ts: datetime | None = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []
        if self.metadata is None:
            self.metadata = {}
        if self.ts is None:
            # Default to "now" for new entries; legacy entries that
            # were reloaded without a ts get a sentinel that
            # includes them in any "what's new" filter.
            self.ts = datetime.now()


class MemoryBackend(Protocol):
    """Protocol for memory backends."""
    
    async def recall(self, query: str, bank_id: str, limit: int = 10) -> list[MemoryEntry]:
        """Recall relevant memories."""
        ...
    
    async def retain(self, content: str, bank_id: str, tags: list[str] = None) -> str:
        """Retain a memory."""
        ...
    
    async def reflect(self, query: str, bank_id: str) -> str:
        """Reflect/synthesize from memories."""
        ...
    
    async def health_check(self) -> bool:
        """Check if backend is healthy."""
        ...


class NoOpMemory:
    """No-op memory backend for testing/disabled."""
    
    async def recall(self, query: str, bank_id: str, limit: int = 10) -> list[MemoryEntry]:
        return []
    
    async def retain(self, content: str, bank_id: str, tags: list[str] = None) -> str:
        return "ok"
    
    async def reflect(self, query: str, bank_id: str) -> str:
        return "No memory backend configured."
    
    async def health_check(self) -> bool:
        return True


class HindsightEmbeddedMemory:
    """Hindsight memory using embedded Python client (hindsight-all-slim)."""
    
    def __init__(
        self,
        api_key: str | None = None,
        api_url: str | None = None,
        embeddings_provider: str = "openai",
        embeddings_api_key: str | None = None,
        embeddings_model: str = "text-embedding-3-small",
        reranker_provider: str = "openai",
        reranker_api_key: str | None = None,
    ):
        self.api_key = api_key
        self.api_url = api_url or "http://localhost:8888"
        self.embeddings_provider = embeddings_provider
        self.embeddings_api_key = embeddings_api_key
        self.embeddings_model = embeddings_model
        self.reranker_provider = reranker_provider
        self.reranker_api_key = reranker_api_key
        self._client = None
        self._initialized = False
    
    async def _ensure_client(self):
        """Lazy initialize Hindsight client."""
        if self._initialized:
            return
        
        try:
            from hindsight_client import Hindsight
            
            self._client = Hindsight(
                base_url=self.api_url,
                api_key=self.api_key or os.environ.get("HINDSIGHT_API_KEY", ""),
                timeout=30.0,
            )
            self._initialized = True
        except ImportError:
            raise RuntimeError(
                "hindsight-client not installed. Install with: pip install hindsight-all-slim"
            )
    
    async def _ensure_bank(self, bank_id: str):
        """Ensure memory bank exists."""
        await self._ensure_client()
        try:
            await asyncio.to_thread(self._client.create_bank, bank_id=bank_id, name=bank_id)
        except Exception:
            pass  # Bank likely exists
    
    async def recall(self, query: str, bank_id: str, limit: int = 10) -> list[MemoryEntry]:
        await self._ensure_client()
        await self._ensure_bank(bank_id)
        
        try:
            response = await asyncio.to_thread(
                self._client.recall,
                bank_id=bank_id,
                query=query,
                budget="mid",
                max_tokens=4096,
            )
            return [
                MemoryEntry(content=r.text, metadata={"score": getattr(r, "score", None)})
                for r in (response.results or [])[:limit]
            ]
        except Exception as e:
            return [MemoryEntry(content=f"Recall error: {e}", metadata={"error": True})]
    
    async def retain(self, content: str, bank_id: str, tags: list[str] = None) -> str:
        await self._ensure_client()
        await self._ensure_bank(bank_id)
        
        try:
            kwargs = {"bank_id": bank_id, "content": content}
            if tags:
                kwargs["tags"] = tags
            await asyncio.to_thread(self._client.retain, **kwargs)
            return "ok"
        except Exception as e:
            return f"Retain error: {e}"
    
    async def reflect(self, query: str, bank_id: str) -> str:
        await self._ensure_client()
        await self._ensure_bank(bank_id)
        
        try:
            response = await asyncio.to_thread(
                self._client.reflect,
                bank_id=bank_id,
                query=query,
                budget="mid",
            )
            return response.text or "No relevant memories found."
        except Exception as e:
            return f"Reflect error: {e}"
    
    async def health_check(self) -> bool:
        try:
            await self._ensure_client()
            return True
        except Exception:
            return False


class HindsightDockerMemory:
    """Hindsight memory via Docker container."""
    
    def __init__(
        self,
        mode: str = "slim",  # "full" or "slim"
        api_key: str | None = None,
        api_url: str = "http://localhost:8888",
        embeddings_provider: str = "openai",
        embeddings_api_key: str | None = None,
        **kwargs,
    ):
        self.mode = mode
        self.api_key = api_key
        self.api_url = api_url
        self.embeddings_provider = embeddings_provider
        self.embeddings_api_key = embeddings_api_key
        self._container_id: str | None = None
        self._embedded = HindsightEmbeddedMemory(
            api_key=api_key,
            api_url=api_url,
            embeddings_provider=embeddings_provider,
            embeddings_api_key=embeddings_api_key,
            **kwargs,
        )
    
    async def start_container(self) -> bool:
        """Start Hindsight Docker container."""
        if self._container_id:
            return True
        
        image = "ghcr.io/vectorize-io/hindsight:latest" if self.mode == "full" else "ghcr.io/vectorize-io/hindsight:slim"
        
        env = {
            "HINDSIGHT_API_LLM_API_KEY": self.api_key or os.environ.get("OPENAI_API_KEY", ""),
            "HINDSIGHT_API_EMBEDDINGS_PROVIDER": self.embeddings_provider,
            "HINDSIGHT_API_EMBEDDINGS_API_KEY": self.embeddings_api_key or os.environ.get("OPENAI_API_KEY", ""),
        }
        
        cmd = [
            "docker", "run", "-d",
            "-p", "8888:8888",
            "-p", "9999:9999",
            "-v", "hindsight-data:/home/hindsight/.pg0",
        ]
        
        for k, v in env.items():
            if v:
                cmd.extend(["-e", f"{k}={v}"])
        
        cmd.append(image)
        
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=creationflags_no_window(),
            )
            if result.returncode == 0:
                self._container_id = result.stdout.strip()
                # Wait for container to be ready
                await asyncio.sleep(5)
                return True
        except Exception:
            pass
        return False
    
    async def stop_container(self):
        """Stop Hindsight Docker container."""
        if self._container_id:
            await asyncio.to_thread(
                subprocess.run,
                ["docker", "stop", self._container_id],
                capture_output=True,
                creationflags=creationflags_no_window(),
            )
            self._container_id = None
    
    async def recall(self, query: str, bank_id: str, limit: int = 10) -> list[MemoryEntry]:
        await self.start_container()
        return await self._embedded.recall(query, bank_id, limit)
    
    async def retain(self, content: str, bank_id: str, tags: list[str] = None) -> str:
        await self.start_container()
        return await self._embedded.retain(content, bank_id, tags)
    
    async def reflect(self, query: str, bank_id: str) -> str:
        await self.start_container()
        return await self._embedded.reflect(query, bank_id)
    
    async def health_check(self) -> bool:
        await self.start_container()
        return await self._embedded.health_check()


class HindsightCloudMemory:
    """Hindsight memory via Hindsight Cloud API."""
    
    def __init__(self, api_key: str, api_url: str = "https://api.hindsight.vectorize.io"):
        self.api_key = api_key
        self.api_url = api_url
        self._embedded = HindsightEmbeddedMemory(api_key=api_key, api_url=api_url)
    
    async def recall(self, query: str, bank_id: str, limit: int = 10) -> list[MemoryEntry]:
        return await self._embedded.recall(query, bank_id, limit)
    
    async def retain(self, content: str, bank_id: str, tags: list[str] = None) -> str:
        return await self._embedded.retain(content, bank_id, tags)
    
    async def reflect(self, query: str, bank_id: str) -> str:
        return await self._embedded.reflect(query, bank_id)
    
    async def health_check(self) -> bool:
        return await self._embedded.health_check()


class MemoryFactory:
    """Factory for creating memory backends."""
    
    @staticmethod
    def create(config: Any) -> MemoryBackend:
        """Create memory backend from config."""
        from sweave.config.schemas import HindsightConfig
        
        if config.backend == "none":
            return NoOpMemory()
        
        if config.backend != "hindsight":
            return NoOpMemory()
        
        h: HindsightConfig = config.hindsight
        
        if h.mode == "cloud":
            if not h.api_key:
                raise ValueError("Hindsight Cloud requires api_key")
            return HindsightCloudMemory(h.api_key, str(h.api_url) if h.api_url else "https://api.hindsight.vectorize.io")
        
        if h.mode == "docker_full":
            return HindsightDockerMemory(
                mode="full",
                api_key=h.api_key,
                api_url=str(h.api_url) if h.api_url else "http://localhost:8888",
                embeddings_provider=h.embeddings_provider,
                embeddings_api_key=h.embeddings_api_key,
            )
        
        if h.mode == "docker_slim":
            return HindsightDockerMemory(
                mode="slim",
                api_key=h.api_key,
                api_url=str(h.api_url) if h.api_url else "http://localhost:8888",
                embeddings_provider=h.embeddings_provider,
                embeddings_api_key=h.embeddings_api_key,
            )
        
        # embedded_slim (default)
        return HindsightEmbeddedMemory(
            api_key=h.api_key,
            api_url=str(h.api_url) if h.api_url else "http://localhost:8888",
            embeddings_provider=h.embeddings_provider,
            embeddings_api_key=h.embeddings_api_key,
            embeddings_model=h.embeddings_model,
            reranker_provider=h.reranker_provider,
            reranker_api_key=h.reranker_api_key,
        )