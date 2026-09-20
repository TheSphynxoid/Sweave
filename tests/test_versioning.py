"""Per-part versioning (ruling 2026-09-20: per-part + contracts).

Parts identify (backend/engine/web semver in their own files);
contracts gate (protocol + Delegation schema). Bumps go through
``scripts/bump_versions.py`` — these tests pin the reader, the
matrix shape, and the script mechanics (dry-run + real bump on tmp
copies + tag format in a tmp git repo).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from sweave.version import (
    get_part_versions,
    get_version_matrix,
    parse_semver,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_part_files_are_valid_semver():
    parts = get_part_versions()
    assert set(parts) == {"backend", "engine", "web"}
    for part, version in parts.items():
        assert version is not None, f"{part} version unreadable"
        assert parse_semver(version) == version, f"{part} not semver: {version!r}"


def test_parse_semver_rejects_garbage():
    for bad in (None, "", "1.2", "v1.2.3", "1.2.3.4", "1.02.3", "latest", 123):
        assert parse_semver(bad) is None, bad
    assert parse_semver("0.1.0") == "0.1.0"
    assert parse_semver("1.0.0-rc.1") == "1.0.0-rc.1"


def test_matrix_carries_contracts():
    from sweave.engine.protocol import PROTOCOL_VERSION
    from sweave.runtime.delegation_store import SCHEMA_VERSION

    matrix = get_version_matrix()
    assert matrix["engine_protocol"] == str(PROTOCOL_VERSION)
    assert matrix["delegation_schema"] == int(SCHEMA_VERSION)


def test_version_endpoint_shape():
    import asyncio

    from sweave.web.routers.version import version_matrix

    body = asyncio.run(version_matrix())
    assert set(body) >= {"backend", "engine", "web", "engine_protocol", "delegation_schema"}


def test_cli_version_command_registered():
    from typer.testing import CliRunner

    from sweave.cli.main import app

    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0, result.output
    assert "backend" in result.output and "engine" in result.output


def _seed_tmp_root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "sweave-engine").mkdir(parents=True)
    (root / "sweave-web").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "sweave"\nversion = "0.1.0"\n', encoding="utf-8")
    (root / "sweave-engine" / "package.json").write_text(
        json.dumps({"name": "sweave-engine", "version": "0.1.0"}, indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "sweave-web" / "package.json").write_text(
        json.dumps({"name": "sweave-web", "version": "0.1.0"}, indent=2) + "\n",
        encoding="utf-8",
    )
    return root


def _bump(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python", str(REPO_ROOT / "scripts" / "bump_versions.py"), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_bump_dry_run_writes_nothing(tmp_path: Path):
    root = _seed_tmp_root(tmp_path)
    proc = _bump("--part", "engine", "--level", "minor", "--dry-run", "--root", str(root))
    assert proc.returncode == 0, proc.stderr
    assert "engine 0.1.0 -> 0.2.0" in proc.stdout
    assert json.loads((root / "sweave-engine" / "package.json").read_text())["version"] == "0.1.0"


def test_bump_levels_and_pyproject(tmp_path: Path):
    root = _seed_tmp_root(tmp_path)
    assert _bump("--part", "backend", "--level", "patch", "--root", str(root)).returncode == 0
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.1.1"' in text and text.count("version") == 1
    assert _bump("--part", "web", "--level", "major", "--root", str(root)).returncode == 0
    assert json.loads((root / "sweave-web" / "package.json").read_text())["version"] == "1.0.0"


def test_bump_tag_format_in_tmp_repo(tmp_path: Path):
    root = _seed_tmp_root(tmp_path)
    subprocess.run(["git", "init"], cwd=str(root), capture_output=True, timeout=60)
    subprocess.run(["git", "add", "-A"], cwd=str(root), capture_output=True, timeout=60)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed"],
        cwd=str(root), capture_output=True, timeout=60,
    )
    proc = _bump("--part", "engine", "--level", "minor", "--root", str(root), "--tag")
    assert proc.returncode == 0, proc.stderr
    assert "[engine-v0.2.0]" in proc.stdout
    tags = subprocess.run(
        ["git", "tag"], cwd=str(root), capture_output=True, text=True, timeout=60
    ).stdout.split()
    assert "engine-v0.2.0" in tags
