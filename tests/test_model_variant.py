"""Model variant tests: thinking-level selection per turn.

Covers the ``+variant`` suffix contract end to end:
* ``parse_model_ref`` splits ``provider/model+variant`` (and bare
  ``model+variant`` / dict forms) without disturbing ids that
  contain ``/``, ``:`` or ``@``.
* ``model_ref_to_wire`` passes ``variant`` through to
  ``body["model"]["variant"]`` and omits it when unset.
* Stored-model round-trip (JSON + bare string) preserves variant.
* ``SpecialistRuntime.run`` carries the variant onto the wire
  (observed via the trace's ``model_used.model_wire``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sweave.harness.base import ModelRef, model_ref_to_wire
from sweave.runtime.delegation_store import Delegation
from sweave.runtime.serve_runner import ServeRunnerRegistry
from sweave.runtime.specialist_runtime import SpecialistRuntime
from sweave.runtime.specialist_store import (
    Specialist,
    _parse_stored_model,
    parse_model_ref,
)
from sweave.runtime.trace_log import TraceLog


@pytest.fixture(autouse=True, scope="module")
def _mock_opencode_env():
    old = os.environ.get("SWEAVE_MOCK_OPENCODE")
    os.environ["SWEAVE_MOCK_OPENCODE"] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("SWEAVE_MOCK_OPENCODE", None)
        else:
            os.environ["SWEAVE_MOCK_OPENCODE"] = old


def test_parse_variant_suffix():
    ref = parse_model_ref("openrouter/thinkingmachines/inkling:free+low")
    assert ref == {
        "provider": "openrouter",
        "model_id": "thinkingmachines/inkling:free",
        "variant": "low",
    }


def test_parse_without_variant_unchanged():
    assert parse_model_ref("openrouter/thinkingmachines/inkling:free") == {
        "provider": "openrouter",
        "model_id": "thinkingmachines/inkling:free",
    }
    assert parse_model_ref("ollama/qwen3:8b") == {
        "provider": "ollama",
        "model_id": "qwen3:8b",
    }
    # @cf ids contain / and @ but no variant.
    assert parse_model_ref("cloudflare-workers-ai/@cf/meta/llama-3.1-8b-instruct-fp8") == {
        "provider": "cloudflare-workers-ai",
        "model_id": "@cf/meta/llama-3.1-8b-instruct-fp8",
    }


def test_parse_bare_model_with_variant():
    assert parse_model_ref("qwen3:8b+high") == {
        "provider": None,
        "model_id": "qwen3:8b",
        "variant": "high",
    }


def test_parse_plus_inside_model_id_survives():
    # A + whose tail contains / is not a variant (variant names are
    # single tokens); the whole string stays the model id.
    assert parse_model_ref("prov/a+b/c") == {
        "provider": "prov",
        "model_id": "a+b/c",
    }
    # Trailing + with nothing after it is not a variant either.
    assert parse_model_ref("prov/model+") == {
        "provider": "prov",
        "model_id": "model+",
    }


def test_parse_dict_preserves_variant():
    assert parse_model_ref(
        {"provider": "openrouter", "model_id": "x/y", "variant": "max"}
    ) == {"provider": "openrouter", "model_id": "x/y", "variant": "max"}
    # Unknown keys still stripped.
    assert parse_model_ref({"provider": "p", "model_id": "m", "bogus": 1}) == {
        "provider": "p",
        "model_id": "m",
    }


def test_wire_passthrough():
    ref: ModelRef = {
        "provider": "openrouter",
        "model_id": "thinkingmachines/inkling:free",
        "variant": "low",
    }
    assert model_ref_to_wire(ref) == {
        "providerID": "openrouter",
        "modelID": "thinkingmachines/inkling:free",
        "variant": "low",
    }
    # Unset variant -> omitted (provider default applies).
    assert model_ref_to_wire(
        {"provider": "openrouter", "model_id": "thinkingmachines/inkling:free"}
    ) == {
        "providerID": "openrouter",
        "modelID": "thinkingmachines/inkling:free",
    }


def test_stored_model_round_trip():
    spec = Specialist(
        name="v",
        scope="global",
        is_orchestrator=True,
        system_prompt="",
        harness="opencode",
        current_model=None,
    )
    spec.set_model_ref(
        {
            "provider": "openrouter",
            "model_id": "thinkingmachines/inkling:free",
            "variant": "none",
        }
    )
    assert spec.model_ref == {
        "provider": "openrouter",
        "model_id": "thinkingmachines/inkling:free",
        "variant": "none",
    }
    # Bare-string shape also carries the suffix.
    assert _parse_stored_model("openrouter/thinkingmachines/inkling:free+high") == {
        "provider": "openrouter",
        "model_id": "thinkingmachines/inkling:free",
        "variant": "high",
    }


@pytest.mark.asyncio
async def test_runtime_run_carries_variant_on_wire(tmp_path: Path):
    """End to end through the mock: the variant reaches body["model"]
    (observed via the trace's model_used.model_wire anchor)."""
    worktree = tmp_path / "wt"
    worktree.mkdir(parents=True, exist_ok=True)
    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    specialist = Specialist(
        name="variant-run",
        scope="global",
        is_orchestrator=True,
        system_prompt="",
        harness="opencode",
        current_model=None,
    )
    delegation = Delegation(agent="variant-run", task="hi", model="")
    trace = TraceLog("d-variant", base_dir=tmp_path)
    await runtime.run(
        specialist=specialist,
        delegation=delegation,
        worktree_path=worktree,
        message="hi",
        trace=trace,
        model_ref={
            "provider": "openrouter",
            "model_id": "thinkingmachines/inkling:free",
            "variant": "low",
        },
    )
    events = [
        json.loads(line)
        for line in trace.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    used = [e for e in events if e.get("event") == "model_used"]
    assert len(used) == 1
    assert used[0]["model_wire"] == {
        "providerID": "openrouter",
        "modelID": "thinkingmachines/inkling:free",
        "variant": "low",
    }
