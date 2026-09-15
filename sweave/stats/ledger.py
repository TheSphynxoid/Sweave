"""Usage-ledger projector (``docs/USAGE_LEDGER_PLAN.md``).

A pure read-side fold over Delegation records + per-delegation trace
``tokens_used`` events into per-day / per-model / per-project /
per-agent / per-kind / per-status cells. Design rule (plan §"derive,
don't instrument"): everything needed already persists — records carry
agent/model/status/stamps/error, traces carry the token audit anchor.
The projector never writes, never raises on bad rows, and never
collects prompt/response TEXT (counts and shapes only — the export
safety boundary).

Attribution rules (plan risks: every number suspect until pinned):
* tokens come from trace ``tokens_used`` events SUMMED per delegation
  (the ``estimate_vs_actual`` precedent — differs from the detail
  view's last-wins display). No trace (or no events) degrades to
  zeros, never an error.
* ``context_input`` is the exception: it is the peak live context
  (MAX, not sum). Per-step prompts re-bill full history, so summing
  them yields steps×context — honest billing but not a size. The
  ``input`` cell keeps the billed sum; ``context_input`` carries the
  largest single-step prompt seen (pre-split traces without the field
  contribute 0 — never invented).
* Phase 1b cost cells: every cell also carries the shared
  ``sweave/stats/pricing.py`` fold — ``estimated_cost`` (summed per
  turn, never invented), ``cost_source`` (``provider`` / ``rates`` /
  ``none`` — worst of the turn sources: none > rates > provider),
  ``unpriced`` (True when no turn priced). Compute-on-read; degrade
  zeros/nulls — unpriced cells never render as $0. The pricing
  needs a model + sidecar rates: the optional ``meta_reader(model)``
  supplies the entry (absent reader = every turn unpriced, the
  counts-only shape).
* day bucket = ``created_at`` calendar date; dateless records count
  in totals only (never invented into a day).
* wall seconds sum completed turns only (``created_at`` →
  ``completed_at`` when both exist); running turns count in turns +
  tokens but never in wall time.
* error class = the ``[chat error: <code>`` prefix code when present
  (``max_steps``, ``turn_timeout_exceeded_900s``, ...), else
  ``"unspecified"`` for failed turns with unparseable text.
* retries are linked by parent, not merged: every record counts
  (the plan's stated rule).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable


def _num(value: Any) -> float:
    """Best-effort number coercion (bool excluded — True is not 1 here)."""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _int(value: Any) -> int:
    return int(_num(value))


def _day_of(record: Any) -> str | None:
    created = getattr(record, "created_at", None)
    if isinstance(created, datetime):
        return created.date().isoformat()
    if isinstance(created, str):
        try:
            return datetime.fromisoformat(created).date().isoformat()
        except ValueError:
            return None
    return None


def _wall_seconds(record: Any) -> float | None:
    start = getattr(record, "created_at", None)
    end = getattr(record, "completed_at", None)
    if isinstance(start, str):
        try:
            start = datetime.fromisoformat(start)
        except ValueError:
            start = None
    if isinstance(end, str):
        try:
            end = datetime.fromisoformat(end)
        except ValueError:
            end = None
    if isinstance(start, datetime) and isinstance(end, datetime):
        return max(0.0, (end - start).total_seconds())
    return None


def error_class(error: Any) -> str | None:
    """Classify a delegation error (None = not failed / no error).

    ``[chat error: max_steps: ...]`` -> ``"max_steps"``; unparseable
    failed-turn text -> ``"unspecified"``. Never raises.
    """
    if not error or not isinstance(error, str):
        return None
    text = error.strip()
    if not text:
        return None
    marker = "[chat error:"
    if marker in text:
        code = text.split(marker, 1)[1].strip().rstrip("]").strip()
        code = code.split(":", 1)[0].strip().split()[0] if code else ""
        return code or "unspecified"
    return "unspecified"


def _sum_tokens_used(events: Iterable[dict[str, Any]]) -> dict[str, float]:
    """Sum every trace ``tokens_used`` event (missing keys read 0).

    ``context_input`` is the one MAX-folded key: the peak live context
    across the delegation's turns (summing cumulative per-step prompts
    would report steps×context as a size).
    """
    total = {
        "input": 0.0, "output": 0.0, "reasoning": 0.0,
        "cache_read": 0.0, "cache_write": 0.0, "cost": 0.0,
        "context_input": 0.0,
    }
    for ev in events:
        if not isinstance(ev, dict) or ev.get("event") != "tokens_used":
            continue
        for key in total:
            if key == "context_input":
                total[key] = max(total[key], _num(ev.get(key)))
            else:
                total[key] += _num(ev.get(key))
    return total


def default_tokens_reader(
    delegation_id: str, trace_dir: Path | None = None
) -> list[dict[str, Any]]:
    """Light trace scan returning only ``tokens_used`` events.

    Lighter than ``read_trace`` (which materialises every event): one
    line scan keeping the audit anchors. Missing file -> []. Never
    raises — the ledger degrades to zeros, never errors.
    """
    base = trace_dir or Path.home() / ".sweave" / "traces"
    path = base / f"{delegation_id}.jsonl"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    try:
        import json as _json

        for raw in text.splitlines():
            raw = raw.strip()
            if not raw or '"tokens_used"' not in raw:
                continue
            try:
                ev = _json.loads(raw)
            except ValueError:
                continue
            if isinstance(ev, dict) and ev.get("event") == "tokens_used":
                out.append(ev)
    except Exception:  # noqa: BLE001 — degrade, never fail the page
        return out
    return out


_SOURCE_RANK = {"provider": 2, "rates": 1, "none": 0}


def _worse_source(have: str, got: str) -> str:
    """Worst of two cost sources (none > rates > provider)."""
    if _SOURCE_RANK.get(have, 0) <= _SOURCE_RANK.get(got, 0):
        return have
    return got


def _price_turn(
    events: list[dict[str, Any]],
    model: str,
    meta_of: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Price one delegation's turn via the shared helper (Phase 1b).

    Passes the sidecar entry under the variant-stripped key (same
    lookup the detail fold uses). Never raises: pricing degrades to
    unpriced, and the meta lookup itself is guarded at the call site.
    """
    try:
        from sweave.stats.pricing import price_for_events, strip_variant

        meta = meta_of(model)
        entry = meta.get(strip_variant(model)) if meta else None
        return price_for_events(events, model, entry)
    except Exception:  # noqa: BLE001
        return {"estimated_cost": None, "source": "none"}


def _cell() -> dict[str, Any]:
    return {
        "turns": 0, "input": 0.0, "output": 0.0, "reasoning": 0.0,
        "cache_read": 0.0, "cache_write": 0.0, "cost": 0.0,
        "context_input": 0.0, "failed": 0,
        "estimated_cost": 0.0, "cost_source": "provider",
        "unpriced_turns": 0,
    }


def _final_cell(cell: dict[str, Any]) -> dict[str, Any]:
    """Round a cell for the wire; derive the unpriced flag."""
    out: dict[str, Any] = {}
    for k, v in cell.items():
        if k in ("turns", "failed"):
            out[k] = int(v)
        elif k == "cost_source":
            out[k] = v
        elif k == "unpriced_turns":
            continue
        elif isinstance(v, (int, float)):
            out[k] = round(float(v), 3)
        else:
            out[k] = v
    out["unpriced"] = bool(cell.get("unpriced_turns", 0) > 0)
    return out


def build_summary(
    records: Iterable[Any],
    tokens_reader: Callable[[str], list[dict[str, Any]]] | None = None,
    *,
    days: int = 30,
    now: datetime | None = None,
    meta_reader: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fold records + traces into the stats summary payload.

    * ``records`` — Delegation records (or duck-typed equivalents).
    * ``tokens_reader(delegation_id)`` — trace ``tokens_used`` events;
      defaults to the light ``~/.sweave/traces`` scanner.
    * ``days`` — window; records with a ``created_at`` older than the
      window are excluded everywhere (dateless records count in
      totals only). Clamped to >= 1 by the router; <= 0 here means
      "no window" (tests).
    * ``meta_reader(model)`` — sidecar meta entry for the shared
      pricing fold (Phase 1b); None = counts-only (every cost cell
      unpriced). Never raises: a throwing reader degrades to {}.
    """
    read = tokens_reader or default_tokens_reader

    def _meta(model: str) -> dict[str, Any]:
        if meta_reader is None:
            return {}
        try:
            got = meta_reader(model)
        except Exception:  # noqa: BLE001 -- pricing inputs never fail rollup
            return {}
        return got if isinstance(got, dict) else {}
    moment = now or datetime.now()
    rows = list(records)

    totals = _cell()
    totals["wall_seconds"] = 0.0
    totals["completed_turns"] = 0
    by_day: dict[str, dict[str, float]] = {}
    by_model: dict[str, dict[str, float]] = {}
    by_project: dict[str, dict[str, float]] = {}
    by_agent: dict[str, dict[str, float]] = {}
    by_kind: dict[str, dict[str, float]] = {}
    by_status: dict[str, int] = {}
    by_error: dict[str, int] = {}

    def _add(bucket: dict[str, dict[str, float]], key: str) -> dict[str, float]:
        return bucket.setdefault(key, _cell())

    for rec in rows:
        try:
            day = _day_of(rec)
        except Exception:  # noqa: BLE001
            day = None
        if day is not None and days > 0:
            try:
                age = (moment.date() - datetime.fromisoformat(day).date()).days
            except ValueError:
                age = 0
            if age < 0 or age >= days:
                continue
        status = str(getattr(rec, "status", "") or "unknown")
        agent = str(getattr(rec, "agent", "") or "unknown")
        model = str(getattr(rec, "model", "") or "unspecified")
        project = str(getattr(rec, "project_name", "") or "unscoped")
        kind = str(getattr(rec, "kind", "") or "task")
        try:
            dep_id = str(getattr(rec, "delegation_id", "") or "")
            events = read(dep_id) if dep_id else []
        except Exception:  # noqa: BLE001
            events = []
        toks = _sum_tokens_used(events)
        price = _price_turn(events, model, _meta)
        wall = _wall_seconds(rec)
        failed = 1 if status == "failed" else 0

        targets = [totals]
        if day is not None:
            targets.append(_add(by_day, day))
        targets.append(_add(by_model, model))
        targets.append(_add(by_project, project))
        targets.append(_add(by_agent, agent))
        targets.append(_add(by_kind, kind))
        for cell in targets:
            cell["turns"] += 1
            cell["failed"] += failed
            for key in ("input", "output", "reasoning", "cache_read", "cache_write", "cost"):
                cell[key] += toks[key]
            # Peak live context across the bucket's turns (max, never
            # summed — the billed ``input`` sum is steps×context).
            cell["context_input"] = max(cell["context_input"], toks["context_input"])
            # Phase 1b: shared-helper cost fold (compute-on-read).
            if price["estimated_cost"] is not None:
                cell["estimated_cost"] += price["estimated_cost"]
            else:
                cell["unpriced_turns"] += 1
            cell["cost_source"] = _worse_source(cell["cost_source"], price["source"])
        by_status[status] = by_status.get(status, 0) + 1
        if failed:
            try:
                cls = error_class(getattr(rec, "error", None)) or "unspecified"
            except Exception:  # noqa: BLE001
                cls = "unspecified"
            by_error[cls] = by_error.get(cls, 0) + 1
        if wall is not None:
            totals["wall_seconds"] += wall
            totals["completed_turns"] += 1

    def _rows(bucket: dict[str, dict[str, Any]], key: str) -> list[dict[str, Any]]:
        return [
            {key: name, **_final_cell(cell)}
            for name, cell in sorted(bucket.items())
        ]

    totals_out = _final_cell(totals)
    totals_out["wall_seconds"] = round(float(totals["wall_seconds"]), 3)
    totals_out["completed_turns"] = int(totals["completed_turns"])
    return {
        "window_days": days,
        "generated_at": moment.isoformat(),
        "totals": totals_out,
        "by_day": _rows(by_day, "day"),
        "by_model": _rows(by_model, "model"),
        "by_project": _rows(by_project, "project"),
        "by_agent": _rows(by_agent, "agent"),
        "by_kind": _rows(by_kind, "kind"),
        "by_status": dict(sorted(by_status.items())),
        "by_error": [
            {"error": name, "count": count}
            for name, count in sorted(by_error.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    }
