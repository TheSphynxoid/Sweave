# Sequential orchestration waves — locked decisions + plan stub

Status: decided 2026-09-17, not yet planned. The planning session
formalizes steps/gates from this record; the executor does not
redesign the budget model below.

Thread: single-pass turns are structural today (straight-line body:
round 0 → join → synthesis → finish), not ruled — a synthesis-round
`defer` is accepted but never joined (orphans to Children by the
not-auto-joined rule). The fix-round workaround (review ping-pong
via fix modes/endpoints) is a wave by another name. This record
makes waves real instead.

## Rulings (user-locked 2026-09-17 — read first)

1. **Budget shape 1 + 2 + sponsored-fix per turn.** Round 0 free
   (initial dispatch, parallel or not). The orchestrator owns a
   margin of **2 extra waves** for follow-up dispatches —
   sequential passes with real contracts (backend settles →
   results ride the synthesis → frontend dispatched on them),
   parallel preserved as a per-round choice, never forced.
2. **Fix rounds are off-budget, never self-certified (proof, not
   claim).** A follow-up wave is a *fix* round iff its dispatches
   target review-flagged work sponsored by a blocking verdict
   (`request_changes`, reviewer or human) on record; the loop
   verifies sponsorship from records, never from the
   orchestrator's claim. No verdict → work wave → spends margin
   (or rejected with a clear line when margin is gone).
3. **Ping-pong fuse.** Sponsored-but-endless still spins in
   principle (finding → fix → finding …), so fix rounds get a
   generous circuit breaker per turn — a fuse, not a budget.
   Blow it and the turn escalates to human, never loops silently.
4. **Attribution at spawn.** Children render under the round that
   deferred them. The spawn round is stamped on the child record
   at defer time (resolved from the loop's live round registry,
   never the model) — a Delegation schema addition (version bump
   + migration per the store gate). The round-0 lanes gate is
   today's approximation only and dies with this feature.

## Goal state (sketch for the planner)

- `_run_turn_body` loops wait → synthesize → continue while the
  synthesis deferred new blocking work and budget remains; each
  cycle is `round += 1` with its own persisted round message.
- Charter defer contract rewritten to wave discipline (defer
  follow-ups, end turn, loop re-invokes); the end-your-turn
  wording that forbids waves goes away.
- Stop stays subtree-wide per delegation id (spans rounds, no new
  cancel machinery); turn_timeout fuse + depth/budget caps ride
  unchanged.
- Trace gains wave-boundary events (join set per wave).

## Explicit non-goals (locked)

- No unbounded waves (cap + fuse are the design, not tuning).
- No self-certified fix rounds (sponsorship check is load-bearing,
  not advisory).
- No lanes change beyond spawn-round attribution (the duplicate
  dies as a consequence).

## Open points for the planning session

- Field fossil (2026-09-18, session
  `Sweave-20260918-002816-34a49c`): reviewer `4e6868a49400`
  created 01:01:10 during the synthesis round, turn closed
  01:01:27 without joining it — the accepted-but-never-joined
  middle in the wild. The loop must join synthesis-round defers
  or reject them; silent orphaning ends here.
- Exact wave-cap number (2 locked as margin; fuse threshold open).
- Fix-round fuse mechanics (per-turn count vs per-chain; escalate
  vs fail-loud on trip).
- Sponsorship lookup details (verdict → target scope matching).
- Prompt deltas beyond the defer contract.
- Test matrix shape (wave loop, cap, sponsorship positive +
  negative, attribution, stop mid-wave-N).
