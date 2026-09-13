"""Go live probe: ONE tiny turn on a free Go model ($0, no spam).

Model: `opencode-go/ox-alpha-free` (free tier; unknown flavor, so a
loud flavor error is itself a finding). No tools (single-shot path),
no server needed. Auth resolves via the standard tiers; when falling
back to the auth store the key is pulled into process env ONLY —
prints output + token counts, never credentials.

Run:  python scripts/engine_go_live_probe.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MODEL = os.environ.get("ENGINE_GO_PROBE_MODEL", "opencode-go/ox-alpha-free")
PROMPT = "Reply with exactly GO-OK and nothing else."


def _mangled(provider: str) -> str:
    return "".join(ch.upper() if ch.isalnum() else "_" for ch in provider)


def ensure_key(provider: str) -> str:
    """Make sure a key for `provider` is reachable without printing it."""
    from sweave.credentials import (
        PROVIDER_ENV_KEYS,
        opencode_auth_paths,
        read_opencode_store,
    )

    if os.environ.get(f"SWEAVE_ENGINE_KEY_{_mangled(provider)}"):
        return "env:override"
    for name in PROVIDER_ENV_KEYS.get(provider, []):
        if os.environ.get(name):
            return f"env:{name}"
    for path in opencode_auth_paths():
        entry = read_opencode_store(path).get(provider)
        if isinstance(entry, dict) and entry.get("key"):
            os.environ[f"SWEAVE_ENGINE_KEY_{_mangled(provider)}"] = entry["key"]
            return f"auth-store:{path.name}"
    raise SystemExit(f"no {provider} credential in any tier (aborting, $0 spent)")


async def main() -> int:
    from sweave.harness.base import AgentSpec, Message
    from sweave.harness.engine import SweaveEngineHarness

    via = ensure_key(MODEL.split("/", 1)[0] if "/" in MODEL else MODEL)
    print(f"auth via: {via}")
    print(f"model: {MODEL}")
    spec = AgentSpec(
        name="go-probe",
        role="specialist",
        model=MODEL,
        system_prompt="",
        worktree_path=Path("."),
        memory_bank="session-go-probe",
        tools=[],
        harness="sweave-engine",
    )
    events: list[tuple] = []

    class Trace:
        def append(self, name, payload):
            events.append((name, payload))

    proc = await SweaveEngineHarness().spawn(spec)
    result = await proc.send(
        Message(type="user", content=PROMPT, metadata={}), trace=Trace()
    )
    print(f"success: {result.success}")
    print(f"output: {(result.output or '')[:200]!r}")
    if result.error:
        print(f"error: {result.error[:300]}")
    for name, payload in events:
        if name == "tokens_used":
            print(f"tokens_used: in={payload.get('input')} out={payload.get('output')}")
    if not result.success:
        return 1
    print("GO LIVE PROBE: GREEN ($0)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
