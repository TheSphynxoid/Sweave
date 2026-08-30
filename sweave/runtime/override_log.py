"""Override log (M1.2 step 3).

When a user submits a task to ``POST /api/v2/tasks`` with an explicit
``agent`` that differs from the rule-router's decision, we record
the discrepancy as a *gold label* for the R6 dispatch training
pipeline. The log is append-only JSONL; per-project file lives at
``{project}/.sweave/override_log.jsonl``, with a global fallback
to ``~/.sweave/override_log.jsonl`` when no project is active
(amendment F: no-active-project submits still get logged).

Volume is expected to be small (each entry is a few hundred bytes;
we only log when the user *overrides* the router, not on every
submission), so JSONL append with a per-file lock is enough. No
debounce, no batch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from sweave.runtime.locking import atomic_write_json  # noqa: F401  (re-exported)

logger = logging.getLogger(__name__)

_GLOBAL_FALLBACK = Path.home() / ".sweave" / "override_log.jsonl"


class OverrideLog:
    """Append-only JSONL writer for routing overrides.

    One file per project; the file path is created on first write
    (parent directories included). The lock is per-instance because
    each instance is bound to one path; concurrent processes writing
    to the same file would interleave at the OS level (acceptable
    for an append-only log -- JSONL is line-oriented and a torn line
    is visible in the next read).
    """

    def __init__(self, file_path: Path) -> None:
        self.file_path = Path(file_path)
        self._lock = threading.Lock()

    @classmethod
    def for_project(cls, project_dir: Path) -> "OverrideLog":
        """Build an OverrideLog rooted at ``{project}/.sweave/override_log.jsonl``.

        Creates the ``.sweave`` subdir if missing (same convention as
        the specialist / delegation stores).
        """
        project_dir = Path(project_dir)
        sweave_dir = project_dir / ".sweave"
        sweave_dir.mkdir(parents=True, exist_ok=True)
        return cls(sweave_dir / "override_log.jsonl")

    @classmethod
    def global_fallback(cls) -> "OverrideLog":
        """Build the global fallback log at ``~/.sweave/override_log.jsonl``."""
        _GLOBAL_FALLBACK.parent.mkdir(parents=True, exist_ok=True)
        return cls(_GLOBAL_FALLBACK)

    async def append(self, entry: dict[str, Any]) -> None:
        """Append one record. Thread-safe via a per-instance lock.

        The dict is JSON-serialised and written as a single line;
        a final newline ensures the file is line-oriented.
        """
        line = json.dumps(entry, ensure_ascii=False, default=str)
        # The lock is short (single file write + flush); off-load to
        # the default executor so the event loop isn't blocked on
        # the actual ``open`` syscall. The lock is still per-instance
        # so concurrent append()s from the same instance serialise.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._sync_append, line)

    def _sync_append(self, line: str) -> None:
        with self._lock:
            try:
                with self.file_path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError as e:
                # Never let a log write kill a task. Log + move on.
                logger.warning(
                    "OverrideLog: cannot append to %s: %s",
                    self.file_path, e,
                )

    def read(self) -> list[dict[str, Any]]:
        """Read every record. Malformed lines are skipped (not raised)."""
        if not self.file_path.exists():
            return []
        out: list[dict[str, Any]] = []
        for raw in self.file_path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return out


def make_override_entry(
    *,
    project: str | None,
    session_id: str | None,
    task: str,
    routed_agent: str,
    routed_model: str | None,
    user_agent: str,
    source: str = "v2_task",
) -> dict[str, Any]:
    """Build a single override-log entry.

    The shape is documented in the M1.2 plan:
    ``{ts, project, session_id, task, routed_agent, routed_model, user_agent, source}``.
    """
    return {
        "ts": datetime.now().isoformat(),
        "project": project,
        "session_id": session_id,
        "task": task,
        "routed_agent": routed_agent,
        "routed_model": routed_model,
        "user_agent": user_agent,
        "source": source,
    }
