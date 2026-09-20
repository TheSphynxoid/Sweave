"""Per-part version matrix (user ruling 2026-09-20: per-part + contracts).

Sweave ships three parts with independent speeds: the Python backend
(``pyproject.toml``), the Node sidecar (``sweave-engine/package.json``),
and the web UI (``sweave-web/package.json``). Each carries its own
semver; compatibility is gated by the CONTRACT versions, not the part
numbers: the engine protocol (``sweave/engine/protocol.py``,
user-locked bumps, loud mismatch at connect), the Delegation schema
(``SCHEMA_VERSION``), and the frozen trace vocabulary.

This module only READS. Bumps go through ``scripts/bump_versions.py``
(part + level in, files + optional tag out) so no version string is
ever typed by hand twice. ``GET /api/version`` and ``sweave version``
both render from here — one reader, every surface.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

#: Accept ``x.y.z`` with optional pre-release/build metadata (which
#: never compares — presence is informational, not ordering).
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$"
)

#: Part -> version-file mapping (paths relative to the repo root).
PART_FILES: dict[str, str] = {
    "backend": "pyproject.toml",
    "engine": "sweave-engine/package.json",
    "web": "sweave-web/package.json",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_semver(text: Any) -> str | None:
    """Return *text* when it is a valid semver string, else ``None``."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    return text if SEMVER_RE.match(text) else None


def _package_json_version(path: Path) -> str | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return parse_semver(raw.get("version"))


def _pyproject_version(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'(?m)^version\s*=\s*["\']([^"\']+)["\']', text)
    if not match:
        return None
    return parse_semver(match.group(1))


def backend_version(*, root: Path | None = None) -> str | None:
    """Backend part version. Source tree (``pyproject.toml`` beside the
    package) wins when present — the running code IS the repo; the
    installed distribution metadata is the fallback for
    site-packages runs (where no source file exists)."""
    base = root or _repo_root()
    if root is None:
        candidate = Path(__file__).resolve().parent.parent / PART_FILES["backend"]
        if candidate.is_file():
            parsed = _pyproject_version(candidate)
            if parsed is not None:
                return parsed
    else:
        parsed = _pyproject_version(base / PART_FILES["backend"])
        if parsed is not None:
            return parsed
    try:
        from importlib.metadata import version as _dist_version

        return parse_semver(_dist_version("sweave"))
    except Exception:  # noqa: BLE001 — not installed / no metadata
        return None


def engine_version(*, root: Path | None = None) -> str | None:
    """Engine part version (the sidecar's ``package.json``)."""
    base = root or _repo_root()
    return _package_json_version(base / PART_FILES["engine"])


def web_version(*, root: Path | None = None) -> str | None:
    """Web part version (``sweave-web/package.json`` — the source the
    backend builds ``dist/`` from)."""
    base = root or _repo_root()
    return _package_json_version(base / PART_FILES["web"])


def get_part_versions(*, root: Path | None = None) -> dict[str, str | None]:
    """``{part: version}`` for the three shippable parts (``None``
    when a file is missing/unparseable — never raises)."""
    return {
        "backend": backend_version(root=root),
        "engine": engine_version(root=root),
        "web": web_version(root=root),
    }


def get_version_matrix(*, root: Path | None = None) -> dict[str, Any]:
    """Full matrix for ``GET /api/version`` + ``sweave version``.

    Parts identify; CONTRACTS gate: the engine protocol version the
    backend requires, and the Delegation schema the stores persist.
    A ``None`` part means "unreadable here" (source running without
    the file, never a crash).
    """
    from sweave.engine.protocol import PROTOCOL_VERSION
    from sweave.runtime.delegation_store import SCHEMA_VERSION

    return {
        **get_part_versions(root=root),
        "engine_protocol": str(PROTOCOL_VERSION),
        "delegation_schema": int(SCHEMA_VERSION),
    }
