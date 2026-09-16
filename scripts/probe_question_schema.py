"""TOOL_CARDS step 0 probe (half 2): opencode `question` tool schema.

Zero-cost where honest; ONE free-tier turn when the schema needs the
wire. Spins a scratch opencode serve (isolated data dir, temp cwd),
subscribes /event, then sends one free-tier turn whose ONLY job is to
call opencode's native `question` tool with TWO questions. Records:
1. the tool-call PART the model emitted (the wire schema proof — params
   actually accepted by the installed binary, 1.18.31);
2. the pending-permission surface shape (questions? options? header?)
   via the M1.12 jump-table routes;
3. the answer post lever + the turn's continuation text;
4. same single-question call from a SECOND turn (batch compat probe).

FREE-TIER ONLY (user ruling): lfm :free first, gemma :free once; any
other outcome exits BLOCKED without burning quota in a retry loop.

Keep as the drift gate: re-run reproduces the same schema table.

Output: JSON to stdout (tables land in docs/TOOL_CARDS_PLAN.md §6).
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
    ("openreader-unused", "x"),
]
FREE_MODELS[0] = ("openrouter", "liquid/lfm-2.5-2.6b:free")
FREE_MODELS.append(("openrouter", "google/gemma-4-26b-a4b-it:free"))

PENDING_GET_ROUTES = (
    "/session/{sid}/permissions",
    "/session/{sid}/permission",
    "/session/{sid}/permission/pending",
    "/api/session/{sid}/permissions",
    "/api/permission",
    "/permission",
)

PROMPT_ZH = (
    "Use the native question tool to ask the user TWO questions at once "
    "by passing an array parameter named questions with two items if the "
    "tool supports it: first 'Which database?' with options ['Postgres', "
    "'SQLite']; second 'Migrate now?' with options ['yes','no']. If the "
    "tool only supports ONE question per call, call it once with the "
    "first question + options exactly. Never answer yourself."
)
PROMPT_EN = (
    "Ask the user one question using the built-in ask-user tool: which "
    "database, options: Postgres, SQLite. Use the tool exactly; do not "
    "answer yourself."
)

ANSWER_BODIES = ([{"response": "Postgres"}] + [{}, {"answers": ["Postgres"]}])


def free_port(start: int) -> int:
    for p in range(start, start + 40):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                continue
            return p
    raise RuntimeError("no free port")


async def _poll_pending(client: httpx.AsyncClient, sid: str):
    hits: list[tuple[str, Any]] = []
    for route in PENDING_GET_ROUTES:
        try:
            r = await client.get(route.format(sid=sid), timeout=5.0)
        except Exception as exc:
            hits.append((route, f"ERR {exc}"))
            continue
        if r.status_code != 404:
            hits.append(
                (route, r.text[:400] if r.status_code == 200 else f"{r.status_code}")
            )
    return hits


async def main() -> int:
    import sweave.harness.opencode as oc

    binary = shutil.which("opencode")
    if not binary:
        print(json.dumps({"outcome": "BLOCKED", "reason": "no opencode"}))
        return 1
    # npm .CMD shims are not directly spawnable on Windows (Popen with
    # shell=False cannot execute them — the probe silently never came
    # up until this). Extract the shim's real .exe via the harness
    # resolver (same logic as OpenCodeHarness._exe_from_shim).
    if binary.lower().endswith((".cmd", ".bat")):
        from sweave.harness.opencode import OpenCodeHarness

        exe = OpenCodeHarness._exe_from_shim(binary)
        if exe:
            binary = exe
        else:
            print(json.dumps({"outcome": "BLOCKED", "reason": "shim unresolved"}))
            return 1
    port = free_port(19280)
    wd = tempfile.mkdtemp(prefix="toolcards-q-")
    env = dict(os.environ)
    # SWEAVE_OPENCODE_SHARED_DATA=1: the isolated 4GB db copy took
    # longer to spin than the readiness window (the 2026-09-10
    # migration). The schema probe is read-only surface-wise; the
    # one session it creates names itself toolcards-q in the
    # shared db (documented, disposable).
    env["SWEAVE_OPENCODE_SHARED_DATA"] = "1"
    proc = subprocess.Popen(
        [binary, "serve", "--port", str(port), "--hostname", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=wd,
        env=env,
        creationflags=creationflags_no_window(),
    )
    base = f"http://127.0.0.1:{port}"
    report: dict[str, Any] = {"version": "1.18.31", "model": None}
    try:
        ready = False
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                if httpx.get(base + "/session", timeout=3).status_code == 200:
                    ready = True
                    break
            except Exception:
                time.sleep(0.5)
        if not ready:
            report["outcome"] = "BLOCKED"
            report["reason"] = "serve never became ready (60x0.5s)"
            print(json.dumps(report, indent=1))
            return 1
        async with httpx.AsyncClient(base_url=base, timeout=10.0) as c:
            sid = (await c.post("/session", json={"title": "toolcards-q"})).json()["id"]
        for _model in FREE_MODELS:
            provider, model = _model
            async with httpx.AsyncClient(base_url=base, timeout=240.0) as c:
                try:
                    resp = httpx.post(
                        base + f"/session/{sid}/message",
                        timeout=200,
                        json={
                            "parts": [
                                {"type": "text", "text": PROMPT_ZH + chr(10) * 2 + PROMPT_EN}
                            ],
                            "model": {"providerID": provider, "modelID": model},
                            "agent": "build",
                        },
                    )
                    txt = resp.text
                except Exception as e:
                    report["turn"] = f"{type(e).__name__}: {str(e)[:120]}"
                    continue
                # Parse the v2 stream for tool parts.
                tools: list[dict] = []
                for piece in _split_json_stream(txt, "")[0]:
                    try:
                        obj = json.loads(piece)
                    except Exception:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    for p in obj.get("parts", []) or []:
                        if isinstance(p, dict) and p.get("type") == "tool":
                            tools.append(p)
                report["tool_parts"] = [
                    {
                        "tool": t.get("tool"),
                        "state_status": (t.get("state") or {}).get("status"),
                        "state_input": (t.get("state") or {}).get("input"),
                        "state_error": (
                            (t.get("state") or {}).get("error") or ""
                        )[:200],
                    }
                    for t in tools
                ]
                report["model"] = f"{provider}/{model}"
                # Durable surface: the session message listing
                # carries the full part shapes even when the
                # non-streaming response body truncated. Keeps the
                # schema table honest for every answer path.
                try:
                    listing = httpx.get(
                        base + f"/session/{sid}/message", timeout=15
                    ).json()
                    rows = (
                        listing
                        if isinstance(listing, list)
                        else listing.get("messages", [])
                    )
                    durable = []
                    for m in rows:
                        for p in (m.get("parts") or []) if isinstance(m, dict) else []:
                            if isinstance(p, dict) and p.get("type") == "tool":
                                durable.append({
                                    "tool": p.get("tool"),
                                    "state_input": (p.get("state") or {}).get("input"),
                                })
                    if durable:
                        report["durable_tool_parts"] = durable[0]
                    # The promoted message listing also reveals the
                    # model's text / no-tool answer
                except Exception as e:
                    report["listing_err"] = str(e)[:120]
                # Pending surface + Q&A state
                async with httpx.AsyncClient(base_url=base, timeout=10.0) as p2:
                    report["pending_before_answer"] = await _poll_pending(p2, sid)
                    break
        print(json.dumps(report, indent=1))
        return 0
    finally:
        try:
            proc.kill()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
