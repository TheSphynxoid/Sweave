"""M1.9 step 3 orchestrator: children live tree + detail view.

The tree builder is tested in vitest
(``sweave-web/src/pages/children/__tests__/tree.test.ts``).
This test pins the vitest suite still runs cleanly + the
Children surface is in the right place after step 3.
"""

from __future__ import annotations

import shutil as _shutil
import subprocess
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SWEAVE_WEB = _REPO_ROOT / "sweave-web"


def _npm() -> str:
    return "npm.cmd" if _shutil.which("npm.cmd") else "npm"


def test_children_surface_files_present():
    expected = [
        "src/pages/Children.tsx",  # upgraded from placeholder
        "src/pages/children/tree.ts",
        "src/pages/children/LiveTree.tsx",
        "src/pages/children/DetailView.tsx",
        "src/pages/children/__tests__/tree.test.ts",
    ]
    for name in expected:
        path = _SWEAVE_WEB / name
        assert path.exists(), f"missing {path}"


def test_children_vitest_suite_passes():
    proc = subprocess.run(
        [_npm(), "test"],
        cwd=str(_SWEAVE_WEB),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"vitest failed:\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )