"""2026-09-13 incident round: v2 model precedence + pre-model bound.

Pins:

* The v2 submit path (used by MCP defer + chat dispatch) resolves the
  model through the FULL M1.2/M1.4 chain: task_override >
  specialist.current_model > default. The specialist tier was missing
  (paid-tier pick silently shadowed by the free default).
* The header phase gets the generous PRE_MODEL bound (legit serve
  warmup: tool-loop steps, compaction, provider admission), while the
  body keeps the 300s silence clock. The 02:05 retry died at exactly
  300s with zero serve-side activity because the header wait used the
  body bound.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def _resolver_with_paid_pick(monkeypatch, tmp_path: Path):
    """Resolver whose global store carries a paid specialist pick."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(tmp_path)

    from sweave.runtime.specialist_store import (
        Specialist,
        SpecialistResolver,
    )

    resolver = SpecialistResolver()
    rec = Specialist(
        name="backend-specialist",
        scope="global",
        role_ref="backend",
        description="d",
        system_prompt="p",
    )
    rec.set_model_ref(
        {"provider": "opencode-go", "model_id": "deepseek-v4.1-flash",
         "variant": "high"}
    )
    resolver.global_store.upsert(rec)
    return resolver


def test_specialist_current_model_resolves_over_default(
    _resolver_with_paid_pick,
):
    """The resolved specialist view carries the paid pick in canonical
    form — the value the v2 submit path must now consult."""
    resolver = _resolver_with_paid_pick
    resolved = resolver.resolve("backend-specialist", None)
    assert resolved is not None
    assert resolved.public_model() == "opencode-go/deepseek-v4.1-flash+high"


def test_pre_model_header_bound_used_for_headers(monkeypatch, tmp_path: Path):
    """The header phase times out at PRE_MODEL (patched small), NOT the
    body stall_seconds (300s class)."""
    import sweave.runtime.specialist_runtime as rt
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    monkeypatch.setattr(rt, "PRE_MODEL_TIMEOUT_SECONDS", 0.05)
    # stall_seconds deliberately LARGER than the pre-model bound: the
    # old code tripped at stall_seconds; the new code must trip at the
    # pre-model bound first.
    monkeypatch.setattr(rt, "STALL_TIMEOUT_SECONDS", 5.0)

    class _SlowOpenClient:
        def stream(self, method: str, url: str, **kwargs: Any) -> Any:
            class _Resp:
                async def __aenter__(self) -> Any:
                    await asyncio.sleep(3600)
                    raise AssertionError("unreachable")

                async def __aexit__(self, *args: Any) -> bool:
                    return False

            return _Resp()

    class _Proc:
        _client = _SlowOpenClient()
        _session_id = "ses_hdr"

    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    from sweave.runtime.trace_log import TraceLog

    trace = TraceLog("d-hdr", base_dir=tmp_path / "traces")

    async def run():
        return await runtime._send_message(
            _Proc(),
            {"parts": [{"type": "text", "text": "hi"}]},
            trace,
            stall_seconds=5.0,
        )

    out = asyncio.new_event_loop().run_until_complete(run())
    assert "stalled after 0s" in out, out  # 0.05s renders as 0s
    lines = (tmp_path / "traces" / "d-hdr.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    stalled = [e for e in events if e.get("event") == "stalled"]
    assert stalled and stalled[0]["stall_seconds"] == 0.05
