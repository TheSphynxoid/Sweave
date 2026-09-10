"""M1.12 amendment-1 LIVE GATE: the in-band permission bridge on a
real 1.18.29 serve + the plugin island + a mini sweave backend.

Scenes (``M1_12_BRIDGE_SCENES`` env, default ``DEF``):

D  auto-allow in scope THROUGH the plugin: the rendered config has no
   roots (catch-all ``ask``), the sweave project record declares
   ``~/.sweave`` -> an in-scope read ASKS opencode-side, the plugin
   ferries it to ``POST /api/permission/hijack``, sweave auto-allows
   ``once``, the turn completes with real content and NO escalation
   record was created.

E  out of scope -> blocking human question -> answered -> turn
   completes with content. Sweave plays the human programmatically
   (``EscalationStore.answer`` "allow once") so the gate is
   unattended; the UI card ride-along is covered by the pytest suite.

F  out of scope -> answered "deny" -> reject reply -> the turn aborts
   loud (no win.ini content lands anywhere).

Serve env carries ``SWEAVE_HOST`` / ``SWEAVE_PORT`` /
``SWEAVE_MCP_TOKEN`` + the island ``OPENCODE_CONFIG_DIR`` (the plugin
is visible ONLY to serves sweave spawns; a standalone run in the same
dir executes nothing — that property is the point). Exit 0 only when
all three hold. Run: ``python scripts/m1_12_bridge_gate.py``
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import Request

sys.path.insert(0, str(os.path.dirname(os.path.abspath(__file__))))
from m1_12_permission_wire_probe import (  # noqa: E402
    PermissionWatch,
    _free_port,
    _resolve_command,
    _kill_tree,
    _wait_ready,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sweave.harness.opencode import _split_json_stream  # noqa: E402
from sweave.runtime.escalation import EscalationStore  # noqa: E402
from sweave.runtime.permission_bridge import (  # noqa: E402
    ensure_permission_bridge,
    register_session,
    resolve_hijack_request,
)
from sweave.runtime.permission_watch import (  # noqa: E402
    fetch_messages,
    final_assistant_text,
    reply_permission_request,
)

TURN_T = 300.0
SCENE_WAIT = 120.0
POST_RES_WAIT = 180.0


# ---------------------------------------------------------------------------
# Mini sweave backend: only /api/permission/hijack, wired to the real
# bridge resolution + a REAL EscalationStore.
# ---------------------------------------------------------------------------

class _ProjectShim:
    def __init__(self, name: str, path: Path, roots: list[str] | None) -> None:
        self.name = name
        self.path = str(path)
        self.permission_roots = roots


class _ProjectsShim:
    def __init__(self, projects: list[_ProjectShim]) -> None:
        self._projects = projects

    def list_projects(self):
        return list(self._projects)


class Ferry:
    """The sweave-side world the bridge endpoint sees."""

    def __init__(self, project_dir: Path, root: str) -> None:
        self.project_dir = Path(project_dir)
        self.root = root
        self.store = EscalationStore(
            base_dir=self.project_dir, timeout_seconds=None, event_bus=None
        )
        self.projects = _ProjectsShim(
            [_ProjectShim("gate-project", self.project_dir, [root])]
        )
        self.auto_answer: dict[str, str] = {}


class _HijackState:
    def __init__(self, ferry: Ferry) -> None:
        self.ferry = ferry


def build_app(state: _HijackState):
    from fastapi import FastAPI

    @asynccontextmanager
    async def lifespan(app):  # type: ignore[no-untyped-def]
        task = asyncio.create_task(
            _auto_answer_loop(state.ferry.store, state.ferry)
        )
        yield
        task.cancel()

    app = FastAPI(lifespan=lifespan)

    @app.post("/api/permission/hijack")
    async def hijack(request: Request):  # type: ignore[no-untyped-def]
        body = await request.json()
        print("HIJACK hit:", str(body.get("session_id"))[:20], str(body.get("request_id")))
        return await resolve_hijack_request(
            body if isinstance(body, dict) else {},
            escalation_store=state.ferry.store,
            project_manager=state.ferry.projects,
        )

    return app


async def _auto_answer_loop(store: EscalationStore, ferry: Ferry) -> None:
    """Unattended-humaner: answer every permission escalation with
    the mapped verdict (scene by delegation_id prefix)."""
    while True:
        try:
            for delegation_id, answer in list(ferry.auto_answer.items()):
                rec = await store.get(delegation_id=delegation_id)
                if rec is not None and rec.get("status") == "pending":
                    await store.answer(
                        delegation_id=delegation_id, response=answer
                    )
        except Exception:
            pass
        await asyncio.sleep(0.5)


# ---------------------------------------------------------------------------
# Turn plumbing (same shapes as scripts/m1_12_live_gate.py)
# ---------------------------------------------------------------------------


async def _fetch_text(base: str, sid: str) -> str:
    async with httpx.AsyncClient(timeout=15.0) as client:
        msgs = await fetch_messages(client, base, sid)
    return final_assistant_text(msgs)


async def _pick_model(base: str) -> tuple[str, str]:
    env_model = os.environ.get("M1_12_PROBE_MODEL", "")
    if "/" in env_model:
        p, _, m = env_model.rpartition("/")
        return p, m
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{base}/config/providers", timeout=10.0)
        data = r.json()
    by_id = {
        str(p.get("id")): p for p in (data.get("providers") or []) if isinstance(p, dict)
    }
    for pid in ("opencode-go", "opencode", "nvidia", "openrouter"):
        p = by_id.get(pid)
        if isinstance(p, dict):
            models = list((p.get("models") or {}).keys())
            if models:
                return pid, models[0]
    raise RuntimeError(f"no provider: {json.dumps(data)[:300]}")


async def _session(base: str) -> str:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(f"{base}/session", json={})
        return r.json()["id"]


def _prompt(path: str) -> str:
    return (
        "MANDATORY: call the bash tool NOW with command "
        f"Get-Content {path}. Do not explain from memory — the only "
        "accepted reply is a bash tool call reading that exact path. "
        "Report the first line you see."
    )


async def run_turn(base: str, sid: str, model: tuple[str, str], prompt: str):
    body = {
        "parts": [{"type": "text", "text": prompt}],
        "model": {"providerID": model[0], "modelID": model[1]},
    }
    notes: list[str] = []
    text = ""
    buf = ""
    async with httpx.AsyncClient(timeout=TURN_T) as client:
        async with client.stream(
            "POST", f"{base}/session/{sid}/message", json=body,
            headers={"content-type": "application/json"},
        ) as resp:
            resp.raise_for_status()
            carry = ""
            async for chunk in resp.aiter_text():
                buf += chunk
                pieces, carry = _split_json_stream(buf, carry)
                buf = ""
                for piece in pieces:
                    try:
                        obj = json.loads(piece)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    info = obj.get("info") or {}
                    if isinstance(info, dict) and info.get("error") is not None:
                        notes.append(f"info.error={json.dumps(info['error'])[:200]}")
                    for part in obj.get("parts", []) or []:
                        if not isinstance(part, dict):
                            continue
                        if part.get("type") == "tool":
                            notes.append(
                                f"tool {part.get('tool') or part.get('toolName')} "
                                f"{(part.get('state') or {}).get('status')}"
                            )
                        elif part.get("type") == "text":
                            text += part.get("text", "")
                    t = (info.get("time") or {}) if isinstance(info, dict) else {}
                    if t.get("completed") is not None and info.get("finish"):
                        return text, notes, True
    return text, notes, False


async def await_ask(bus: PermissionWatch, sid: str, baseline: int) -> dict | None:
    deadline = asyncio.get_running_loop().time() + SCENE_WAIT
    while asyncio.get_running_loop().time() < deadline:
        candidates = [a for a in bus.requests()[baseline:] if a.get("sessionID") in (None, sid)]
        if candidates:
            return candidates[0]
        await asyncio.sleep(0.5)
    return None


async def _run_scene(
    *,
    scene: str,
    base: str,
    bus: PermissionWatch,
    model: tuple[str, str],
    path: str,
    expected_answer: str,
    ferry: Ferry,
    ok_flag: list[bool],
    expects_no_escalation: bool = False,
) -> None:
    delegation_id = f"bridge-gate-{scene.lower()}"
    sid = await _session(base)
    register_session(sid, base, ferry.project_dir, delegation_id)
    ask0 = len(bus.requests())
    turn = asyncio.create_task(run_turn(base, sid, model, _prompt(path)))
    ask = await await_ask(bus, sid, ask0)
    if ask is None:
        text, notes, done = await asyncio.wait_for(turn, 10)
        print(f"scene {scene}: NO ASK; done={done} text[:120]={text[:120]!r}")
        print(f"FAIL scene {scene} (model didn't touch {path})")
        ok_flag[0] = False
        return
    if expected_answer is not None:  # the human answers (E/F)
        ferry.auto_answer[delegation_id] = expected_answer
        print(f"scene {scene}: ask pinned {ask['id']}; auto-answer={expected_answer!r}")
    try:
        text, notes, done = await asyncio.wait_for(turn, POST_RES_WAIT)
    except asyncio.TimeoutError:
        text, notes, done = "", ["timeout"], False
    fetched = await _fetch_text(base, sid)
    joined = text + fetched
    esc = await ferry.store.get(delegation_id=delegation_id)
    esc_exists = esc is not None
    print(
        f"scene {scene}: done={done} text[:100]={text[:100]!r} "
        f"notes={notes[-4:]} esc={esc.get('status') if esc else None}"
    )
    if expects_no_escalation:
        if done and not esc_exists and "GATE-CANARY" in joined:
            print(f"scene {scene} OK: plugin bridge auto-allowed in scope")
        else:
            print(
                f"FAIL scene {scene} (auto-allow: done={done} "
                f"esc_created={esc_exists} content={('GATE-CANARY' in joined)!r})"
            )
            ok_flag[0] = False
        return
    if "16-bit app support" in joined and expected_answer == "allow once":
        print(f"scene {scene} OK: answered 'allow once' -> content resumed")
    elif expected_answer == "deny" and "16-bit app support" not in joined:
        print(f"scene {scene} OK: deny -> no content, turn aborted loud")
    else:
        print(f"FAIL scene {scene} (unexpected content shape / reply)")
        ok_flag[0] = False


async def main() -> int:
    scratch = Path(tempfile.gettempdir()) / "opencode" / (
        f"m1_12-bridge-{time.strftime('%H%M%S')}"
    )
    scratch.mkdir(parents=True, exist_ok=True)
    # Scene D setup: opencode.json with catch-all ask AND NO roots (a
    # deliberate mismatch with the sweave record which DOES declare
    # ~/.sweave) — the plugin route is the only way to get content.
    (scratch / "opencode.json").write_text(
        json.dumps({
            "$schema": "https://opencode.ai/config.json",
            "permission": {"external_directory": {"*": "ask"}},
        }),
        encoding="utf-8",
    )
    token = secrets.token_urlsafe(24)
    mini_port = _free_port()
    os.environ["SWEAVE_PORT"] = str(mini_port)
    os.environ["SWEAVE_HOST"] = "127.0.0.1"
    os.environ["SWEAVE_MCP_TOKEN"] = token
    # The island plugin: bundled template, env-only secrets.
    env_extra = ensure_permission_bridge()
    print(f"plugin island: {env_extra}")
    # Builtin root target (~/.sweave gate canary per scene D).
    sweave_root = str(Path.home() / ".sweave")

    ferry = Ferry(project_dir=scratch, root=sweave_root)
    state = _HijackState(ferry)
    app = build_app(state)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=mini_port, log_level="warning"
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(1.0)

    port = _free_port()
    env = {
        **os.environ,
        **env_extra,
    }
    cmd = [_resolve_command(), "serve", "--hostname", "127.0.0.1",
           "--port", str(port)]
    flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags = subprocess.CREATE_NO_WINDOW
    log_path = Path(tempfile.gettempdir()) / "sweave-m1-12-bridge-serve.log"
    log_file = open(log_path, "ab")
    proc = subprocess.Popen(
        cmd, cwd=str(scratch), stdout=log_file, stderr=subprocess.STDOUT,
        creationflags=flags, env=env,
    )
    base = f"http://127.0.0.1:{port}"
    ok = True
    ok_flag = [ok]
    try:
        _wait_ready(port)
        print("serve ready")
        bus = PermissionWatch(base)
        bus_task = asyncio.create_task(bus.listen())
        await asyncio.sleep(0.5)
        model = await _pick_model(base)
        print(f"model: {model}")

        scenes = os.environ.get("M1_12_BRIDGE_SCENES", "DEF")

        sweave_canary = Path(sweave_root) / "gate-canary.txt"
        sweave_canary.write_text("GATE-CANARY-OK", encoding="utf-8")
        if "D" in scenes:
            await _run_scene(
                scene="D", base=base, bus=bus, model=model,
                path=str(sweave_canary),
                expected_answer=None, ferry=ferry, ok_flag=ok_flag,
                expects_no_escalation=True,
            )
        if "E" in scenes:
            await _run_scene(
                scene="E", base=base, bus=bus, model=model,
                path="C:\\Windows\\win.ini",
                expected_answer="allow once", ferry=ferry, ok_flag=ok_flag,
            )
        if "F" in scenes:
            await _run_scene(
                scene="F", base=base, bus=bus, model=model,
                path="C:\\Windows\\win.ini",
                expected_answer="deny", ferry=ferry, ok_flag=ok_flag,
            )

        bus_task.cancel()
        try:
            await bus_task
        except Exception:  # noqa: BLE001
            pass
        print(f"summary ok={ok_flag[0]}")
        return 0 if ok_flag[0] else 1
    finally:
        _kill_tree(proc)
        log_file.close()
        server.should_exit = True
        shutil.rmtree(scratch, ignore_errors=True)
        (Path(sweave_root) / "gate-canary.txt").unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
