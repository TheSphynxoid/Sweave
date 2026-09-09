# M1.10 Plan — Specialist transparency follow-ups: drawer + LLM fork + fork_policy

Status: **planned** (2026-09-09). Rulings user-locked 2026-09-09
(DESIGN.md §2.2 "LLM-called fork" + §5 item 6; PROJECT_STATE.md).
This is the remainder of the 2026-09-09 chat-transparency discussion
after the inline-cards slice landed (`00e014c`).

## Starting point (verified against code, not older bullets)

- `TurnDelegations` inline cards landed: children per chat turn
  (`GET /api/delegations?parent_task_id=`), WS-pulsed, expandable
  output summary, shared M1.9 `DetailView` modal. Null when childless.
- MCP tools on the params-model wire: `defer` / `list_specialists` /
  `ask_human` (`sweave/mcp/__init__.py`). No fork tool yet.
- Escalation store + `needs_attention` lane exist (M1.9) — the
  `confirm` path reuses them, no new wire.
- Specialists resolve project → global → seed
  (`SpecialistResolver`); `POST /api/specialists` is project/global
  scoped with name-shape + orchestrator guards.
- Suite isolation: autouse `_isolate_project_manager_singleton`
  (`tests/conftest.py`); full run is 486 pytest / 111 vitest green
  with zero real-home growth.

## Goal state

1. **Read-only specialist drawer** — per-specialist delegation
   history (task → output, newest last, each expandable to full
   trace via the existing detail endpoint), header (name, model,
   scope). "Ask to follow up" prefills the chat composer; no direct
   send (one input funnel).
2. **`fork_specialist` MCP tool** — `fork_specialist(base, name?,
   focus, reason)` → project-scoped create (reuses `POST
   /api/specialists` semantics: name validation, orchestrator-name
   409, dedup-to-reuse), records `forked_from` + reason on the trace,
   emits existing `specialist.created`. Prompt rule: reuse-first
   (`list_specialists` → reuse if fit → fork only with reason).
3. **`fork_policy: "auto" | "confirm" | "disabled"`** — resolution
   session → project → global → `"auto"`. `auto` creates
   immediately; `confirm` files an M1.9 escalation (timeout →
   orchestrator reuses best existing); `disabled` returns
   `rejected: forking disabled by policy` (`isError=True`).
   Creation-only gate: existing specialists, `defer`, and manual UI
   creation are unaffected.

## Steps

1. **Backend: fork tool + policy** (~1 session). New MCP tool +
   resolver (`sweave/runtime/fork_policy.py`, `worktree_base.py`
   pattern) + nullable `fork_policy` on Project/Session records +
   orchestrator prompt contract update. Gate: new pytest
   (resolve-order matrix, confirm→escalation, disabled→rejected,
   dedup, 409s) + full suite + `run.py --check`.
2. **UI: specialist drawer** (~1 session). Drawer component reusing
   `DetailView` + delegation list query; entry points from
   `TurnDelegations` cards and the Agents workbench; follow-up
   prefill into the composer. Gate: new vitest (render/history/
   prefill/no-send-path) + full vitest + `npm run build`.
3. **Docs + live gate** (~half session). DESIGN §4 row(s),
   PROJECT_STATE progress, GOTCHAS additions; live mini-scene
   (fork → defer → inline cards → drawer) on mock opencode.

## Explicit non-goals

- **Singleton removal (non-urgent hygiene).** The import-time
  `project_manager` singleton (`sweave/projects.py:617`,
  early-bound by `sweave/api/projects.py` + `sweave/web/server.py`)
  stays; AppState-owned lifetime is the correct end state but is
  explicitly out of scope here. Containment (autouse suite fixture,
  `6eeccd6`) holds; revisit only if a second incident forces it.
- Specialist token streaming into chat (status + timeline suffice).
- Threads/sub-sessions under one specialist name (rejected —
  forks are separate identities, one session each).
- Writable specialist chat (rejected — second input funnel).
- Fork deletion/GC policy (user deletes via Agents tab; revisit on
  sprawl evidence, not speculation).
- Per-service auto-fork heuristics (the orchestrator decides per
  turn with the reuse-first rule; no prefetching).

## Risks

- Fork sprawl from a confused orchestrator → mitigate with a
  per-project cap + user-deletable forks (cap value is executor's
  call, default conservative).
- Prompt drift on forks-of-forks → `forked_from` chain on the
  trace makes it auditable; no auto-merge of prompts.
- Name collisions under concurrency → dedup-to-reuse on exact
  name match (documented, tested).
