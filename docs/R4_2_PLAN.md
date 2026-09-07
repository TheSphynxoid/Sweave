# R4.2 — Chat surface to the quality bar (execution plan)

Status: planned, not started. Est. ~1.25 sessions. Predecessor: R4.1 ✅ (React 19 +
Tailwind v4 live; WS events for projects/sessions published). Direction locked
(2026-09-06): assistant-ui runtime + agent-elements-derived cards; delegation
tree/detail stay custom (R4.3).

## Starting point (do NOT rebuild)
- Wave-1 chat page: `pages/chat/` = Composer.tsx, MessageList.tsx, reducer.ts,
  SessionPicker.tsx — hand-rolled state machine over WS events. Functional but the
  exact "badly written" surface the user rejected; R4.2 replaces the machinery,
  keeping the routes and backend contract.
- Backend contract (stable): `POST /api/sessions/{id}/messages` (user role) →
  chat-turn delegation → WS `chat.delta {session_id, delegation_id, text}` (coalesced
  ~100ms) → WS `message.added` (authoritative persisted assistant message) →
  `delegation.status_changed`. Serial turns per session (second message queues).
  Errors arrive as persisted error messages, never silent.
- assistant-ui: MIT, React-19-ready, `@assistant-ui/react-opencode@0.2.22` exists
  (OpenCode runtime adapter, actively maintained); `useExternalStoreRuntime` /
  LocalRuntime = custom-backend path; agent-elements = MIT shadcn registry
  (React 19 + Tailwind v4 required — satisfied by R4.1 step 1c).
- The M1.8 invariants carry over: `message.added` is authoritative; chat.delta
  patches, never re-renders the whole thread.

## Steps

### Step 0 — Adapter spike (decision gate) ~0.25
- Evaluate `@assistant-ui/react-opencode`: what does its runtime expect — direct
  browser→opencode-serve access (bypassing our backend) or a pluggable transport?
  If pluggable: point it at our REST/WS (keep the funnel: backend owns transcript,
  memory, delegation gating). If it demands direct serve access: reject it and use
  `useExternalStoreRuntime` with a custom adapter (same primitives, our state).
- Deliverable: decision recorded here + the adapter module skeleton
  (`sweave-web/src/lib/chat/runtime.ts`) with the event→runtime mapping stubbed.
- Rule: the backend stays the source of truth (Session.messages); the runtime
  adapter is a *view projection* of our WS stream + REST history — no
  browser-to-opencode calls.

**Decision (recorded 2026-09-07): REJECT `@assistant-ui/react-opencode`; adopt
`useExternalStoreRuntime` with a custom adapter.**

Evidence (assistant-ui docs + npm registry, 2026-09-07):
- `useOpenCodeRuntime({ baseUrl: "http://localhost:4096" })` opens an **SSE event
  stream directly against the OpenCode serve from the browser** and is layered on
  `ExternalStoreRuntime` + `RemoteThreadList` via `@opencode-ai/sdk`. The browser
  would talk straight to the serve — bypassing our backend funnel (transcript
  composition, memory recall/synthesis, delegation gating, and the R4.0
  per-Session orchestrator binding). This violates the plan's hard rule
  ("no browser-to-opencode calls").
- `useExternalStoreRuntime` (from `@assistant-ui/react`) is the designed
  fallback and is what the opencode adapter itself is built on: same primitives
  (`Thread`, `Composer`, `AssistantRuntimeProvider`), our state. The adapter is a
  view projection: REST history (`GET /api/sessions/{id}`) → thread messages;
  WS `chat.delta` → in-flight assistant message; `message.added` → finalize;
  `delegation.status_changed` → turn status. Capability-based: providing
  `onNew`/`onCancel`/`setMessages`/`queue` turns on the matching UI affordances
  (the `queue` adapter maps to our serial-turn semantics).
- Package pinned: `@assistant-ui/react@0.15.18` (exact; peer `react ^18 || ^19` —
  React 19 satisfied by R4.1 step 1c). `npm run build` green with it installed.

### Step 1 — Thread runtime integration ~0.4
- Implement the adapter: REST history load (`GET /api/sessions/{id}`) → runtime
  messages; WS `chat.delta` → append deltas to the running assistant message;
  `message.added` → finalize (authoritative); `delegation.status_changed` →
  turn-status display; serial-queue indicator (turn queued/running).
- Composer: `POST /api/sessions/{id}/messages` on submit; disable-during-turn per
  serial semantics; optimistic user message (confirmed by the persisted user
  message event).
- Replace MessageList/reducer with assistant-ui `Thread` primitives; keep the
  SessionPicker (R4.1 tree owns session switching).
- Gate: vitest for the adapter mapping (delta ordering, finalize, queue states);
  streaming smoke (ported test_m1_8_streaming_ui.js logic) green against the mock
  server.

### Step 2 — Rendering quality (agent-elements-derived) ~0.4
- Markdown with streaming safety + code highlighting (agent-elements `Markdown`
  pattern — Shiki — or react-markdown + Shiki; decide in-step, prefer shadcn-style
  copied components, we own them).
- Tool-part generative-UI: delegation turns render their tool timeline pieces
  (from M1.9 traces via the detail endpoint) as cards — Bash/Edit-diff/Search
  renderers lifted from agent-elements, adapted to our ToolPart shape.
- Escalation card: `ask_human` questions inline in the thread with answer input →
  `POST /api/delegations/{id}/answer` (the R4.3 escalation lane stays in the
  Children surface too — the thread card is the in-flow path).
- Gate: chat with code blocks + a mocked tool-using turn renders tool cards with
  correct lifecycle states (pending/running/completed/error).

### Step 3 — Session lifecycle in-thread + polish ~0.3
- Session create/switch/rename without leaving the thread (closes M1.9 funnel
  leak #1): session menu in the thread header; project switch via the R4.1 tree.
- Polish: auto-scroll with user-scroll-interruption respect, retry affordance on
  error messages, copy-code buttons, keyboard (Enter send / Shift+Enter newline),
  empty states.
- Gate: funnel-leak #1 closed — session created, chatted, switched, all in-thread
  (Playwright).

### Step 4 — Gates + docs ~0.2
- vitest (60 + ~12), Playwright streaming + lifecycle, `npm run build`, 458 pytest
  untouched (no backend change expected; if adapter needs a backend tweak, keep it
  additive).
- Docs: DESIGN §4 (chat surface row → assistant-ui ✅), R4 hub row flipped,
  `docs/R4_2_PLAN.md` status + execution summary, §8 adoption entry updated with
  the adapter verdict.

## Amendment (2026-09-07, user ruling — mid-execution)

**The visual polish is the bulk of step 2, not a step-3 cosmetic finish.**

The user reviewed the `/chat` surface after R4.2 step 1 + step 2a
shipped and rejected it as "unpolished and frankly bad." The R4.2
plan under-scoped the visual polish — steps 2b/2c/3 only build cards
+ keyboard polish on top of a bare-bones Thread. The bars named in
`DESIGN.md §8` (assistant-ui shadcn registry + agent-elements, LibreChat
visual/interaction reference) are the contract; the work to reach
them is the primary deliverable, not garnish.

**Full design spec**: `docs/R4_2_VISUAL_POLISH.md`. The spec
diagnoses the gap (per region: thread, message bubble, tool / thinking
surfaces, composer, sidebar, header), specifies the surfaces to the
LibreChat v0.8.x + assistant-ui shadcn-registry bar, specifies the
visual test (replace the markdown-only `/dev/chat-lab` with a
real-Thread visual test — the current lab is a component gallery
that couldn't have surfaced the polish gap), and proposes a new
step structure: a **"2-pre" polish step (~0.6)** that copies the
assistant-ui shadcn-registry Thread + supporting primitives and
builds the polished message bubble / composer / scroll viewport
before 2b/2c/3. The open questions for the planner (round
boundaries, cancel affordance, edit affordance, lab removal) are
listed at the end of the design spec.

This amendment does not change the **decision** in step 0
(REJECT `@assistant-ui/react-opencode`; use
`useExternalStoreRuntime` with a custom adapter) — it changes
the **scope** of step 2: the polish work moves from "step-3 finish"
to "step 2-pre prerequisite."

## Planner rulings (2026-09-07, answering the hand-back)

1. **Multi-round + visual gate.** 2-pre (polish) is its own execution round;
   the round gate is the user's visual sign-off of `/chat` against the
   LibreChat reference BEFORE 2b/2c build on it. R4.2 step numbering:
   2-pre (this round) -> 2b -> 2c -> 3.
2. **Stop affordance: wire it.** Surface a stop button hitting the existing
   VERIFIED (planner, 2026-09-07): the claim was wrong as stated - the
   asyncio.Lock is a queue gate, not a cancel path; no cancel endpoint exists;
   JobRunner handles CancelledError -> failed/error=cancelled (plumbing exists) but
   nothing triggers it. opencode HAS POST /session/{id}/abort (v2, verified in the
   reference clone). Total work = endpoint + task cancellation + abort call + status
   semantics => NON-TRIVIAL -> degrade clause triggers: ship disabled-with-tooltip,
   cancel affordance moves to R4.3 (design note: opencode abort endpoint exists).
   chat-loop cancel path. If the cancel path proves non-trivial at
   implementation time, degrade to disabled-with-tooltip and move it to R4.3
   (record which happened).
3. **Edit + rerun: deferred to R4.3** (semantics with the serial queue and the
   Session.messages system of record need design). The polish step ships a
   small REAL action bar (copy message, timestamp) — no disabled fake buttons.
4. **chat-lab: replacement confirmed.** The real-Thread lab replaces the
   markdown-only lab; the markdown demo survives as a section inside it;
   `Markdown.test.tsx` renderer tests stay (they pin the component).

## Explicit non-goals
- Children tree / detail view / promote (R4.3). Memory/Agents/Settings (R4.4).
- Voice, attachments (later waves). Multi-turn parallel conversations in one
  session (serial queue is the semantics). SSE (WS is the transport).
- Any change to the backend chat contract (it is stable and tested).

## Risks
- `@assistant-ui/react-opencode` bypass risk — the spike decides; the custom
  `useExternalStoreRuntime` path is the designed fallback (identical primitives).
- assistant-ui version churn (25 releases on the adapter) — pin exact versions.
- React 19 + assistant-ui compatibility — their primitives are React-19-ready;
  the spike confirms.
- Agent-elements components assume `useChat`/AI-SDK message shapes — lift the
  *component JSX/styling*, adapt props to our runtime parts (shadcn model: we own
  the code).
- Streaming render perf on long turns — assistant-ui's internal memoization +
  our coalescer (M1.8) both apply; watch the vitest/e2e timing.
