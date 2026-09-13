"""Sweave-owned provider credentials (user ruling 2026-09-14).

The canonical credential home is ``~/.sweave/credentials.json``
(mode 0o600, best-effort). The opencode auth store is demoted from
*mechanism* to *import source*: keys are adopted once (boot +
explicit import), drift surfaces as pending-imports for the user to
approve, and providers authed with us but missing there are pushed
back (isolated copy always; real store with backup). API-key
providers sync bidirectionally; OAuth/token flows are
detect-only until probed and ruled (claude/codex-class).

File shape (secrets live ONLY in ``providers.<name>.key``)::

    {
      "providers": {
        "openrouter": {
          "type": "api_key",
          "key": "sk-...",
          "fingerprint": "sha256:…",
          "key_suffix": "…4242",
          "source": "imported:opencode | manual | env-adopted",
          "added_at": iso, "updated_at": iso
        },
        "github-copilot": {"type": "oauth", "adopted": False,
                           "reason": "flow pending"}
      },
      "adopted_from_opencode": {"openrouter": "sha256:…", ...}
    }

The ``adopted_from_opencode`` ledger is the drift detector: an
opencode entry whose provider is absent (or whose fingerprint
differs) is a pending-import candidate — never auto-merged after
the boot adopt. Public views (``list_public``) never carry secrets.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sweave.runtime.locking import atomic_write_json_sync

logger = logging.getLogger(__name__)

CREDENTIALS_FILENAME = "credentials.json"

#: Opencode entry types we adopt keys from (``{"key", "type": "api"}``).
#: OAuth entries (access/refresh) are detect-only: recorded, never copied.
ADOPTABLE_OPENCODE_TYPES = ("api", "api_key", "key")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def fingerprint_key(key: str) -> str:
    """Stable non-reversible id for a secret (ledger + drift compare)."""
    return "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def credentials_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".sweave" / CREDENTIALS_FILENAME


def opencode_auth_paths(home: Path | None = None) -> list[Path]:
    """Auth-store candidates, isolated copy first (mirrors providers.js)."""
    base = home or Path.home()
    xdg_data = os.environ.get("XDG_DATA_HOME") or str(
        base / ".local" / "share"
    )
    return [
        base / ".sweave" / "opencode-data" / "opencode" / "auth.json",
        Path(xdg_data) / "opencode" / "auth.json",
    ]


#: Providers needing no credential (local serves). User ruling:
#: these get special care later (reachability, not keys) — the
#: credentials system marks but never manages them. Custom
#: user-defined endpoints (arbitrary baseURLs in opencode.json)
#: are likewise out of scope: they stay configured where they are.
LOCAL_NO_KEY_PROVIDERS = frozenset({"ollama"})


#: Conventional env key names per provider (transport twin: the
#: `envKeys` column of the sidecar TABLE in
#: `sweave-engine/src/providers.js` — keep the two aligned).
PROVIDER_ENV_KEYS: dict[str, list[str]] = {
    "openrouter": ["OPENROUTER_API_KEY"],
    "zai": ["ZAI_API_KEY", "Z_AI_API_KEY"],
    "gmicloud": ["GMI_API_KEY"],
    "nvidia": ["NVIDIA_API_KEY"],
    "opencode-go": ["OPENCODE_GO_API_KEY"],
    "opencode": ["OPENCODE_API_KEY"],
}


def _explicit_env_key(provider: str) -> str | None:
    """The `SWEAVE_ENGINE_KEY_*` override (highest precedence tier)."""
    mangled = "".join(
        ch.upper() if ch.isalnum() else "_" for ch in provider
    )
    value = os.environ.get(f"SWEAVE_ENGINE_KEY_{mangled}")
    return value if value else None


def credential_source(provider: str, store: CredentialStore) -> str | None:
    """Where a provider's credential would come from today.

    Tiers: ``env`` (explicit override or conventional key) →
    ``sweave`` (our store) → ``opencode-legacy`` (adoptable key in
    their store, not yet ours) → None. ``None`` means the next turn
    fails loud with ``auth_missing``.
    """
    mangled_envs = [
        f"SWEAVE_ENGINE_KEY_{''.join(ch.upper() if ch.isalnum() else '_' for ch in provider)}",
        *PROVIDER_ENV_KEYS.get(provider, []),
    ]
    if any(os.environ.get(name) for name in mangled_envs):
        return "env"
    if store.get_key(provider):
        return "sweave"
    for path in opencode_auth_paths():
        entry = read_opencode_store(path).get(provider)
        ok, _ = CredentialStore._adoptable(provider, entry)
        if ok:
            return "opencode-legacy"
    return None


def read_opencode_store(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_opencode_store(path: Path, data: dict[str, Any], *, backup: bool = True) -> None:
    """Write an opencode auth store, preserving unknown fields.

    The store is another app's private file (wire-drift applies to
    its format): we merge entry-by-entry, never rewrite from
    scratch, and keep a ``.sweave-bak`` copy when asked.
    """
    if backup and path.is_file():
        try:
            backup_path = path.with_name(path.name + ".sweave-bak")
            backup_path.write_bytes(path.read_bytes())
        except OSError as e:  # noqa: BLE001 — backup is courtesy, not gate
            logger.warning("credentials: auth backup failed for %s: %s", path, e)
    atomic_write_json_sync(path, data)


class CredentialStore:
    """Sweave-owned credential file (0600, atomic writes)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or credentials_path()
        self._data: dict[str, Any] | None = None

    # -- persistence ----------------------------------------------------

    def load(self) -> dict[str, Any]:
        if self._data is None:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                data = raw if isinstance(raw, dict) else {}
            except (OSError, ValueError):
                data = {}
            data.setdefault("providers", {})
            data.setdefault("adopted_from_opencode", {})
            if not isinstance(data["providers"], dict):
                data["providers"] = {}
            if not isinstance(data["adopted_from_opencode"], dict):
                data["adopted_from_opencode"] = {}
            self._data = data
        return self._data

    def _save(self) -> None:
        assert self._data is not None
        atomic_write_json_sync(self.path, self._data)
        try:
            os.chmod(self.path, 0o600)
        except OSError:  # noqa: BLE001 — best-effort on Windows
            pass

    # -- CRUD (ours only) -------------------------------------------------

    def get_key(self, provider: str) -> str | None:
        entry = self.load()["providers"].get(provider)
        if isinstance(entry, dict) and entry.get("type") == "api_key":
            key = entry.get("key")
            return key if isinstance(key, str) and key else None
        return None

    def set_api_key(
        self, provider: str, key: str, *, source: str = "manual"
    ) -> dict[str, Any]:
        """Store (or rotate) an API key. Rotation pushes on next sync."""
        if not isinstance(key, str) or not key.strip():
            raise ValueError("key must be a non-empty string")
        now = _now_iso()
        providers = self.load()["providers"]
        entry = providers.get(provider)
        added_at = (
            entry.get("added_at", now)
            if isinstance(entry, dict)
            else now
        )
        providers[provider] = {
            "type": "api_key",
            "key": key.strip(),
            "fingerprint": fingerprint_key(key.strip()),
            "key_suffix": key.strip()[-4:],
            "source": source,
            "added_at": added_at,
            "updated_at": now,
        }
        self._save()
        return self.public_entry(provider)

    def delete(self, provider: str) -> bool:
        """Delete OUR copy only — opencode copies are left alone (never
        destroy access elsewhere silently)."""
        providers = self.load()["providers"]
        if provider not in providers:
            return False
        del providers[provider]
        self._save()
        return True

    def public_entry(self, provider: str) -> dict[str, Any] | None:
        entry = self.load()["providers"].get(provider)
        if not isinstance(entry, dict):
            return None
        return {
            "provider": provider,
            "type": entry.get("type"),
            "adopted": entry.get("adopted", True),
            "reason": entry.get("reason"),
            "fingerprint": entry.get("fingerprint"),
            "key_suffix": entry.get("key_suffix"),
            "source": entry.get("source"),
            "added_at": entry.get("added_at"),
            "updated_at": entry.get("updated_at"),
        }

    def list_public(self) -> list[dict[str, Any]]:
        return [
            self.public_entry(name)
            for name in sorted(self.load()["providers"])
            if self.public_entry(name) is not None
        ]

    # -- adopt / drift (opencode store is the import source) ---------------

    @staticmethod
    def _adoptable(name: str, entry: Any) -> tuple[bool, str]:
        """Classify one opencode entry: adoptable key, or why not."""
        if not isinstance(entry, dict):
            return False, "unshaped entry"
        key = entry.get("key")
        if isinstance(key, str) and key.strip():
            return True, ""
        if entry.get("type") == "oauth" or "refresh" in entry:
            return False, "oauth flow pending (detect-only)"
        return False, "no key material"

    def adopt(
        self, store: dict[str, Any], *, force: bool = False
    ) -> dict[str, list]:
        """Adopt adoptable keys from an opencode store dict.

        Idempotent: re-adopting an unchanged key is a no-op (ledger
        compare). Ours-is-truth: an existing api_key entry is never
        overwritten unless ``force`` (the explicit user-approved
        import path) — rotation flows ours→theirs via push, never
        the reverse unasked. Records fingerprints so later drift is
        detectable. Returns ``{"adopted": [...], "skipped":
        [{provider, reason}]}``.
        """
        data = self.load()
        adopted: list[str] = []
        skipped: list[dict[str, str]] = []
        for name, entry in store.items():
            ok, reason = self._adoptable(name, entry)
            if not ok:
                if reason.startswith("oauth"):
                    data["providers"].setdefault(
                        name,
                        {
                            "type": "oauth",
                            "adopted": False,
                            "reason": "oauth flow pending (detect-only)",
                            "added_at": _now_iso(),
                        },
                    )
                    skipped.append({"provider": name, "reason": reason})
                elif isinstance(entry, dict):
                    skipped.append({"provider": name, "reason": reason})
                continue
            key = entry["key"].strip()
            fp = fingerprint_key(key)
            own = data["providers"].get(name)
            if (
                isinstance(own, dict)
                and own.get("type") == "api_key"
                and not force
            ):
                # Ours is truth: converge the ledger when fingerprints
                # agree, otherwise leave ours for push to reconcile.
                if own.get("fingerprint") == fp:
                    data["adopted_from_opencode"][name] = fp
                continue
            if data["adopted_from_opencode"].get(name) == fp and (
                data["providers"].get(name) or {}
            ).get("fingerprint") == fp:
                continue  # already adopted, unchanged
            now = _now_iso()
            providers = data["providers"]
            added_at = (
                providers[name].get("added_at", now)
                if isinstance(providers.get(name), dict)
                else now
            )
            providers[name] = {
                "type": "api_key",
                "key": key,
                "fingerprint": fp,
                "key_suffix": key[-4:],
                "source": "imported:opencode",
                "added_at": added_at,
                "updated_at": now,
            }
            data["adopted_from_opencode"][name] = fp
            adopted.append(name)
        if adopted:
            self._save()
        elif any(
            isinstance(v, dict) and v.get("type") == "oauth"
            for v in data["providers"].values()
        ):
            # OAuth markers are informational — persist cheaply.
            self._save()
        return {"adopted": sorted(adopted), "skipped": skipped}

    def pending_imports(self, store: dict[str, Any]) -> list[dict[str, str]]:
        """Opencode entries needing a user-approved import.

        A provider is pending when absent from the ledger or its
        fingerprint differs (added or rotated over there). OAuth
        entries surface as pending with their flow reason — the UI
        explains instead of silently skipping.
        """
        data = self.load()
        ledger = data["adopted_from_opencode"]
        ours = data["providers"]
        out: list[dict[str, str]] = []
        for name, entry in store.items():
            if not isinstance(entry, dict):
                continue
            ok, reason = self._adoptable(name, entry)
            if ok:
                fp = fingerprint_key(entry["key"].strip())
                if ledger.get(name) == fp:
                    continue  # adopted, unchanged
                own = ours.get(name)
                if (
                    isinstance(own, dict)
                    and own.get("type") == "api_key"
                    and own.get("fingerprint") == fp
                ):
                    continue  # ours, pushed there — not new
                out.append(
                    {
                        "provider": name,
                        "reason": "rotated in opencode"
                        if name in ledger
                        else "new in opencode",
                    }
                )
            elif reason.startswith("oauth"):
                marker = data["providers"].get(name)
                if not (isinstance(marker, dict) and marker.get("type") == "oauth"):
                    out.append({"provider": name, "reason": reason})
        return sorted(out, key=lambda r: r["provider"])

    def push_candidates(self, store: dict[str, Any]) -> list[str]:
        """OUR api_key providers safe to write over there.

        Ledger-aware (2026-09-14 fix: the naive differ-push
        overwrote a user rotation made in the opencode TUI with our
        stale copy on the next boot). For ours O, theirs T, ledger L:
        * O == T → converged, nothing to do.
        * T missing/unshaped → additive push (nothing destroyed).
        * L == T → they hold the last converged state; WE rotated →
          push O.
        * L == O (they rotated) or anything else (conflict) → NO
          push; the drift surfaces via :meth:`pending_imports` for
          the user to resolve (import or keep-mine). Never destroy
          access elsewhere unasked.
        OAuth markers are never pushed.
        """
        data = self.load()
        ledger = data["adopted_from_opencode"]
        out: list[str] = []
        for name, entry in data["providers"].items():
            if not isinstance(entry, dict) or entry.get("type") != "api_key":
                continue
            key = entry.get("key")
            if not isinstance(key, str) or not key:
                continue
            ours_fp = entry.get("fingerprint")
            theirs = store.get(name)
            their_fp = (
                fingerprint_key(theirs["key"].strip())
                if isinstance(theirs, dict)
                and isinstance(theirs.get("key"), str)
                and theirs["key"].strip()
                else None
            )
            if their_fp == ours_fp:
                continue  # converged
            if their_fp is None:
                out.append(name)  # additive: nothing destroyed
                continue
            if ledger.get(name) == their_fp:
                out.append(name)  # we rotated; they hold last-converged
                continue
            # They rotated (ledger == ours) or conflict: pending, no push.
        return sorted(out)

    def mark_pushed(self, provider: str) -> None:
        """Record that theirs now holds our key (post-push converge)."""
        data = self.load()
        entry = data["providers"].get(provider)
        if isinstance(entry, dict) and entry.get("fingerprint"):
            data["adopted_from_opencode"][provider] = entry["fingerprint"]
            self._save()

    def render_push_entry(self, provider: str) -> dict[str, Any] | None:
        """The ``{"key", "type"}`` shape opencode stores natively."""
        entry = self.load()["providers"].get(provider)
        if (
            not isinstance(entry, dict)
            or entry.get("type") != "api_key"
            or not isinstance(entry.get("key"), str)
        ):
            return None
        return {"key": entry["key"], "type": "api"}


def sync_with_opencode(
    store: CredentialStore | None = None,
) -> dict[str, Any]:
    """Adopt-in + push-out across every existing opencode store.

    Boot + explicit-import path: adoptable keys flow INTO our store
    (ledgered, idempotent); our api_key providers are pushed back
    only where the ledger proves WE are the newer side (or theirs
    is missing) — merged entry-by-entry, backup kept. Their-side
    rotations are never overwritten; they surface as pending.
    Best-effort per path — one corrupt store never blocks the
    others. Returns counts for logging.
    """
    store = store or CredentialStore()
    adopted: list[str] = []
    skipped: list[dict[str, str]] = []
    pushed: dict[str, list[str]] = {}
    pending: list[dict[str, str]] = []
    seen_pending: set[str] = set()
    for path in opencode_auth_paths():
        if not path.is_file():
            continue
        current = read_opencode_store(path)
        try:
            result = store.adopt(current)
        except Exception as e:  # noqa: BLE001 — adopt never breaks boot
            logger.warning("credentials: adopt failed for %s: %s", path, e)
            continue
        adopted.extend(n for n in result["adopted"] if n not in adopted)
        skipped.extend(result["skipped"])
        try:
            candidates = store.push_candidates(current)
        except Exception as e:  # noqa: BLE001
            logger.warning("credentials: push-scan failed for %s: %s", path, e)
            continue
        if candidates:
            merged = dict(current)
            for name in candidates:
                rendered = store.render_push_entry(name)
                if rendered is None:
                    continue
                prior = merged.get(name)
                merged[name] = (
                    {**prior, **rendered}
                    if isinstance(prior, dict)
                    else dict(rendered)
                )
            try:
                write_opencode_store(path, merged, backup=True)
                for name in candidates:
                    try:
                        store.mark_pushed(name)
                    except Exception:  # noqa: BLE001 — ledger lag is harmless
                        pass
                pushed[str(path)] = candidates
            except Exception as e:  # noqa: BLE001
                logger.warning("credentials: push failed for %s: %s", path, e)
        try:
            for item in store.pending_imports(read_opencode_store(path)):
                if item["provider"] not in seen_pending:
                    seen_pending.add(item["provider"])
                    pending.append(item)
        except Exception as e:  # noqa: BLE001
            logger.warning("credentials: drift-scan failed for %s: %s", path, e)
    return {
        "adopted": sorted(adopted),
        "skipped": skipped,
        "pushed": pushed,
        "pending": sorted(pending, key=lambda r: r["provider"]),
    }
