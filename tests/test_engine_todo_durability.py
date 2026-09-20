"""Gap-2 proof: sidecar todo state survives restarts (sessions journal).

The no-rotation invariant makes sessions immortal — the
orchestrator's plan (its todo list) must be equally durable. The
journal (sharded ``sessions/<id>.json`` since the 2026-09-20 journal
surgery) persists the whole session object
including `todos`; every tool execution is followed synchronously
by append+save, so no explicit todo save path is needed. This
test pins the guarantee end-to-end at the store level: write
todos, drop the store (simulated restart), reload from the same
dir, assert the plan survived — including alongside the
always-grant scrub (which must never touch todos).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

node_missing = shutil.which("node") is None
needs_node = pytest.mark.skipif(node_missing, reason="node not on PATH")

SESSIONS_URI = (
    Path(__file__).resolve().parent.parent
    / "sweave-engine" / "src" / "sessions.js"
).as_uri()

_PROBE = """
import { SessionStore } from 'SRC';
const dir = process.argv[1];
const phase = process.argv[2];
if (phase === 'write') {
  const store = new SessionStore(dir);
  const s = store.ensure('eng_probe');
  s.todos = [{ content: 'plan it', status: 'in_progress', priority: 'high' }];
  s.approvals = [{ tool: 'bash', grant: 'always' }];
  store.save();
} else {
  const store = new SessionStore(dir);
  const s = store.get('eng_probe');
  if (!s || !Array.isArray(s.todos) || s.todos[0]?.content !== 'plan it'
      || s.todos[0]?.status !== 'in_progress') {
    console.error('todos lost across restart');
    process.exit(1);
  }
  if ('approvals' in s) {
    console.error('always-grant scrub failed to wipe approvals');
    process.exit(1);
  }
  console.log('todos durable, approvals scrubbed');
}
""".replace("SRC", SESSIONS_URI)


def _node(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", "--input-type=module", "-e", _PROBE, str(tmp_path), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


@needs_node
def test_todos_survive_store_reload(tmp_path: Path):
    wrote = _node(tmp_path, "write")
    assert wrote.returncode == 0, wrote.stderr
    assert (tmp_path / "sessions" / "eng_probe.json").exists()
    read = _node(tmp_path, "read")
    assert read.returncode == 0, read.stderr
    assert "todos durable" in read.stdout


@needs_node
def test_todos_absent_without_write(tmp_path: Path):
    """A fresh session has no todos key (no phantom plan)."""
    probe = (
        "import { SessionStore } from 'SRC';\n"
        "const store = new SessionStore(process.argv[1]);\n"
        "const s = store.ensure('eng_probe_fresh');\n"
        "if ('todos' in s) { console.error('phantom todos'); process.exit(1); }\n"
        "console.log('no phantom todos');\n"
    ).replace("SRC", SESSIONS_URI)
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", probe, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "no phantom todos" in proc.stdout
