"""Automated per-part version bumps (versioning ruling 2026-09-20).

Mechanics (this script): ``--part {backend,engine,web} --level
{major,minor,patch}`` reads the part file, computes the next semver,
writes it back, and with ``--tag`` creates the ``<part>-vX.Y.Z`` git
tag. ``--dry-run`` prints without writing. ``--root`` overrides the
repo root (tests point it at tmp copies).

Judgment (NOT this script): which level. Bump rules, locked with the
ruling — patch = fixes / hygiene batches / behavior-preserving
changes; minor = milestone plan done, new tool or endpoint, on-disk
format change, any behavior models must adapt to; major = reserved
for the 1.0 public cut (standing proposal, not locked). Contracts
(engine protocol, Delegation schema) are NEVER bumped here: protocol
bumps need a user-locked ruling, the schema bumps with its own
migration inside the shipping change.

Tag format: ``<part>-v<semver>`` (e.g. ``engine-v0.2.0``) — one tag
per bumped part, unbumped parts get no tag. Never force-moves a tag.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sweave.version import (  # noqa: E402
    PART_FILES,
    engine_version,
    parse_semver,
    web_version,
)

_READERS = {
    "backend": None,  # pyproject needs the surgical line edit below
    "engine": engine_version,
    "web": web_version,
}

_LEVELS = ("major", "minor", "patch")


def next_version(current: str, level: str) -> str:
    """Next semver triple (any pre-release suffix is dropped — a bump
    always ships a clean triple)."""
    base = re.match(r"^(\d+)\.(\d+)\.(\d+)", current.strip() or "")
    if not base:
        raise ValueError(f"not a semver triple: {current!r}")
    major, minor, patch = (int(base.group(i)) for i in (1, 2, 3))
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    if level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"unknown level: {level!r} (want one of {', '.join(_LEVELS)})")


def _read_current(part: str, root: Path) -> str:
    if part == "backend":
        from sweave.version import _pyproject_version

        current = _pyproject_version(root / PART_FILES[part])
    else:
        current = _READERS[part](root=root)
    if current is None:
        raise ValueError(f"cannot read current {part} version from {root}")
    return current


def _write_version(part: str, root: Path, new: str) -> None:
    path = root / PART_FILES[part]
    if part == "backend":
        text = path.read_text(encoding="utf-8")
        updated, count = re.subn(
            r'(?m)^version\s*=\s*["\'][^"\']+["\']',
            f'version = "{new}"',
            text,
            count=1,
        )
        if count != 1:
            raise ValueError(f"no version line found in {path}")
        path.write_text(updated, encoding="utf-8")
        return
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} is not a JSON object")
    raw["version"] = new
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")


def _create_tag(root: Path, part: str, new: str) -> str:
    tag = f"{part}-v{new}"
    proc = subprocess.run(
        ["git", "tag", tag],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git tag {tag} failed: {(proc.stderr or proc.stdout).strip()}")
    return tag


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bump one part version.")
    parser.add_argument("--part", required=True, choices=sorted(PART_FILES))
    parser.add_argument("--level", required=True, choices=_LEVELS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--tag", action="store_true", help="create the <part>-vX.Y.Z tag")
    parser.add_argument("--root", default=None, help="repo root (default: script's parent)")
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else Path(__file__).resolve().parents[1]
    try:
        current = _read_current(args.part, root)
        if parse_semver(current) is None:
            raise ValueError(f"current {args.part} version is not semver: {current!r}")
        new = next_version(current, args.level)
        if args.dry_run:
            print(f"{args.part} {current} -> {new} (dry run, nothing written)")
            return 0
        _write_version(args.part, root, new)
        line = f"{args.part} {current} -> {new}"
        if args.tag:
            line += f" [{_create_tag(root, args.part, new)}]"
        print(line)
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"bump_versions: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
