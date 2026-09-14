"""Usage ledger (local-first product analytics).

The projector behind ``GET /api/stats/summary`` (see
``docs/USAGE_LEDGER_PLAN.md``): a pure read-side fold over Delegation
records + trace ``tokens_used`` events. No new writes, no schema
migration, no prompt/response text — counts and shapes only.
"""

from sweave.stats.ledger import build_summary

__all__ = ["build_summary"]
