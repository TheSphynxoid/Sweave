"""TOOL_CARDS 2b-backend tests: write old-capture + the overwrite wire.

Contract (plan sec.3 step 1 write row + user-locked 2026-09-17
option-A fold-in): the engine writePath captures the PRE-WRITE bytes
best-effort (small file cap 200K, fail-safe, never fails the turn),
toolStateExtra emits {mode:'overwrite' when old present, linesRemoved,
old_capture} else {mode:'create'}, and the Python build_tool_detail
projects the engine-carried extras FIRST (falling back to the
input.old path for pre-state rows) - one wire shape for the frontend
to bind.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sweave.chat.tools import WRITE_OLD_CAPTURE_CHARS, compact_tool_record


def _rec_write(state: dict) -> dict:
    ev = dict(state)
    ev["tool"] = "write"
    ev["callID"] = "c-1"
    return compact_tool_record(ev)


def test_detail_write_overwrite_from_engine_state():
    row = _rec_write({
        "status": "completed",
        "input": {"filePath": "f.js", "content": "xx" + chr(10) + "yy"},
        "mode": "overwrite",
        "linesAdded": 2,
        "linesRemoved": 4,
        "old_capture": "aa" + chr(10) + "bb" + chr(10) + "cc" + chr(10),
        "preview": "xx",
        "output": "wrote f.js",
    })
    d = row["detail"]
    assert d["mode"] == "overwrite"
    assert d["linesRemoved"] == 4
    assert d["old_capture"] == "aa" + chr(10) + "bb" + chr(10) + "cc" + chr(10)
    assert d["linesAdded"] == 2
    assert d["preview"] == "xx"


def test_detail_write_create_from_engine_state():
    row = _rec_write({
        "status": "completed",
        "input": {"filePath": "g.js", "content": "fresh"},
        "mode": "create",
        "linesAdded": 1,
        "preview": "fresh",
        "output": "wrote g.js",
    })
    d = row["detail"]
    assert d["mode"] == "create"
    assert "old_capture" not in d
    assert "linesRemoved" not in d


def test_detail_write_pre_state_row_uses_input_old():
    """Rows persisted BEFORE the sidecar upgrade (no state extras):
    fallback to the input.old path (the _capped_input edit-like rule
    kept)."""
    row = _rec_write({
        "status": "completed",
        "input": {"filePath": "x.py", "content": "n", "old": "a" + chr(10) + "b" + chr(10) + "c"},
        "output": "wrote x.py",
    })
    d = row["detail"]
    assert d["mode"] == "overwrite"
    assert d["old_capture"] == "a" + chr(10) + "b" + chr(10) + "c"
    assert d["linesRemoved"] == 3


def test_write_old_capture_capped():
    huge = "z" * (WRITE_OLD_CAPTURE_CHARS + 5000)
    row = _rec_write({
        "status": "completed",
        "input": {"filePath": "x", "content": "n", "old": huge},
        "output": "wrote x",
    })
    d = row["detail"]
    assert len(d["old_capture"]) <= WRITE_OLD_CAPTURE_CHARS + 20
    assert "truncated" in d["old_capture"]


def test_old_capture_single_source_of_truth():
    row = _rec_write({
        "status": "completed",
        "input": {"filePath": "x", "content": "new", "old": "a" + chr(10) + "b"},
        "mode": "overwrite", "linesRemoved": 2,
        "old_capture": "a" + chr(10) + "b",
        "output": "wrote x",
    })
    assert row["detail"]["old_capture"] == "a" + chr(10) + "b"  # once
    assert row["detail"]["linesRemoved"] == 2


def test_row_budget_with_a_huge_old(tmp_path):
    huge = "z" * (WRITE_OLD_CAPTURE_CHARS * 4)
    row = _rec_write({
        "status": "completed",
        "input": {"filePath": "x", "content": "n", "old": huge},
        "mode": "overwrite", "linesRemoved": len(huge.splitlines()),
        "output": "wrote x",
    })
    blob = json.dumps(row, default=str)
    assert len(blob) < 12_000, len(blob)


NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node unavailable (repo gate)"
)


@NEEDS_NODE
def test_engine_write_state_extras_end_to_end(tmp_path: Path):
    """Real sidecar gate: writePath captures the old bytes pre-write
    (never fails), toolStateExtra emits {mode:'overwrite',
    linesRemoved, old_capture}; a fresh file emits {mode:'create'}."""
    engine = Path(__file__).resolve().parents[1] / "sweave-engine" / "src" / "tools.js"
    LF = chr(10)
    script = (
        "import { executeTool, toolStateExtra } from "
        + repr(engine.resolve().as_uri())
        + ";" + LF
        + "import fs from 'node:fs/promises';" + LF
        + "import path from 'node:path';" + LF
        + "const tmp = await fs.mkdtemp('tc2b-');" + LF
        + "const BF = String.fromCharCode(10);" + LF
        + "const LF = String.fromCharCode(10);" + LF
        + "await fs.writeFile(path.join(tmp, 'f.js'), 'aa' + LF + 'bb' + LF + 'cc' + LF, 'utf8');" + LF
        + "const r = await executeTool('write', {filePath: 'f.js', content: 'zz'}, {cwd: tmp, session: {}, signal: undefined});" + LF
        + "const over = toolStateExtra('write', {filePath: 'f.js', content: 'xx'}, r);" + LF
        + "const r2 = await executeTool('write', {filePath: 'new.js', content: 'fresh'}, {cwd: tmp, session: {}, signal: undefined});" + LF
        + "const create = toolStateExtra('write', {filePath: 'new.js', content: 'fresh'}, r2);" + LF
        + "console.log(JSON.stringify({over, create: {mode: create.mode, has_old: 'old_capture' in create}}));" + LF
    )
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr[:600]
    payload = json.loads(res.stdout.splitlines()[-1])
    assert payload["over"]["mode"] == "overwrite"
    assert payload["over"]["linesRemoved"] == 4
    assert payload["over"]["old_capture"] == "aa" + chr(10) + "bb" + chr(10) + "cc" + LF
    assert payload["create"]["mode"] == "create"
    assert payload["create"]["has_old"] is False
