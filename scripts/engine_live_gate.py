"""Engine step-1 live gate (FREE-TIER ONLY, never paid).

Drives one real chat turn through the sidecar + Python adapter against
an OpenRouter :free catalog model (auth bootstrapped from the opencode
auth store by the sidecar itself — no key material here, no key args).

Asserts the step-1 done-gate shape: incremental SSE tokens, joined
output, tokens_used terminal. Run by hand, never in pytest:

    python scripts/engine_live_gate.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

MODEL = os.environ.get(
    "ENGINE_GATE_MODEL", "openrouter/google/gemma-4-26b-a4b-it:free"
)
PROMPT = os.environ.get("ENGINE_GATE_PROMPT", "Reply with exactly: ENGINE LIVE")


async def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.Popen(
        [
            "node",
            str(repo_root / "sweave-engine" / "src" / "serve.js"),
            "--port",
            "0",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env={**os.environ},
    )
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline().strip()
        assert line.startswith("SWEAVE_ENGINE_PORT="), line
        url = f"http://127.0.0.1:{line.split('=', 1)[1]}"
        for _ in range(50):
            try:
                resp = httpx.get(f"{url}/health", timeout=2.0)
                if resp.status_code == 200:
                    break
            except httpx.ConnectError:
                time.sleep(0.1)
        print(f"sidecar: {url} (protocol {resp.headers.get('X-Sweave-Engine-Protocol')})")

        from sweave.harness.base import AgentSpec, Message
        from sweave.harness.engine import SweaveEngineHarness

        os.environ["SWEAVE_ENGINE_URL"] = url
        provider, model_id = MODEL.split("/", 1)
        spec = AgentSpec(
            name="orchestrator",
            role="orchestrator",
            model=MODEL,
            system_prompt="",
            worktree_path=Path("."),
            memory_bank="session-live",
            tools=[],
            harness="sweave-engine",
        )
        harness = SweaveEngineHarness()
        assert await harness.health_check(), "sidecar unhealthy"
        chunks: list[str] = []
        trace_events: list[tuple] = []

        class Trace:
            def append(self, name, payload):
                trace_events.append((name, payload))

        proc_handle = await harness.spawn(spec)
        started = time.time()
        result = await proc_handle.send(
            Message(type="user", content=PROMPT, metadata={"turn_timeout": 300}),
            on_chunk=chunks.append,
            trace=Trace(),
        )
        elapsed = time.time() - started
        print(f"chunks: {len(chunks)} in {elapsed:.1f}s")
        print(f"output: {result.output[:200]!r}")
        print(f"error: {result.error}")
        anchored = [p for n, p in trace_events if n == "tokens_used"]
        print(f"tokens_used: {anchored}")
        ok = bool(result.success and result.output and anchored)
        print("LIVE GATE:", "GREEN" if ok else "RED")
        print(f"(provider={provider} model={model_id} — free tier, $0)")
        return 0 if ok else 1
    finally:
        proc.kill()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
