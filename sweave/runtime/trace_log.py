"""Per-delegation structured trace log.

Each delegation gets a JSONL file at ``~/.sweave/traces/{delegation_id}.jsonl``
(one JSON object per line). The trace captures everything a human or a
later tool needs to reconstruct what happened:

* status transitions (queued → running → review → done/failed)
* prompts sent to the agent
* output chunks from the agent
* tool calls and their results
* error payloads

The format is intentionally simple so it can be tailed (``tail -f``),
ingested into a log pipeline, or parsed with one ``json.loads`` per line.

This module is dependency-free (stdlib only) and can be imported from
both sync (ProjectManager) and async (JobRunner) contexts.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


# Event type vocabulary (free-form strings; we don't enforce a closed set
# but a few common ones are documented here for reference).
#
#   "status_changed"  -- status transition; payload must include "status"
#   "prompt_sent"     -- prompt text sent to the agent
#   "output_chunk"    -- a chunk of agent output (M1.8 streams these)
#   "tool_call"       -- agent invoked a tool; payload has tool_name + args
#   "tool_result"     -- tool returned; payload has tool_name + result
#   "error"           -- agent or harness raised; payload has the error
#   "metadata"        -- arbitrary structured context (worktree, branch, PR)


def _now_iso() -> str:
    return datetime.now().isoformat()


class TraceLog:
    """JSONL trace writer for a single delegation.

    Call :meth:`append` to write one event. The first call to ``append``
    creates the parent directory if it does not exist; subsequent calls
    are just ``append + flush`` for crash-safety.

    Threading: a single TraceLog is safe to use from one asyncio loop OR
    multiple threads, but not both at once. The runner (M1.prep step 6)
    is single-loop; the agent process (if it ever writes) is a separate
    process with its own TraceLog instance.
    """

    def __init__(self, delegation_id: str, base_dir: Path | None = None) -> None:
        self.delegation_id = delegation_id
        self.base_dir = base_dir or Path.home() / ".sweave" / "traces"
        self.path = self.base_dir / f"{delegation_id}.jsonl"
        self._lock = threading.Lock()
        self._fp: Any = None  # lazily opened

    def _ensure_open(self) -> None:
        if self._fp is not None:
            return
        self.base_dir.mkdir(parents=True, exist_ok=True)
        # Open in line-buffered append mode so each write is durable on
        # close / flush; the fsync per line is the caller's choice.
        self._fp = open(self.path, "a", encoding="utf-8", buffering=1)

    def append(self, event: str, payload: dict[str, Any] | None = None) -> None:
        """Append one event to the trace.

        ``payload`` may be omitted for status-only events. The written
        object always has ``ts`` and ``event``; the rest of the payload
        is preserved verbatim.
        """
        record = {
            "ts": _now_iso(),
            "event": event,
            "delegation_id": self.delegation_id,
        }
        if payload:
            record.update(payload)
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            self._ensure_open()
            self._fp.write(line + "\n")
            self._fp.flush()
            try:
                os.fsync(self._fp.fileno())
            except OSError:
                pass

    def fsync(self) -> None:
        """Force a flush to disk. Mostly for tests."""
        with self._lock:
            if self._fp is not None:
                self._fp.flush()
                try:
                    os.fsync(self._fp.fileno())
                except OSError:
                    pass

    def close(self) -> None:
        with self._lock:
            if self._fp is not None:
                try:
                    self._fp.flush()
                    os.fsync(self._fp.fileno())
                except OSError:
                    pass
                self._fp.close()
                self._fp = None

    # -- async wrappers --------------------------------------------------

    async def aappend(self, event: str, payload: dict[str, Any] | None = None) -> None:
        """Async wrapper around :meth:`append`. File I/O happens on a
        thread to keep the event loop responsive for large output chunks.
        """
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.append, event, payload)

    async def aclose(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.close)

    # -- context-manager sugar ------------------------------------------

    def __enter__(self) -> "TraceLog":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    async def __aenter__(self) -> "TraceLog":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()


def read_trace(delegation_id: str, base_dir: Path | None = None) -> list[dict]:
    """Read every line of a trace file. For debugging / Children detail view.

    Malformed lines are skipped with a warning, never raised — the trace
    is a debugging artefact, not a critical store.
    """
    base = base_dir or Path.home() / ".sweave" / "traces"
    path = base / f"{delegation_id}.jsonl"
    if not path.exists():
        return []
    out: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out
