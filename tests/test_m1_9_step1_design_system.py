"""M1.9 step 1 tests: design system + theme tokens.

The plan's Step 1: theme tokens (CSS variables porting v1's presets
+ custom-color support) + theme switcher with localStorage persistence.

The design system is plain TypeScript/CSS — no React, no DOM tests
needed. The tests below pin:

* Each preset (dark / light / dracula / nord / catppuccin) produces
  a valid CSS variable map (no missing keys, valid hex values).
* The custom-color override extends the active preset (the
  customization ruling from the plan: users can recolor without
  picking a fresh preset).
* The theme switcher's storage round-trip persists + restores
  the active theme name (the v1 parity + localStorage pin).

These run as Vitest unit tests (Node-side; no DOM, no React).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


_THIS = Path(__file__).resolve()
_REPO_ROOT = _THIS.parent.parent
_SWEAVE_WEB = _REPO_ROOT / "sweave-web"


# A small bridge: invoke the node-based vitest suite that the
# sweave-web test setup provides. The sweave-web project owns its
# test runner; this Python test invokes npm test (or vitest run)
# and asserts the result + parses JSON output for pass counts.


def _run_node_tests() -> dict:
    """Run the sweave-web vitest suite via ``npm test``.

    The sweave-web project owns its test runner; the npm script
    is the canonical entry point (defined in package.json as
    ``"test": "vitest run"``). On Windows, subprocess requires
    the ``.cmd`` extension for npm.
    """
    import shutil as _shutil
    npm_cmd = "npm.cmd" if _shutil.which("npm.cmd") else "npm"
    proc = subprocess.run(
        [npm_cmd, "test"],
        cwd=str(_SWEAVE_WEB),
        capture_output=True,
        text=True,
        timeout=300,
    )
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


# ---------------------------------------------------------------------------
# Design system tests (Node-side; mirror in sweave-web/src/lib/theme/__tests__)
# ---------------------------------------------------------------------------
#
# We test the design system at the source (where it lives) rather
# than in the Python test suite. The vitest runner is the source
# of truth. This Python test is the orchestrator: it runs the
# vitest suite + asserts the result + summarises.


def test_design_system_tests_pass():
    """All vitest unit tests in the theme module pass.

    The test definitions themselves live in
    ``sweave-web/src/lib/theme/__tests__/``; this test is the
    orchestrator that runs the suite + asserts zero failures. The
    design system is the source of truth for theme tokens (CSS
    variables) + the theme switcher (localStorage persistence);
    the Node-side tests verify the actual generated CSS variable
    map + persistence behaviour. The Python test is the gate."""
    result = _run_node_tests()
    assert result["returncode"] == 0, (
        f"npm test failed:\nstdout:\n{result['stdout']}\n"
        f"stderr:\n{result['stderr']}"
    )
    # Sanity: the test summary should mention tests passing.
    out = (result["stdout"] + result["stderr"]).lower()
    assert "passed" in out, (
        "vitest output should include a 'passed' summary"
    )


def test_design_system_module_is_pure_typescript():
    """The design system source lives in TypeScript; the test files
    end in ``.test.ts`` or ``.test.tsx``. The CI gate is
    ``tsc --noEmit`` (added in step 1.2)."""
    theme_dir = _SWEAVE_WEB / "src" / "lib" / "theme"
    assert theme_dir.exists(), f"missing {theme_dir}"
    # Tokens + switcher + barrel exist after step 1. Presets are
    # folded into tokens.ts (a single TokenMap per preset).
    expected = [
        "index.ts",
        "tokens.ts",
        "switcher.ts",
    ]
    for name in expected:
        path = theme_dir / name
        assert path.exists(), f"missing {path}"


def test_sweave_web_typechecks_clean():
    """``tsc --noEmit`` returns 0: no TS errors in the new shell.

    The new shell is the source of truth for the wave-1 UI. Any
    type error here blocks the v4 cutover (step 4); this test is
    the cheap CI gate. The M1.9 step 1 plan calls for ``tsc``
    to be green as part of the step 1 gate.
    """
    import shutil as _shutil
    npx_cmd = "npx.cmd" if _shutil.which("npx.cmd") else "npx"
    proc = subprocess.run(
        [npx_cmd, "tsc", "--noEmit"],
        cwd=str(_SWEAVE_WEB),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        f"tsc --noEmit failed:\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )


def test_sweave_web_build_succeeds():
    """``npm run build`` produces the dist/ artefact the flag-day
    cutover (step 4) will mount. A failed build blocks cutover;
    a green build is the wave-1 shape that's safe to ship.
    """
    import shutil as _shutil
    npm_cmd = "npm.cmd" if _shutil.which("npm.cmd") else "npm"
    proc = subprocess.run(
        [npm_cmd, "run", "build"],
        cwd=str(_SWEAVE_WEB),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"npm run build failed:\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    # The dist/ artefact must exist after the build.
    dist = _SWEAVE_WEB / "dist"
    assert dist.exists(), "dist/ missing after build"
    assert (dist / "index.html").exists(), "dist/index.html missing"