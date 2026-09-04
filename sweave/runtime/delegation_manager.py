"""DelegationManager (M1.6 step 2).

The runtime's gate for the deferral chain. Every child delegation
submitted via the MCP ``defer`` tool (or the v2 task API with a
``parent_task_id``) flows through :meth:`DelegationManager.validate`
before reaching the JobRunner.

Three rules, in this order (first failure short-circuits):
  1. **Depth cap** (``routing.max_depth``, default 2). Orchestrator
     delegations have depth 0; a defer child has depth 1; a grandchild
     is depth 2. ``parent.depth + 1 > max_depth`` -> reject.
  2. **Loop detect** (per-chain). Walking the parent's chain_root_id
     links, the new target must not appear. Cached per chain_root_id
     in-memory (per-process); cleared on terminal state.
  3. **Budget** (``routing.chain_budget``, default 200K coordination
     tokens). The new delegation's ``coordination_tokens`` (tiktoken
     estimate of the defer payload + parent chain turn summaries)
     plus the chain accumulator must stay under the cap.

The budget counts **coordination traffic only** by design (see plan
§Rulings): the opencode v2 stream exposes no per-turn token count, so
specialist-internal usage is opaque. The cap targets runaway
*coordination*, not work quality.

Usage::

    from sweave.runtime.delegation_manager import DelegationManager

    mgr = DelegationManager(
        max_depth=cm.routing.max_depth,
        chain_budget=cm.routing.chain_budget,
    )
    try:
        new_delegation = mgr.validate(parent, target, task, reason)
    except ChainError as e:
        # surface as a 4xx to the MCP caller
        ...
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Iterable

import tiktoken

from sweave.config.schemas import SweaveConfig
from sweave.runtime.delegation_store import Delegation

logger = logging.getLogger(__name__)


class ChainError(Exception):
    """Base class for chain-rule violations. Subclasses set the
    specific ``code`` so the MCP tool can pick the right wording
    ('rejected: <reason>')."""


class DepthExceededError(ChainError):
    code = "depth_exceeded"


class LoopDetectedError(ChainError):
    code = "loop_detected"


class BudgetExceededError(ChainError):
    code = "budget_exceeded"


# Lazily-instantiated encoder (cl100k_base is a stable choice that
# works for most modern models; we don't claim it matches a specific
# model — it's an estimate).
_encoder: tiktoken.Encoding | None = None
_encoder_lock = threading.Lock()


def _get_encoder() -> tiktoken.Encoding:
    global _encoder
    if _encoder is None:
        with _encoder_lock:
            if _encoder is None:
                _encoder = tiktoken.get_encoding("cl100k_base")
    return _encoder


def estimate_tokens(*texts: str) -> int:
    """Return the tiktoken estimate of total tokens for the given
    texts. Empty inputs return 0.

    Used for coordination budget accounting; the estimate is
    intentionally approximate (cl100k_base; cheap; no model
    fingerprint required).
    """
    if not texts:
        return 0
    enc = _get_encoder()
    return sum(len(enc.encode(t or "")) for t in texts)


class DelegationManager:
    """Per-process chain validator for the deferral tree.

    Holds two caches (per chain_root_id):
    * **active set**: the set of agent names currently in the chain
      (loop detect). Cleared on terminal state of the root.
    * **token accumulator**: total coordination tokens spent so far on
      this chain. Updated on every defer.

    Both are in-memory. Process restart clears them; the per-project
    delegation store is the persistent record, and the manager
    rebuilds the cache from the store on demand via
    :meth:`rebuild_chain_state`.

    Concurrency: the per-project ``asyncio.Lock`` from the per-project
    delegation store (M1.1) covers the store writes; the manager's
    own updates are guarded by an internal lock. Two concurrent
    defers in the same process serialise on this lock.
    """

    def __init__(self, max_depth: int = 2, chain_budget: int = 200_000) -> None:
        self.max_depth = max_depth
        self.chain_budget = chain_budget
        self._active_sets: dict[str, set[str]] = {}
        self._chain_tokens: dict[str, int] = {}
        self._lock = threading.Lock()

    # -- public API ----------------------------------------------------

    def validate(
        self,
        parent: Delegation | None,
        target: str,
        task: str,
        reason: str = "",
    ) -> Delegation:
        """Validate a defer and return a new (unpersisted) Delegation.

        ``parent`` is the orchestrator's own delegation when
        ``target`` is the first defer in a chain; ``None`` means
        "no parent" (a top-level user task, not a defer).

        Raises:
          * ``DepthExceededError`` if parent.depth + 1 > max_depth
          * ``LoopDetectedError`` if target is in the active chain
          * ``BudgetExceededError`` if the new chain tokens would
            exceed the cap
        """
        with self._lock:
            depth = (parent.depth + 1) if parent is not None else 0
            if depth > self.max_depth:
                raise DepthExceededError(
                    f"depth {depth} exceeds max_depth={self.max_depth} "
                    f"(parent depth={parent.depth if parent else 'None'})"
                )

            chain_root_id = (
                parent.chain_root_id if (parent and parent.chain_root_id) else (
                    parent.delegation_id if parent else None
                )
            )
            # Top-level delegations (no parent) carry chain_root_id=None
            # and don't participate in the chain caches; they're the
            # user-initiated root and not subject to chain rules.
            # coordination_tokens=0 by design: the cap bounds the
            # *coordination* traffic between defer hops, not the
            # orchestrator's first turn (which is the user's task
            # and isn't a defer). The first defer under a top-level
            # delegation establishes the chain budget.
            if chain_root_id is None:
                return self._build_new(
                    parent=parent,
                    target=target,
                    task=task,
                    reason=reason,
                    depth=0,
                    chain_root_id=None,
                    coordination_tokens=0,
                )

            # Loop detect
            active = self._active_sets.setdefault(chain_root_id, set())
            if target in active:
                raise LoopDetectedError(
                    f"loop detected: '{target}' is already in the active chain "
                    f"(chain_root_id={chain_root_id}, active={sorted(active)})"
                )

            # Budget
            new_tokens = estimate_tokens(task, reason)
            current = self._chain_tokens.get(chain_root_id, 0)
            if current + new_tokens > self.chain_budget:
                raise BudgetExceededError(
                    f"chain budget exceeded: current={current} + new={new_tokens} "
                    f"> chain_budget={self.chain_budget} (chain_root_id={chain_root_id})"
                )

            # Accept: update caches + build the new record
            active.add(target)
            self._chain_tokens[chain_root_id] = current + new_tokens
            return self._build_new(
                parent=parent,
                target=target,
                task=task,
                reason=reason,
                depth=depth,
                chain_root_id=chain_root_id,
                coordination_tokens=new_tokens,
            )

    def record_terminal(self, delegation: Delegation) -> None:
        """Called when a delegation reaches a terminal state. Frees
        the per-chain cache when the **root** is terminal (no more
        defers possible on this chain); for non-roots, just removes
        the agent from the active set (its subtree may still be
        active).
        """
        if delegation.status not in {"done", "failed"}:
            return
        with self._lock:
            # chain_root_id is None for top-level roots; in that
            # case the delegation IS the root. Otherwise, the root
            # is identified by the chain_root_id field; the
            # delegation is the root iff its id == chain_root_id.
            chain_root_id = delegation.chain_root_id or delegation.delegation_id
            is_root = (
                chain_root_id == delegation.delegation_id
                and delegation.parent_task_id is None
            )
            if is_root:
                # Root reached terminal: chain is over.
                self._active_sets.pop(chain_root_id, None)
                self._chain_tokens.pop(chain_root_id, None)
            else:
                # Non-root: remove THIS agent from the active set
                # (its subtree may still be active).
                active = self._active_sets.get(chain_root_id)
                if active is not None:
                    active.discard(delegation.agent)

    def rebuild_chain_state(
        self, chain_root_id: str, records: Iterable[Delegation]
    ) -> None:
        """Rebuild the active-set + token accumulator from the
        per-project store. Used after a process restart (the
        in-memory caches start empty) so loop/budget checks resume
        correctly.

        Only **child records** (those with a non-null
        ``parent_task_id``) contribute to the active set and the
        token accumulator. The chain root itself doesn't enter the
        cache -- it owns the cache, the orchestrator is the caller
        (not a defer target), and the root's coordination_tokens
        are not the cost the cap is bounding (the cap targets
        coordination traffic *between* deferral hops, not the
        orchestrator's own first turn).
        """
        with self._lock:
            active: set[str] = set()
            tokens = 0
            for r in records:
                if r.status not in {"queued", "running", "review"}:
                    continue
                # Only defer targets count toward the active set
                # and the budget. The root's record is included in
                # the iteration only to anchor the chain -- the
                # other fields (parent_task_id == None) identify
                # the root and skip below.
                if not r.parent_task_id:
                    continue
                if r.agent:
                    active.add(r.agent)
                tokens += r.coordination_tokens
            self._active_sets[chain_root_id] = active
            self._chain_tokens[chain_root_id] = tokens

    def snapshot(self) -> dict[str, Any]:
        """Read-only view of the in-memory caches (for tests + the
        Children tab's optional "chain status" indicator).

        Returns a deep copy: callers cannot mutate the live cache.
        Active sets are exposed as frozensets so set-equality
        comparisons work in tests (``snap["active_sets"]["R"] ==
        {"backend", "frontend"}``); the orchestrator's UI consumer
        can convert to a list on its side.
        """
        with self._lock:
            return {
                "active_sets": {k: frozenset(v) for k, v in self._active_sets.items()},
                "chain_tokens": dict(self._chain_tokens),
            }

    # -- helpers --------------------------------------------------------

    def _build_new(
        self,
        *,
        parent: Delegation | None,
        target: str,
        task: str,
        reason: str,
        depth: int,
        chain_root_id: str | None,
        coordination_tokens: int,
    ) -> Delegation:
        """Construct the new (unpersisted) Delegation record. The
        caller (JobRunner integration in step 3) is responsible for
        persisting via the per-project store.
        """
        manifest: dict[str, Any] | None = None
        if reason:
            manifest = {"intent": reason, "source": "orchestrator_defer"}
        return Delegation(
            agent=target,
            task=task,
            model=parent.model if parent is not None else "",
            parent_task_id=parent.delegation_id if parent is not None else None,
            depth=depth,
            chain_root_id=chain_root_id,
            coordination_tokens=coordination_tokens,
            manifest=manifest,
        )

    @classmethod
    def from_config(cls, config: SweaveConfig) -> "DelegationManager":
        """Build a manager from the loaded config. The plan's defaults
        (depth=2, budget=200K) come from
        ``SweaveConfig.routing.max_depth`` / ``chain_budget``.
        """
        routing = config.routing
        return cls(max_depth=routing.max_depth, chain_budget=routing.chain_budget)
