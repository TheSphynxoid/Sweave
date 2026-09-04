"""Transcript system (M1.7 step 4): the runtime's view of the per-turn
prompt.

The runtime owns the per-turn composed prompt -- the LLM is a
consumer of *what the runtime builds*, not the composer. The
composed prompt is a single user message to the orchestrator's
opencode session; the LLM sees:

1. The seed (in the opencode session's system prompt -- set at
   session creation; the runtime points opencode at
   ``sweave/agents/orchestrator/config.yaml``).
2. The runtime's per-turn additions: curated memory (top-k by
   relevance), what's new (multi-source: memory + git diff since
   last snapshot), synthesis (when children are present --
   already built in M1.7 step 3), a one-paragraph transcript
   reference (NOT the full transcript -- that lives in
   ``Session.messages`` for the audit trail + UI), and the user
   message.

Per-section token budgets are the binding constraint. The composed
prompt size is O(memory + synthesis + user_message), NOT
O(transcript_length). Long conversations don't bloat the per-turn
prompt.

External engines (opencode today) ALSO see the engine's own
session memory on top of the runtime's composed prompt -- that's
engine-specific and outside the runtime's control. The "LLM is a
consumer" framing is about the runtime's contribution; the engine's
view is whatever the engine accumulated.

For the sweave-internal engine (side-project, future) the runtime
fully owns the transcript end-to-end -- the LLM is a pure consumer
of the runtime's composed prompt. M1.7's v1 with opencode is the
external-engine case.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, List, Optional, Protocol

from sweave.chat.synthesis import _approx_tokens, truncate_to_tokens
from sweave.runtime.delegation_store import Delegation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-section token budgets. All values are defaults; tests may
# override via the ComposedPrompt builder.
# ---------------------------------------------------------------------------

DEFAULT_MEMORY_BUDGET = 2_000
DEFAULT_WHATS_NEW_BUDGET = 1_000
DEFAULT_TRANSCRIPT_REF_BUDGET = 100
DEFAULT_SYNTHESIS_BUDGET = 8_000
DEFAULT_TOPK = 5


# ---------------------------------------------------------------------------
# Backend protocols (the composer doesn't import the runtime
# memory / git helpers; callers wire in implementations).
# ---------------------------------------------------------------------------


class MemoryRecallLike(Protocol):
    """Minimal contract the composer needs from a memory backend.

    Production: HindsightMemory or NoOpMemory. Tests can pass an
    in-memory list. The composer only calls ``recall(query, bank,
    limit)`` -- the backend does the relevance scoring.
    """
    async def recall(
        self,
        query: str,
        bank_id: str,
        limit: int = 10,
    ) -> list[Any]: ...


class GitSnapshotterLike(Protocol):
    """Minimal contract for the multi-source "what's new" git section.

    ``snapshot(project_dir)`` returns a stable token (commit SHA +
    dirty-state hash, or None if the project isn't a git repo).
    ``diff_since(project_dir, last_snapshot)`` returns a small
    summary string of changes since *last_snapshot* (file count,
    line counts, top changed files), bounded at ~500 tokens; the
    full diff lives in the trace, not the prompt.
    """
    def snapshot(self, project_dir: Path) -> Optional[str]: ...
    def diff_since(
        self, project_dir: Path, last_snapshot: Optional[str]
    ) -> str: ...


class GitSnapshotter:
    """Default GitSnapshotter implementation.

    ``snapshot`` returns ``"<commit_sha>:<dirty_hash>"`` for git
    repos, or ``None`` if the project isn't a git repo (graceful
    fallback per the plan: the git section of "what's new" is
    empty, no error).
    """

    def snapshot(self, project_dir: Path) -> Optional[str]:
        if not (project_dir / ".git").exists():
            return None
        try:
            sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=str(project_dir),
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            ).decode("utf-8", errors="replace").strip()
        except Exception:  # noqa: BLE001
            return None
        dirty = ""
        try:
            status = subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=str(project_dir),
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            ).decode("utf-8", errors="replace")
            # Stable hash of the dirty-state; we don't need crypto,
            # just stability so consecutive snapshots are comparable.
            dirty = str(hash(status))
        except Exception:  # noqa: BLE001
            dirty = ""
        return f"{sha}:{dirty}" if dirty else sha

    def diff_since(
        self, project_dir: Path, last_snapshot: Optional[str]
    ) -> str:
        if not (project_dir / ".git").exists():
            return ""
        if last_snapshot is None:
            # No prior snapshot -- "what's new" doesn't include a
            # git diff. The plan rules: session A's first turn
            # initialises last_memory_recall_ts to "now" and we
            # don't have a prior git snapshot. The first-turn git
            # section is empty.
            return ""
        # Parse last_snapshot; we only need the commit SHA (the
        # dirty hash was for detecting dirty-state changes between
        # snapshots, not for diffing).
        last_sha = last_snapshot.split(":", 1)[0]
        try:
            current = self.snapshot(project_dir) or ""
            current_sha = current.split(":", 1)[0]
        except Exception:  # noqa: BLE001
            return ""
        if not current_sha or current_sha == last_sha:
            # Same commit -- but the dirty hash may differ. We
            # include a brief dirty-state summary.
            try:
                status = subprocess.check_output(
                    ["git", "status", "--porcelain"],
                    cwd=str(project_dir),
                    stderr=subprocess.DEVNULL,
                    timeout=2.0,
                ).decode("utf-8", errors="replace").strip()
            except Exception:  # noqa: BLE001
                return ""
            if not status:
                return ""
            lines = status.splitlines()
            return _summarise_git_status(lines, max_lines=10)
        try:
            stat = subprocess.check_output(
                ["git", "diff", "--stat", f"{last_sha}..{current_sha}"],
                cwd=str(project_dir),
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            ).decode("utf-8", errors="replace").strip()
        except Exception:  # noqa: BLE001
            return ""
        if not stat:
            return ""
        return _summarise_git_stat(stat, max_lines=10)


def _summarise_git_stat(stat: str, max_lines: int) -> str:
    """Bound the diff summary at *max_lines* files."""
    lines = [l for l in stat.splitlines() if l.strip()]
    if len(lines) <= max_lines:
        return "\n".join(lines)
    head = "\n".join(lines[:max_lines])
    return f"{head}\n... ({len(lines) - max_lines} more files changed)"


def _summarise_git_status(status_lines: list[str], max_lines: int) -> str:
    head = status_lines[:max_lines]
    suffix = f"\n... ({len(status_lines) - max_lines} more)" if len(status_lines) > max_lines else ""
    return "\n".join(head) + suffix


# ---------------------------------------------------------------------------
# Composed prompt
# ---------------------------------------------------------------------------


@dataclass
class ComposedPrompt:
    """The runtime's view of the per-turn prompt.

    Sections are stored as separate fields so the trace can record
    *what was injected* and *what was dropped* (the plan: "Trace
    records what was injected and what was dropped"). Tests can
    inspect individual sections.

    The string form (the actual user message posted to the opencode
    session) is the composed ``body`` -- the per-section text joined
    into a single user message.
    """
    memory_section: str = ""
    whats_new_section: str = ""
    synthesis_section: str = ""
    transcript_ref: str = ""
    user_message: str = ""
    # Audit trail: what was dropped because of the cap (so the
    # trace can record the trade-off).
    dropped_memory: list[str] = None
    dropped_whats_new: list[str] = None
    dropped_synthesis: list[str] = None

    def __post_init__(self):
        if self.dropped_memory is None:
            self.dropped_memory = []
        if self.dropped_whats_new is None:
            self.dropped_whats_new = []
        if self.dropped_synthesis is None:
            self.dropped_synthesis = []

    def to_body(self) -> str:
        """Return the single user-message body the runtime posts."""
        parts: list[str] = []
        if self.memory_section:
            parts.append(self.memory_section)
        if self.whats_new_section:
            parts.append(self.whats_new_section)
        if self.synthesis_section:
            parts.append(self.synthesis_section)
        if self.transcript_ref:
            parts.append(self.transcript_ref)
        if self.user_message:
            parts.append(self.user_message)
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Composer
# ---------------------------------------------------------------------------


async def compose_turn_prompt(
    *,
    session: Any,
    user_message: str,
    project_dir: Path | None,
    memory_bank_id: str | None,
    memory_backend: MemoryRecallLike | None,
    git_snapshotter: GitSnapshotterLike | None,
    children: Iterable[Delegation] | None = None,
    transcript_messages: list[Any] | None = None,
    memory_budget: int = DEFAULT_MEMORY_BUDGET,
    whats_new_budget: int = DEFAULT_WHATS_NEW_BUDGET,
    transcript_ref_budget: int = DEFAULT_TRANSCRIPT_REF_BUDGET,
    synthesis_budget: int = DEFAULT_SYNTHESIS_BUDGET,
    topk: int = DEFAULT_TOPK,
    now: datetime | None = None,
) -> ComposedPrompt:
    """Build the runtime's per-turn composed prompt.

    Sections (in order):
    1. Memory (curated, top-k, anti-pollution via the backend's
       relevance ranking). Bounded by ``memory_budget``.
    2. What's new (multi-source: memory entries with
       ``ts > session.last_memory_recall_ts`` + git diff since
       ``session.last_git_snapshot``). Bounded by
       ``whats_new_budget``.
    3. Synthesis (server-composed, when children are present).
       Bounded by ``synthesis_budget``.
    4. Transcript reference (one paragraph summary of
       ``Session.messages``; NOT the full transcript). Bounded by
       ``transcript_ref_budget``.
    5. User message (unbounded, typically <1K).

    The runtime calls this once per turn; the LLM sees what the
    runtime built. The audit trail (``dropped_*``) is what R6
    compaction and observability hooks read.
    """
    now = now or datetime.now()
    is_first_turn = session.last_memory_recall_ts is None

    # 1) Curated memory
    memory_section, dropped_memory = await _curated_memory(
        user_message=user_message,
        bank_id=memory_bank_id,
        backend=memory_backend,
        budget=memory_budget,
        topk=topk,
    )

    # 2) What's new (multi-source)
    whats_new_section, dropped_whats_new = await _whats_new(
        session=session,
        project_dir=project_dir,
        git_snapshotter=git_snapshotter,
        backend=memory_backend,
        bank_id=memory_bank_id,
        is_first_turn=is_first_turn,
        now=now,
        budget=whats_new_budget,
        topk=topk,
    )

    # 3) Synthesis (when children are present)
    synthesis_section = ""
    dropped_synthesis: list[str] = []
    if children:
        from sweave.chat.synthesis import build_synthesis_prompt

        synthesis_text = build_synthesis_prompt(
            children=children,
            original_user_message=user_message,
            token_cap=synthesis_budget,
        )
        if _approx_tokens(synthesis_text) > synthesis_budget:
            synthesis_section = truncate_to_tokens(
                synthesis_text, synthesis_budget
            )
            dropped_synthesis.append("synthesis overflow")
        else:
            synthesis_section = synthesis_text

    # 4) Transcript reference (one paragraph)
    transcript_ref = _transcript_reference(
        transcript_messages=transcript_messages,
        budget=transcript_ref_budget,
    )

    return ComposedPrompt(
        memory_section=memory_section,
        whats_new_section=whats_new_section,
        synthesis_section=synthesis_section,
        transcript_ref=transcript_ref,
        user_message=user_message,
        dropped_memory=dropped_memory,
        dropped_whats_new=dropped_whats_new,
        dropped_synthesis=dropped_synthesis,
    )


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


async def _curated_memory(
    *,
    user_message: str,
    bank_id: str | None,
    backend: MemoryRecallLike | None,
    budget: int,
    topk: int,
) -> tuple[str, list[str]]:
    if backend is None or bank_id is None:
        return "", []
    try:
        entries = await backend.recall(user_message, bank_id, limit=topk)
    except Exception as e:  # noqa: BLE001
        logger.warning("compose_turn_prompt: memory recall failed: %s", e)
        return "", []
    if not entries:
        return "", []
    lines: list[str] = [
        f"## Memory (curated, top {len(entries)})",
        "",
    ]
    kept: list[str] = []
    dropped: list[str] = []
    used = _approx_tokens("\n".join(lines))
    for entry in entries:
        content = _entry_content(entry)
        line = f"- {content}"
        cost = _approx_tokens(line) + 1
        if used + cost > budget:
            dropped.append(content[:80])
            continue
        lines.append(line)
        used += cost
        kept.append(content[:80])
    if kept:
        return "\n".join(lines), dropped
    return "", [content[:80] for content in (e.content if hasattr(e, "content") else str(e) for e in entries)]


async def _whats_new(
    *,
    session: Any,
    project_dir: Path | None,
    git_snapshotter: GitSnapshotterLike | None,
    backend: MemoryRecallLike | None,
    bank_id: str | None,
    is_first_turn: bool,
    now: datetime,
    budget: int,
    topk: int,
) -> tuple[str, list[str]]:
    """Build the multi-source "what's new" section.

    The first turn of a new session has no prior recall_ts / git
    snapshot, so the section is empty (the plan: "session B
    initialises last_memory_recall_ts to 'now'"). On subsequent
    turns, we include:
    - [memory] entries with ts > session.last_memory_recall_ts
    - [git] the diff between last_git_snapshot and now
    """
    if is_first_turn:
        return "", []

    lines: list[str] = ["## What's New", ""]
    used = _approx_tokens("\n".join(lines))
    dropped: list[str] = []

    # Memory entries since last recall. The ``since_ts`` callback
    # is distinct from the relevance-routed memory_recall: "what's
    # new" is a temporal filter (ts > last_recall_ts), not a
    # relevance filter. The composer is the gate; the backend
    # returns the candidate set.
    if backend is not None and bank_id is not None:
        # The ``backend`` here is the same object the chat loop
        # passed in (MemoryRecallLike protocol). For the "what's
        # new" pass we call the same .recall() with a broad query;
        # the composer filters by ts > last_recall_ts. A
        # purpose-built ``recall_since`` is the v2 framing; the
        # composer's v1 does relevance+filter, which is what
        # today's backends can answer.
        try:
            entries = await backend.recall(
                "*", bank_id, limit=20
            )
        except Exception:  # noqa: BLE001
            entries = []
        # The composer's caller must supply entries-with-ts that
        # passed the backend's recall. For the v1 framing, we
        # expect the caller to have already filtered; the composer
        # here just formats. A real backend integration lands in
        # step 5 / R6; the composer's behaviour is pinned by tests.
        for entry in entries:
            ts = getattr(entry, "ts", None)
            if ts is None or ts <= session.last_memory_recall_ts:
                continue
            content = _entry_content(entry)
            line = f"- [memory] {content}"
            cost = _approx_tokens(line) + 1
            if used + cost > budget:
                dropped.append(content[:80])
                continue
            lines.append(line)
            used += cost

    # Git diff since last snapshot
    if (
        git_snapshotter is not None
        and project_dir is not None
        and session.last_git_snapshot is not None
    ):
        diff = git_snapshotter.diff_since(
            project_dir, session.last_git_snapshot
        )
        if diff:
            line = f"- [git] Changes since last snapshot:\n{diff}"
            cost = _approx_tokens(line) + 1
            if used + cost > budget:
                dropped.append("git diff")
            else:
                lines.append(line)
                used += cost

    if len(lines) > 2:  # has actual content
        return "\n".join(lines), dropped
    return "", dropped


def _transcript_reference(
    *,
    transcript_messages: list[Any] | None,
    budget: int,
) -> str:
    """Build a one-paragraph transcript reference.

    NOT the full transcript. The full conversation lives in
    ``Session.messages`` for the audit trail + the UI; the LLM
    sees only a short reference so the per-turn prompt size is
    bounded regardless of conversation length.
    """
    if not transcript_messages:
        return ""
    # One short paragraph: count messages + last user message
    # (truncated). The runtime never inlines the full transcript.
    n = len(transcript_messages)
    last_user = next(
        (
            m
            for m in reversed(transcript_messages)
            if getattr(m, "role", None) == "user"
        ),
        None,
    )
    last_user_text = (
        getattr(last_user, "content", "") if last_user is not None else ""
    )
    last_user_text = truncate_to_tokens(last_user_text, budget // 2)
    ref = (
        f"## Transcript Reference\n"
        f"Conversation has {n} messages. "
        f"Most recent user message: {last_user_text}"
    )
    if _approx_tokens(ref) > budget:
        ref = truncate_to_tokens(ref, budget)
    return ref


def _entry_content(entry: Any) -> str:
    if hasattr(entry, "content"):
        return str(entry.content)
    return str(entry)
