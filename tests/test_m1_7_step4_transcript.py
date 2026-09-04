"""M1.7 step 4 tests: transcript system + memory curation + multi-source what's new.

Covers:

* Session gains last_memory_recall_ts and last_git_snapshot fields.
  Legacy session files (without the fields) load with defaults
  (None) -- the migration is a no-op for pre-M1.7 files.
* ComposedPrompt composition: memory section, what's new section,
  synthesis section, transcript reference, user message.
* Per-section token budgets: when a section overflows its cap, the
  lowest-priority entries are dropped; the trace records what was
  dropped.
* Multi-source "what's new": memory entries since
  last_memory_recall_ts + git diff since last_git_snapshot, tagged
  by source. Graceful fallback: not-a-git-repo -> empty git section.
* Inter-session mechanics: first turn has empty "what's new";
  subsequent turns have it populated.
* Transcript reference: one paragraph, NOT the full transcript.
* Memory entries are timestamped; legacy entries (ts=None) are
  filtered out of the "what's new" section.
* GitSnapshotter: snapshot returns commit SHA + dirty hash; diff
  summarises changes; non-git dirs return None / empty.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from sweave.chat.transcript import (
    ComposedPrompt,
    DEFAULT_MEMORY_BUDGET,
    DEFAULT_WHATS_NEW_BUDGET,
    GitSnapshotter,
    compose_turn_prompt,
    truncate_to_tokens,
)
from sweave.memory.backends import MemoryEntry
from sweave.projects import ProjectManager, Session


# ---------------------------------------------------------------------------
# Session field tests
# ---------------------------------------------------------------------------


def test_session_default_fields_are_none():
    s = Session(id="s1", project_name="p", name="n")
    assert s.last_memory_recall_ts is None
    assert s.last_git_snapshot is None


def test_session_to_dict_roundtrip_preserves_recall_and_snapshot():
    now = datetime(2026, 9, 4, 12, 0, 0)
    s = Session(
        id="s1",
        project_name="p",
        name="n",
        last_memory_recall_ts=now,
        last_git_snapshot="abc123",
    )
    d = s.to_dict()
    assert d["last_memory_recall_ts"] == now.isoformat()
    assert d["last_git_snapshot"] == "abc123"
    s2 = Session.from_dict(d)
    assert s2.last_memory_recall_ts == now
    assert s2.last_git_snapshot == "abc123"


def test_session_legacy_file_migrates_with_defaults():
    legacy = {
        "id": "s1",
        "project_name": "p",
        "name": "n",
        "created_at": "2026-09-01T00:00:00",
        "updated_at": "2026-09-01T00:00:00",
        "status": "active",
        "current_agent": None,
        "context": {},
        "messages": [],
        "children": [],
        "memory_bank": "session-s1",
        "schema_version": 1,
        "orchestrator_session_id": None,
        # NOTE: no last_memory_recall_ts, no last_git_snapshot
    }
    s = Session.from_dict(legacy)
    assert s.last_memory_recall_ts is None
    assert s.last_git_snapshot is None
    # Roundtrip writes the new fields
    d = s.to_dict()
    assert d["last_memory_recall_ts"] is None
    assert d["last_git_snapshot"] is None


# ---------------------------------------------------------------------------
# ComposedPrompt tests
# ---------------------------------------------------------------------------


def test_composed_prompt_to_body_joins_sections_in_order():
    p = ComposedPrompt(
        memory_section="## Memory",
        whats_new_section="## What's New",
        synthesis_section="## Synthesis",
        transcript_ref="## Transcript Ref",
        user_message="hi",
    )
    body = p.to_body()
    assert body.index("## Memory") < body.index("## What's New")
    assert body.index("## What's New") < body.index("## Synthesis")
    assert body.index("## Synthesis") < body.index("## Transcript Ref")
    assert body.index("## Transcript Ref") < body.index("hi")


def test_composed_prompt_to_body_skips_empty_sections():
    p = ComposedPrompt(user_message="hi")
    body = p.to_body()
    assert body == "hi"


def test_composed_prompt_default_dropped_lists_are_empty():
    p = ComposedPrompt()
    assert p.dropped_memory == []
    assert p.dropped_whats_new == []
    assert p.dropped_synthesis == []


# ---------------------------------------------------------------------------
# Composed prompt composition (async)
# ---------------------------------------------------------------------------


class _FakeMemory:
    """In-memory memory backend for the composer tests."""

    def __init__(self, entries: list[MemoryEntry] | None = None):
        self.entries = entries or []

    async def recall(self, query: str, bank_id: str, limit: int = 10) -> list[MemoryEntry]:
        # Relevance ranking is order-preserving in the test -- the
        # composer doesn't test the backend's ranking, it tests
        # the composer's filtering.
        return list(self.entries[:limit])


@pytest.mark.asyncio
async def test_compose_first_turn_has_empty_whats_new(tmp_path: Path):
    """First turn of a session: last_memory_recall_ts is None ->
    "what's new" is empty (the plan's inter-session rules).
    """
    s = Session(id="s1", project_name="p", name="n")
    entries = [
        MemoryEntry(content=f"entry {i}", ts=datetime.now() - timedelta(minutes=i))
        for i in range(3)
    ]
    backend = _FakeMemory(entries)
    composed = await compose_turn_prompt(
        session=s,
        user_message="hello",
        project_dir=tmp_path,
        memory_bank_id="project-p",
        memory_backend=backend,
        git_snapshotter=GitSnapshotter(),  # not a git repo; fine
    )
    assert composed.whats_new_section == ""


@pytest.mark.asyncio
async def test_compose_curated_memory_caps_at_topk(tmp_path: Path):
    """Memory section: backend's recall returns top-k=5 by default;
    7 entries in the bank -> 5 reach the prompt (top-k is the
    backend's responsibility, not the budget-cap's).
    """
    s = Session(id="s1", project_name="p", name="n")
    entries = [
        MemoryEntry(content=f"entry-{i}", ts=datetime.now())
        for i in range(7)
    ]
    backend = _FakeMemory(entries)
    composed = await compose_turn_prompt(
        session=s,
        user_message="hi",
        project_dir=tmp_path,
        memory_bank_id="project-p",
        memory_backend=backend,
        git_snapshotter=None,
        memory_budget=DEFAULT_MEMORY_BUDGET,
    )
    mem_lines = [
        l for l in composed.memory_section.splitlines()
        if l.startswith("- ")
    ]
    assert len(mem_lines) == 5
    # No budget-cap drops; top-k is upstream
    assert len(composed.dropped_memory) == 0


@pytest.mark.asyncio
async def test_compose_memory_budget_drops_lowest_priority(tmp_path: Path):
    """Budget cap: oldest entries (lowest priority) are dropped
    when the cap is exceeded. The audit trail records what was
    dropped.
    """
    s = Session(id="s1", project_name="p", name="n")
    # Five long entries, each ~50 tokens; budget is 100.
    long = "word " * 50
    entries = [MemoryEntry(content=f"entry-{i}: {long}", ts=datetime.now()) for i in range(5)]
    backend = _FakeMemory(entries)
    composed = await compose_turn_prompt(
        session=s,
        user_message="hi",
        project_dir=tmp_path,
        memory_bank_id="project-p",
        memory_backend=backend,
        git_snapshotter=None,
        memory_budget=100,
        topk=10,
    )
    # At least one entry was dropped (cap is binding)
    assert len(composed.dropped_memory) >= 1
    kept_lines = [l for l in composed.memory_section.splitlines() if l.startswith("- ")]
    assert len(kept_lines) + len(composed.dropped_memory) == 5


@pytest.mark.asyncio
async def test_compose_whats_new_filters_by_ts(tmp_path: Path):
    """Memory entries with ts <= last_memory_recall_ts are not
    in the "what's new" section.
    """
    now = datetime.now()
    last_recall = now - timedelta(hours=1)
    s = Session(
        id="s1", project_name="p", name="n",
        last_memory_recall_ts=last_recall,
    )
    entries = [
        MemoryEntry(content="old", ts=now - timedelta(hours=2)),  # before
        MemoryEntry(content="recent-1", ts=now - timedelta(minutes=5)),  # after
        MemoryEntry(content="recent-2", ts=now - timedelta(minutes=2)),  # after
    ]
    backend = _FakeMemory(entries)
    composed = await compose_turn_prompt(
        session=s,
        user_message="hi",
        project_dir=tmp_path,
        memory_bank_id="project-p",
        memory_backend=backend,
        git_snapshotter=None,
        whats_new_budget=DEFAULT_WHATS_NEW_BUDGET,
    )
    assert "old" not in composed.whats_new_section
    assert "recent-1" in composed.whats_new_section
    assert "recent-2" in composed.whats_new_section


@pytest.mark.asyncio
async def test_compose_legacy_memory_entries_excluded_from_whats_new(tmp_path: Path):
    """Pre-M1.7 memory entries (ts=None) are NOT in the "what's new"
    section -- only timestamped entries are.

    Bypass MemoryEntry.__post_init__ (which auto-fills ts to
    ``datetime.now()``) by setting ts back to None after construction
    -- simulating a legacy entry reloaded from disk.
    """
    now = datetime.now()
    last_recall = now - timedelta(hours=1)
    s = Session(
        id="s1", project_name="p", name="n",
        last_memory_recall_ts=last_recall,
    )
    legacy = MemoryEntry(content="legacy", ts=now - timedelta(minutes=5))
    legacy.ts = None  # simulate legacy entry on disk
    recent = MemoryEntry(content="recent", ts=now - timedelta(minutes=2))
    backend = _FakeMemory([legacy, recent])
    composed = await compose_turn_prompt(
        session=s,
        user_message="hi",
        project_dir=tmp_path,
        memory_bank_id="project-p",
        memory_backend=backend,
        git_snapshotter=None,
    )
    # The legacy entry may still be in the curated memory section
    # (it goes through relevance ranking), but the "what's new"
    # filter excludes it (ts is None).
    if composed.whats_new_section:
        assert "legacy" not in composed.whats_new_section
        assert "recent" in composed.whats_new_section


@pytest.mark.asyncio
async def test_compose_works_when_backend_is_none(tmp_path: Path):
    """No memory backend wired: memory + what's new are empty,
    user message still flows through.
    """
    s = Session(id="s1", project_name="p", name="n")
    composed = await compose_turn_prompt(
        session=s,
        user_message="hi",
        project_dir=tmp_path,
        memory_bank_id="project-p",
        memory_backend=None,
        git_snapshotter=None,
    )
    assert composed.memory_section == ""
    assert composed.whats_new_section == ""
    assert composed.user_message == "hi"
    assert "hi" in composed.to_body()


@pytest.mark.asyncio
async def test_compose_transcript_ref_is_short_paragraph(tmp_path: Path):
    """Transcript reference is bounded, NOT the full transcript."""
    s = Session(id="s1", project_name="p", name="n")
    # 50 messages, each 1KB; the full transcript is 50KB. The
    # reference should be a small fraction.
    msgs = []
    for i in range(50):
        msgs.append(
            type("M", (), {"role": "user" if i % 2 == 0 else "assistant",
                            "content": "x" * 1000})()
        )
    composed = await compose_turn_prompt(
        session=s,
        user_message="hi",
        project_dir=tmp_path,
        memory_bank_id="project-p",
        memory_backend=None,
        git_snapshotter=None,
        transcript_messages=msgs,
    )
    # Reference is one paragraph; the full 50KB transcript would be
    # many paragraphs.
    ref_lines = composed.transcript_ref.splitlines()
    assert len(ref_lines) <= 4
    # 50 messages mentioned
    assert "50" in composed.transcript_ref


# ---------------------------------------------------------------------------
# GitSnapshotter tests
# ---------------------------------------------------------------------------


def test_git_snapshotter_returns_none_for_non_git_dir(tmp_path: Path):
    s = GitSnapshotter()
    assert s.snapshot(tmp_path) is None


def test_git_snapshotter_diff_since_returns_empty_for_non_git_dir(tmp_path: Path):
    s = GitSnapshotter()
    assert s.diff_since(tmp_path, "abc123") == ""


def test_git_snapshotter_initialised_repo(tmp_path: Path):
    """Initialise a real git repo with a commit, snapshot it."""
    s = GitSnapshotter()
    try:
        subprocess.check_call(["git", "init", "-q"], cwd=str(tmp_path))
        (tmp_path / "README").write_text("init")
        subprocess.check_call(["git", "add", "README"], cwd=str(tmp_path))
        subprocess.check_call(
            ["git", "-c", "user.email=a@b", "-c", "user.name=a",
             "commit", "-q", "-m", "init"],
            cwd=str(tmp_path),
        )
    except FileNotFoundError:
        pytest.skip("git not on PATH")
    snap = s.snapshot(tmp_path)
    assert snap is not None
    sha = snap.split(":", 1)[0]
    assert len(sha) >= 7
    assert all(c in "0123456789abcdef" for c in sha)


def test_git_snapshotter_diff_since_same_sha(tmp_path: Path):
    """Same commit, no dirty-state changes -> empty diff."""
    s = GitSnapshotter()
    try:
        subprocess.check_call(["git", "init", "-q"], cwd=str(tmp_path))
        (tmp_path / "file.txt").write_text("hello")
        subprocess.check_call(["git", "add", "file.txt"], cwd=str(tmp_path))
        subprocess.check_call(
            ["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "-m", "init"],
            cwd=str(tmp_path),
        )
    except FileNotFoundError:
        pytest.skip("git not on PATH")
    snap = s.snapshot(tmp_path)
    diff = s.diff_since(tmp_path, snap)
    assert diff == ""  # clean, no changes


def test_git_snapshotter_diff_since_with_change(tmp_path: Path):
    """Add a new file -> diff is non-empty."""
    s = GitSnapshotter()
    try:
        subprocess.check_call(["git", "init", "-q"], cwd=str(tmp_path))
        (tmp_path / "file.txt").write_text("hello")
        subprocess.check_call(["git", "add", "file.txt"], cwd=str(tmp_path))
        subprocess.check_call(
            ["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "-m", "init"],
            cwd=str(tmp_path),
        )
        initial_snap = s.snapshot(tmp_path)
        (tmp_path / "new.txt").write_text("new")
    except FileNotFoundError:
        pytest.skip("git not on PATH")
    # Force the snapshot to differ: simulate a new commit (so the
    # diff-since path is exercised via the --stat branch). For the
    # dirty-state branch, we just need the dirty hash to differ.
    diff = s.diff_since(tmp_path, initial_snap)
    # Either a --stat or a --porcelain summary is acceptable
    assert diff  # non-empty
