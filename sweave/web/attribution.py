"""Commit-attribution projector (M2.1-follow-up, C track).

Read-side only (the ``detail_view.py`` precedent): given a worktree
and a base ref, list the commits in range and flag which ones lack
the ``Sweave-Delegation:`` provenance trailer. Diagnoses like
Sweave-20260911-213619-096e65 (work appears with no attributable
author — parallel sessions, background runs, ghosts) are unanswerable
without this: the commit-authority map *requires* the trailer on
specialist commits, but nothing ever checked.

No writes, never raises for non-repos (empty list). Git calls go
through the no-window discipline (``sweave.platform``).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

TRAILER = "Sweave-Delegation:"


def _git_ok() -> bool:
    return shutil.which("git") is not None


def commits_in_range(
    worktree: Path, base: str, head: str = "HEAD"
) -> list[dict[str, Any]]:
    """Commits in ``base..head`` with trailer presence.

    Returns [{sha, author, date, subject, has_trailer}]. Empty list
    when git is absent, the dir is not a repo, or the range is
    unknown/empty — attribution is best-effort forensics, never a
    gate that can wedge a turn.
    """
    from sweave.platform import run_no_window

    if not _git_ok():
        return []
    # NOTE: each record starts with a literal REC sentinel instead
    # of ending with the body: git eats a trailing separator when
    # %b is empty, so empty-body records would parse short. Body =
    # parts[5] when present, "" otherwise.
    try:
        proc = run_no_window(
            ["git", "-C", str(worktree), "log",
             f"{base}..{head}",
             "--format=REC%x1f%H%x1f%an%x1f%ad%x1f%s%x1f%b%x1e",
             "--date=iso"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    out: list[dict[str, Any]] = []
    for chunk in proc.stdout.split("\x1e"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split("\x1f")
        if len(parts) < 5 or parts[0] != "REC":
            continue
        _, sha, author, date, subject = parts[:5]
        body = parts[5] if len(parts) > 5 else ""
        trailer_hit = any(
            line.strip().lower().startswith(TRAILER.lower())
            for line in body.splitlines()
        )
        out.append(
            {
                "sha": sha,
                "author": author,
                "date": date,
                "subject": subject,
                "has_trailer": trailer_hit,
            }
        )
    return out


def unattributed_commits(
    worktree: Path, base: str, head: str = "HEAD"
) -> list[dict[str, Any]]:
    """Commits in range WITHOUT the provenance trailer.

    These are precisely the unattributable ones: human commits made
    directly, background sessions that skipped the map, or work no
    delegation owns. The caller decides what each means; this
    function only separates them from trailer-carrying work.
    """
    return [c for c in commits_in_range(worktree, base, head)
            if not c["has_trailer"]]
