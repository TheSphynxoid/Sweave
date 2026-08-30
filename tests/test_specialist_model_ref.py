"""Tests for the M1.3 K-revised ModelRef on Specialist (M1.3 step 2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sweave.runtime.specialist_store import (
    ModelRef,
    Specialist,
    _parse_stored_model,
    model_ref_to_wire,
    parse_model_ref,
)


# --- parse_model_ref / model_ref_to_wire -------------------------------


def test_parse_model_ref_none_and_empty():
    assert parse_model_ref(None) is None
    assert parse_model_ref("") is None


def test_parse_model_ref_slash_form():
    assert parse_model_ref("ollama/qwen3:8b") == ModelRef(
        provider="ollama", model_id="qwen3:8b"
    )


def test_parse_model_ref_bare_string_keeps_provider_none():
    """v1 records (bare string) -> ModelRef(provider=None, model_id=raw)."""
    assert parse_model_ref("qwen3:8b") == ModelRef(
        provider=None, model_id="qwen3:8b"
    )


def test_parse_model_ref_dict_strips_unknown_keys():
    out = parse_model_ref({"provider": "gmi", "model_id": "m", "extra": "x"})
    assert out == ModelRef(provider="gmi", model_id="m")


def test_model_ref_to_wire_returns_structured_pair():
    ref = ModelRef(provider="gmi", model_id="MiniMaxAI/MiniMax-M3")
    assert model_ref_to_wire(ref) == {
        "providerID": "gmi", "modelID": "MiniMaxAI/MiniMax-M3"
    }


def test_model_ref_to_wire_returns_none_when_provider_missing():
    """v1-style ref (provider=None) -> wire returns None; caller falls back
    to the unqualified-name path with a warning."""
    ref = ModelRef(provider=None, model_id="qwen3:8b")
    assert model_ref_to_wire(ref) is None


def test_model_ref_to_wire_returns_none_when_model_id_missing():
    ref = ModelRef(provider="ollama", model_id=None)
    assert model_ref_to_wire(ref) is None


# --- _parse_stored_model (the on-disk decoder) --------------------------


def test_parse_stored_model_none():
    assert _parse_stored_model(None) is None
    assert _parse_stored_model("") is None


def test_parse_stored_model_json_encoded_structured_pair():
    """The v2 / K-revised on-disk shape: a JSON string."""
    raw = json.dumps({"provider": "gmi", "model_id": "MiniMaxAI/MiniMax-M3"})
    ref = _parse_stored_model(raw)
    assert ref == ModelRef(provider="gmi", model_id="MiniMaxAI/MiniMax-M3")


def test_parse_stored_model_bare_string_falls_back_to_legacy_path():
    """v1 on-disk shape: a bare string. Returns ModelRef with provider=None."""
    ref = _parse_stored_model("qwen3:8b")
    assert ref == ModelRef(provider=None, model_id="qwen3:8b")


def test_parse_stored_model_slash_form():
    ref = _parse_stored_model("ollama/qwen3:8b")
    assert ref == ModelRef(provider="ollama", model_id="qwen3:8b")


def test_parse_stored_model_garbage_json_falls_back_to_bare():
    """A corrupted JSON value should be treated as a bare string (legacy
    path), not raise. The store loads; routing uses the warning path."""
    ref = _parse_stored_model("{not valid json")
    assert ref == ModelRef(provider=None, model_id="{not valid json")


# --- Specialist.model_ref property + set_model_ref ---------------------


def test_specialist_model_ref_none_when_unset():
    s = Specialist(name="alpha")
    assert s.model_ref is None
    assert s.current_model is None


def test_specialist_model_ref_parses_bare_string():
    s = Specialist(name="alpha", current_model="qwen3:8b")
    assert s.model_ref == ModelRef(provider=None, model_id="qwen3:8b")


def test_specialist_model_ref_parses_slash_string():
    s = Specialist(name="alpha", current_model="ollama/qwen3:8b")
    assert s.model_ref == ModelRef(provider="ollama", model_id="qwen3:8b")


def test_specialist_model_ref_parses_json_encoded():
    raw = json.dumps({"provider": "gmi", "model_id": "MiniMaxAI/MiniMax-M3"})
    s = Specialist(name="alpha", current_model=raw)
    assert s.model_ref == ModelRef(provider="gmi", model_id="MiniMaxAI/MiniMax-M3")


def test_specialist_set_model_ref_round_trips_via_storage():
    s = Specialist(name="alpha")
    s.set_model_ref(ModelRef(provider="zai", model_id="glm-5.3"))
    # current_model now holds the JSON-encoded structured pair
    assert s.current_model is not None
    parsed = json.loads(s.current_model)
    assert parsed == {"provider": "zai", "model_id": "glm-5.3"}
    # model_ref decodes it back
    assert s.model_ref == ModelRef(provider="zai", model_id="glm-5.3")


def test_specialist_set_model_ref_none_clears_current_model():
    s = Specialist(name="alpha", current_model="qwen3:8b")
    s.set_model_ref(None)
    assert s.current_model is None
    assert s.model_ref is None


def test_specialist_set_model_ref_partial_stores_empty():
    """A ModelRef with both fields None -> current_model=None."""
    s = Specialist(name="alpha", current_model="qwen3:8b")
    s.set_model_ref(ModelRef(provider=None, model_id=None))
    assert s.current_model is None


# --- v1 -> v2 migration round-trip --------------------------------------


def test_v1_record_round_trip_preserves_bare_string():
    """A v1 record (bare string) round-trips through to_dict / from_dict
    unchanged -- the v1 path stays valid for the v1 path. The harness
    handles the legacy-path warning."""
    v1_dict = {
        "schema_version": 1,
        "name": "alpha",
        "current_model": "qwen3:8b",
    }
    s = Specialist.from_dict(v1_dict)
    assert s.current_model == "qwen3:8b"
    # model_ref decodes to legacy shape (provider None)
    assert s.model_ref == ModelRef(provider=None, model_id="qwen3:8b")
    # Round-trip preserves the v1 bare-string form
    d2 = s.to_dict()
    assert d2["current_model"] == "qwen3:8b"


def test_v2_record_round_trips_with_model_ref():
    s = Specialist(name="alpha")
    s.set_model_ref(ModelRef(provider="zai", model_id="glm-5.3"))
    d = s.to_dict()
    # current_model is JSON-encoded in storage
    assert json.loads(d["current_model"]) == {
        "provider": "zai", "model_id": "glm-5.3"
    }
    s2 = Specialist.from_dict(d)
    assert s2.model_ref == ModelRef(provider="zai", model_id="glm-5.3")
