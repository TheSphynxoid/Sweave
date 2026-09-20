"""Session auto-titling (background nano-turn, never blocking chat).

A Sweave session starts life as ``Session 2026-.. ..:..`` (or a user
name). After the first completed exchange, a fire-and-forget
micro-turn asks a cheap model for a ≤6-word title and renames the
session — the opencode/Copilot pattern, minus their provider
lock-in: the model resolves cost-first (cheapest $0 text model that
is engine-reachable and keyed right now) with an explicit
``titling.model`` config override.

Hard rules (the failure modes this design refuses):
* never blocks or fails the chat turn (fire-and-forget; every error
  keeps the timestamp);
* never overwrites a user-chosen name (fires once, only while the
  name is still the timestamp default);
* never spends money (free-tier only; no paid fallback — a $0 model
  that flakes keeps the timestamp);
* never touches host inference (cloud call, ~200 in / ~10 out
  tokens, short timeout).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Timestamp-default detection (mirrors
#: ``ProjectManager.create_session`` exactly — a user-typed identical
#: string is accepted as default; vanishingly rare, harmless).
DEFAULT_NAME_RE = re.compile(r"^Session \d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

#: Exactly-once marker in ``session.context`` (persisted with the
#: session file, so restarts don't re-fire).
TITLE_ATTEMPTED_KEY = "title_attempted"

TITLE_MAX_WORDS = 6
TITLE_MAX_CHARS = 60
TITLE_TIMEOUT_S = 90.0
TITLE_USER_CHARS = 500
TITLE_REPLY_CHARS = 500
TITLE_MAX_ATTEMPTS = 3


def is_default_name(name: Any) -> bool:
    """True for the untouched timestamp default (and nothing else)."""
    return isinstance(name, str) and DEFAULT_NAME_RE.match(name) is not None


#: Trailing words that dangle after a word-count cut ("Fix the
#: login with", "Status update: backends are not yet" is fine but
#: "retested with" is not). Dropped from the tail so cuts land on
#: content words.
DANGLING_TAIL_WORDS = frozenset(
    "a an the and or with for of to in on at by from is are was were be as &".split()
)


def clean_title(text: Any) -> str | None:
    """Normalize one model reply into a session title (or None).

    Strips quotes/preamble ("Title:"-style prefixes), collapses
    whitespace, caps at TITLE_MAX_WORDS words + TITLE_MAX_CHARS
    chars — both cuts land on word boundaries with no dangling tail
    words, never mid-word. Degenerate outputs (empty, error-shaped)
    degrade to None — the timestamp stands.
    """
    if not isinstance(text, str):
        return None
    cleaned = " ".join(text.strip().split())
    # Strip wrapping quotes and "Title:"-style preambles.
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'":
        cleaned = cleaned[1:-1].strip()
    lowered = cleaned.lower()
    for prefix in ("title:", "session title:", "suggested title:"):
        if lowered.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            lowered = cleaned.lower()
            break
    if not cleaned or cleaned.startswith("[chat error:"):
        return None
    words = cleaned.split()
    if len(words) > TITLE_MAX_WORDS:
        words = words[:TITLE_MAX_WORDS]
    while len(words) > 1 and words[-1].lower().rstrip(":") in DANGLING_TAIL_WORDS:
        words.pop()
    cleaned = " ".join(words)
    if len(cleaned) > TITLE_MAX_CHARS:
        cut = cleaned[:TITLE_MAX_CHARS].rsplit(" ", 1)[0]
        cleaned = cut if cut else cleaned[:TITLE_MAX_CHARS]
    cleaned = cleaned.strip()
    if not cleaned or len(cleaned) > 200:
        return None
    return cleaned or None


def build_title_prompt(user_text: str, reply_text: str) -> str:
    """The titling micro-prompt (inputs capped — titles cost tokens)."""
    user = (user_text or "")[:TITLE_USER_CHARS]
    reply = (reply_text or "")[:TITLE_REPLY_CHARS]
    return (
        "Title this conversation in 6 words or fewer. "
        "Reply with ONLY the title, no quotes, no preamble.\n\n"
        f"User: {user}\nAssistant: {reply}"
    )


#: Non-chat model families (embedders, rerankers, STT/TTS, image
#: generators) whose meta modalities still claim text. A title turn
#: on one returns vectors, not prose — and clean_title would happily
#: persist "[0.12, -0.4, ..." as a session name. Substring match on
#: the qualified id; over-exclusion only shrinks the candidate pool
#: (safe direction), and the silent absorb covers the rest.
NON_CHAT_PATTERNS = (
    "embed",
    "bge",
    "rerank",
    "whisper",
    "tts",
    "flux",
    "sdxl",
    "dall-e",
    "stable-diffusion",
)


def free_text_candidates(
    meta_path: Path | None = None,
) -> list[tuple[str, str]]:
    """Cheapest $0 text-capable ``(provider, model)`` pairs, sorted.

    Reads ``models.meta.json`` (cost + modalities): input cost 0,
    text in/out, minus NON_CHAT_PATTERNS. Excludes ``gemini*`` ids
    (google flavor has no engine transport — attempting one burns
    an attempt). Sorted by (provider, model) for determinism. Never
    raises (missing file reads as no candidates).
    """
    if meta_path is None:
        meta_path = Path(__file__).resolve().parents[2] / "models.meta.json"
    try:
        import json

        raw = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — no meta, no candidates
        return []
    if not isinstance(raw, dict):
        return []
    out: list[tuple[str, str]] = []
    for qualified, entry in raw.items():
        if not isinstance(qualified, str) or "/" not in qualified:
            continue
        if not isinstance(entry, dict):
            continue
        cost = entry.get("cost") or {}
        if not isinstance(cost, dict) or cost.get("input") != 0:
            continue
        modalities = entry.get("modalities") or {}
        if not isinstance(modalities, dict):
            continue
        if "text" not in (modalities.get("input") or []) or "text" not in (
            modalities.get("output") or []
        ):
            continue
        lowered = qualified.lower()
        if "gemini" in lowered:
            continue
        if any(pattern in lowered for pattern in NON_CHAT_PATTERNS):
            continue
        provider, model_id = qualified.split("/", 1)
        out.append((provider, model_id))
    out.sort()
    return out


def resolve_title_models(
    *,
    explicit: str | None = None,
    credential_source: Callable[[str], Any | None] | None = None,
    candidates: list[tuple[str, str]] | None = None,
    max_models: int = TITLE_MAX_ATTEMPTS,
) -> list[str]:
    """Ordered ``provider/model`` picks for the title turn.

    Explicit config wins verbatim (validated lightly — reachability
    is the turn's problem, silently absorbed). Otherwise the
    cheapest $0 text models whose provider currently resolves a
    credential. Empty = don't attempt (timestamp stands).
    """
    if explicit is not None and str(explicit).strip():
        value = str(explicit).strip()
        if "/" not in value:
            return []
        return [value]
    if candidates is None:
        candidates = free_text_candidates()
    if credential_source is None:
        # Default to the real credential tiers (env → sweave →
        # opencode-legacy), same predicate the Providers tab uses.
        # CredentialStore is cheap to construct (home-anchored reads,
        # no writes).
        try:
            from sweave.credentials import CredentialStore
            from sweave.credentials import credential_source as real_source

            _store = CredentialStore()

            def _source(provider: str) -> Any | None:
                return real_source(provider, _store)

            credential_source = _source
        except Exception:  # noqa: BLE001 — no tiers, no attempt
            return []
    picked: list[str] = []
    for provider, model_id in candidates:
        # No engine transport speaks the google flavor (resolve
        # would fail the turn pre-work): skip gemini rows even when
        # keyed. The silent absorb below would catch it, but picking
        # a known-dead model first wastes the turn budget.
        if "gemini" in model_id.lower():
            continue
        try:
            if credential_source(provider) is None:
                continue
        except Exception:  # noqa: BLE001 — a broken source skips, never kills
            continue
        picked.append(f"{provider}/{model_id}")
        if len(picked) >= max(1, max_models):
            break
    return picked


async def request_title(
    *,
    project_manager: Any,
    publish: Callable[..., Any],
    session_id: str,
    user_text: str,
    reply_text: str,
    models: list[str],
    worktree_path: str | Path | None,
    timeout: float = TITLE_TIMEOUT_S,
    harness_factory: Callable[[], Any] | None = None,
) -> str | None:
    """Run one background title attempt. Returns the title or None.

    Never raises (every failure keeps the timestamp): model turn
    failure, degenerate title, session vanished/renamed meanwhile,
    rename or publish failure. Re-verifies the default name AFTER
    the turn — a user rename mid-flight always wins.
    """
    if not models:
        return None
    # Pre-check the name BEFORE spending any turn: a custom-named
    # session never titles (the ChatLoop flag usually prevents even
    # reaching here; this is the belt-and-suspenders for direct
    # callers).
    try:
        session = project_manager.get_session_meta(session_id)
    except Exception:  # noqa: BLE001
        return None
    if session is None or not is_default_name(session.name):
        return None
    prompt = build_title_prompt(user_text, reply_text)
    title: str | None = None
    for model in models:
        try:
            text = await asyncio.wait_for(
                _run_title_turn(
                    model,
                    prompt,
                    worktree_path,
                    timeout,
                    harness_factory=harness_factory,
                ),
                timeout=timeout + 30.0,
            )
        except Exception as turn_err:  # noqa: BLE001 — next candidate
            logger.info("titling: turn failed on %s: %s", model, turn_err)
            continue
        title = clean_title(text)
        if title:
            break
    if not title:
        return None
    try:
        session = project_manager.get_session_meta(session_id)
        if session is None or not is_default_name(session.name):
            return None
        renamed = project_manager.rename_session(session_id, title)
        event = {
            "id": renamed.id,
            "name": renamed.name,
            "project_name": renamed.project_name,
        }
        try:
            result = publish("session.renamed", event)
            if hasattr(result, "__await__"):
                await result
        except Exception as pub_err:  # noqa: BLE001 — rename stands
            logger.warning("titling: publish failed for %s: %s", session_id, pub_err)
        return title
    except Exception as rename_err:  # noqa: BLE001
        logger.warning("titling: rename failed for %s: %s", session_id, rename_err)
        return None


async def _run_title_turn(
    model: str,
    prompt: str,
    worktree_path: str | Path | None,
    timeout: float,
    harness_factory: Callable[[], Any] | None = None,
) -> str:
    """One tools-less engine turn on a throwaway session."""
    from sweave.harness.base import AgentSpec, Message
    from sweave.harness.engine import SweaveEngineHarness

    harness = harness_factory() if harness_factory is not None else SweaveEngineHarness()
    spec = AgentSpec(
        name="titler",
        role="specialist",
        model=model,
        system_prompt="",
        worktree_path=Path(worktree_path) if worktree_path else Path.home() / ".sweave",
        memory_bank="",
        tools=[],
        harness="sweave-engine",
    )
    proc = await harness.spawn(spec)
    try:
        result = await proc.send(
            Message(type="user", content=prompt, metadata={"turn_timeout": timeout}),
        )
    finally:
        try:
            await proc.terminate()
        except Exception:  # noqa: BLE001 — best-effort teardown
            pass
    if not result.success:
        raise RuntimeError(result.error or "title turn failed")
    return result.output or ""
