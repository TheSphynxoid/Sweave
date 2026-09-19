"""USAGE_LEDGER Phase 0: shared pricing helper (``sweave/stats/pricing.py``).

Pins the plan-locked math: variant strip per ``parse_model_ref``
semantics, sidecar-rates estimate (``{input, output}`` per 1M;
reasoning never additive; cache/tiered ignored v1 approximate),
missing/0-0 rates -> nulls (unpriced, never zeros), provider cost
wins on key-presence incl. 0-with-tokens (absent-vs-zero distinct;
mixed-turn ANY provider-reported), ``free: True`` only explicit-free
or provider-0-with-tokens. Output shape
``{model, rates_per_1m, estimated_cost, free, estimated, source}``.
"""

from __future__ import annotations


def _ev(**kw):
    ev = {"event": "tokens_used", "input": 0, "output": 0}
    ev.update(kw)
    return ev


def test_strip_variant_mirrors_parse_model_ref():
    from sweave.stats.pricing import strip_variant

    assert strip_variant("opencode/m+low") == "opencode/m"
    # A "+" tail containing "/" stays model id (not a variant).
    assert strip_variant("openrouter/x/y:free+low") == "openrouter/x/y:free"
    assert strip_variant("openrouter/x/y+z/w") == "openrouter/x/y+z/w"
    assert strip_variant("opencode/m") == "opencode/m"
    assert strip_variant(None) == ""
    assert strip_variant("") == ""


def test_rates_estimate_input_plus_output():
    from sweave.stats.pricing import price_for_events

    out = price_for_events(
        [_ev(input=1_000_000, output=500_000)],
        "x/m",
        {"cost": {"input": 1.0, "output": 4.0}},
    )
    assert out["estimated_cost"] == 3.0
    assert out["estimated"] is True
    assert out["source"] == "rates"
    assert out["free"] is False
    assert out["rates_per_1m"] == {"input": 1.0, "output": 4.0}


def test_reasoning_never_additive():
    from sweave.stats.pricing import price_for_events

    out = price_for_events(
        [_ev(input=0, output=100, reasoning=100)],
        "x/m",
        {"cost": {"input": 0.0, "output": 2.0}},
    )
    # 100 output tokens at $2/1M — reasoning counted once via output.
    assert out["estimated_cost"] == 0.0002


def test_free_explicit_zero_zero_rates():
    from sweave.stats.pricing import price_for_events

    out = price_for_events(
        [_ev(input=10, output=5)], "free/m", {"cost": {"input": 0, "output": 0}}
    )
    assert out["free"] is True
    assert out["estimated"] is False
    assert out["estimated_cost"] == 0.0
    assert out["source"] == "rates"


def test_missing_rates_is_unpriced_not_zero():
    from sweave.stats.pricing import price_for_events

    out = price_for_events([_ev(input=10, output=5)], "x/m", {})
    assert out["estimated_cost"] is None
    assert out["free"] is None
    assert out["estimated"] is True
    assert out["source"] == "none"
    assert out["rates_per_1m"] is None


def test_provider_cost_wins_including_zero_with_tokens():
    from sweave.stats.pricing import price_for_events

    meta = {"cost": {"input": 99.0, "output": 99.0}}
    out = price_for_events([_ev(input=10, output=5, cost=0)], "x/m", meta)
    assert out["source"] == "provider"
    assert out["estimated"] is False
    assert out["estimated_cost"] == 0.0
    assert out["free"] is True  # explicit provider 0 with tokens

    out = price_for_events([_ev(input=10, output=5, cost=0.5)], "x/m", meta)
    assert out["source"] == "provider"
    assert out["estimated_cost"] == 0.5


def test_mixed_turn_any_provider_reported():
    from sweave.stats.pricing import price_for_events

    meta = {"cost": {"input": 99.0, "output": 99.0}}
    out = price_for_events(
        [_ev(input=10, output=5), _ev(input=10, output=5, cost=0.25)],
        "x/m",
        meta,
    )
    assert out["source"] == "provider"
    assert out["estimated_cost"] == 0.25


def test_absent_vs_zero_distinct():
    from sweave.stats.pricing import price_for_events

    meta = {"cost": {"input": 1.0, "output": 1.0}}
    absent = price_for_events([_ev(input=10, output=0)], "x/m", meta)
    assert absent["source"] == "rates"  # no cost key anywhere -> estimate
    zero = price_for_events([_ev(input=10, output=0, cost=0)], "x/m", meta)
    assert zero["source"] == "provider"  # present 0 -> actual


def test_null_cost_is_unknown_never_certified_free():
    """Hygiene B6: the native engine emits cost:null (no provider
    costing) — null must flow to the rates estimate, never read as
    provider-certified Free the way numeric 0 does."""
    from sweave.stats.pricing import price_for_events

    meta = {"cost": {"input": 1.0, "output": 4.0}}
    out = price_for_events([_ev(input=10, output=5, cost=None)], "x/m", meta)
    assert out["source"] == "rates"
    assert out["estimated"] is True
    assert out["free"] is False
    # Without rates either: unpriced, never zeros.
    out = price_for_events([_ev(input=10, output=5, cost=None)], "x/m", {})
    assert out["source"] == "none"
    assert out["estimated_cost"] is None
    assert out["free"] is None


def test_output_shape_keys():
    from sweave.stats.pricing import price_for_events

    out = price_for_events([], "x/m+low", {"cost": {"input": 1.0, "output": 2.0}})
    assert set(out) == {
        "model", "rates_per_1m", "estimated_cost", "free", "estimated", "source",
    }
    assert out["model"] == "x/m"  # variant stripped for lookup/display
