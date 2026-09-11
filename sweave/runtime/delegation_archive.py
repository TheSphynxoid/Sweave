"""Delegation archive — dead-scope sweep + persisted aggregate index.

M1.13 cleanup pass (user ruling 2026-09-11, ARCHIVE-not-delete):

* Records referencing dead projects (the project is no longer
  registered in the data-dir, or its workdir path is gone) are marked
  ``archived`` in place — status, output and every stat field are
  preserved. Nothing is ever deleted here.
* On project/session deletion the project's / the session's
  delegations cascade to ``archived`` (called from the delete
  endpoints as a post-cascade hook).
* Per-dead-project aggregates are persisted under
  ``~/.sweave/archived/{slug}.json`` so the Children tab's compact
  "Archived" group keeps its per-project counts (total / by-status /
  by-kind) even after the dead project's delegations.json is cleaned
  up from disk or the store is no longer reachable at boot.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from sweave.runtime.delegation_store import Delegation
from sweave.runtime.locking import atomic_write_json_sync

logger = logging.getLogger(__name__)

ARCHIVE_INDEX_DIRNAME = "archived"


def _default_index_dir() -> Path:
    """``~/.sweave/archived`` (home-anchored at *call* time — never cache
    the path in a module constant; tests redirect Path.home())."""
    return Path.home() / ".sweave" / ARCHIVE_INDEX_DIRNAME


def slugify_project_name(name: str) -> str:
    """Filesystem-safe slug for a project name (index filename)."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", str(name)).strip("_")
    return slug or "unnamed"


def archive_group_entry(
    project_name: str,
    records: list[Delegation],
    *,
    workdir: str | None = None,
) -> dict[str, Any]:
    """Aggregate one project's archived records into an index row.

    Shape (identical for live-store aggregates and index rows, so the
    frontend renders them uniformly):

    ``{"project_name", "workdir", "total", "by_status", "by_kind",
      "archived_at", "last_created_at", "source"}``
    """
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    archived_at: datetime | None = None
    last_created_at: datetime | None = None
    for r in records:
        by_status[r.status] = by_status.get(r.status, 0) + 1
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
        if r.archived_at is not None and (
            archived_at is None or r.archived_at < archived_at
        ):
            archived_at = r.archived_at
        if r.created_at is not None and (
            last_created_at is None or r.created_at > last_created_at
        ):
            last_created_at = r.created_at
    return {
        "project_name": project_name,
        "workdir": workdir,
        "total": len(records),
        "by_status": by_status,
        "by_kind": by_kind,
        "archived_at": archived_at.isoformat() if archived_at else None,
        "last_created_at": (
            last_created_at.isoformat() if last_created_at else None
        ),
        "source": "store",
    }


class ArchiveIndexStore:
    """Persisted per-project archive aggregates at ``~/.sweave/archived``.

    One small JSON file per (slugged) project name. Written by the
    boot sweep / the project-delete cascade; read by
    ``GET /api/delegations?include_archived=true`` for projects whose
    own store is no longer reachable. Writes are atomic + UTF-8
    (see docs/GOTCHAS.md "Windows console flashes + locale I/O" 2).
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = (
            Path(base_dir) if base_dir is not None else _default_index_dir()
        )

    def path_for(self, project_name: str) -> Path:
        return self.base_dir / f"{slugify_project_name(project_name)}.json"

    def upsert(self, entry: dict[str, Any]) -> Path:
        """Write/refresh the index entry for the row's ``project_name``.

        The earliest ``archived_at`` seen is preserved: an entry must
        not lose its original archive timestamp when a later sweep
        recomputes the same group.
        """
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(str(entry.get("project_name")))
        # "source" is derived at READ time (store rows vs index rows);
        # never store it.
        entry = {k: v for k, v in entry.items() if k != "source"}
        existing: dict[str, Any] | None = None
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(
                    "ArchiveIndexStore: cannot read %s: %s -- overwriting",
                    path, e,
                )
        if isinstance(existing, dict):
            old_ts = existing.get("archived_at")
            new_ts = entry.get("archived_at")
            if old_ts and (not new_ts or new_ts > old_ts):
                entry["archived_at"] = old_ts
        atomic_write_json_sync(path, entry)
        return path

    def list_entries(self) -> list[dict[str, Any]]:
        """Read every index row. Bad files are skipped (never poison)."""
        if not self.base_dir.exists():
            return []
        out: list[dict[str, Any]] = []
        for path in sorted(self.base_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(
                    "ArchiveIndexStore: skipping bad index file %s: %s",
                    path, e,
                )
                continue
            if isinstance(data, dict) and data.get("project_name"):
                data.setdefault("source", "index")
                out.append(data)
        return out

    def get(self, project_name: str) -> dict[str, Any] | None:
        path = self.path_for(project_name)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("ArchiveIndexStore: cannot read %s: %s", path, e)
            return None
        if isinstance(data, dict):
            data.setdefault("source", "index")
            return data
        return None


def _project_is_dead(registered: dict[str, Any], name: str) -> bool:
    """A project scope is dead when the name is no longer registered
    in the data-dir, or its registered workdir path no longer exists
    on disk."""
    project = registered.get(name)
    if project is None:
        return True
    return not Path(project.path).exists()


def _registered_projects(project_manager=None) -> dict[str, Any]:
    """project_name -> Project for the registry (lazy singleton import
    so tests patching ``sweave.projects.project_manager`` win)."""
    if project_manager is None:
        from sweave.projects import project_manager
    return {p.name: p for p in project_manager.list_projects()}


async def sweep_archive_delegations(
    stores,
    index: ArchiveIndexStore,
    *,
    fallback_dir: Path | None = None,
    project_manager=None,
) -> dict[str, int]:
    """Boot sweep: archive dead-scope delegations + refresh the index.

    Enumerates every reachable store (registered projects' workdirs +
    the ``~/.sweave`` fallback) and marks ``archived`` any record whose
    ``project_name`` is dead (unregistered, or its workdir is gone).
    Records without a project scope (``project_name=None``, the
    fallback store's own records) are left alone. Idempotent: records
    already archived are skipped.

    Then persists per-dead-project aggregates into the index so the
    Children tab keeps the stats even after the underlying files are
    cleaned up or become unreachable on the next boot.

    Returns ``{"archived_records": n, "index_entries": m}``.
    """
    registered = _registered_projects(project_manager)
    seen: set[str] = set()
    store_targets = []
    dirs: list[Path] = [Path(p.path) for p in registered.values()]
    if fallback_dir is not None:
        dirs.append(Path(fallback_dir))
    # Also every store already in the in-process registry: a long-lived
    # server keeps stores for projects that were later removed from
    # the registry (the live "ghost" scope) — their records are
    # reachable ONLY here, so the sweep must see them.
    for known_store in stores.known_projects_stores():
        dirs.append(known_store.project_dir)
    for dir_path in dirs:
        key = str(Path(dir_path).resolve())
        if key in seen:
            continue
        seen.add(key)
        store_targets.append(await stores.for_project(dir_path))

    archived = 0
    for store in store_targets:
        dead_ids = [
            r.delegation_id
            for r in store.list()
            if not r.archived and r.project_name
            and _project_is_dead(registered, r.project_name)
        ]
        if dead_ids:
            archived += await store.archive_many(dead_ids)

    # Index: group every ARCHIVED record of a dead scope per project
    # name (from the same reachable stores) and persist the aggregate.
    index_entries = 0
    groups: dict[str, list[Delegation]] = {}
    for store in store_targets:
        for r in store.list():
            if r.archived and r.project_name:
                groups.setdefault(r.project_name, []).append(r)
    for name, recs in sorted(groups.items()):
        project = registered.get(name)
        workdir = str(project.path) if project is not None else None
        index.upsert(archive_group_entry_from_records(recs, name, workdir))
        index_entries += 1
    return {"archived_records": archived, "index_entries": index_entries}


def archive_group_entry_from_records(
    records: list[Delegation], project_name: str, workdir: str | None = None
) -> dict[str, Any]:
    """Build the aggregate row (see :func:`aggregate_records_projects`)."""
    entry = _aggregate_group(records)
    entry["project_name"] = project_name
    entry["workdir"] = workdir
    return entry


def _aggregate_group(records: list[Delegation]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    first_archived: datetime | None = None
    px: datetime | None = None
    for r in records:
        by_status[r.status] = by_status.get(r.status, 0) + 1
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
        if r.archived_at is not None and (
            first_archived is None or r.archived_at < first_archived
        ):
            first_archived = r.archived_at
        if px is None or r.created_at > px:
            px = r.created_at
    return {
        "total": len(records),
        "by_status": by_status,
        "by_kind": by_kind,
        "archived_at": first_archived.isoformat() if first_archived else None,
        "last_created_at": px.isoformat() if px else None,
        "source": "store",
    }


def archived_project_aggregates(
    all_records: list, index: ArchiveIndexStore | None = None
) -> list[dict[str, Any]]:
    """Compact per-project aggregate rows over archived records.

    Source of truth for the Children tab's archived group:

    * Groups every ARCHIVED record in the reachable stores by
      ``project_name`` (source ``"store"`` — freshest).
    * Unions rows from the persisted index for projects whose own
      records are no longer reachable at all (source ``"index"``).
    * Live-store rows win on collision with index rows.
    """
    groups: dict[str, list] = {}
    for r in all_records:
        if getattr(r, "archived", False) and r.project_name:
            groups.setdefault(r.project_name, []).append(r)

    known: dict[str, str | None] = {}
    from sweave.projects import project_manager  # lazy singleton import

    registered = {p.name: p for p in project_manager.list_projects()}
    for name, recs in groups.items():
        project = registered.get(name)
        known[name] = str(project.path) if project is not None else None

    rows: list[dict[str, Any]] = []
    for name in sorted(groups):
        entry = _aggregate_group(groups[name])
        entry["project_name"] = name
        entry["workdir"] = known[name]
        rows.append(entry)

    if index is not None:
        for entry in index.list_entries():
            name = entry.get("project_name")
            if name and name not in groups:
                entry = dict(entry)
                entry.setdefault("source", "index")
                entry.setdefault("workdir", None)
                rows.append(entry)
    rows.sort(key=lambda e: (-(e.get("total") or 0), e.get("project_name", "")))
    return rows


async def archive_project_scope(
    stores,
    project_name: str,
    *,
    index: ArchiveIndexStore | None = None,
    project_manager=None,
) -> int:
    """Cascade: archive every delegation of a deleted project.

    Called (best-effort, never fatal) from the project-delete endpoint
    BEFORE the registry entry is removed, so the project's workdir is
    still resolvable. ARCHIVE only — the records persist in the
    project's per-store file with their full stats. Persists the
    project's aggregate into the index (the store may become
    unreachable after restart). Returns the number archived.
    """
    if project_manager is None:
        from sweave.projects import project_manager

    project = project_manager.get_project(project_name)
    dir_path = Path(project.path) if project is not None else (
        Path.home() / ".sweave"
    )
    store = await stores.for_project(dir_path)
    to_archive = [
        r.delegation_id
        for r in store.list()
        # Cascade is scoped to the deleted project's own delegations —
        # the sweep owns dead foreign records (e.g. ghost names
        # sharing the same workdir file); don't double-hand them here.
        if not r.archived and r.project_name == project_name
    ]
    n = await store.archive_many(to_archive)
    if index is not None and n:
        recs = [
            r
            for r in store.list()
            if r.archived and r.project_name == project_name
        ]
        index.upsert(
            archive_group_entry_from_records(
                recs, project_name, workdir=str(dir_path)
            )
        )
    return n


async def archive_session_scope(
    stores,
    session_id: str,
    *,
    project_name: str | None = None,
    project_manager=None,
) -> int:
    """Cascade: archive every delegation whose parent session was
    deleted. The records keep their status + stats in the project's
    own live store (aggregate surfaces keep counting them).
    Returns the number archived."""
    if project_manager is None:
        from sweave.projects import project_manager

    dir_path: Path | None = None
    if project_name:
        project = project_manager.get_project(project_name)
        if project is not None:
            dir_path = Path(project.path)
    if dir_path is None:
        dir_path = Path.home() / ".sweave"  # fallback store
    store = await stores.for_project(dir_path)
    to_archive = [
        r.delegation_id
        for r in store.list()
        if not r.archived and r.parent_session_id == session_id
    ]
    return await store.archive_many(to_archive)
