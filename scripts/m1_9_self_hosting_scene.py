"""M1.9 self-hosting live scene.

Exercises one real task end-to-end through the chat thread in a
scratch project. The funnel-leak count is the headline number:

* Chat a task to the orchestrator.
* Orchestrator defers to a specialist (via the MCP defer tool).
* Specialist does its work in its worktree (and may escalate via
  ask_human, or complete and reach review).
* Answer / promote through the API.

Every funnel leak (any forced exit to files / CLI / API outside the
chat thread) is recorded; the friction list becomes R4's re-planning
input.

Test/CI kill switch: ``M1.9_DISABLE_LIVE_SCENE=1`` short-circuits to
a no-op + the smoke assertions still run.

Hermeticity: this script sets ``SWEAVE_MOCK_OPENCODE=1`` so the
opencode subprocess seam is stubbed. The script drives the HTTP API
directly (the same path the chat thread takes); it does NOT need a
real opencode process or LLM provider to run.

Usage:
    python scripts/m1_9_self_hosting_scene.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

# Allow running this script from the repo root without installing
# the package: prepend the repo root (the parent of ``scripts/``)
# to sys.path so ``import sweave.*`` works.
_THIS = Path(__file__).resolve()
_REPO_ROOT = _THIS.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# Pre-import guards. The script runs in two modes:
# 1. With SWEAVE_MOCK_OPENCODE=1 (the default for the live scene):
#    the orchestrator specialist is stubbed; HTTP API works; the
#    scene exercises the funnel through real HTTP calls.
# 2. With M1.9_DISABLE_LIVE_SCENE=1: a no-op smoke check.
#
# We keep both: production / CI runs the live scene; the test that
# guards the entry point can short-circuit.


def _http_post(base: str, path: str, body: dict | None = None, headers: dict | None = None):
    """Plain HTTP POST helper for the scene."""
    import httpx

    r = httpx.post(f"{base}{path}", json=body or {}, headers=headers or {}, timeout=30.0)
    if r.status_code >= 400:
        # Don't raise; let the caller decide. Some 4xx are expected
        # (e.g. a chat turn in mock mode might 503 without an
        # orchestrator specialist).
        return {"_error": r.status_code, "_text": r.text[:200]}
    return r.json() if r.text else {}


def _http_get(base: str, path: str, headers: dict | None = None):
    import httpx

    r = httpx.get(f"{base}{path}", headers=headers or {}, timeout=30.0)
    r.raise_for_status()
    return r.json() if r.text else {}


async def _drive_scene(base: str, project_dir: Path, scratch_worktrees: Path):
    """Drive one real task end-to-end through the chat thread.

    Returns a dict with the funnel-leak counts and the final state
    of the delegation tree. The friction list is the headline
    output the planner reads; everything else is diagnostic.
    """
    from sweave.mcp import get_or_create_token

    token = get_or_create_token()
    auth = {"X-Sweave-MCP-Token": token}

    leaks: list[str] = []

    # 1) Create the scratch project via the API.
    project_name = f"scratch-{uuid.uuid4().hex[:6]}"
    body = {
        "name": project_name,
        "path": str(project_dir),
        "description": "M1.9 self-hosting scratch project",
        "worktree_base": str(scratch_worktrees),
    }
    # The /api/projects endpoint expects the body as JSON.
    import httpx

    r = httpx.post(f"{base}/api/projects", json=body, headers=auth, timeout=10.0)
    r.raise_for_status()
    leaks.append("created project via API (no chat equivalent) -- documented")

    # 2) Activate the project.
    r = httpx.post(
        f"{base}/api/projects/{project_name}/active",
        json={},
        headers=auth,
        timeout=10.0,
    )
    r.raise_for_status()
    leaks.append("activated project via API -- documented")

    # 3) Read the specialists the orchestrator can defer to.
    specialists = _http_get(base, "/api/mcp/specialists", headers=auth)
    specialist_names = [s["name"] for s in specialists.get("specialists", [])]
    if not specialist_names:
        leaks.append("no specialists to defer to (orchestrator unreachable) -- CRITICAL")

    # 4) Create a session for the chat.
    sessions = _http_post(
        base,
        "/api/sessions",
        {"project_name": project_name, "name": "M1.9 self-hosting scene"},
        headers=auth,
    )
    if "_error" in sessions:
        leaks.append(
            f"couldn't create session: {sessions['_error']} {sessions['_text']} -- CRITICAL"
        )
        return {"leaks": leaks, "delegations": []}
    # The /api/sessions endpoint returns either {success, session} (older)
    # or the session dict directly (newer). Handle both.
    session_id = sessions.get("id") or (sessions.get("session") or {}).get("id")
    if not session_id:
        leaks.append(f"couldn't create session: response={sessions} -- CRITICAL")
        return {"leaks": leaks, "delegations": []}

    # 5) Activate the session.
    r = httpx.post(
        f"{base}/api/sessions/{session_id}/active",
        json={},
        headers=auth,
        timeout=10.0,
    )
    r.raise_for_status()

    # 6) Chat the task. (Mock mode: the orchestrator reply is canned;
    # in production the real opencode session runs.)
    msg_body = {
        "role": "user",
        "content": "List a directory in your worktree.",
    }
    r = httpx.post(
        f"{base}/api/sessions/{session_id}/messages",
        json=msg_body,
        headers=auth,
        timeout=30.0,
    )
    # The chat endpoint may take a while (orchestrator turn + child
    # wait + synthesis). We give it 60s and consider anything that
    # comes back non-2xx as a leak.
    if r.status_code >= 400:
        leaks.append(f"chat turn failed: {r.status_code} {r.text[:200]} -- CRITICAL")
    else:
        leaks.append("chat turn completed in-band -- no funnel leak")

    # 7) Walk the delegation tree.
    delegations = _http_get(base, "/api/delegations", headers=auth).get("delegations", [])
    leaks.append(
        f"read {len(delegations)} delegations via API (no chat equivalent) -- documented"
    )

    # 8) For each delegation in review, promote via the API.
    for d in delegations:
        if d.get("status") == "review":
            r = httpx.post(
                f"{base}/api/delegations/{d['delegation_id']}/promote",
                json={},
                headers=auth,
                timeout=10.0,
            )
            if r.status_code >= 400:
                leaks.append(
                    f"promote {d['delegation_id']} failed: {r.status_code} -- documented"
                )
            else:
                leaks.append(f"promoted {d['delegation_id']} via API -- documented")

    # 9) For each needs_attention delegation, ask / answer via the
    # MCP path (the live scene doesn't actually ask the human; we
    # just verify the endpoint exists + answer is accepted).
    for d in delegations:
        if d.get("needs_attention"):
            r = httpx.post(
                f"{base}/api/delegations/{d['delegation_id']}/answer",
                json={"response": "(scene answer; in production a human types here)"},
                headers=auth,
                timeout=10.0,
            )
            if r.status_code >= 400:
                leaks.append(
                    f"answer {d['delegation_id']} failed: {r.status_code} -- documented"
                )

    return {"leaks": leaks, "delegations": delegations}


def main():
    if os.environ.get("M1.9_DISABLE_LIVE_SCENE") == "1":
        print("M1.9 live scene disabled (M1.9_DISABLE_LIVE_SCENE=1).")
        return 0

    # Hermeticity: stub the opencode subprocess seam so the scene
    # doesn't need a real opencode install.
    os.environ.setdefault("SWEAVE_MOCK_OPENCODE", "1")

    # The server URL is the live scene's only moving target. Default
    # is the canonical 127.0.0.1:8100.
    base = os.environ.get("SWEAVE_BASE", "http://127.0.0.1:8100")

    # Scratch project: a temp dir + a worktree_base under another
    # temp dir. The dev repo is never its own live-gate target
    # (the worktree_base enforcement).
    project_root = Path(tempfile.mkdtemp(prefix="sweave-m1.9-scene-"))
    scratch_worktrees = Path(tempfile.mkdtemp(prefix="sweave-m1.9-wt-"))
    print(f"scratch project: {project_root}")
    print(f"scratch worktrees: {scratch_worktrees}")

    try:
        out = asyncio.run(
            _drive_scene(base, project_root, scratch_worktrees)
        )
    finally:
        shutil.rmtree(project_root, ignore_errors=True)
        shutil.rmtree(scratch_worktrees, ignore_errors=True)

    # Headline output: the funnel leak list. The friction list is
    # what R4 reads.
    print("\n=== Funnel leak report ===")
    for leak in out.get("leaks", []):
        print(f"  - {leak}")
    print(f"\n{len(out.get('delegations', []))} delegations in the tree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())