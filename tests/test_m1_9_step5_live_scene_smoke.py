"""M1.9 step 5 smoke test: the self-hosting live scene runs without
crashing.

The full live scene is a manual / scripted artifact
(``scripts/m1_9_self_hosting_scene.py``); the test pins the entry
point is importable + the no-op kill switch is honored. The scene
itself runs against a real server + an SWEAVE_MOCK_OPENCODE=1 stub,
which is the live-gate pattern M1.7-M1.8 established.

Hermeticity: this test does NOT spin up a real server; the kill
switch (``M1.9_DISABLE_LIVE_SCENE=1``) exercises the importable
script + the short-circuit branch.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_live_scene_script_is_importable():
    """The script imports without syntax errors or import-time
    crashes. Same import test pattern M1.6/M1.7/M1.8 used for
    the live scene scripts."""
    script = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "m1_9_self_hosting_scene.py"
    )
    assert script.exists(), f"missing {script}"
    # We import the script via runpy (don't execute main()).
    import importlib.util
    spec = importlib.util.spec_from_file_location("m1_9_scene", script)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # main exists + is callable
    assert callable(mod.main)


def test_live_scene_short_circuit_when_disabled(tmp_path):
    """``M1.9_DISABLE_LIVE_SCENE=1`` short-circuits the scene to a
    no-op. The script exits 0 and prints the disabled marker."""
    env = os.environ.copy()
    env["M1.9_DISABLE_LIVE_SCENE"] = "1"
    # Use the repo root as cwd (where the script's sys.path tweak
    # prepends to find ``sweave``).
    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "m1_9_self_hosting_scene.py")],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "M1.9 live scene disabled" in result.stdout