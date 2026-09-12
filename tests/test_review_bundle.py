"""REVIEW Phase 1 step 1: review bundle builder + artifact store.

Entering ``review`` captures diff material synchronously: unified
diff + file list + stats to ``{project}/.sweave/reviews/{id}.diff``
with a small pointer on the record. Secrets pass through the
Phase-1 redaction boundary; captures degrade to a pointer without
a file (never fail the transition).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sweave.platform import run_no_window
from sweave.runtime.review_bundle import (
    MAX_BUNDLE_BYTES,
    build_review_bundle,
    redact_secrets,
    write_bundle_artifact,
)


def _git(cwd: Path, *args: str) -> None:
    proc = run_no_window(
        ["git", *args],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=30.0,
    )
    assert proc.returncode == 0, (args, proc.stdout)


def _repo_with_change(tmp_path: Path, name: str) -> Path:
    """A git repo (branch main) with one committed file + one
    uncommitted modification. Returns the repo dir."""
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@sweave.local")
    _git(repo, "config", "user.name", "sweave-test")
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "seed")
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    return repo


# ---------------------------------------------------------------------------
# Redaction boundary
# ---------------------------------------------------------------------------


def test_redaction_covers_known_secret_shapes():
    text = (
        "aws_key = AKIAIOSFODNN7EXAMPLE\n"
        "token ghp_abcdefghij1234567890 in code\n"
        'password = "s3cr3t-value"\n'
        "Authorization: Bearer abcdef1234567890\n"
        "-----BEGIN RSA PRIVATE KEY-----\nMIIBSECRET\n-----END RSA PRIVATE KEY-----\n"
    )
    redacted, count = redact_secrets(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in redacted
    assert "ghp_abcdefghij1234567890" not in redacted
    assert "s3cr3t-value" not in redacted
    assert "abcdef1234567890" not in redacted
    assert "MIIBSECRET" not in redacted
    assert redacted.count("[REDACTED:") == count
    assert count == 5
    # Structure survives (the diff stays reviewable).
    assert "aws_key =" in redacted
    assert "password=" in redacted


def test_redaction_leaves_clean_text_alone():
    text = "just a normal diff line\n+ x = 2\n"
    redacted, count = redact_secrets(text)
    assert redacted == text
    assert count == 0


# ---------------------------------------------------------------------------
# Builder scopes
# ---------------------------------------------------------------------------


def test_worktree_scope_captures_diff_and_files(tmp_path: Path):
    repo = _repo_with_change(tmp_path, "wt")
    bundle = build_review_bundle(
        delegation_id="d1",
        worktree_path=str(repo),
        manifest_files=None,
        project_dir=tmp_path,
    )
    assert bundle["scope"] == "worktree"
    assert bundle["base"] == "main"
    assert "-x = 1" in bundle["diff"].replace(" ", "") or "x = 1" in bundle["diff"]
    assert "+x = 2" in bundle["diff"].replace(" ", "") or "x = 2" in bundle["diff"]
    assert any("app.py" in f for f in bundle["files"])
    assert bundle["truncated"] is False
    assert bundle["redactions"] == 0


def test_worktree_scope_includes_untracked_new_files(tmp_path: Path):
    """Untracked files are appended as marked sections (a delegation
    that only created new files must not read as empty)."""
    repo = _repo_with_change(tmp_path, "wt-new")
    (repo / "new_mod.py").write_text("def fresh():\n    return 1\n", encoding="utf-8")
    bundle = build_review_bundle(
        delegation_id="d8",
        worktree_path=str(repo),
        manifest_files=None,
        project_dir=tmp_path,
    )
    assert bundle["scope"] == "worktree"
    assert "+++ b/new_mod.py" in bundle["diff"]
    assert "+def fresh():" in bundle["diff"]
    assert "new_mod.py" in bundle["files"]


def test_in_tree_with_manifest_files_is_paths_scoped(tmp_path: Path):
    repo = _repo_with_change(tmp_path, "proj")
    bundle = build_review_bundle(
        delegation_id="d2",
        worktree_path=None,
        manifest_files=["app.py"],
        project_dir=repo,
    )
    assert bundle["scope"] == "paths"
    assert "x = 2" in bundle["diff"]
    assert bundle["files"] == ["app.py"]


def test_in_tree_without_files_is_unscoped_but_honest(tmp_path: Path):
    repo = _repo_with_change(tmp_path, "proj2")
    bundle = build_review_bundle(
        delegation_id="d3",
        worktree_path=None,
        manifest_files=None,
        project_dir=repo,
    )
    assert bundle["scope"] == "unscoped"
    # Honesty: status names the file even when the diff is the
    # whole worktree.
    assert "app.py" in bundle["stats"]
    assert "x = 2" in bundle["diff"]


def test_missing_worktree_degrades_to_pointer_without_file(tmp_path: Path):
    bundle = build_review_bundle(
        delegation_id="d4",
        worktree_path=str(tmp_path / "gone"),
        manifest_files=None,
        project_dir=tmp_path,
    )
    assert bundle["scope"] == "missing-worktree"
    assert bundle["diff"] == ""
    assert bundle["files"] == []


def test_non_repo_worktree_degrades(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    bundle = build_review_bundle(
        delegation_id="d5",
        worktree_path=str(plain),
        manifest_files=None,
        project_dir=tmp_path,
    )
    assert bundle["scope"] == "missing-worktree"


def test_truncation_cap_is_recorded(tmp_path: Path):
    repo = _repo_with_change(tmp_path, "big")
    (repo / "big.txt").write_text("y\n" * 5000, encoding="utf-8")
    # Stage it: untracked files are invisible to `git diff <base>`;
    # staged-but-uncommitted content shows (worktree vs base).
    _git(repo, "add", "big.txt")
    bundle = build_review_bundle(
        delegation_id="d6",
        worktree_path=str(repo),
        manifest_files=None,
        project_dir=tmp_path,
        max_bytes=200,
    )
    assert bundle["truncated"] is True
    assert len(bundle["diff"].encode("utf-8")) <= 200
    # Still valid text (no split surrogate/sequence).
    bundle["diff"].encode("utf-8").decode("utf-8")


def test_default_cap_is_pinned():
    assert MAX_BUNDLE_BYTES == 256 * 1024


# ---------------------------------------------------------------------------
# Schema v9→v10
# ---------------------------------------------------------------------------


def test_v9_record_loads_as_v10_with_bundle_none():
    from sweave.runtime.delegation_store import SCHEMA_VERSION, Delegation

    rec = {
        "schema_version": 9,
        "delegation_id": "del-v9",
        "task_id": "t9",
        "agent": "backend",
        "model": "",
        "task": "x",
        "status": "review",
        "created_at": "2026-09-12T00:00:00",
        "updated_at": "2026-09-12T00:00:00",
    }
    d = Delegation.from_dict(rec)
    assert d.schema_version == SCHEMA_VERSION == 10
    assert d.review_bundle is None


def test_v10_pointer_round_trips():
    from sweave.runtime.delegation_store import Delegation

    d = Delegation(
        agent="a", task="t",
        review_bundle={
            "path": ".sweave/reviews/abc.diff",
            "bytes": 1234,
            "truncated": False,
            "scope": "worktree",
        },
    )
    back = Delegation.from_dict(d.to_dict())
    assert back.review_bundle is not None
    assert back.review_bundle["scope"] == "worktree"
    assert back.schema_version == 10


# ---------------------------------------------------------------------------
# Artifact store
# ---------------------------------------------------------------------------


def test_artifact_write_and_header(tmp_path: Path):
    repo = _repo_with_change(tmp_path, "wt2")
    bundle = build_review_bundle(
        delegation_id="d7",
        worktree_path=str(repo),
        manifest_files=None,
        project_dir=tmp_path,
    )
    path, nbytes = write_bundle_artifact(
        project_dir=tmp_path,
        delegation_id="d7",
        agent="backend",
        bundle=bundle,
    )
    assert path == tmp_path / ".sweave" / "reviews" / "d7.diff"
    assert path.exists()
    assert nbytes == len(path.read_bytes())
    head = path.read_text(encoding="utf-8").splitlines()[:8]
    assert head[0] == "# review bundle for d7 (agent: backend)"
    assert any(line.startswith("# scope: worktree") for line in head)


# ---------------------------------------------------------------------------
# Runner integration: capture on the review transition
# ---------------------------------------------------------------------------


def _stub_job_runner(stores, succeed: bool, project_dir: Path):
    from sweave.runtime.job_runner import JobRunner

    class _StubDelegateTool:
        async def execute(self, agent, task, model=None, task_id=None):
            from sweave.tools import DelegationResult

            if succeed:
                return DelegationResult(
                    success=True, agent=agent, task_id=task_id or "stub",
                    output="did the work", error=None,
                )
            return DelegationResult(
                success=False, agent=agent, task_id=task_id or "stub",
                output="", error="boom",
            )

    return JobRunner(
        delegate_tool=_StubDelegateTool(),  # type: ignore[arg-type]
        delegation_stores=stores,
        project_dir_resolver=lambda name: project_dir,
        turn_timeout=10.0,
    )


@pytest.mark.asyncio
async def test_review_transition_captures_bundle_with_pointer(tmp_path: Path):
    import tempfile

    from sweave.runtime.delegation_store import (
        Delegation,
        PerProjectDelegationStores,
    )
    from sweave.runtime.trace_log import TraceLog

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-review-bundle-"))
    wt = _repo_with_change(project_dir, "wt-delegation")
    stores = PerProjectDelegationStores()
    store = await stores.for_project(project_dir)
    runner = _stub_job_runner(stores, succeed=True, project_dir=project_dir)

    d = Delegation(agent="backend", task="t", project_name="p",
                   worktree_path=str(wt), branch="feat-x")
    await store.add(d)
    await runner._run(d, TraceLog(d.delegation_id, base_dir=project_dir))

    rec = store.get(d.delegation_id)
    assert rec is not None and rec.status == "review"
    ptr = rec.review_bundle
    assert ptr is not None
    assert ptr["scope"] == "worktree"
    assert ptr["path"] == ".sweave/reviews/" + d.delegation_id + ".diff"
    assert ptr["bytes"] > 0
    assert ptr["truncated"] is False
    assert (project_dir / ptr["path"]).exists()

    import json

    events = [
        json.loads(line)
        for line in TraceLog(
            d.delegation_id, base_dir=project_dir
        ).path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    bundled = [e for e in events if e.get("event") == "review_bundled"]
    assert len(bundled) == 1
    assert bundled[0]["scope"] == "worktree"


@pytest.mark.asyncio
async def test_review_transition_degrades_without_worktree(tmp_path: Path):
    import tempfile

    from sweave.runtime.delegation_store import (
        Delegation,
        PerProjectDelegationStores,
    )
    from sweave.runtime.trace_log import TraceLog

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-review-degrade-"))
    stores = PerProjectDelegationStores()
    store = await stores.for_project(project_dir)
    runner = _stub_job_runner(stores, succeed=True, project_dir=project_dir)

    d = Delegation(agent="backend", task="t", project_name="p",
                   worktree_path=str(project_dir / "removed"))
    await store.add(d)
    await runner._run(d, TraceLog(d.delegation_id, base_dir=project_dir))

    rec = store.get(d.delegation_id)
    assert rec is not None and rec.status == "review"
    # Pointer WITHOUT a file (the detail surface says why).
    assert rec.review_bundle is not None
    assert rec.review_bundle["path"] is None
    assert rec.review_bundle["scope"] == "missing-worktree"

    import json

    events = [
        json.loads(line)
        for line in TraceLog(
            d.delegation_id, base_dir=project_dir
        ).path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    degraded = [e for e in events if e.get("event") == "review_bundle_degraded"]
    assert len(degraded) == 1


@pytest.mark.asyncio
async def test_failed_run_captures_no_bundle(tmp_path: Path):
    import tempfile

    from sweave.runtime.delegation_store import (
        Delegation,
        PerProjectDelegationStores,
    )
    from sweave.runtime.trace_log import TraceLog

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-review-fail-"))
    stores = PerProjectDelegationStores()
    store = await stores.for_project(project_dir)
    runner = _stub_job_runner(stores, succeed=False, project_dir=project_dir)

    d = Delegation(agent="backend", task="t", project_name="p")
    await store.add(d)
    await runner._run(d, TraceLog(d.delegation_id, base_dir=project_dir))

    rec = store.get(d.delegation_id)
    assert rec is not None and rec.status == "failed"
    assert rec.review_bundle is None
