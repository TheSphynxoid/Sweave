"""build_context() extension point (engine step 3, server-side).

The orchestrator owns ALL knowledge; the engine receives finished
context, never builds it. This module loads the session-stable basics
(instruction files, skill index) and enforces a cross-section budget
with a ``context.built {sections, tokens, dropped}`` audit — on every
harness, identically (engine-agnostic by construction).

Standards adopted verbatim (never re-designed here; sources in
``docs/CUSTOM_ENGINE_PLAN.md`` "basics standards" appendix):

* Instruction files: AGENTS.md (Linux Foundation open standard).
  Discovery mirrors Codex: global file -> project root -> worktree
  walk (root-down, at most one file per dir, blank-joined, empty
  skipped, 32 KiB total cap). Session-scoped cache: re-inject only
  on new session, worktree change, file change (content-gated),
  or explicit invalidate (post-compaction / post-revert rewind).
* Skills: SKILL.md (agentskills.io open spec). ``skills/{name}/
  SKILL.md`` with required ``name`` + ``description`` frontmatter;
  progressive disclosure L1 (index, standing) -> L2 (body, on
  trigger) -> L3 (bundled files, as needed). Read-not-run v1: no
  code execution by Sweave itself, zero new MCP tools (the agent
  reads bodies itself via ``read``; the native engine via its own
  read path).
* Compaction: opencode mechanics (threshold preflight + keep-tokens
  tail + anchored summary template + skill-output prune protection);
  the engine-side compactor implementation lands with the engine —
  this module only owns the invalidate hook.
* todo: opencode ``todowrite`` shape (full-list write
  ``{content, status, priority}``, one ``in_progress`` at a time);
  the tool itself is step-2 executor scope, not here.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from sweave.chat.synthesis import _approx_tokens

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Budgets + priorities (higher kept first when over the total budget)
# ---------------------------------------------------------------------------

#: Total instruction-chain cap — Codex ``project_doc_max_bytes``
#: parity (32 KiB default).
INSTRUCTION_MAX_BYTES = 32 * 1024

#: Skill-index (L1) token cap. ~100 tokens/skill steady-state, so this
#: is ~10 skills before the drop policy engages.
SKILL_INDEX_BUDGET = 1_000

#: SKILL.md body guide — agentskills.io recommends <5000 tokens /
#: <500 lines; bodies past it are still loaded (the agent needs
#: them), but the length is recorded in the audit.
SKILL_BODY_GUIDE_TOKENS = 5_000

PRIORITY_INSTRUCTIONS = 100
PRIORITY_SYNTHESIS = 90
PRIORITY_MEMORY = 80
PRIORITY_SKILLS = 60
PRIORITY_WHATS_NEW = 50
PRIORITY_TRANSCRIPT_REF = 40

#: Session-cache entry cap (FIFO eviction). Fingerprints gate
#: staleness, so this bounds memory only, never correctness.
CACHE_MAX_ENTRIES = 512

_VALID_SKILL_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")


# ---------------------------------------------------------------------------
# Instruction files (AGENTS.md standard)
# ---------------------------------------------------------------------------


def _global_dir(explicit: Path | None = None) -> Path:
    """Sweave home root. Resolved at CALL time (never a module
    constant) so tests can redirect home; pass *explicit* to skip
    the home lookup entirely."""
    if explicit is not None:
        return explicit
    return Path.home() / ".sweave"


def discover_instruction_files(
    project_dir: Path | None,
    worktree_dir: Path | None = None,
    *,
    global_dir: Path | None = None,
) -> list[Path]:
    """Return the instruction chain, root-down (global first).

    Order: ``<global>/AGENTS.md`` -> project root -> worktree walk
    (each ancestor from the project root down to the worktree, at
    most one file per dir: ``AGENTS.md`` wins, ``CLAUDE.md`` is the
    fallback). Missing/empty handling happens at load time, not
    here — discovery only names candidates. Resolved paths are
    deduplicated (project_dir == worktree_dir yields one entry).
    """
    chain: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            chain.append(path)

    _add(_global_dir(global_dir) / "AGENTS.md")
    if project_dir is None:
        return chain
    project_dir = Path(project_dir)
    worktree_dir = Path(worktree_dir) if worktree_dir is not None else project_dir
    # Ancestor walk: project root down to the worktree (Codex
    # nested-file parity — closest file wins on conflict because it
    # sorts later in the concatenated chain).
    dirs: list[Path] = []
    cursor: Path | None = worktree_dir
    while cursor is not None:
        dirs.append(cursor)
        if cursor == project_dir:
            break
        parent = cursor.parent
        if parent == cursor:  # filesystem root — escaped the project
            break
        cursor = parent
    else:  # pragma: no cover - defensive; loop always breaks/skips
        dirs = []
    for directory in reversed(dirs):
        if not directory.is_dir():
            continue
        agentic = directory / "AGENTS.md"
        if agentic.is_file():
            _add(agentic)
        else:
            claude = directory / "CLAUDE.md"
            if claude.is_file():
                _add(claude)
    return chain


def _resolve_claude_imports(text: str, base_dir: Path) -> str:
    """Inline ``@path`` imports in a CLAUDE.md wrapper (one level).

    The ecosystem convention is a one-line ``@AGENTS.md`` wrapper;
    imports resolve relative to the wrapper's dir, are cycle-guarded,
    and only ``.md`` targets inline (anything else stays verbatim).
    Unknown/missing targets stay verbatim — never an error.
    """
    out: list[str] = []
    seen = {str(base_dir / "__self__")}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("@") and len(stripped) > 1:
            target = (base_dir / stripped[1:].strip()).resolve()
            key = str(target).lower()
            if (
                target.suffix.lower() == ".md"
                and key not in seen
                and target.is_file()
            ):
                seen.add(key)
                try:
                    out.append(
                        target.read_text(encoding="utf-8", errors="replace")
                    )
                    continue
                except OSError:  # noqa: BLE001
                    pass
        out.append(line)
    return "\n".join(out)


@dataclass
class InstructionChain:
    """A loaded instruction chain (one entry per candidate file)."""

    text: str = ""
    files: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    sha: str = ""


def load_instruction_chain(files: list[Path]) -> InstructionChain:
    """Read + concatenate *files* (root-down, blank-joined).

    Skips missing/empty files with a recorded reason; applies the
    32 KiB total cap (Codex parity) with truncation recorded. Never
    raises on I/O — an unreadable file is a skipped file.
    """
    parts: list[str] = []
    metas: list[dict[str, Any]] = []
    used_bytes = 0
    truncated = False
    for path in files:
        meta: dict[str, Any] = {"path": str(path)}
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            meta.update({"included": False, "reason": "unreadable"})
            metas.append(meta)
            continue
        if path.name == "CLAUDE.md":
            raw = _resolve_claude_imports(raw, path.parent)
        if not raw.strip():
            meta.update({"included": False, "reason": "empty"})
            metas.append(meta)
            continue
        encoded = raw.encode("utf-8")
        meta["bytes"] = len(encoded)
        meta["sha"] = hashlib.sha256(encoded).hexdigest()[:16]
        room = INSTRUCTION_MAX_BYTES - used_bytes
        if room <= 0:
            meta.update({"included": False, "reason": "over-cap"})
            metas.append(meta)
            truncated = True
            continue
        if len(encoded) > room:
            cut = encoded[:room].decode("utf-8", errors="ignore")
            parts.append(cut)
            used_bytes += len(cut.encode("utf-8"))
            meta.update({"included": True, "truncated": True})
            truncated = True
        else:
            parts.append(raw)
            used_bytes += len(encoded)
            meta.update({"included": True})
        metas.append(meta)
    text = "\n\n".join(parts)
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else ""
    return InstructionChain(text=text, files=metas, truncated=truncated, sha=sha)


@dataclass
class InstructionSnapshot:
    """One session's cached instruction text + freshness proof."""

    text: str = ""
    status: str = "injected"  # injected | cached | empty
    sha: str = ""
    files: list[dict[str, Any]] = field(default_factory=list)


class InstructionCache:
    """Session-scoped instruction cache (change-gated, not per-turn).

    The hook runs pre-turn but the section is re-injected only on:
    new session key, worktree-identity change, file change
    (content hash — same-tick rewrites included), or explicit
    :meth:`invalidate`
    (post-compaction / post-revert rewind). The trace records
    cached-vs-injected + hash, so staleness is auditable.
    """

    def __init__(self, max_entries: int = CACHE_MAX_ENTRIES):
        self._entries: dict[str, dict[str, Any]] = {}
        self._max_entries = max_entries

    @staticmethod
    def _fingerprint(
        project_dir: Path | None,
        worktree_dir: Path | None,
        files: list[Path],
    ) -> str:
        # Content-hashed, not mtime-gated: same-tick rewrites (same
        # mtime_ns + size) are real on coarse filesystems and a
        # stat-only fingerprint would serve stale instructions. Chain
        # candidates are a handful of KB-scale files, so hashing per
        # turn is microseconds out of OS cache.
        h = hashlib.sha256()
        h.update(f"project={project_dir}".encode("utf-8"))
        h.update(f"worktree={worktree_dir}".encode("utf-8"))
        for path in files:
            try:
                content = path.read_bytes()
                digest = hashlib.sha256(content).hexdigest()[:16]
                h.update(f"{path}:{digest};".encode("utf-8"))
            except OSError:
                h.update(f"{path}:missing;".encode("utf-8"))
        return h.hexdigest()[:16]

    def get(
        self,
        session_key: str,
        project_dir: Path | None,
        worktree_dir: Path | None = None,
        *,
        global_dir: Path | None = None,
    ) -> InstructionSnapshot:
        files = discover_instruction_files(
            project_dir, worktree_dir, global_dir=global_dir
        )
        fingerprint = self._fingerprint(project_dir, worktree_dir, files)
        entry = self._entries.get(session_key)
        if entry is not None and entry["fingerprint"] == fingerprint:
            entry["hits"] += 1
            snapshot: InstructionSnapshot = entry["snapshot"]
            return InstructionSnapshot(
                text=snapshot.text,
                status="cached" if snapshot.text else "empty",
                sha=snapshot.sha,
                files=snapshot.files,
            )
        chain = load_instruction_chain(files)
        snapshot = InstructionSnapshot(
            text=chain.text,
            status="injected" if chain.text else "empty",
            sha=chain.sha,
            files=chain.files,
        )
        self._entries[session_key] = {
            "fingerprint": fingerprint,
            "snapshot": snapshot,
            "hits": 0,
        }
        while len(self._entries) > self._max_entries:
            self._entries.pop(next(iter(self._entries)))
        return snapshot

    def invalidate(self, session_key: str) -> None:
        """Force re-injection next turn (post-compaction/revert)."""
        self._entries.pop(session_key, None)


#: Process-wide instruction cache. Keyed by Sweave session id +
#: content fingerprint (fingerprints gate staleness, so sharing is
#: safe); tests pass their own instance.
INSTRUCTION_CACHE = InstructionCache()


# ---------------------------------------------------------------------------
# Skills (SKILL.md open spec, read-not-run v1)
# ---------------------------------------------------------------------------


@dataclass
class SkillRecord:
    """One validated skill (L1 metadata + L2/L3 locators)."""

    name: str
    description: str
    path: str  # SKILL.md location
    source: str  # project | global
    body_tokens: int = 0


def _parse_skill(path: Path, source: str) -> SkillRecord | str:
    """Parse + validate one SKILL.md. Returns the record, or a
    human-readable skip reason (invalid skills never fail a turn)."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unreadable"
    front: dict[str, Any] = {}
    body = raw
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end == -1:
            return "bad-frontmatter (unterminated)"
        try:
            front = yaml.safe_load(raw[3:end]) or {}
        except yaml.YAMLError:
            return "bad-frontmatter (unparseable)"
        if not isinstance(front, dict):
            return "bad-frontmatter (not a mapping)"
        body = raw[end + 4 :]
    name = front.get("name")
    description = front.get("description")
    if not name or not isinstance(name, str):
        return "missing required frontmatter: name"
    if not description or not isinstance(description, str):
        return "missing required frontmatter: description"
    if len(name) > 64 or _VALID_SKILL_NAME.match(name) is None:
        return f"invalid name {name!r} (1-64 chars, a-z 0-9 hyphen)"
    if "--" in name:
        return f"invalid name {name!r} (no consecutive hyphens)"
    if len(description) > 1024:
        return f"description over 1024 chars ({len(description)})"
    if name != path.parent.name:
        return f"name {name!r} != directory {path.parent.name!r}"
    return SkillRecord(
        name=name,
        description=description.strip(),
        path=str(path),
        source=source,
        body_tokens=_approx_tokens(body),
    )


def discover_skills(
    project_dir: Path | None,
    *,
    global_dir: Path | None = None,
) -> tuple[list[SkillRecord], list[dict[str, str]]]:
    """Find ``skills/{name}/SKILL.md``: project first, then global.

    Resolution mirrors the specialist store (project shadows global
    on name collision). Returns ``(skills, skipped)`` — invalid
    skills are audit rows, never turn failures.
    """
    roots: list[tuple[Path, str]] = []
    if project_dir is not None:
        roots.append((Path(project_dir) / "skills", "project"))
    roots.append((_global_dir(global_dir) / "skills", "global"))
    skills: dict[str, SkillRecord] = {}
    skipped: list[dict[str, str]] = []
    for root, source in roots:
        if not root.is_dir():
            continue
        for candidate in sorted(root.glob("*/SKILL.md")):
            parsed = _parse_skill(candidate, source)
            if isinstance(parsed, str):
                skipped.append({"path": str(candidate), "reason": parsed})
                continue
            if parsed.name in skills:
                skipped.append(
                    {
                        "path": str(candidate),
                        "reason": f"shadowed by {skills[parsed.name].source}",
                    }
                )
                continue
            skills[parsed.name] = parsed
    return list(skills.values()), skipped


def render_skill_index(
    skills: list[SkillRecord],
    budget: int = SKILL_INDEX_BUDGET,
) -> tuple[str, list[str]]:
    """Render the L1 index (``- name: description`` lines), capped.

    Bodies stay files the agent reads itself (opencode ``read``,
    native engine read path) — the index is what rides every turn.
    """
    if not skills:
        return "", []
    lines = ["## Skills (index — read the SKILL.md body when relevant)", ""]
    used = _approx_tokens("\n".join(lines))
    dropped: list[str] = []
    for skill in skills:
        line = f"- {skill.name}: {skill.description}"
        if used + _approx_tokens(line) + 1 > budget:
            dropped.append(skill.name)
            continue
        lines.append(line)
        used += _approx_tokens(line) + 1
    if len(lines) <= 2:
        return "", [s.name for s in skills]
    return "\n".join(lines), dropped


def read_skill_body(
    name: str,
    project_dir: Path | None,
    *,
    global_dir: Path | None = None,
) -> str | None:
    """L2 fetch: full SKILL.md body for *name* (None when absent)."""
    skills, _ = discover_skills(project_dir, global_dir=global_dir)
    for skill in skills:
        if skill.name == name:
            try:
                raw = Path(skill.path).read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                return None
            if raw.startswith("---"):
                end = raw.find("\n---", 3)
                if end != -1:
                    return raw[end + 4 :].lstrip("\n")
            return raw
    return None


# ---------------------------------------------------------------------------
# Cross-section budget (the build_context hook)
# ---------------------------------------------------------------------------


@dataclass
class ContextSection:
    """One named section offered to the turn context."""

    name: str
    text: str
    priority: int
    tokens: int = 0

    def __post_init__(self):
        if not self.tokens and self.text:
            self.tokens = _approx_tokens(self.text)


@dataclass
class ContextResult:
    """Finalized turn context: kept text + the audit trail."""

    text: str
    kept: list[str] = field(default_factory=list)
    dropped: list[dict[str, str]] = field(default_factory=list)
    audit: dict[str, Any] = field(default_factory=dict)
    texts: dict[str, str] = field(default_factory=dict)


class ContextBuilder:
    """Collect sections, enforce one total budget, audit everything.

    Drop policy: lowest priority first (ties: most-recently-added
    first). ``total_budget=None`` disables cross-section drops while
    still emitting the ``context.built`` audit — the composer default,
    so per-section caps keep their exact current behavior until a
    caller opts into a total.
    """

    def __init__(self):
        self._sections: list[ContextSection] = []

    def add(
        self,
        name: str,
        text: str,
        priority: int,
        tokens: int | None = None,
    ) -> None:
        if not text:
            return
        self._sections.append(
            ContextSection(
                name=name,
                text=text,
                priority=priority,
                tokens=tokens if tokens else _approx_tokens(text),
            )
        )

    def finalize(self, total_budget: int | None = None) -> ContextResult:
        indexed = list(enumerate(self._sections))
        # Drop order: lowest priority first; ties -> later-added first.
        drop_order = sorted(indexed, key=lambda t: (t[1].priority, -t[0]))
        kept_flags = [True] * len(self._sections)
        if total_budget is not None:
            used = sum(s.tokens for s in self._sections)
            for i, section in drop_order:
                if used <= total_budget:
                    break
                kept_flags[i] = False
                used -= section.tokens
        kept = [s for s, keep in zip(self._sections, kept_flags) if keep]
        dropped = [
            {"section": s.name, "reason": "over total budget"}
            for s, keep in zip(self._sections, kept_flags)
            if not keep
        ]
        total = sum(s.tokens for s in kept)
        audit: dict[str, Any] = {
            "sections": {s.name: s.tokens for s in kept},
            "tokens": total,
            "dropped": dropped,
        }
        return ContextResult(
            text="\n\n".join(s.text for s in kept),
            kept=[s.name for s in kept],
            dropped=dropped,
            audit=audit,
            texts={s.name: s.text for s in kept},
        )


def build_context(
    *,
    session_key: str,
    project_dir: Path | None,
    worktree_dir: Path | None = None,
    extra_sections: list[tuple[str, str, int]] | None = None,
    total_budget: int | None = None,
    instructions_text: str | None = None,
    skills_text: str | None = None,
    cache: InstructionCache | None = None,
    global_dir: Path | None = None,
) -> tuple[str, dict[str, Any], InstructionSnapshot]:
    """Build the session-stable half of the turn context.

    Loads instructions (session-cached) + skill index, merges
    caller-built *extra_sections* ``[(name, text, priority)]``
    (memory/synthesis/… — the composer owns those), enforces the
    total budget, and returns ``(text, audit, instruction_snapshot)``.
    ``audit`` is the ``context.built`` trace payload verbatim.
    Pass explicit *instructions_text*/*skills_text* to skip loading
    (tests + callers that already hold the text).
    """
    active_cache = cache if cache is not None else INSTRUCTION_CACHE
    if instructions_text is None:
        snapshot = active_cache.get(
            session_key, project_dir, worktree_dir, global_dir=global_dir
        )
        instructions_text = snapshot.text
    else:
        snapshot = InstructionSnapshot(
            text=instructions_text,
            status="provided",
            sha=(
                hashlib.sha256(instructions_text.encode("utf-8")).hexdigest()[
                    :16
                ]
                if instructions_text
                else ""
            ),
        )
    if skills_text is None:
        skills, _ = discover_skills(project_dir, global_dir=global_dir)
        skills_text, _skills_dropped = render_skill_index(skills)
    builder = ContextBuilder()
    builder.add("instructions", instructions_text or "", PRIORITY_INSTRUCTIONS)
    builder.add("skills", skills_text or "", PRIORITY_SKILLS)
    for name, text, priority in extra_sections or []:
        builder.add(name, text, priority)
    result = builder.finalize(total_budget)
    audit = dict(result.audit)
    audit["instructions_status"] = snapshot.status
    audit["instructions_sha"] = snapshot.sha
    return result.text, audit, snapshot
