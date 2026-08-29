"""Specialist records + per-scope stores (M1.2 step 1).

A :class:`Specialist` is the persisted identity + config for one named
worker. The runtime has three tiers (Orchestrator / Specialist /
SubAgent) per the M1.2 plan's tier framing section:

* **Orchestrator** -- per-project singleton. Same record type, but
  ``is_orchestrator=True`` gates create/delete (409 on user
  attempt). Auto-seeded on first ``resolve("orchestrator", project)``
  using the ``sweave/agents/orchestrator/`` seed.
* **Specialist** -- persistent, named, user-creatable. The work-doer.
  Per-project + global scopes; the 4 ``sweave/agents/*`` seeds are
  starter specialists (read-only via the store; the user edits
  ``config.yaml`` to change them).
* **SubAgent** -- already in M1.1's :mod:`sweave.runtime.subagent_store`.
  Out of scope here.

The store split:

* :class:`GlobalSpecialistStore` -- ``~/.sweave/agents.yaml``
  (home-anchored, replaces the M1.prep CWD-relative ``agents.yaml``).
* :class:`ProjectSpecialistStore` -- ``{project_dir}/.sweave/agents.json``.
  Constructor creates the ``.sweave`` subdir (amendment D).

Resolution: ``resolve(name, project_dir)`` walks project -> global ->
seed; the first hit wins. The orchestrator singleton is *not* in the
specialist pool (it lives in the per-project store as a Specialist
with ``is_orchestrator=True``, but it's resolved via a separate
``resolve_orchestrator`` accessor and isn't returned by
``list_resolved`` for the specialist pool).

Schema versioning: ``schema_version=1`` on every persisted record. The
first M1.2 plan is v1; a future migration will land in
``from_dict``. The current :meth:`Specialist.from_dict` does
``setdefault("schema_version", 1)`` and field-filters to known fields
(same forward-compat pattern as M1.1's Delegation).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from sweave.agents.loader import AGENTS_DIR, AgentDefinition, load_seed_agents
from sweave.runtime.locking import atomic_write_json_sync

logger = logging.getLogger(__name__)


SCHEMA_VERSION = 1
ORCHESTRATOR_NAME = "orchestrator"
ORCHESTRATOR_ROLE_REF = "orchestrator"
VALID_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")  # lowercase + digits + _-


def _now() -> datetime:
    return datetime.now()


def _validate_name(name: str) -> None:
    """Lowercase alphanumeric + ``_``/``-``; 1-63 chars; starts with letter or digit.

    Rejects names that would break filesystem paths or HTTP paths.
    """
    if not isinstance(name, str) or not VALID_NAME.match(name):
        raise ValueError(
            f"invalid specialist name: {name!r} (lowercase alphanumeric + _-; 1-63 chars)"
        )


@dataclass
class Specialist:
    """One persisted specialist (or orchestrator singleton).

    ``scope`` is one of ``"project" | "global" | "seed"``. Seeds are
    derived views over the on-disk ``sweave/agents/*/config.yaml``
    files; they're never persisted through the store, so
    ``scope="seed"`` records are read-only via the public API.
    """

    schema_version: int = SCHEMA_VERSION
    name: str = ""
    scope: str = "project"  # project | global | seed
    is_orchestrator: bool = False
    role_ref: str | None = None  # optional hint to model resolve
    description: str = ""
    system_prompt: str = ""
    harness: str = "opencode"
    current_model: str | None = None
    session_id: str | None = None  # M1.3 fills
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        # Validate the name *shape* if set. The empty string is the
        # default and is treated as "incomplete record" (tolerated
        # so that default-construction works; the from_dict loader
        # sets name from JSON, and the public API (create/update) never
        # leaves name empty by accident because both paths call
        # _validate_name() on the inbound name before constructing
        # the Specialist).
        if self.name:
            _validate_name(self.name)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("created_at", "updated_at"):
            if d[k] is not None:
                d[k] = d[k].isoformat()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Specialist":
        d = dict(data)
        d.setdefault("schema_version", SCHEMA_VERSION)
        for k in ("created_at", "updated_at"):
            v = d.get(k)
            if isinstance(v, str):
                d[k] = datetime.fromisoformat(v)
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in d.items() if k in known}
        return cls(**kwargs)

    def public_dict(self) -> dict[str, Any]:
        """API-facing view: scope, name, role_ref, description, model, harness.

        Omits internal fields (schema_version, timestamps, session_id) so
        the wire format doesn't carry noise the UI doesn't need.
        """
        return {
            "name": self.name,
            "scope": self.scope,
            "is_orchestrator": self.is_orchestrator,
            "role_ref": self.role_ref,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "harness": self.harness,
            "current_model": self.current_model,
        }


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------


class _BaseSpecialistStore:
    """Common load/persist for both global and project stores."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = Path(file_path)
        self._lock = threading.Lock()
        self._records: dict[str, Specialist] = {}
        self._load()

    def _load(self) -> None:
        if not self.file_path.exists():
            return
        try:
            text = self.file_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning(
                "SpecialistStore: cannot read %s: %s -- starting empty",
                self.file_path, e,
            )
            return
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(
                "SpecialistStore: %s is corrupt (%s) -- starting empty",
                self.file_path, e,
            )
            return
        if not isinstance(data, dict) or "specialists" not in data:
            logger.warning(
                "SpecialistStore: %s has unexpected shape -- starting empty",
                self.file_path,
            )
            return
        for entry in data["specialists"]:
            if not isinstance(entry, dict):
                continue
            try:
                rec = Specialist.from_dict(entry)
            except (TypeError, ValueError) as e:
                logger.warning(
                    "SpecialistStore: skipping bad entry in %s: %s",
                    self.file_path, e,
                )
                continue
            # Seed records never come from a file; defensive guard.
            if rec.scope == "seed":
                rec.scope = "global"  # fold seeds to global if persisted (shouldn't happen)
            self._records[rec.name] = rec

    def _persist(self) -> None:
        payload = {
            "specialists": [r.to_dict() for r in self._records.values()]
        }
        atomic_write_json_sync(self.file_path, payload)

    # ---- read API (lock-free; the dict is stable under read) ---------------

    def get(self, name: str) -> Specialist | None:
        return self._records.get(name)

    def list(self) -> list[Specialist]:
        return list(self._records.values())

    def exists(self, name: str) -> bool:
        return name in self._records

    # ---- write API (lock + persist) --------------------------------------

    def upsert(self, rec: Specialist) -> None:
        with self._lock:
            rec.updated_at = _now()
            self._records[rec.name] = rec
            self._persist()

    def delete(self, name: str) -> bool:
        with self._lock:
            if name not in self._records:
                return False
            del self._records[name]
            self._persist()
            return True


class GlobalSpecialistStore(_BaseSpecialistStore):
    """``~/.sweave/agents.yaml`` (new anchored path; replaces the M1.prep
    CWD-relative ``agents.yaml``)."""

    @staticmethod
    def default_path() -> Path:
        return Path.home() / ".sweave" / "agents.yaml"

    def __init__(self, file_path: Path | None = None) -> None:
        super().__init__(Path(file_path) if file_path else self.default_path())


class ProjectSpecialistStore(_BaseSpecialistStore):
    """``{project_dir}/.sweave/agents.json`` (per-project, lazy, atomic
    write-through). The constructor creates the ``.sweave`` subdir if
    missing (amendment D).
    """

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir)
        # Amendment D: store creates the .sweave subdir.
        sweave_dir = self.project_dir / ".sweave"
        sweave_dir.mkdir(parents=True, exist_ok=True)
        super().__init__(sweave_dir / "agents.json")


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _seed_as_specialist(defn: AgentDefinition) -> Specialist:
    """Map an :class:`AgentDefinition` to a seed-view :class:`Specialist`.

    The seed's ``role`` becomes ``role_ref`` at the resolution boundary
    (amendment A). The seed view is read-only; ``scope="seed"``.
    """
    return Specialist(
        name=defn.name,
        scope="seed",
        is_orchestrator=(defn.name == ORCHESTRATOR_NAME),
        role_ref=defn.role,
        description=defn.description,
        system_prompt=defn.prompt,
        harness=defn.harness,
        current_model=None,  # seeds don't pin a model; resolve() does
        session_id=None,
    )


class SpecialistResolver:
    """Resolves a name to a :class:`Specialist` (project -> global -> seed).

    Stateful only in that the per-project store is constructed lazily
    on first access. Reads are lock-free against the stores; writes go
    through each store's own lock + atomic write.

    The orchestrator singleton is *not* returned by ``list_resolved`` /
    ``resolve(name, project)`` (it's resolved by
    ``resolve_orchestrator(project)``). Reasoning: the orchestrator
    isn't in the routing pool (M1.7's defer tool will pull specialists
    only); keeping it out of the default resolution prevents accidental
    routing into "orchestrator" via the rule-router's keyword matching.
    """

    def __init__(self) -> None:
        self.global_store = GlobalSpecialistStore()
        self._project_stores: dict[str, ProjectSpecialistStore] = {}
        self._meta_lock = asyncio.Lock()
        self._seed_defs = load_seed_agents()

    def _project_store(self, project_dir: Path) -> ProjectSpecialistStore:
        key = str(Path(project_dir).resolve())
        existing = self._project_stores.get(key)
        if existing is not None:
            return existing
        # Note: this is sync; safe in single-loop contexts. Multiple
        # concurrent first-callers for the same path get separate
        # ProjectSpecialistStore objects reading the same file -- benign
        # because the file is the same and each store's write goes
        # through the atomic rename. We don't lock here to keep resolve
        # synchronous (M1.3's specialist runtime is the first consumer
        # that may need a tighter guarantee).
        store = ProjectSpecialistStore(project_dir)
        self._project_stores[key] = store
        return store

    def drop_project(self, project_dir: Path) -> bool:
        """Forget the in-memory store for *project_dir* (file persists on disk).

        Called from :meth:`ProjectManager.delete_project` so we don't leak
        a stale store when a project is removed.
        """
        key = str(Path(project_dir).resolve())
        return self._project_stores.pop(key, None) is not None

    # ---- resolution -----------------------------------------------------

    def resolve(
        self,
        name: str,
        project_dir: Path | None = None,
    ) -> Specialist | None:
        """project -> global -> seed. Returns None if no match."""
        if name == ORCHESTRATOR_NAME:
            # Orchestrator is only resolved via resolve_orchestrator().
            return None
        if project_dir is not None:
            rec = self._project_store(project_dir).get(name)
            if rec is not None:
                return rec
        rec = self.global_store.get(name)
        if rec is not None:
            return rec
        seed_def = self._seed_defs.get(name)
        if seed_def is not None:
            return _seed_as_specialist(seed_def)
        return None

    def resolve_orchestrator(
        self,
        project_dir: Path,
        *,
        auto_seed: bool = True,
    ) -> Specialist | None:
        """Resolve the orchestrator singleton for *project_dir*.

        With ``auto_seed=True`` (default), the per-project record is
        created on first call using the orchestrator seed (read-only
        seed view -> persisted Specialist with is_orchestrator=True).
        Returns None only if the seed is missing (corrupt install).
        """
        store = self._project_store(project_dir)
        existing = store.get(ORCHESTRATOR_NAME)
        if existing is not None:
            return existing
        if not auto_seed:
            return None
        seed_def = self._seed_defs.get(ORCHESTRATOR_NAME)
        if seed_def is None:
            logger.warning(
                "Orchestrator seed missing from %s -- cannot auto-seed",
                project_dir,
            )
            return None
        rec = Specialist(
            name=ORCHESTRATOR_NAME,
            scope="project",
            is_orchestrator=True,
            role_ref=ORCHESTRATOR_ROLE_REF,
            description=seed_def.description,
            system_prompt=seed_def.prompt,
            harness=seed_def.harness,
            current_model=None,
        )
        store.upsert(rec)
        logger.info(
            "Auto-seeded orchestrator singleton for project %s", project_dir
        )
        return rec

    def list_resolved(
        self,
        project_dir: Path | None = None,
        *,
        include_seeds: bool = True,
    ) -> list[Specialist]:
        """All specialists the routing pool can see, in stable order.

        Order: project-scoped first (sorted by name), then global, then
        seeds (sorted by name). Dedup by name (project shadows global
        shadows seed). Excludes the orchestrator.
        """
        seen: set[str] = set()
        out: list[Specialist] = []

        def _add(rec: Specialist) -> None:
            if rec.name in seen or rec.is_orchestrator:
                return
            seen.add(rec.name)
            out.append(rec)

        if project_dir is not None:
            for r in sorted(
                self._project_store(project_dir).list(), key=lambda r: r.name
            ):
                _add(r)
        for r in sorted(self.global_store.list(), key=lambda r: r.name):
            _add(r)
        if include_seeds:
            for seed_name in sorted(self._seed_defs):
                if seed_name == ORCHESTRATOR_NAME:
                    continue  # orchestrator isn't a routing-pool member
                _add(_seed_as_specialist(self._seed_defs[seed_name]))
        return out

    # ---- write API (gated) -----------------------------------------------

    def create(
        self,
        rec: Specialist,
        project_dir: Path | None = None,
    ) -> Specialist:
        """Insert *rec*; raise ``ValueError`` on collision or orchestrator."""
        if rec.is_orchestrator:
            raise ValueError(
                "cannot create a specialist with is_orchestrator=True via the API; "
                "the orchestrator is auto-seeded via resolve_orchestrator()"
            )
        if rec.name == ORCHESTRATOR_NAME:
            raise ValueError(f"name '{ORCHESTRATOR_NAME}' is reserved for the orchestrator")
        store = self._project_store(project_dir) if project_dir is not None else None
        if store is None:
            if rec.scope == "project":
                raise ValueError("project-scope create requires project_dir")
            store = self.global_store
        if store.exists(rec.name):
            raise ValueError(f"specialist '{rec.name}' already exists in scope")
        store.upsert(rec)
        return rec

    def update(
        self,
        rec: Specialist,
        project_dir: Path | None = None,
    ) -> Specialist:
        """Replace *rec* in its store."""
        if rec.is_orchestrator:
            raise ValueError(
                "cannot overwrite an is_orchestrator=True record via update(); "
                "use resolve_orchestrator() to access the singleton"
            )
        store = self._project_store(project_dir) if project_dir is not None else None
        if store is None:
            if rec.scope == "project":
                raise ValueError("project-scope update requires project_dir")
            store = self.global_store
        store.upsert(rec)
        return rec

    def delete(
        self,
        name: str,
        project_dir: Path | None = None,
    ) -> bool:
        """Remove *name*. Orchestrator name -> refused."""
        if name == ORCHESTRATOR_NAME:
            raise ValueError(
                f"name '{ORCHESTRATOR_NAME}' is reserved for the orchestrator; "
                "cannot delete the singleton"
            )
        store = self._project_store(project_dir) if project_dir is not None else None
        if store is None:
            store = self.global_store
        return store.delete(name)
