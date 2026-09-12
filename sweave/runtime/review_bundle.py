"""Review bundle builder (REVIEW_PLAN Phase 1, step 1).

Entering ``review`` captures what the human needs to judge the work:

* unified diff + file list + stats, written to
  ``{project}/.sweave/reviews/{id}.diff`` (a FILE, not inline —
  ``delegations.json`` stays small), with a small
  ``ReviewBundle`` pointer (``{path, bytes, truncated, scope}``) on
  the record;
* captured synchronously on the review transition (the worktree may
  move on; later is never).

Scopes:

* ``worktree`` — the delegation has a worktree dir that is a git
  repo: ``git diff <base>`` (working tree vs base, so committed +
  staged + unstaged tracked work all show) plus untracked files
  (``git status`` ``??`` entries, appended as synthetic
  ``/dev/null``-vs-file sections and marked — new files are the
  most common uncommitted specialist output and must not read as
  "nothing to review") plus ``git diff --stat <base>`` for the
  file list. Base is the first resolvable of the configured base
  branch then ``main``/``master``.
* ``paths`` — in-tree delegation (no worktree) WITH
  ``manifest.files_touched``: ``git diff HEAD -- <files>`` in the
  project dir (missing files are skipped, never fatal).
* ``unscoped`` — in-tree WITHOUT file list: ``git status
  --porcelain`` + capped ``git diff HEAD``, flagged HONESTLY (may
  include unrelated user changes — shown, never hidden).
* ``missing:<reason>`` — degraded pointer without a diff file
  (``missing-worktree``, ``not-a-repo``, ``no-base``...). The
  transition still lands; the trace names the reason.

Secrets: every diff body passes through :func:`redact_secrets`
(the Phase-1 minimal boundary — regex over known secret shapes,
value replaced by ``[REDACTED:<kind>]``; the full tag-and-vault is
still R4.4). A review must never become a secret-exfil surface.

Sizes: the stored body is capped at ``MAX_BUNDLE_BYTES``
(truncation recorded on the pointer + artifact header).
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from sweave.platform import run_no_window

logger = logging.getLogger(__name__)


#: Stored diff-body cap (executor pick, pinned by test). Large
#: enough for real reviews, small enough that the reviews dir never
#: becomes a second trace store.
MAX_BUNDLE_BYTES = 256 * 1024


#: (kind, compiled pattern) pairs. Order matters: specific shapes
#: first, the generic assignment catch-all last. Every pattern MUST
#: capture the secret value in group 1 (or be a full-value match for
#: the block kinds) — the replacer keeps the surrounding text.
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access", re.compile(r"\b(AKIA[0-9A-Z]{16})\b")),
    ("github_token",
     re.compile(r"\b((?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{10,})\b")),
    ("slack_token", re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b")),
    ("bearer", re.compile(
        r"(?i)\b(bearer\s+)([A-Za-z0-9\-._~+/=]{12,})")),
    # Generic `name = value` / `name: value` assignments for
    # secret-ish names (quoted or bare values). Group 1 = name,
    # group 2 = value.
    ("assignment", re.compile(
        r"(?i)\b((?:password|passwd|secret|api[_-]?key|access[_-]?token|"
        r"auth[_-]?token|private[_-]?key|client[_-]?secret))\b"
        r"\s*[:=]\s*"
        r"(?:\"([^\"]{4,})\"|'([^']{4,})'|([A-Za-z0-9\-._~+/=]{4,}))")),
    ("pem_block", re.compile(
        r"(-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----)"
        r".*?"
        r"(-----END [A-Z0-9 ]*PRIVATE KEY-----)", re.DOTALL)),
]


def redact_secrets(text: str) -> tuple[str, int]:
    """Redact known secret shapes in *text*.

    Returns ``(redacted_text, count)``. Values become
    ``[REDACTED:<kind>]``; surrounding text (names, structure) is
    kept so the diff stays reviewable. Never raises on odd input —
    the bundle path must not fail a review transition.
    """
    count = 0

    def _one(kind: str, pattern: re.Pattern[str], body: str) -> str:
        nonlocal count

        def _repl(m: re.Match[str]) -> str:
            nonlocal count
            count += 1
            # Assignment kind: keep the name, redact the value
            # (whichever quote-group matched).
            if kind == "assignment":
                name = m.group(1)
                return f"{name}=[REDACTED:{kind}]"
            if kind == "bearer":
                return f"{m.group(1)}[REDACTED:{kind}]"
            if kind == "pem_block":
                return f"{m.group(1)}\n[REDACTED:{kind}]\n{m.group(2)}"
            return f"[REDACTED:{kind}]"

        try:
            return pattern.sub(_repl, body)
        except Exception:  # noqa: BLE001
            logger.warning("review_bundle: redaction pass %s failed", kind)
            return body

    out = text
    for kind, pattern in _SECRET_PATTERNS:
        out = _one(kind, pattern, out)
    return out, count


def _git(cwd: Path, *args: str, timeout: float = 30.0) -> tuple[bool, str]:
    """Run ``git`` in *cwd*. Returns ``(ok, stdout-text)``; any
    failure (missing binary, not a repo, bad ref) is ``(False,
    "")`` — callers degrade, never raise."""
    try:
        proc = run_no_window(
            ["git", *args],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001
        return False, ""
    if proc.returncode != 0:
        return False, ""
    return True, proc.stdout or ""


def _untracked_sections(
    cwd: Path, *, per_file_cap: int = 32 * 1024
) -> tuple[str, list[str]]:
    """Synthetic diff sections for untracked files.

    ``git diff <base>`` never shows untracked files; a review that
    omits them reads as "nothing to review" for exactly the
    delegations that created new files. Each readable file becomes a
    ``/dev/null``-vs-file section (binary/unreadable entries become
    a one-line note, never content). Returns ``(text, rel_paths)``.
    """
    _, status = _git(cwd, "status", "--porcelain")
    rels = [
        line[3:].strip().strip('"')
        for line in status.splitlines()
        if line.startswith("?? ")
    ]
    sections: list[str] = []
    for rel in rels:
        if not rel:
            continue
        path = cwd / rel
        try:
            if not path.is_file():
                continue
            raw = path.read_bytes()[:per_file_cap]
        except OSError:
            continue
        if b"\x00" in raw[:4096]:
            sections.append(
                f"--- /dev/null\n+++ b/{rel}\n"
                "@@ -0,0 +0 @@\n[binary file, content omitted]\n"
            )
            continue
        text = raw.decode("utf-8", errors="replace")
        body = "".join(f"+{line}\n" for line in text.splitlines())
        n = len(text.splitlines())
        sections.append(
            f"--- /dev/null\n+++ b/{rel}\n"
            f"@@ -0,0 +1,{n} @@\n{body}"
        )
    return "".join(sections), rels


def _is_repo(path: Path) -> bool:
    ok, _ = _git(path, "rev-parse", "--git-dir")
    return ok


def _resolve_base(cwd: Path, preferred: str | None = None) -> str | None:
    """First resolvable base branch: preferred, then main/master."""
    candidates = [c for c in [preferred, "main", "master"] if c]
    for branch in candidates:
        ok, _ = _git(cwd, "rev-parse", "--verify", f"refs/heads/{branch}")
        if ok:
            return branch
    return None


def build_review_bundle(
    *,
    delegation_id: str,  # noqa: ARG001 — kept for call-site clarity
    worktree_path: str | None,
    manifest_files: list[str] | None,
    project_dir: Path | None = None,
    base_branch: str | None = None,
    max_bytes: int = MAX_BUNDLE_BYTES,
) -> dict[str, Any]:
    """Build the review material for one delegation.

    Returns a dict with ``scope`` (worktree/paths/unscoped/
    missing:<reason>), ``diff`` (redacted body, may be ""), ``files``
    (from ``--stat`` or the manifest list), ``stats`` (raw stat text),
    ``redactions`` (count), ``truncated`` (bool), and ``base``
    (resolved base branch or None). Pure computation — no disk
    writes (the runner persists the artifact).
    """
    files: list[str] = []
    stats = ""
    base: str | None = None

    wt = Path(worktree_path) if worktree_path else None
    if wt is not None and wt.is_dir() and _is_repo(wt):
        base = _resolve_base(wt, preferred=base_branch)
        if base is None:
            return {
                "scope": "missing:no-base", "diff": "", "files": [],
                "stats": "", "redactions": 0, "truncated": False,
                "base": None,
            }
        _, stats = _git(wt, "diff", "--stat", base)
        _, diff = _git(wt, "diff", base)
        for line in stats.splitlines():
            # ` a/b | 12 +++---` — last `|`-separated field is the
            # graph; the path is the first field.
            if "|" in line:
                files.append(line.split("|")[0].strip())
        # Untracked files are invisible to the diff above; append
        # them as marked sections (new files are the most common
        # uncommitted specialist output).
        extra, extra_files = _untracked_sections(wt)
        if extra:
            diff += ("\n" if diff and not diff.endswith("\n") else "") + extra
            for rel in extra_files:
                if rel not in files:
                    files.append(rel)
        scope = "worktree"
    elif wt is not None:
        return {
            "scope": "missing-worktree", "diff": "", "files": [],
            "stats": "", "redactions": 0, "truncated": False,
            "base": None,
        }
    else:
        # In-tree: project dir is the repo (or nowhere).
        repo = project_dir if project_dir is not None else None
        if repo is None or not repo.is_dir() or not _is_repo(repo):
            return {
                "scope": "missing-worktree", "diff": "", "files": [],
                "stats": "", "redactions": 0, "truncated": False,
                "base": None,
            }
        touched = [f for f in (manifest_files or []) if f]
        if touched:
            _, diff = _git(repo, "diff", "HEAD", "--", *touched)
            _, stats = _git(repo, "diff", "--stat", "HEAD", "--", *touched)
            files = list(touched)
            scope = "paths"
        else:
            _, status = _git(repo, "status", "--porcelain")
            _, diff = _git(repo, "diff", "HEAD")
            stats = status
            files = [
                line[3:] for line in status.splitlines() if len(line) > 3
            ]
            scope = "unscoped"

    redacted, redactions = redact_secrets(diff)
    body = redacted.encode("utf-8", errors="replace")
    truncated = len(body) > max_bytes
    if truncated:
        # Cut on a byte boundary that stays valid UTF-8 (the
        # artifact is read as text by the detail surface).
        cut = body[:max_bytes]
        body = cut.decode("utf-8", errors="ignore").encode("utf-8")
    return {
        "scope": scope,
        "diff": body.decode("utf-8", errors="replace"),
        "files": files,
        "stats": stats,
        "redactions": redactions,
        "truncated": truncated,
        "base": base,
    }


def artifact_header(
    *,
    delegation_id: str,
    agent: str,
    scope: str,
    base: str | None,
    files: list[str],
    stats: str,
    redactions: int,
    truncated: bool,
) -> str:
    """Human-readable header prepended to the stored ``.diff`` file."""
    lines = [
        f"# review bundle for {delegation_id} (agent: {agent})",
        f"# scope: {scope}",
        f"# base: {base or '(none)'}",
        f"# files: {len(files)}",
        f"# redactions: {redactions}",
        f"# truncated: {truncated}",
        "#",
        "# scopes: worktree = worktree-vs-base; paths = manifest "
        "files vs HEAD; unscoped = worktree-wide vs HEAD (may "
        "include unrelated user changes — shown, never hidden).",
        "",
    ]
    if stats.strip():
        lines.append("# --stat --")
        lines.extend(f"# {s}" for s in stats.splitlines())
        lines.append("")
    return "\n".join(lines)


def write_bundle_artifact(
    *,
    project_dir: Path,
    delegation_id: str,
    agent: str,
    bundle: dict[str, Any],
) -> tuple[Path, int]:
    """Persist the artifact; returns ``(path, bytes_written)``."""
    reviews_dir = project_dir / ".sweave" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    header = artifact_header(
        delegation_id=delegation_id,
        agent=agent,
        scope=bundle["scope"],
        base=bundle.get("base"),
        files=bundle.get("files", []),
        stats=bundle.get("stats", ""),
        redactions=bundle.get("redactions", 0),
        truncated=bundle.get("truncated", False),
    )
    content = header + bundle.get("diff", "")
    data = content.encode("utf-8", errors="replace")
    path = reviews_dir / f"{delegation_id}.diff"
    # Binary-mode write (repo rule: never PowerShell redirection;
    # utf-8 bytes round-trip exactly).
    with open(path, "wb") as f:
        f.write(data)
    return path, len(data)
