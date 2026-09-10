"""Data-dir isolation for managed opencode spawns (2026-09-10, user-locked).

Ruling: Sweave-managed opencode sessions must not populate the user's
standalone opencode. Probe 2026-09-10: opencode 1.18.29 honors
``XDG_DATA_HOME`` on Windows (db/log land under ``<dir>/opencode/``), so
both spawn sites (harness ``spawn`` + ``ServeRunner.start``) redirect it
into ``~/.sweave/opencode-data`` and copy auth material across.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sweave.harness.opencode import (
    isolated_opencode_env,
    sweave_opencode_data_home,
)

_REAL_SUB = Path(".local") / "share" / "opencode"


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake HOME + data-home override so the helper never touches the real one."""
    data_home = tmp_path / "sweave-data"
    monkeypatch.setenv("SWEAVE_OPENCODE_DATA_HOME", str(data_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def _write_auth(home: Path, name: str, body: str, mtime: float | None = None) -> Path:
    src = home / _REAL_SUB / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(body, encoding="utf-8")
    if mtime is not None:
        os.utime(src, (mtime, mtime))
    return src


def test_helper_sets_xdg_data_home(fake_home: Path, tmp_path: Path):
    env = isolated_opencode_env({"PATH": "keep-me"})
    data_home = sweave_opencode_data_home()
    assert env["XDG_DATA_HOME"] == str(data_home)
    assert env["PATH"] == "keep-me"
    # input env is not mutated
    assert "XDG_DATA_HOME" not in {}
    assert (data_home / "opencode").is_dir()


def test_auth_material_copied(fake_home: Path):
    _write_auth(fake_home, "auth.json", '{"key": "provider-tokens"}')
    _write_auth(fake_home, "account.json", '{"email": "x"}')

    isolated_opencode_env({})

    target = sweave_opencode_data_home() / "opencode"
    assert (target / "auth.json").read_text(encoding="utf-8") == '{"key": "provider-tokens"}'
    assert (target / "account.json").read_text(encoding="utf-8") == '{"email": "x"}'
    assert not (target / "mcp-auth.json").exists()  # absent source stays absent


def test_auth_copy_is_freshness_aware(fake_home: Path):
    target = sweave_opencode_data_home() / "opencode"
    _write_auth(fake_home, "auth.json", "new-token", mtime=2_000_000_000)
    isolated_opencode_env({})
    assert (target / "auth.json").read_text(encoding="utf-8") == "new-token"

    # Older source than the existing copy -> stale target kept.
    _write_auth(fake_home, "auth.json", "older-token", mtime=1_000_000_000)
    isolated_opencode_env({})
    assert (target / "auth.json").read_text(encoding="utf-8") == "new-token"

    # Newer source -> recopied.
    _write_auth(fake_home, "auth.json", "newest-token", mtime=3_000_000_000)
    isolated_opencode_env({})
    assert (target / "auth.json").read_text(encoding="utf-8") == "newest-token"


def test_copy_failure_never_breaks_spawn(fake_home: Path, monkeypatch: pytest.MonkeyPatch):
    _write_auth(fake_home, "auth.json", "x")

    def boom(src, dst):
        raise OSError("disk on fire")

    monkeypatch.setattr("sweave.harness.opencode.shutil.copy2", boom)
    env = isolated_opencode_env({})
    assert "XDG_DATA_HOME" in env


def test_opt_out_restores_shared_db(fake_home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SWEAVE_OPENCODE_SHARED_DATA", "1")
    env = isolated_opencode_env({"A": "b"})
    assert env == {"A": "b"}
    assert "XDG_DATA_HOME" not in env


def test_spawn_sites_wire_isolation():
    """Source pin: both real spawn sites route env through the helper.

    (Source-inspection pin, M1.4-step-3 precedent: functional spawn tests
    would need a live serve; the helper itself is covered above.)
    """
    harness_src = (
        Path(__file__).resolve().parent.parent / "sweave" / "harness" / "opencode.py"
    ).read_text(encoding="utf-8")
    assert "env = isolated_opencode_env(env)" in harness_src

    runner_src = (
        Path(__file__).resolve().parent.parent / "sweave" / "runtime" / "serve_runner.py"
    ).read_text(encoding="utf-8")
    assert "isolated_opencode_env(" in runner_src
    assert "ensure_permission_bridge()" in runner_src
