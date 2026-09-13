"""Zen live probe driver: real-store key, free model, $0, no spam.

Reads the REAL opencode store's key into process env (never printed),
then runs the shared probe main. Temporary driver until the boot sync
converges the stores (after that the plain Go probe suffices).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["ENGINE_GO_PROBE_MODEL"] = os.environ.get(
    "ENGINE_GO_PROBE_MODEL", "opencode/deepseek-v4-flash-free"
)

from sweave.credentials import opencode_auth_paths, read_opencode_store  # noqa: E402

paths = opencode_auth_paths()
if len(paths) > 1:
    entry = read_opencode_store(paths[1]).get("opencode")
    if isinstance(entry, dict) and entry.get("key"):
        os.environ["SWEAVE_ENGINE_KEY_OPENCODE"] = entry["key"]

import runpy  # noqa: E402

runpy.run_path(
    str(Path(__file__).resolve().parent / "engine_go_live_probe.py"),
    run_name="__main__",
)
