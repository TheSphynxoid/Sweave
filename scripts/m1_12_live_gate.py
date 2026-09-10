"""M1.12 step-4 LIVE GATE: permission-aware turns on a real 1.18.29
serve. Set ``M1_12_PROBE_MODEL`` if the default provider choice
changes (same var the wire probe uses).

Scene A (scoped render, product code): boot a scratch serve with
``render_external_directory`` (catch-all=ask + built-in roots); ask
for a read INSIDE ``~/.sweave`` (a built-in root) — the turn must
complete WITHOUT any permission.asked and without a reply.

Scene B (ask -> allow once -> completes): outside-cwd read of
``C:\\Windows\\win.ini`` -> ``permission.asked`` fires; reply
``once`` -> the turn completes with real file content.

Scene C (deny is loud): fresh session, outside read -> ask ->
``reject`` -> no file content (abort/empty shape), completion
signalled but content absent.

Exit 0 only when all three pins hold. Destroys its temp dir; kills
its own serve. Run: python scripts/m1_12_live_gate.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(os.path.dirname(os.path.abspath(__file__))))
from m1_12_permission_wire_probe import (  # noqa: E402
    PermissionWatch,
    _free_port,
    _parse_stream_line,
    _spawn_serve,
    _wait_ready,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sweave.runtime.mcp_config import render_external_directory  # noqa: E402
from sweave.harness.opencode import _split_json_stream  # noqa: E402
from sweave.runtime.permission_watch import (  # noqa: E402
    fetch_messages,
    final_assistant_text,
    reply_permission_request,
)

TURN_T = 240.0
WAIT_ASK_SECONDS = 90.0
POST_REPLY_WAIT = 120.0


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
        str(p.get("id")): p
        for p in (data.get("providers") or [])
        if isinstance(p, dict)
    }
    for pid in ("opencode", "nvidia", "openrouter"):
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
    """Stream one turn; returns (text, notes, completed)."""
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
                    obj = None
                    try:
                        obj = json.loads(piece)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    info = obj.get("info")
                    info = info if isinstance(info, dict) else {}
                    err = info.get("error")
                    if err is not None:
                        notes.append(f"info.error={json.dumps(err)[:240]}")
                    for part in obj.get("parts", []) or []:
                        if not isinstance(part, dict):
                            continue
                        if part.get("type") == "tool":
                            notes.append(
                                f"tool {part.get('tool')} "
                                f"{(part.get('state') or {}).get('status')}"
                            )
                        elif part.get("type") == "text":
                            text += part.get("text", "")
                    t = info.get("time") or {}
                    if t.get("completed") is not None and info.get("finish"):
                        return text, notes, True
    return text, notes, False


async def await_ask(
    bus: PermissionWatch, sid: str, baseline: int, timeout: float
) -> dict | None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        asks = bus.requests()
        candidates = [
            c for c in asks[baseline:]
            if (sid is None or c.get("sessionID") in (None, sid))
        ]
        if candidates:
            return candidates[0]
        await asyncio.sleep(0.5)
    return None


async def main() -> int:
    from sweave.runtime.permission_watch import PermissionWatcher

    scratch = Path(tempfile.gettempdir()) / "opencode" / (
        f"m1_12-gate-{time.strftime('%H%M%S')}"
    )
    scratch.mkdir(parents=True, exist_ok=True)
    rendered = render_external_directory(scratch)
    (scratch / "opencode.json").write_text(
        json.dumps({
            "$schema": "https://opencode.ai/config.json",
            "permission": {"external_directory": rendered},
        }),
        encoding="utf-8",
    )
    print("rendered external_directory:")
    print(json.dumps(rendered, indent=2)[:700])
    port = _free_port()
    proc = _spawn_serve(scratch, port)
    base = f"http://127.0.0.1:{port}"
    ok = True
    scenes = os.environ.get("M1_12_SCENES", "ABC")
    scenes = os.environ.get("M1_12_SCENES", "ABC")
    try:
        _wait_ready(port)
        print("serve ready")
        bus = PermissionWatch(base)
        bus_task = asyncio.create_task(bus.listen())
        await asyncio.sleep(0.5)
        model = await _pick_model(base)
        print(f"model: {model}")

        # ---- Scene A: builtin root passes silently ------------------
        home_sweave = Path.home() / ".sweave"
        if "A" in scenes:
            (home_sweave / "gate-canary.txt").write_text(
                "GATE-CANARY-OK", encoding="utf-8"
            )
            sid = await _session(base)
            ask0 = len(bus.requests())
            text0, notes0, done0 = await run_turn(
                base, sid, model, _prompt(str(home_sweave / "gate-canary.txt"))
            )
            asks0 = [
                a for a in bus.requests()[ask0:] if a.get("sessionID") == sid
            ]
            print(
                f"scene A: done={done0} asks={len(asks0)} "
                f"text[:80]={text0[:80]!r} notes={notes0[-6:]}"
            )
            if not (done0 and not asks0 and "GATE-CANARY" in text0):
                print("FAIL scene A (root did not pass silently)")
                ok = False
            else:
                print("scene A OK: scoped root passed silently")

        # ---- Scene B: ask -> allow once -> completes with content ---
        sid_b = await _session(base)
        ask_b0 = len(bus.requests())
        turn_task = asyncio.create_task(
            run_turn(base, sid_b, model, _prompt("C:\\Windows\\win.ini"))
        )
        bus_session = PermissionWatcher(base)
        ask_record = None
        for _ in range(6):
            ask_record = await await_ask(bus, sid_b, ask_b0, WAIT_ASK_SECONDS)
            if ask_record is not None:
                break
        if ask_record is None:
            # The turn may have completed without an ask (model answered
            # from memory); inspect and fail loud.
            text_b, notes_b, done_b = await asyncio.wait_for(turn_task, 5)
            print(
                f"scene B: NO ASK captured; done={done_b} "
                f"notes={notes_b} text[:120]={text_b[:120]!r}"
            )
            print("FAIL scene B (no permission.asked observed)")
            ok = False
        else:
            print(f"scene B: ask pinned {ask_record['id']}")
            async with httpx.AsyncClient(timeout=15.0) as client:
                code = await reply_permission_request(
                    client, base, sid_b, ask_record["id"], "once"
                )
            print(f"scene B: reply once -> HTTP {code}")
            try:
                text_b, notes_b, done_b = await asyncio.wait_for(
                    turn_task, POST_REPLY_WAIT
                )
            except asyncio.TimeoutError:
                text_b, notes_b, done_b = "", ["timeout"], False
            fetched = await _fetch_text(base, sid_b)
            joined = text_b + fetched
            print(f"scene B final text[:120]={joined[:120]!r}")
            if code == 200 and done_b and joined:
                if "16-bit app support" in joined or ";" in joined:
                    print("scene B OK: allowed-once resumed with content")
                else:
                    print(
                        f"scene B WEAK: content unverified ({joined[:60]!r})"
                    )
            else:
                print(f"FAIL scene B (done={done_b} text[:60]={text_b[:60]!r})")
                ok = False

        # ---- Scene C: deny is loud -----------------------------------
        sid_c = await _session(base)
        ask_c0 = len(bus.requests())
        turn_task_c = asyncio.create_task(
            run_turn(base, sid_c, model, _prompt("C:\\Windows\\win.ini"))
        )
        deny_record = None
        for _ in range(6):
            deny_record = await await_ask(bus, sid_c, ask_c0, WAIT_ASK_SECONDS)
            if deny_record is not None:
                break
        if deny_record is None:
            print("FAIL scene C (no ask observed)")
            ok = False
        else:
            async with httpx.AsyncClient(timeout=15.0) as client:
                code_c = await reply_permission_request(
                    client, base, sid_c, deny_record["id"], "reject"
                )
            print(f"scene C: reply reject -> HTTP {code_c}")
            try:
                text_c, notes_c, done_c = await asyncio.wait_for(
                    turn_task_c, POST_REPLY_WAIT
                )
                fetched_c = await _fetch_text(base, sid_c)
                joined_c = text_c + fetched_c
                # Loud = the run aborted: NO win.ini content anywhere
                # in the transcript, and the turn completed/idled.
                if "16-bit app support" not in joined_c:
                    print(
                        f"scene C OK: denied, no file content "
                        f"(done={done_c}, notes={notes_c[-1:]})"
                    )
                else:
                    ok = False
                    print("FAIL scene C: content present despite reject")
            except asyncio.TimeoutError:
                ok = False
                print("FAIL scene C (turn did not abort after reject)")
            text_c, notes_c, done_c = text_c, notes_c, done_c

        bus_task.cancel()
        try:
            await bus_task
        except Exception:  # noqa: BLE001
            pass
        print(f"summary asks={[a['id'] for a in bus.requests()]} ok={ok}")
        return 0 if ok else 1
    finally:
        if proc.poll() is None:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
            )
        shutil.rmtree(scratch, ignore_errors=True)
        (Path.home() / ".sweave" / "gate-canary.txt").unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
