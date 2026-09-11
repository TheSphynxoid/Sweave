"""Locking primitives + atomic file writes for the runtime layer.

Goals
-----
* **Atomic JSON writes** that survive a mid-write crash. The contract is
  ``write to <path>.tmp`` → ``fsync`` → ``os.replace`` so an interrupted
  process never leaves a half-written file at the canonical path.
* **Per-project asyncio locks** so concurrent writes to the same project
  serialise in event-loop order. Different projects do not contend.

This module is intentionally small and dependency-free (stdlib only) so it
can be imported from any layer without cycle risk.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any


async def atomic_write_json(
    path: Path | str,
    data: Any,
    *,
    use_yaml: bool = False,
) -> None:
    """Async wrapper around :func:`atomic_write_json_sync` for callers that
    want to await. Both share the same on-disk contract.
    """
    atomic_write_json_sync(path, data, use_yaml=use_yaml)


def atomic_write_text_sync(path: Path | str, text: str) -> None:
    """Write *text* to *path* atomically (synchronous, UTF-8).

    Same tmp-file + fsync + ``os.replace`` contract as
    :func:`atomic_write_json_sync`, for callers that already hold
    rendered text (e.g. a comment-preserving config edit that must
    not round-trip through a YAML dump).
    """
    path = Path(path)
    parent = path.parent
    if parent and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(parent) if parent else None,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def atomic_write_json_sync(
    path: Path | str,
    data: Any,
    *,
    use_yaml: bool = False,
) -> None:
    """Write *data* to *path* atomically (synchronous).

    The write goes to a sibling temp file, is fsynced, then ``os.replace``-d
    onto the destination. ``os.replace`` is atomic on both Windows and POSIX.
    YAML is supported for the dynamic-agents file (and any other YAML-shaped
    config that the runtime might persist); JSON is the default.

    Raises whatever ``json.dump`` / ``yaml.safe_dump`` raise on bad data.
    """
    path = Path(path)
    parent = path.parent
    if parent and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)

    # Use NamedTemporaryFile in the same directory so os.replace is atomic
    # (it requires same-filesystem on POSIX; same-dir is good enough on Windows).
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(parent) if parent else None,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            if use_yaml:
                import yaml

                yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
            else:
                json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                # Some filesystems (e.g. some Windows network drives) don't
                # support fsync; the data is on disk after flush anyway.
                pass
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup of the tmp file on failure
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


class ProjectLockRegistry:
    """Map of project name → :class:`asyncio.Lock`.

    Locks are created lazily on first request. There is no eviction: a
    process restart clears the map. Per the M1.prep design, the registry
    lives on :class:`sweave.web.state.AppState` and is therefore shared
    across all requests in a single process.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()

    async def lock_for(self, project_name: str) -> asyncio.Lock:
        """Return the asyncio lock for *project_name*, creating it if needed."""
        # Fast path: already created
        existing = self._locks.get(project_name)
        if existing is not None:
            return existing

        # Slow path: take the meta lock to safely create
        async with self._meta_lock:
            existing = self._locks.get(project_name)
            if existing is None:
                existing = asyncio.Lock()
                self._locks[project_name] = existing
            return existing

    def known_projects(self) -> list[str]:
        """Return project names that currently have a lock. For diagnostics."""
        return list(self._locks.keys())
