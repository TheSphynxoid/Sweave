"""Commit-attribution projector (M2.1-follow-up, C track).

``commits_in_range`` / ``unattributed_commits`` over tmp git repos:
trailer presence decides attributability, non-repos degrade to [].
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def _git_available() -> bool:
    return shutil.which("git") is not None


needs_git = pytest.mark.skipif(not _git_available(), reason="git binary required")


def _repo_with_commits(tmp_path: Path) -> Path:
    from sweave.platform import run_no_window

    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    import os

    full_env = dict(os.environ, **env)
    run_no_window(["git", "init", "-b", "main", str(repo)],
                  capture_output=True, check=True)
    run_no_window(["git", "-C", str(repo), "config", "user.email", "t@t"],
                  capture_output=True, check=True)
    run_no_window(["git", "-C", str(repo), "config", "user.name", "t"],
                  capture_output=True, check=True, env=full_env)
    (repo / "a.txt").write_text("one", encoding="utf-8")
    run_no_window(["git", "-C", str(repo), "add", "."], capture_output=True, check=True)
    run_no_window(["git", "-C", str(repo), "commit", "-m", "first"],
                  capture_output=True, check=True, env=full_env)
    (repo / "b.txt").write_text("two", encoding="utf-8")
    run_no_window(["git", "-C", str(repo), "add", "."], capture_output=True, check=True)
    run_no_window(
        ["git", "-C", str(repo), "commit", "-m", "second",
         "-m", "Sweave-Delegation: abc123"],
        capture_output=True, check=True, env=full_env,
    )
    (repo / "c.txt").write_text("three", encoding="utf-8")
    run_no_window(["git", "-C", str(repo), "add", "."], capture_output=True, check=True)
    run_no_window(["git", "-C", str(repo), "commit", "-m", "third"],
                  capture_output=True, check=True, env=full_env)
    return repo


@needs_git
def test_trailer_decides_attributability(tmp_path: Path):
    from sweave.web.attribution import commits_in_range, unattributed_commits

    repo = _repo_with_commits(tmp_path)
    commits = commits_in_range(repo, "HEAD~2")
    assert len(commits) == 2
    by_subject = {c["subject"]: c for c in commits}
    assert by_subject["third"]["has_trailer"] is False
    assert by_subject["second"]["has_trailer"] is True
    unattributed = unattributed_commits(repo, "HEAD~2")
    assert [c["subject"] for c in unattributed] == ["third"]


@needs_git
def test_non_repo_degrades_to_empty(tmp_path: Path):
    from sweave.web.attribution import commits_in_range

    assert commits_in_range(tmp_path, "HEAD") == []


@needs_git
def test_unknown_range_degrades_to_empty(tmp_path: Path):
    from sweave.web.attribution import commits_in_range

    repo = _repo_with_commits(tmp_path)
    assert commits_in_range(repo, "no-such-base-xyz") == []
