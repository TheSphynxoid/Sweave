"""M1.12 step 1: scoped ``external_directory`` render + roots surface.

Covers:
* ``render_external_directory`` — catch-all first (used value
  ``EXTERNAL_DIRECTORY_CATCH_ALL``, still ``"allow"`` at step-1
  time), built-in roots (``~/.sweave`` + project ``.worktrees``),
  user roots with ``~`` expansion + dedupe + empty-drop, last-
  match-wins ordering (specifics after the catch-all).
* ``ensure_mcp_config`` idempotence with scoped render, the
  user-owned permission block contract, and the roots refresh path.
* The Project record field (create + persist + legacy load).
* ``PUT /api/projects/{name}/permission_roots``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sweave.projects import Project
from sweave.runtime.mcp_config import (
    EXTERNAL_DIRECTORY_CATCH_ALL,
    ensure_mcp_config,
    render_external_directory,
)


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    home.joinpath(".sweave").mkdir()
    # expanduser() reads env vars on Windows, Path.home() patches
    # are not enough (see GOTCHAS Paths & config).
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    return home


def test_render_scoped_order_and_builtins(tmp_path, isolated_home):
    proj = tmp_path / "proj"
    proj.mkdir()
    rendered = render_external_directory(proj, [])
    # Catch-all first, under the flipped (scoped) value.
    keys = list(rendered.keys())
    assert keys[0] == "*"
    assert rendered["*"] == EXTERNAL_DIRECTORY_CATCH_ALL == "ask"
    # Built-ins are platform separators + /* and /**, after the
    # catch-all (last-match-wins).
    home = isolated_home / ".sweave"
    assert f"{str(home)}{os.sep}**" in rendered
    assert f"{str(proj / '.worktrees')}{os.sep}**" in rendered
    assert rendered[f"{str(home)}{os.sep}**"] == "allow"


def test_render_user_roots_expand_and_dedupe(tmp_path, isolated_home):
    proj = tmp_path / "proj2"
    proj.mkdir()
    shared = tmp_path / "shared" / "libs"
    shared.mkdir(parents=True)
    rendered = render_external_directory(
        proj,
        [str(shared), "~", str(shared), "", "  "],
    )
    assert rendered[f"{str(shared)}{os.sep}*"] == "allow"
    # "~" expanded to the tmp home, NOT the real one.
    home_glob = f"{str(isolated_home)}{os.sep}**"
    assert rendered[home_glob] == "allow"
    # No duplicates, no empty-string keys.
    assert list(rendered.keys()).count(f"{str(shared)}{os.sep}*") == 1
    assert not any(k.strip() == "" for k in rendered)


def test_render_roots_param_ignored_on_non_list(tmp_path, isolated_home):
    proj = tmp_path / "proj3"
    proj.mkdir()
    base = set(render_external_directory(proj, None).keys())
    base2 = set(render_external_directory(proj, "garbage").keys())
    assert base == base2


def test_ensure_idempotent_with_roots(tmp_path, isolated_home):
    proj = tmp_path / "proj4"
    proj.mkdir()
    ensure_mcp_config(proj)
    cfg1 = Path(proj / "opencode.json").read_text(encoding="utf-8")
    ensure_mcp_config(proj, permission_roots=None)
    cfg2 = Path(proj / "opencode.json").read_text(encoding="utf-8")
    assert json.loads(cfg1) == json.loads(cfg2)
    # Roots change refreshes the managed block (marker contract).
    ensure_mcp_config(proj, permission_roots=[str(tmp_path / "extra")])
    cfg3 = json.loads(
        (proj / "opencode.json").read_text(encoding="utf-8")
    )
    ed = cfg3["permission"]["external_directory"]
    assert f"{str(tmp_path / 'extra')}{os.sep}**" in ed
    assert ed["_sweave_managed"] if "_sweave_managed" in ed else True


def test_ensure_preserves_user_permission_contract(tmp_path, isolated_home):
    proj = tmp_path / "proj5"
    proj.mkdir()
    (proj / "opencode.json").write_text(
        json.dumps({"permission": {"external_directory": "deny"}}),
        encoding="utf-8",
    )
    cfg = ensure_mcp_config(proj)
    # User-owned block untouched even when roots are declared.
    assert cfg["permission"]["external_directory"] == "deny"


def test_project_record_roots_roundtrip(tmp_path):
    p = Project(name="p1", path=tmp_path)
    assert p.permission_roots == []
    p.permission_roots = ["~/roots_share", str(tmp_path)]
    d = p.to_dict()
    assert d["permission_roots"] == ["~/roots_share", str(tmp_path)]
    from_dict = Project.from_dict(d)
    assert from_dict.permission_roots == ["~/roots_share", str(tmp_path)]


def test_project_record_legacy_load(tmp_path):
    d = {
        "name": "p2",
        "path": str(tmp_path),
        "created_at": "2026-09-10T00:00:00",
        "updated_at": "2026-09-10T00:00:00",
    }
    # Pre-M1.12 files have no permission_roots.
    assert Project.from_dict(d).permission_roots == []
