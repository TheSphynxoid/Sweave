"""Supervisor step-4 live gate: island plugin ferries tool-started.

Spins a scratch `opencode serve` WITH the sweave island plugin
(same provisioning ServeRunner uses), drives one FREE-tier turn
whose long tool (sleep 60) + quick tool must each ferry a
``tool-started`` POST to a stub HTTP server (standing in for the
Sweave endpoint — this gate proves the PLUGIN half live: hook
fires, token valid, session + tool + target present, mid-tool
timing). Attribution (stub -> trace) is pinned hermetically in
tests/test_supervisor_step4.py.

FREE-TIER MODELS ONLY (user ruling): lfm :free, then gemma :free
once; anything else exits BLOCKED without quota burn.

Reap discipline (probe_bus_inventory litter lesson): poll-then-kill
with verification — a gate must never leave a serve behind.

Run:  python scripts/supervisor_ferry_gate.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from sweave.harness.opencode import isolated_opencode_env
from sweave.platform import creationflags_no_window
from sweave.runtime import permission_bridge as bridge

FREE_MODELS = [
    ("openrouter", "liquid/lfm-2.5-2.6b:free"),
    ("openrouter", "google/gemma-4-26b-a4b-it:free"),
]
PROMPT = (
    "Use the bash tool to run exactly this command and wait for it: "
    'python -c "import time; time.sleep(60)". '
    "Pass a timeout of at least 70000 ms so the sleep survives. "
    "When it finishes, use the bash tool once more to run `echo ferry-done`. "
    "Then reply with exactly DONE and nothing else."
)


def free_port(start: int) -> int:
    for port in range(start, start + 32):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port")


def reap(proc: subprocess.Popen, name: str, timeout: float = 10.0) -> None:
    """Poll-then-kill with verification (never leave a serve)."""
    try:
        proc.wait(timeout=timeout)
        print(f"REAP {name}: exited clean")
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=timeout)
        print(f"REAP {name}: killed")
    except subprocess.TimeoutExpired:
        print(f"REAP {name}: STILL ALIVE pid={proc.pid} (kill by hand)")
        raise SystemExit(f"stray {name} serve at pid {proc.pid}")


async def main() -> int:
    binary = shutil.which("opencode")
    assert binary, "opencode not on PATH"
    # The island plugin must be the CURRENT bundled source (with the
    # ferry hook): refresh, then serve from the island env.
    island_env = bridge.ensure_permission_bridge()
    island_plugin = bridge.plugin_path().read_text(encoding="utf-8")
    assert '"tool.execute.before"' in island_plugin, "island plugin predates the ferry"
    print("ISLAND: plugin carries the ferry hook")

    received: list[dict] = []

    async def stub_app(scope, receive, send):
        assert scope["type"] == "http"
        body = b""
        while True:
            msg = await receive()
            if msg["type"] == "http.request":
                body += msg.get("body", b"")
                if not msg.get("more_body"):
                    break
            elif msg["type"] == "http.disconnect":
                return
        rec = {"path": scope["path"], "headers": dict(scope.get("headers", []))}
        try:
            rec["json"] = json.loads(body or b"{}")
        except ValueError:
            rec["json"] = {}
        received.append(rec)
        payload = json.dumps({"status": "ok", "noted": True}).encode()
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": payload})

    import uvicorn  # type: ignore

    stub_port = free_port(18980)
    server = uvicorn.Server(uvicorn.Config(stub_app, host="127.0.0.1", port=stub_port, log_level="error"))
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.1)

    serve_port = free_port(18990)
    workdir = tempfile.mkdtemp(prefix="ferry-gate-")
    env = isolated_opencode_env(dict(os.environ))
    env.update(island_env)
    env["SWEAVE_PORT"] = str(stub_port)
    env["SWEAVE_MCP_TOKEN"] = "ferry-gate-token"
    proc = subprocess.Popen(
        [binary, "serve", "--port", str(serve_port), "--hostname", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=workdir,
        env=env,
        creationflags=creationflags_no_window(),
    )
    base = f"http://127.0.0.1:{serve_port}"
    t0 = time.time()
    try:
        async with httpx.AsyncClient(base_url=base, timeout=10.0) as c:
            for _ in range(60):
                try:
                    if (await c.get("/session")).status_code == 200:
                        break
                except httpx.ConnectError:
                    await asyncio.sleep(0.5)
            sid = (await c.post("/session", json={"title": "ferry-gate"})).json()["id"]
        print(f"SESSION {sid}")

        used = None
        for provider, model in FREE_MODELS:
            print(f"TRY {provider}/{model}")
            try:
                async with httpx.AsyncClient(base_url=base, timeout=300.0) as c:
                    async with c.stream(
                        "POST", f"/session/{sid}/message",
                        json={
                            "parts": [{"type": "text", "text": PROMPT}],
                            "model": {"providerID": provider, "modelID": model},
                        },
                    ) as resp:
                        if resp.status_code != 200:
                            body = (await resp.aread()).decode()[:160]
                            print(f"  failed: http={resp.status_code} {body}")
                            if "429" not in body and resp.status_code != 429:
                                break
                            continue
                        async for _ in resp.aiter_text():
                            pass
                used = (provider, model)
                break
            except Exception as e:
                print(f"  error: {e}")
                break
        # Let trailing ferries land.
        await asyncio.sleep(5.0)
        ferry_posts = [r for r in received if r["path"] == "/api/activity/tool-started"]
        print(f"\nUSED: {used}")
        print(f"FERRY POSTS: {len(ferry_posts)}")
        ok = True
        if not used:
            print("RESULT: BLOCKED (no free-tier turn; rerun later)")
            return 2
        if not ferry_posts:
            print("RESULT: FAIL (no ferry posts during a tool turn)")
            return 1
        for r in ferry_posts:
            headers = {k.decode(): v.decode() for k, v in r["headers"].items()}
            j = r["json"]
            tok_ok = headers.get("x-sweave-mcp-token") == "ferry-gate-token"
            has_ids = bool(j.get("session_id")) and bool(j.get("tool"))
            dt = round(time.time() - t0, 1)
            print(f"  tool={j.get('tool')} target={str(j.get('target'))[:60]!r} token_ok={tok_ok} ids={has_ids}")
            ok = ok and tok_ok and has_ids
        tools = {r["json"].get("tool") for r in ferry_posts}
        if "bash" not in tools:
            print("RESULT: FAIL (bash start never ferried)")
            return 1
        print("RESULT: PASS" if ok else "RESULT: FAIL (token/ids)")
        return 0 if ok else 1
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=10.0)
        except asyncio.TimeoutError:
            server_task.cancel()
        reap(proc, "ferry-gate")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
