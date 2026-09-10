"""M1.9 step 2 orchestrator: chat streaming + session picker.

The actual reducer tests live in vitest
(``sweave-web/src/pages/__tests__/chat.reducer.test.ts``).
This test pins that the vitest suite still runs cleanly + the
chat surface is in the right place after step 2.
"""

from __future__ import annotations

import shutil as _shutil
import subprocess
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SWEAVE_WEB = _REPO_ROOT / "sweave-web"


def _npm() -> str:
    return "npm.cmd" if _shutil.which("npm.cmd") else "npm"


def test_chat_surface_files_present():
    """Step 2 ships: a real Chat page (not the step-1 placeholder)
    + a chat reducer (pure-function streaming patch logic) +
    a session picker + a composer."""
    expected = [
        "src/pages/Chat.tsx",  # upgraded from placeholder
        "src/pages/chat/reducer.ts",
        "src/pages/chat/SessionPicker.tsx",
        "src/pages/chat/Composer.tsx",
        "src/pages/__tests__/chat.reducer.test.ts",
    ]
    for name in expected:
        path = _SWEAVE_WEB / name
        assert path.exists(), f"missing {path}"


def test_chat_vitest_suite_passes():
    """The chat reducer tests + the design-system tests pass via
    ``npm test``."""
    proc = subprocess.run(
        [_npm(), "test"],
        cwd=str(_SWEAVE_WEB),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"vitest failed:\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    out = (proc.stdout + proc.stderr).lower()
    assert "passed" in out, "vitest should report a 'passed' summary"