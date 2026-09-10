"""Model registry sync tests: deterministic generation contract.

Covers the pure parts of ``sweave.models_sync`` (no serve is
spawned here — the serve fetch is thin I/O over /config/providers):
* provider/model/variant ordering is sorted (re-syncs are no-op
  diffs when nothing changed upstream)
* every advertised variant becomes a ``{id}+{variant}`` row
* non-dict / variant-less entries contribute the base row only
* diff reports per-provider added/removed sets
* write -> reload round-trips through ConfigManager's registry
  shape, and every generated variant row parses back with its
  variant via ``parse_model_ref``
"""

from __future__ import annotations

from pathlib import Path

import yaml

from sweave.models_sync import (
    _normalize_providers,
    apply_serve_overlay,
    build_metadata,
    build_registry,
    build_upstream_registry,
    diff_registries,
    effort_variants,
    ensure_nonempty,
    merge_custom_rows,
    strip_variant_rows,
    write_registry,
)
from sweave.runtime.specialist_store import parse_model_ref


PAYLOAD = {
    "openrouter": {
        "thinkingmachines/inkling:free": {
            "variants": {"low": {}, "none": {}},
        },
        "openai/gpt-5": {
            "variants": {"minimal": {}, "low": {}, "medium": {}, "high": {}},
        },
        "plain/model": {},
        "weird": "not-a-dict",
    },
    "ollama": {
        "qwen3:8b": {},
    },
    "empty-provider": "not-a-dict",
}


def test_build_registry_sorted_with_variant_rows():
    registry = build_registry(PAYLOAD)
    assert list(registry) == ["ollama", "openrouter"]
    assert registry["ollama"] == ["qwen3:8b"]
    assert registry["openrouter"] == [
        "openai/gpt-5",
        "openai/gpt-5+high",
        "openai/gpt-5+low",
        "openai/gpt-5+medium",
        "openai/gpt-5+minimal",
        "plain/model",
        "thinkingmachines/inkling:free",
        "thinkingmachines/inkling:free+low",
        "thinkingmachines/inkling:free+none",
        "weird",
    ]


def test_build_registry_deterministic():
    assert build_registry(PAYLOAD) == build_registry(PAYLOAD)


def test_normalize_list_shape():
    payload = {
        "providers": [
            {
                "id": "openrouter",
                "name": "OpenRouter",
                "models": {
                    "thinkingmachines/inkling:free": {"variants": {"low": {}}},
                },
            },
            {"id": "empty", "name": "Empty"},
        ]
    }
    assert _normalize_providers(payload) == {
        "openrouter": {"thinkingmachines/inkling:free": {"variants": {"low": {}}}},
        "empty": {},
    }


def test_normalize_rejects_garbage():
    import pytest

    with pytest.raises(RuntimeError):
        _normalize_providers({"providers": []})
    with pytest.raises(RuntimeError):
        _normalize_providers({"unexpected": True})
    with pytest.raises(RuntimeError):
        ensure_nonempty({})
    with pytest.raises(RuntimeError):
        ensure_nonempty({"p": []})


def test_diff_reports_added_removed():
    old = {"openrouter": ["a", "b"], "ollama": ["qwen3:8b"]}
    new = {"openrouter": ["b", "c"], "zai": ["glm"]}
    diff = diff_registries(old, new)
    assert diff["openrouter"] == {"added": ["c"], "removed": ["a"]}
    assert diff["ollama"] == {"added": [], "removed": ["qwen3:8b"]}
    assert diff["zai"] == {"added": ["glm"], "removed": []}


def test_write_reload_round_trip(tmp_path: Path):
    registry = build_registry(PAYLOAD)
    target = tmp_path / "models.yaml"
    write_registry(target, registry, "openrouter/thinkingmachines/inkling:free+low")

    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["models"]["default"] == "openrouter/thinkingmachines/inkling:free+low"
    assert data["models"]["providers"] == registry


def test_every_variant_row_parses_back():
    registry = build_registry(PAYLOAD)
    for provider, rows in registry.items():
        for row in rows:
            ref = parse_model_ref(f"{provider}/{row}")
            assert ref is not None
            assert ref["provider"] == provider
            if "+" in row:
                base, _, variant = row.rpartition("+")
                assert ref["model_id"] == base
                assert ref["variant"] == variant
            else:
                assert ref["model_id"] == row
                assert "variant" not in ref


UPSTREAM = {
    "openrouter": {
        "thinkingmachines/inkling:free": {
            "reasoning_options": [{"type": "effort", "values": ["low", "none"]}],
            "limit": {"context": 8, "output": 2},
            "modalities": {"input": ["text"], "output": ["text"]},
            "cost": {"input": 0, "output": 0},
        },
        "plain/model": {"limit": {"context": 4, "output": 1}},
        "toggle/model": {"reasoning_options": [{"type": "toggle"}]},
    },
    "ollama": "not-a-dict",
}

SERVE = {
    "openrouter": {
        "thinkingmachines/inkling:free": {"variants": {"low": {}, "none": {}}},
        "plain/model": {},
        "toggle/model": {},
        "custom/extra": {"variants": {"high": {}}},
    },
    "gmi": {
        "MiniMaxAI/MiniMax-M3": {"variants": {"none": {}, "thinking": {}}},
    },
}


def test_effort_variants_only_from_effort_type():
    assert effort_variants(
        {"reasoning_options": [{"type": "effort", "values": ["low", "none"]}]}
    ) == ["low", "none"]
    assert effort_variants({"reasoning_options": [{"type": "toggle"}]}) == []
    assert effort_variants({"reasoning_options": [{"type": "budget_tokens"}]}) == []
    assert effort_variants({}) == []
    assert effort_variants("not-a-dict") == []


def test_build_upstream_registry():
    registry = build_upstream_registry(UPSTREAM)
    assert list(registry) == ["openrouter"]
    assert registry["openrouter"] == [
        "plain/model",
        "thinkingmachines/inkling:free",
        "thinkingmachines/inkling:free+low",
        "thinkingmachines/inkling:free+none",
        "toggle/model",
    ]


def test_apply_serve_overlay_appends_only():
    upstream = build_upstream_registry(UPSTREAM)
    merged, overlay = apply_serve_overlay(upstream, SERVE)
    # Custom provider copied whole.
    assert merged["gmi"] == ["MiniMaxAI/MiniMax-M3", "MiniMaxAI/MiniMax-M3+none", "MiniMaxAI/MiniMax-M3+thinking"]
    # Serve-only model under a known provider copied.
    assert "custom/extra" in merged["openrouter"]
    assert "custom/extra+high" in merged["openrouter"]
    # Upstream rows never removed.
    for row in upstream["openrouter"]:
        assert row in merged["openrouter"]
    # Overlay report lists only what it added.
    assert overlay["gmi"] == ["MiniMaxAI/MiniMax-M3", "MiniMaxAI/MiniMax-M3+none", "MiniMaxAI/MiniMax-M3+thinking"]
    assert "openrouter" in overlay
    # Deterministic + sorted.
    assert merged["openrouter"] == sorted(merged["openrouter"], key=lambda r: (r.split("+")[0], r))
    # Overlay providers merge into alphabetical provider order
    # (not appended at the end — that showed a relocation diff).
    assert list(merged) == sorted(merged)


def test_build_metadata_unions_variants():
    meta = build_metadata(UPSTREAM, SERVE)
    inkling = meta["openrouter/thinkingmachines/inkling:free"]
    assert inkling["variants"] == ["low", "none"]
    assert inkling["limit"] == {"context": 8, "output": 2}
    assert inkling["modalities"] == {"input": ["text"], "output": ["text"]}
    assert inkling["reasoning_options"] == [{"type": "effort", "values": ["low", "none"]}]
    custom = meta["openrouter/custom/extra"]
    assert custom["variants"] == ["high"]
    assert "reasoning_options" not in custom


def test_merge_custom_rows_additive():
    registry = {"openrouter": ["a", "a+low"], "ollama": ["qwen3:8b"]}
    customs = {"providers": {"openrouter": ["a+high", "a"], "mine": ["m+max"]}}
    merged = merge_custom_rows(registry, customs)
    assert merged["openrouter"] == ["a", "a+high", "a+low"]
    assert merged["mine"] == ["m+max"]
    assert merged["ollama"] == ["qwen3:8b"]
    assert merge_custom_rows(registry, None) == registry
    assert merge_custom_rows(registry, {}) == registry


def test_strip_variant_rows():
    registry = {
        "openrouter": ["a", "a+high", "a+low", "b"],
        "ollama": ["qwen3:8b"],
    }
    assert strip_variant_rows(registry) == {
        "ollama": ["qwen3:8b"],
        "openrouter": ["a", "b"],
    }
    assert strip_variant_rows({}) == {}
