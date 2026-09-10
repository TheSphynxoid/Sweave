"""M1.12 step-0 probe: pin the opencode 1.18.29 permission wire.

Runs a *bare* scratch ``opencode serve`` (default policy, every
outside-cwd access resolves to ``ask``) against a temp cwd and
reproduces the hang scene from 2026-09-10, this time with the
``/event`` bus subscribed BEFORE the turn:

1. Subscribe ``GET /event`` (newline/SSE JSON stream). Record every
   event type containing ``permission`` verbatim.
2. Create session (``POST /session``), send a message asking for a
   bash read of ``C:\\Windows\\win.ini`` (outside cwd -> ask).
3. While the turn streams, poll the candidate pending-permission
   GET routes; correlate with the bus events. Pin which route
   actually lists the pending request (v1/v2 store split:
   upstream #36835).
4. Reply ``reject`` (with message); record the failure shape
   (tool error? turn error? nothing?).
5. Turn 2: same ask, reply ``once``; record that the tool
   completes and the turn finishes.
6. Kill the serve tree (``taskkill /T /F``), delete the temp dir.

Output: annotated wire dumps + a final summary of WHICH routes
fired. Exit 0 only if a pending-permission signal was captured in
both scenes.

Usage:  python scripts/m1_12_permission_wire_probe.py
        [--model provider/model]
Requires opencode on PATH. Destroys its own temp dir; leaves no
orphans behind.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

TURN_TIMEOUT = 240.0
POLL_PERMISSION_INTERVAL = 2.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _resolve_command() -> str:
    """Same shim resolution the harness/serve_runner use (CMD -> .exe)."""
    resolved = shutil.which("opencode")
    if resolved and resolved.lower().endswith((".cmd", ".bat")):
        text = Path(resolved).read_text(encoding="utf-8", errors="replace")
        m = re.search(r'"([^"]+\.exe)"', text, re.IGNORECASE)
        if m:
            target = m.group(1)
            shim_dir = str(Path(resolved).resolve().parent)
            target = re.sub(
                r"%~?dp0%|%basedir%", lambda _m: shim_dir, target,
                flags=re.IGNORECASE,
            )
            target = Path(os.path.expandvars(target))
            if target.is_file():
                return str(target)
    return resolved or "opencode"


def _spawn_serve(cwd: Path, port: int) -> subprocess.Popen:
    flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(
        [_resolve_command(), "serve", "--hostname", "127.0.0.1",
         "--port", str(port)],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )


def _wait_ready(port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/session", timeout=3.0)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"serve did not bind on {port} within {timeout}s")


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True, check=False,
        )
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def _parse_stream_line(line: str) -> dict | None:
    """JSON line off a v2 stream (plain newline JSON or SSE)."""
    line = line.strip()
    if line.startswith("data:"):
        line = line[len("data:"):].strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


class PermissionWatch:
    """Subscribes the /event bus; keeps every permission event."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.events: list[dict[str, Any]] = []
        self.types_seen: dict[str, int] = {}
        self.printed_types: set[str] = set()

    async def listen(self) -> None:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", f"{self.base}/event") as resp:
                resp.raise_for_status()
                buf = ""
                async for chunk in resp.aiter_text():
                    if not chunk:
                        continue
                    buf += chunk
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        obj = _parse_stream_line(line)
                        if obj is None:
                            continue
                        etype = str(obj.get("type", ""))
                        self.types_seen[etype] = (
                            self.types_seen.get(etype, 0) + 1
                        )
                        if "permission" in etype:
                            self.events.append(obj)
                            props = json.dumps(
                                obj.get("properties", "")
                            )[:300]
                            print(f"  [bus] {etype}: {props}")
                        elif etype in ("session.error",):
                            props = json.dumps(
                                obj.get("properties", "")
                            )[:500]
                            print(f"  [bus] {etype}: {props}")
                        elif etype not in self.printed_types:
                            print(f"  [bus?type] {etype}")
                        self.printed_types.add(etype)

    def requests(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for evt in self.events:
            props: Any = evt.get("properties") or evt.get("data") or {}
            candidates: list[dict[str, Any]] = []
            if isinstance(props, dict) and isinstance(
                props.get("permission"), dict
            ):
                candidates = [props["permission"]]
            elif isinstance(props, dict) and props.get("id"):
                candidates = [props]
            elif isinstance(props, list):
                candidates = [x for x in props if isinstance(x, dict)]
            for c in candidates:
                status = str(c.get("status", "pending"))
                if status in ("pending", "asked", "") and c.get("id"):
                    found.append(c)
        return found


def _dump(label: str, payload: Any) -> None:
    print(f"--- {label} ---")
    print(json.dumps(payload, indent=2, default=str)[:1500])


PENDING_GET_ROUTES = (
    "/session/{sid}/permissions",
    "/session/{sid}/permission",
    "/session/{sid}/permission/pending",
    "/api/session/{sid}/permissions",
    "/api/permission",
)


def _poll_pending(client: httpx.AsyncClient, sid: str) -> list[tuple[str, Any]]:
    hits: list[tuple[str, Any]] = []
    for route in PENDING_GET_ROUTES:
        try:
            r = client.get(route.format(sid=sid), timeout=5.0)
        except Exception as exc:
            hits.append((route, f"ERR {exc}"))
            continue
        if r.status_code != 404:
            hits.append(
                (route, r.json() if r.status_code == 200 else f"{r.status_code}")
            )
    return hits


REPLY_ROUTES = (
    "/session/{sid}/permissions/{rid}",
    "/session/{sid}/permission/{rid}/reply",
)


async def _try_reply(
    base: str, client: httpx.AsyncClient, sid: str, rid: str,
    bodies: list[dict],
) -> list[tuple[str, int, str]]:
    """Try each body variant in order on the accepted route(s);
    stop after the first non-400, non-404 (a 200 consumes the
    pending request)."""
    results: list[tuple[str, int, str]] = []
    for route in REPLY_ROUTES:
        url = f"{base}{route.format(sid=sid, rid=rid)}"
        for body in bodies:
            try:
                r = await client.post(url, json=body, timeout=5.0)
            except Exception as exc:
                results.append((url, -1, str(exc)))
                continue
            results.append((url, r.status_code, r.text[:200]))
            if r.status_code not in (400, 404):
                return results
    return results


async def _run_turn(
    base: str, sid: str, model: tuple[str, str], prompt: str
) -> tuple[str, list[str]]:
    """Stream a turn. Returns (final_text, notes) — notes capture
    every tool-part state change and any info.error seen."""
    body: dict[str, Any] = {
        "parts": [{"type": "text", "text": prompt}],
        "model": {"providerID": model[0], "modelID": model[1]},
    }
    notes: list[str] = []
    final_text = ""
    buf = ""
    async with httpx.AsyncClient(timeout=TURN_TIMEOUT) as client:
        async with client.stream(
            "POST", f"{base}/session/{sid}/message", json=body,
            headers={"content-type": "application/json"},
        ) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_text():
                buf += chunk
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    obj = _parse_stream_line(line)
                    if obj is None:
                        continue
                    info = obj.get("info")
                    info = info if isinstance(info, dict) else {}
                    for part in obj.get("parts", []) or []:
                        if not isinstance(part, dict):
                            continue
                        if part.get("type") == "tool":
                            state = part.get("state") or {}
                            note = (
                                f"tool {part.get('tool')} "
                                f"call={str(part.get('callID', ''))[:12]} "
                                f"{state.get('status')} "
                                f"{json.dumps(state.get('metadata', ''))[:160]}"
                            )
                            notes.append(note)
                            print(f"  [turntool] {note}")
                        elif part.get("type") == "text":
                            final_text += part.get("text", "")
                    err = info.get("error")
                    if err is not None:
                        notes.append(f"info.error={json.dumps(err)[:300]}")
                    t = info.get("time") or {}
                    if t.get("completed") is not None and info.get("finish"):
                        return final_text, notes
    notes.append("STREAM-ENDED-WITHOUT-TERMINAL-SIGNAL")
    return final_text, notes


PROMPT = (
    "MANDATORY: call the bash tool NOW with command "
    "Get-Content C:\\Windows\\win.ini. Do not explain, do not answer "
    "from memory — the ONLY accepted reply is a bash tool call that "
    "reads that exact file path. Report the first line you see."
)

SCENE_ATTEMPTS = 4
SCENE_ATTEMPT_TIMEOUT = 90.0


async def _capture_request(
    base: str, sid: str, bus: PermissionWatch
) -> tuple[str | None, list[tuple[str, Any]]]:
    """Poll pending GET routes until something non-empty shows up.

    2026-09-10 evidence: the pending list GET returned ``{"data":[]}``
    for a hung turn (v1/v2 store split #36835) while the bus event
    fired — so the bus subscription is consulted every round too.

    Returns (request_id_or_None, candidate GET findings).
    """
    while True:
        bus_hits = bus.requests()
        matching = [
            c for c in bus_hits
            if sid is None or c.get("sessionID") in (None, sid)
        ]
        if matching:
            return str(matching[0]["id"]), [("bus", matching[0])]
        hits: list[tuple[str, Any]] = []
        async with httpx.AsyncClient(timeout=15.0) as client:
            for route in PENDING_GET_ROUTES:
                try:
                    r = await client.get(
                        f"{base}{route.format(sid=sid)}", timeout=5.0
                    )
                except Exception as exc:
                    hits.append((route, f"ERR {exc}"))
                    continue
                if r.status_code != 404:
                    try:
                        body: Any = r.json()
                    except Exception:
                        body = r.text[:200] or "<empty>"
                    hits.append((route, body))
        live = [
            (r, p) for r, p in hits
            if p != [] and p != {"data": []}
            and not str(p).startswith("404")
        ]
        for r, p in live:
            if not isinstance(p, str):
                _dump(f"pending GET {r}", p)
            rid = _extract_request_id(p)
            if rid:
                return rid, live
        await asyncio.sleep(POLL_PERMISSION_INTERVAL)


MESSAGE_FETCH_ROUTES = (
    "/session/{sid}/message",
    "/session/{sid}/messages",
)


async def _fetch_messages(base: str, sid: str) -> None:
    """Dump the session's message list (final-turn recovery shape)."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        for route in MESSAGE_FETCH_ROUTES:
            try:
                r = await client.get(
                    f"{base}{route.format(sid=sid)}", timeout=5.0
                )
            except Exception as exc:
                print(f"  [fetch] {route}: ERR {exc}")
                continue
            if r.status_code == 200 and not r.text.lstrip().startswith("<"):
                try:
                    data = r.json()
                except Exception:
                    continue
                msgs = data.get("data", data) if isinstance(data, dict) else data
                if isinstance(msgs, list):
                    print(f"  [fetch] {route}: {len(msgs)} messages")
                    for m in msgs:
                        info = m.get("info", m)
                        mid = info.get("id") or m.get("id")
                        parts = info.get("parts", []) or m.get("parts", [])
                        text = " ".join(
                            p.get("text", "") for p in parts
                            if isinstance(p, dict) and p.get("type") == "text"
                        )
                        tcom = (info.get("time") or {}).get("completed")
                        print(
                            f"    id={mid} role={info.get('role') or m.get('role')} "
                            f"completed={tcom} text[:160]={text[:160]!r}"
                        )


def _extract_request_id(payload: Any) -> str | None:
    """Find a pending permission id in a GET-payload arbor."""
    if isinstance(payload, str):
        return None
    if isinstance(payload, dict):
        if payload.get("status") == "pending" and payload.get("id"):
            return str(payload["id"])
        for v in payload.values():
            rid = _extract_request_id(v)
            if rid:
                return rid
    if isinstance(payload, list):
        for item in payload:
            rid = _extract_request_id(item)
            if rid:
                return rid
    return None


async def _pick_model(base: str) -> tuple[str, str]:
    """Prefer an explicitly-declared working model; try the live
    providers list otherwise. (The models.yaml default may be a
    dead openrouter route — the probe must not inherit a ghost.)"""
    env_model = os.environ.get("M1_12_PROBE_MODEL", "")
    if "/" in env_model:
        p, _, m = env_model.rpartition("/")
        return p, m
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{base}/config/providers", timeout=10.0)
        data = r.json()
    providers = (
        data.get("providers") if isinstance(data, dict) else None
    ) or []
    order = ["openrouter", "gmi", "ollama", "cloudflare-workers-ai"]
    by_id = {str(p.get("id") or p.get("id_", "")): p for p in providers
             if isinstance(p, dict)}
    for pid in order:
        p = by_id.get(pid)
        if isinstance(p, dict):
            models = list((p.get("models") or {}).keys())
            if models:
                # prefer a :free / think-capable default by scanning
                preferred = [m for m in models if ":free" in m]
                return pid, (preferred[0] if preferred else models[0])
    raise RuntimeError(f"no provider model found: {json.dumps(data)[:400]}")


async def _scene(
    base: str, bus: PermissionWatch, model: tuple[str, str]
) -> tuple[str | None, str, asyncio.Task]:
    """One ask-scene: fresh session, turns until a pending
    permission is pinned (models are not tool-compliant every
    turn; retry up to SCENE_ATTEMPTS times).

    Returns (request_id, session_id, turn_task) of the LAST turn;
    the caller decides the reply, then awaits the task to observe
    the post-reply shape from the notes.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        sid = (await client.post(f"{base}/session", json={})).json().get("id")
    print(f"session: {sid}")
    task: asyncio.Task | None = None
    rid: str | None = None
    live: list[tuple[str, Any]] = []
    for attempt in range(1, SCENE_ATTEMPTS + 1):
        print(f"  attempt {attempt}/{SCENE_ATTEMPTS}")
        task = asyncio.create_task(_run_turn(base, sid, model, PROMPT))
        cap = _capture_request(base, sid, bus)
        try:
            rid, live = await asyncio.wait_for(
                cap, SCENE_ATTEMPT_TIMEOUT
            )
            break
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # a previous attempt's permission may have been pinned
            # already after the deadline raced it; re-check once
            matching = [
                c for c in bus.requests()
                if c.get("sessionID") in (None, sid)
            ]
            if matching:
                rid = str(matching[0]["id"])
                break
    if rid is None:
        return None, sid, task  # type: ignore[return-value]
    print(f"pinned request id: {rid!r} (from: {[r for r, _ in live][:3]})")
    return rid, sid, task


async def main() -> int:
    scratch = Path(tempfile.gettempdir()) / "opencode" / (
        f"m1_12-wire-{time.strftime('%H%M%S')}"
    )
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "opencode.json").write_text(json.dumps({}), encoding="utf-8")
    port = _free_port()
    print(f"scratch: {scratch}  port: {port}")
    proc = _spawn_serve(scratch, port)
    base = f"http://127.0.0.1:{port}"
    ok = True
    try:
        _wait_ready(port)
        print("serve ready")
        bus = PermissionWatch(base)
        bus_task = asyncio.create_task(bus.listen())
        await asyncio.sleep(0.5)
        model = await _pick_model(base)
        print(f"model: {model}")

        print("\n== scene 1: reject ==")
        rid, sid, turn1 = await _scene(base, bus, model)
        if not rid:
            ok = False
        else:
            async with httpx.AsyncClient(timeout=15.0) as client:
                _dump(
                    "REPLY reject",
                    await _try_reply(
                        base, client, sid, rid,
                        [
                            {"response": "reject", "message": "probe denied"},
                            {"response": "reject"},
                        ],
                    ),
                )
            try:
                text, notes = await asyncio.wait_for(turn1, 60.0)
                for n in notes:
                    print(f"  [turn1] {n}")
                print(f"turn1 final ({len(text)} chars): {text[:300]!r}")
            except asyncio.TimeoutError:
                print("turn1 still streaming 60s after reject (hang shape)")
        await asyncio.sleep(2.0)
        await _fetch_messages(base, sid)
        await asyncio.sleep(3.0)
        print(f"bus events so far: {[(e.get('type'), json.dumps(e.get('properties', ''))[:300]) for e in bus.events]}")

        print("\n== scene 2: once ==")
        rid2, sid2, turn2 = await _scene(base, bus, model)
        if not rid2:
            ok = False
        else:
            async with httpx.AsyncClient(timeout=15.0) as client:
                _dump(
                    "REPLY once",
                    await _try_reply(
                        base, client, sid2, rid2,
                        [
                            {"response": "once"},
                            {"response": "always"},
                        ],
                    ),
                )
            try:
                text2, notes2 = await asyncio.wait_for(turn2, 90.0)
                for n in notes2:
                    print(f"  [turn2] {n}")
                print(f"turn2 final ({len(text2)} chars): {text2[:300]!r}")
            except asyncio.TimeoutError:
                ok = False
                print("turn2 still streaming 90s after once-reply")
        await asyncio.sleep(2.0)
        await _fetch_messages(base, sid2)
        await asyncio.sleep(1.0)
        print(f"bus events after reply: {[(e.get('type'), json.dumps(e.get('properties', ''))[:300]) for e in bus.events][-6:]}")

        bus_task.cancel()
        try:
            await bus_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

        print("\n== summary ==")
        print(f"all bus permission event types: "
              f"{[e.get('type') for e in bus.events]}")
        print(f"bus event types seen: "
              f"{sorted(bus.types_seen.items(), key=lambda x: -x[1])}")
        print(f"bus total events: {sum(bus.types_seen.values())}")
        print(f"OK={ok}")
        return 0 if ok else 1
    finally:
        _kill_tree(proc)
        try:
            shutil.rmtree(scratch, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
