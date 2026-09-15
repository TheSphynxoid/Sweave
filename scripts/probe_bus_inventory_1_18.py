"""View-probe step 0: /event bus + hook inventory during a real long-tool turn.

Spins a scratch `opencode serve` (installed 1.18.30, isolated data dir,
temp cwd), subscribes to the /event SSE bus recording EVERY event type
with timestamps, then sends one free-tier turn whose only job is a
~150s bash sleep followed by DONE. Also records message-stream chunk
timing (byte-flow proof) and one tool-part JSON shape (step-2 parser
input) from the post-turn message listing.

Output: the event inventory table + the step-1 sensor decision input
(did anything arrive mid-tool, or was the bus as silent as the
stream?). Kept as the wire-drift gate: a second run must reproduce
the same type set.

FREE-TIER MODELS ONLY (user ruling). Tries lfm :free first (known
good), then gemma :free once; any other outcome exits BLOCKED without
burning quota in a retry loop.

Run:  python scripts/probe_bus_inventory_1_18.py
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
from sweave.runtime.specialist_runtime import _split_json_stream

FREE_MODELS = [
    ("openrouter", "liquid/lfm-2.5-2.6b:free"),
    ("openrouter", "google/gemma-4-26b-a4b-it:free"),
]
PROMPT = (
    "Use the bash tool to run exactly this command and wait for it: "
    'python -c "import time; time.sleep(150)". '
    "The bash tool kills long commands by default: pass a timeout of at "
    "least 170 seconds (170000 ms) so the sleep survives. "
    "When it finishes, reply with exactly DONE and nothing else."
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


async def main() -> int:
    binary = shutil.which("opencode")
    assert binary, "opencode not on PATH"
    port = free_port(18960)
    workdir = tempfile.mkdtemp(prefix="bus-probe-")
    env = isolated_opencode_env(dict(os.environ))
    proc = subprocess.Popen(
        [binary, "serve", "--port", str(port), "--hostname", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=workdir,
        env=env,
        creationflags=creationflags_no_window(),
    )
    base = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(base_url=base, timeout=10.0) as c:
            for _ in range(60):
                try:
                    if (await c.get("/session")).status_code == 200:
                        break
                except httpx.ConnectError:
                    await asyncio.sleep(0.5)
            sid = (await c.post("/session", json={"title": "bus-probe"})).json()["id"]
        print(f"SESSION {sid} on {base}")

        events: list[dict] = []
        samples: dict[str, list] = {}
        t0 = time.time()

        def _record(obj: dict) -> None:
            etype = obj["type"]
            events.append({"t": round(time.time() - t0, 1), "type": etype})
            # Keep 3 truncated property samples per type: enough to
            # tell tool-progress apart from model-streaming.
            if len(samples.get(etype, [])) < 3:
                props = obj.get("properties", obj)
                samples.setdefault(etype, []).append(
                    json.dumps(props, default=str)[:300]
                )

        async def watch_bus():
            async with httpx.AsyncClient(base_url=base, timeout=None) as c:
                async with c.stream("GET", "/event") as resp:
                    buf = ""
                    async for chunk in resp.aiter_text():
                        buf += chunk
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            line = line.strip()
                            if line.startswith("data:"):
                                line = line[len("data:"):].strip()
                            if not line:
                                continue
                            try:
                                obj = json.loads(line)
                            except ValueError:
                                continue
                            if isinstance(obj, dict) and obj.get("type"):
                                _record(obj)

        async def run_turn(provider: str, model: str):
            chunks: list[tuple[float, int]] = []
            outcome: dict = {}
            async with httpx.AsyncClient(base_url=base, timeout=420.0) as c:
                async with c.stream(
                    "POST",
                    f"/session/{sid}/message",
                    json={
                        "parts": [{"type": "text", "text": PROMPT}],
                        "model": {"providerID": provider, "modelID": model},
                    },
                ) as resp:
                    if resp.status_code != 200:
                        outcome["http"] = resp.status_code
                        outcome["body"] = (await resp.aread()).decode()[:300]
                        return chunks, outcome
                    buf = ""
                    carry = ""
                    async for piece in resp.aiter_text():
                        now = time.time() - t0
                        pieces, carry = _split_json_stream(piece, carry)
                        for p in pieces:
                            chunks.append((round(now, 1), len(p)))
                    outcome["ok"] = True
            return chunks, outcome

        watcher = asyncio.create_task(watch_bus())
        await asyncio.sleep(1.0)  # let the bus connect before the turn
        chunks, outcome = None, None
        used = None
        for provider, model in FREE_MODELS:
            print(f"TRY {provider}/{model}")
            chunks, outcome = await run_turn(provider, model)
            body = (outcome.get("body") or "")
            if outcome.get("ok"):
                used = (provider, model)
                break
            print(f"  failed: http={outcome.get('http')} {body[:160]}")
            if "429" not in body and outcome.get("http") != 429:
                break  # not a quota issue — a different model won't help
        # Drain the bus briefly for the terminal session.idle, then stop.
        await asyncio.sleep(5.0)
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass

        print(f"\nUSED: {used}")
        print(f"TURN OUTCOME: {'ok' if outcome and outcome.get('ok') else outcome}")
        by_type: dict[str, list[float]] = {}
        for e in events:
            by_type.setdefault(e["type"], []).append(e["t"])
        print(f"\nBUS INVENTORY ({len(events)} events):")
        for etype in sorted(by_type):
            ts = by_type[etype]
            print(f"  {etype}: n={len(ts)} first={ts[0]}s last={ts[-1]}s")
            for sample in samples.get(etype, [])[:2]:
                if etype.startswith(("message.part", "session.")):
                    print(f"    e.g. {sample[:220]}")
        if chunks:
            gaps = [
                round(chunks[i + 1][0] - chunks[i][0], 1)
                for i in range(len(chunks) - 1)
            ]
            big = [g for g in gaps if g >= 10]
            print(
                f"\nSTREAM: {len(chunks)} chunks, "
                f"max_gap={max(gaps) if gaps else 0}s, gaps>=10s: {len(big)}"
            )
        else:
            print("\nSTREAM: no chunks captured")
        # Tool-part shape for the step-2 parser.
        try:
            async with httpx.AsyncClient(base_url=base, timeout=15.0) as c:
                listing = (await c.get(f"/session/{sid}/message")).json()
            rows = listing if isinstance(listing, list) else listing.get("messages", [])
            n_tools = 0
            for row in rows:
                info = row.get("info", row) if isinstance(row, dict) else {}
                for part in (row.get("parts", []) if isinstance(row, dict) else []):
                    if not (isinstance(part, dict) and part.get("type") == "tool"):
                        continue
                    n_tools += 1
                    state = part.get("state", {})
                    print(
                        f"\nTOOL PART #{n_tools}: keys=" + ",".join(sorted(part.keys()))
                    )
                    print(
                        "  state keys=" + ",".join(sorted(state.keys()))
                        + f" tool={part.get('tool')!r} status={state.get('status')!r}"
                    )
                    print(f"  input: {json.dumps(state.get('input'), default=str)[:300]}")
                    out = state.get("output") or ""
                    print(f"  output preview: {str(out)[:200]!r}")
            if not n_tools:
                print("\nTOOL PART SHAPE: none found (turn may not have run a tool)")
        except Exception as e:
            print(f"\nTOOL PART SHAPE: listing failed: {e}")
        if not used:
            print("\nRESULT: BLOCKED (no free-tier turn; quotas hot — rerun later)")
            return 2
        mid_tool = [
            e for e in events if e["type"] not in ("session.idle",)
        ]
        print(
            f"\nSENSOR INPUT: {len(mid_tool)} non-idle bus events; "
            + ("bus speaks mid-turn" if mid_tool else "bus silent mid-turn")
        )
        # Distribution histogram (10s buckets) for the progress-class
        # events: proves activity DURING the tool window, not just at
        # its boundaries. Heartbeats excluded (keepalives prove the
        # serve is alive, never that the tool progresses).
        progress = {"message.part.delta", "message.part.updated", "message.updated", "session.status", "session.diff"}
        buckets: dict[int, int] = {}
        for e in events:
            if e["type"] in progress:
                buckets.setdefault(int(e["t"] // 10) * 10, 0)
                buckets[e["t"] // 10 * 10] += 1
        print("\nPROGRESS BUCKETS (10s, heartbeats excluded):")
        for start in sorted(buckets):
            print(f"  {start:5d}s: {'#' * min(buckets[start], 60)} ({buckets[start]})")
        return 0
    finally:
        # Reap discipline (2026-09-15: proc.kill() alone left the
        # scratch serve behind — poll-then-kill with verification).
        try:
            proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                print(f"STRAY serve at pid {proc.pid} (kill by hand)")
                raise SystemExit(f"stray probe serve at pid {proc.pid}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
