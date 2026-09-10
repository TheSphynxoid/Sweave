"""M1.12 amendment 1 (2026-09-10): in-band permission bridge.

The out-of-band ask converter (stall watchdog →
``_resolve_pending_permission``) never fired on the 2026-09-10
incident (session ``Sweave-20260910-071906-787887``): two consecutive
900s turn deaths on a live ``external_directory`` ask, with no
``stalled`` trace event and no escalation record. This module moves
the ask→question bridge INTO the opencode process:

* A tiny opencode plugin (bundled here, copied ONCE to a sweave-owned
  config island ``~/.sweave/opencode/plugins/`) subscribes the
  ``permission.asked`` event in-process and POSTs the ask to Sweave's
  HTTP endpoint (``POST /api/permission/hijack``).
* ``ServeRunner`` injects ``OPENCODE_CONFIG_DIR`` at spawn, so the
  plugin is loaded ONLY by serves sweave launched -- a standalone
  opencode in the same repo neither sees nor runs it (a project-dir
  ``.opencode/plugins/`` copy would auto-execute in every standalone
  session; user ruled against that 2026-09-10).
* The endpoint scope-evaluates against the project record: inside the
  built-in/user roots → allow immediately; outside → blocking human
  question (EscalationStore, kind=permission, no timeout — the M1.11
  ruling). The answer is POSTed back to the pinned wire
  (``POST /session/{sid}/permissions/{rid}``) by sweave, so the
  plugin never needs the serve's URL.
* The out-of-band stall-branch ask-dance stays as fallback for the
  plugin-absent / fetch-failed case (rare, not the primary).

Why a registry here: the plugin knows only WHO asked (sessionID +
requestID); sweave knows which serve + chat delegation owns that
session because ``SpecialistRuntime._ensure_session`` registers every
resolved ``(session_id → base_url, worktree_path, delegation_id)``
triple at turn start (:func:`register_session`).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# The island: sweave-owned plugins dir injected as OPENCODE_CONFIG_DIR
# into every ServeRunner-spawned serve (opencode searches it like
# .opencode/ but it lives under ~/.sweave, invisible to standalone
# opencode instances).
def _island_dir() -> Path:
    return Path.home() / ".sweave" / "opencode"

# Session registry: session_id -> {base_url, worktree, delegation_id,
# project}. Written by SpecialistRuntime on _ensure_session; read by
# the hijack endpoint to find the serve + the owning delegation when
# the human question must be routed (and to know which serve to POST
# the pinned reply to).
_SESSIONS: dict[str, dict[str, Any]] = {}


def register_session(
    session_id: str,
    base_url: str,
    worktree_path: str | Path,
    delegation_id: str | None = None,
) -> None:
    _SESSIONS[str(session_id)] = {
        "base_url": str(base_url),
        "worktree": str(worktree_path),
        "delegation_id": delegation_id,
    }


def lookup_session(session_id: str) -> dict[str, Any] | None:
    return _SESSIONS.get(str(session_id))


# ---- plugin provisioning -------------------------------------------

PLUGIN_UNIQUE_MARKER = "_sweave_managed"


def _plugin_dir() -> Path:
    return _island_dir() / "plugins"


def plugin_path() -> Path:
    return _plugin_dir() / "sweave-permission.ts"


def bundled_plugin_source() -> str:
    """The plugin source shipped with the sweave package."""
    return (Path(__file__).resolve().parent / "permission_bridge_plugin.ts").read_text(
        encoding="utf-8"
    )


def ensure_permission_bridge() -> dict[str, str]:
    """Copy the bundled plugin into the config island (idempotent
    copy: refreshed on every serve spawn so template improvements
    land on the next serve). Returns the env overrides for the
    serve spawn process (``OPENCODE_CONFIG_DIR``). Never raises:
    a failed copy means the bridge degrades to the existing
    out-of-band path, never a hard serve failure."""
    try:
        target_dir = _plugin_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "sweave-permission.ts"
        source = bundled_plugin_source()
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current != source:
            target.write_text(source, encoding="utf-8")
            logger.info(
                "permission_bridge: wrote %s (OPENCODE_CONFIG_DIR=%s)",
                target, _island_dir(),
            )
        return {"OPENCODE_CONFIG_DIR": str(_island_dir())}
    except OSError as e:
        logger.warning("permission_bridge: plugin copy failed: %s", e)
        return {}


# ---- scope evaluation ------------------------------------------------

def pattern_in_root(pattern: str, roots: list[Path]) -> bool:
    """True when a checked permission pattern lives under any root.

    Opencode's checked patterns are generated as
    ``path.join(dirname, "*")`` (platform separators); stripping the
    wildcard gives the directory actually being touched. Resolve
    before comparing so ``~``-rooted or relative matches normalize.
    """
    text = str(pattern).replace("*", "").rstrip("\\/").strip()
    if not text:
        return False
    candidate = Path(text)
    try:
        resolved = candidate.resolve()
    except OSError:
        return False
    for root in roots:
        try:
            if resolved.is_relative_to(root.resolve()):
                return True
        except (OSError, ValueError):
            continue
    return False


def scope_decision(
    *,
    patterns: list[str] | None,
    project_dir: Path | None,
    permission_roots: Any = None,
) -> bool:
    """True = in scope (auto-allow), False = out of scope (ask human).

    Built-in roots (same list the scoped render emits): ``~/.sweave``,
    the project dir itself, and the project's worktree base. Plus the
    human-declared ``Project.permission_roots``. Unknown/no project →
    out of scope (ask the human: never silent-auto-allow).
    """
    from sweave.runtime.mcp_config import _root_globs

    roots: list[Path] = []
    if project_dir is not None:
        roots.extend(_root_globs(Path(project_dir), permission_roots))
        roots.append(Path(project_dir))
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        key = str(root).lower()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    if not unique:
        return False
    texts = patterns or []
    if not texts:
        return False
    # A checked id outside every root means the ask deserves a human.
    # The binary matches the last rendered rule per checked pattern;
    # our render's only "allow" rules are root-prefixed, so
    # root-ancestry of the stripped id is equivalent for the allow
    # side and strictly safer for the catch-all ("ask").
    return all(pattern_in_root(t, unique) for t in texts)


# ---- endpoint-side resolution (wired in server.py) --------------------

async def resolve_hijack_request(
    payload: dict[str, Any],
    *,
    escalation_store: Any,
    project_manager: Any,
) -> dict[str, Any]:
    """Decide + execute the bridge answer for one ``permission.asked``.

    Returns ``{"response": "once"|"always"|"reject", "status": ...}``.
    The HTTP reply to the opencode serve happens HERE (the plugin
    only ferries the ask): the pinned wire
    ``POST {base_url}/session/{sid}/permissions/{rid}`` is fired by
    sweave once the scope decision (auto) or the human answer is in.

    Out-of-scope asks create a blocking human escalation (kind=
    permission, no timeout per the M1.11 ruling) under the
    delegation that owns the session (or ``session:{sid}`` when
    unknown), then await resolution forever.
    """
    import httpx

    from sweave.runtime.permission_watch import reply_permission_request

    session_id = str(payload.get("session_id") or "")
    request_id = str(payload.get("request_id") or "")
    permission = str(payload.get("permission") or "")
    patterns = [str(p) for p in (payload.get("patterns") or [])]
    command = str((payload.get("metadata") or {}).get("command", "") or "")
    if not session_id or not request_id:
        return {"status": "rejected", "reason": "missing ids"}
    rec = lookup_session(session_id) or {}
    base_url = str(rec.get("base_url") or "")
    if not base_url:
        # Session unregistered (spawn from outside the runtime): ask
        # the human anyway, reply is impossible without a serve.
        logger.warning(
            "permission_bridge: session %s not registered; asking human only",
            session_id,
        )
        state = {
            "delegation_id": f"session:{session_id}",
            "project": None,
            "roots": None,
        }
    else:
        state = {
            "delegation_id": str(rec.get("delegation_id") or f"session:{session_id}"),
            "project": None,
            "roots": None,
        }
        # Project record: the runner's worktree belongs to exactly one
        # project (root or one of its worktrees).
        worktree = Path(rec.get("worktree") or "")
        if project_manager is not None:
            try:
                for project in project_manager.list_projects():
                    pd = Path(project.path)
                    if worktree.resolve().is_relative_to(pd.resolve()):
                        state["project"] = project.name
                        state["roots"] = getattr(
                            project, "permission_roots", None
                        )
                        state["project_dir"] = str(pd)
                        break
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "permission_bridge: project resolve failed: %s", e
                )
    in_scope = scope_decision(
        patterns=patterns,
        project_dir=Path(state["project_dir"]) if state.get("project_dir") else None,
        permission_roots=state["roots"],
    )
    if in_scope:
        response_value = "once"
        status = "auto_allowed"
    else:
        summary = (
            f"opencode asks {permission} for {patterns}"
            + (f" (command: {command})" if command else "")
        )
        try:
            await escalation_store.create(
                delegation_id=state["delegation_id"],
                question=(
                    f"Permission required: {summary}. Answer 'allow once'"
                    f" / 'always allow' / 'deny' (or skip = deny)."
                ),
                options=["allow once", "always allow", "deny"],
                kind="permission",
                audience="human",
                timeout_seconds=None,
                metadata={
                    "requestID": request_id,
                    "sessionID": session_id,
                    "permission": permission,
                    "patterns": patterns,
                    "command": command,
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("permission_bridge: escalation create failed: %s", e)
            return {"status": "error", "reason": str(e)}
        while True:
            await asyncio.sleep(0.5)
            try:
                esc = (
                    await escalation_store.get(
                        delegation_id=state["delegation_id"]
                    )
                ) or {}
            except Exception:
                esc = {}
            if esc.get("status") != "pending":
                break
        status = str(esc.get("status", ""))
        if status == "answered":
            low = str(esc.get("response", "") or "").strip().lower()
            if "always" in low:
                response_value = "always"
            elif "deny" in low or "reject" in low or low == "no":
                response_value = "reject"
            else:
                response_value = "once"
        else:
            response_value = "reject"
    if base_url:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                code = await reply_permission_request(
                    client, base_url, session_id, request_id, response_value
                )
            if code != 200:
                logger.warning(
                    "permission_bridge: reply POST rejected (HTTP %s)", code
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("permission_bridge: reply POST failed: %s", e)
    return {
        "status": status,
        "response": response_value,
        "delegation_id": state["delegation_id"],
    }
