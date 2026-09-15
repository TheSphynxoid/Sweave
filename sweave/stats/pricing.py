"""Shared per-turn pricing helper (USAGE_LEDGER Phase 0).

One math shared by ``sweave/web/detail_view.py`` (per-delegation
``price`` fold) and ``sweave/stats/ledger.py`` (per-cell rollup):
summed trace ``tokens_used`` x ``models.meta.json`` sidecar rates.

Rules (plan-locked 2026-09-14):
* Input: summed ``tokens_used`` events + a qualified model id.
* Variant strip mirrors ``parse_model_ref`` semantics
  (``sweave/runtime/specialist_store.py:129`` + ``:166``): a
  ``+tail`` suffix is a variant only when non-empty and ``/``-free;
  a ``+`` tail containing ``/`` stays model id.
* Rates: sidecar ``cost`` shape ``{input, output}`` per 1M tokens
  (``sweave/models_sync.py:379-381``); formula
  ``input*in_rate/1M + output*out_rate/1M``. Reasoning is a subset
  of output — never additive. ``cache_*`` ignored v1 (approximate).
  Tiered ``context_over_200k`` ignored v1 (approximate).
* Missing or 0/0-absent rates -> nulls: ``estimated_cost`` None,
  ``free`` None (unknown), ``estimated`` True — display "unpriced",
  never zeros.
* Provider cost wins on key-presence, not value: a present numeric
  ``cost`` key (including 0-with-tokens) is the actual
  (``source: "provider"``, ``estimated: False``); an absent key
  falls back to the rates estimate. Absent-vs-zero stays distinct.
  Mixed-turn source: ANY provider-reported event makes the turn
  provider-sourced (defined here, not at call sites).
* ``free: True`` only when explicitly free (0/0 rates) or provider
  cost 0 with tokens. Never inferred from missing data.
* Output: ``{model, rates_per_1m, estimated_cost, free, estimated,
  source}``. No retention/ZDR field (dropped per ruling).

Pure: no I/O, never raises on bad rows (degrades to unpriced).
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

PER_MILLION = 1_000_000


def _num(value: Any) -> float:
    """Best-effort number coercion (bool excluded — True is not 1 here)."""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def strip_variant(qualified: Any) -> str:
    """Strip a ``+variant`` suffix per ``parse_model_ref`` semantics.

    Split provider on the FIRST ``/``, then the variant on the LAST
    ``+`` — the suffix is a variant only when non-empty and free of
    ``/``. Otherwise the whole string is the id (a ``+`` inside a
    model id survives). Non-strings degrade to ``""``.
    """
    if not isinstance(qualified, str) or not qualified:
        return ""
    if "/" not in qualified:
        return qualified
    provider, _, rest = qualified.partition("/")
    head, sep, tail = rest.rpartition("+")
    if sep and tail and "/" not in tail:
        rest = head
    return f"{provider}/{rest}"


def rates_from_meta(meta_entry: Any) -> tuple[float | None, float | None]:
    """Per-1M ``(input, output)`` rates from a sidecar meta entry.

    ``None`` per missing/non-numeric leg (unpriceable on that leg).
    Non-dict entries degrade to ``(None, None)``.
    """
    if not isinstance(meta_entry, Mapping):
        return (None, None)
    cost = meta_entry.get("cost")
    if not isinstance(cost, Mapping):
        return (None, None)
    in_rate = cost.get("input")
    out_rate = cost.get("output")
    return (
        float(in_rate) if _is_num(in_rate) else None,
        float(out_rate) if _is_num(out_rate) else None,
    )


def price_for_events(
    events: Iterable[Mapping[str, Any]] | None,
    model: Any,
    meta_entry: Any = None,
) -> dict[str, Any]:
    """Price one turn from its ``tokens_used`` events + model id.

    Sums ``input``/``output`` across every ``tokens_used`` event
    (reasoning folded into output already — never added again).
    Provider ``cost`` wins on key-presence (any event carrying a
    numeric ``cost`` key, including 0-with-tokens). Otherwise the
    sidecar-rates estimate applies when both priced legs resolve
    (a leg with zero tokens needs no rate). Anything else is
    unpriced (nulls, never zeros). Never raises.
    """
    try:
        return _price_for_events(events, model, meta_entry)
    except Exception:  # noqa: BLE001 — pricing never fails a read path
        return {
            "model": strip_variant(model),
            "rates_per_1m": None,
            "estimated_cost": None,
            "free": None,
            "estimated": True,
            "source": "none",
        }


def _price_for_events(
    events: Iterable[Mapping[str, Any]] | None,
    model: Any,
    meta_entry: Any = None,
) -> dict[str, Any]:
    bare = strip_variant(model)
    input_t = 0.0
    output_t = 0.0
    has_provider_cost = False
    provider_cost = 0.0
    for ev in events or []:
        if not isinstance(ev, Mapping) or ev.get("event") != "tokens_used":
            continue
        input_t += _num(ev.get("input"))
        output_t += _num(ev.get("output"))
        if "cost" in ev and _is_num(ev.get("cost")):
            has_provider_cost = True
            provider_cost += float(ev["cost"])

    in_rate, out_rate = rates_from_meta(meta_entry)
    rates = (
        {"input": in_rate, "output": out_rate}
        if (in_rate is not None or out_rate is not None)
        else None
    )

    if has_provider_cost:
        return {
            "model": bare,
            "rates_per_1m": rates,
            "estimated_cost": round(provider_cost, 6),
            "free": bool(
                provider_cost == 0 and (input_t + output_t) > 0
            ),
            "estimated": False,
            "source": "provider",
        }

    needs_in = input_t > 0 and in_rate is None
    needs_out = output_t > 0 and out_rate is None
    if needs_in or needs_out:
        return {
            "model": bare,
            "rates_per_1m": rates,
            "estimated_cost": None,
            "free": None,
            "estimated": True,
            "source": "none",
        }
    estimated = (input_t * (in_rate or 0.0) + output_t * (out_rate or 0.0)) / PER_MILLION
    free = (
        in_rate is not None
        and out_rate is not None
        and in_rate == 0
        and out_rate == 0
    )
    return {
        "model": bare,
        "rates_per_1m": rates,
        "estimated_cost": round(estimated, 6),
        "free": bool(free),
        "estimated": bool(not free),
        "source": "rates",
    }
