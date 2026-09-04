# M1.7 — Orchestrator chat loop (execution plan)

Status: planned, not started. Est. ~2.0 sessions (was ~1.5; grew by ~0.5 to absorb the
transcript system as a step — interpretation #1 of the earlier thread). Predecessors:
M1.prep → M1.6 all ✅. Inherits M1.6's deferred synthesis scope and fixes the §2.1
context-scope wrinkle as a dedicated step. Supersedes the DESIGN.md §6 R1 M1.7 bullet.

## Starting point (do NOT rebuild)
- `POST /api/sessions/{id}/messages` (routers/projects.py:146) is **persist-only** —
  this endpoint becomes the chat loop's entry point.
- `Session` record (projects.py:121) has **no `schema_version`** and **no orchestrator
  session binding**; messages/children lists live in `{project}/sessions/{id}.json`.
- `SpecialistRuntime.run(delegation, …)` + `ServeRunner` keyed per (specialist,
  worktree); orchestrator seed cwd = "." (project root); orchestrator has the MCP
  `defer` tool (M1.6); parent gating: parents stay `running` until children terminal,
  then `review` (M1.6 step 3).
- ModelRef contract (M1.5); status transitions via DelegationManager; WSEventBus.
- **Transcript system gap** (this plan absorbs): today, the conversation transcript
  lives in opencode's session storage. The runtime has no view of it, can't
  audit it, can't cap it, can't compact it, and can't make it the system of record
  across opencode-session restarts. The chat loop needs the runtime to own
  the transcript; this plan delivers that.

## The wrinkle (dedicated step 1)
§2.1: orchestrator context is **per-Session**, but M1.3 stored durable `session_id` on
the **Specialist** (per project). Three Sweave sessions would share one orchestrator
conversation — wrong. Fix: the orchestrator's durable opencode session binding lives on
the **Session record**, one per (project, session).

## Rulings/defaults applied (from the chat confirming this plan)
- Chat turns **auto-complete** (`done` after synthesis): the review state exists for
  implementation diffs, not conversation answers. Implementation children still stop
  at `review` (human promotes).
- Concurrent user messages **queue** (serial conversation semantics) — second message
  while a turn is live waits, not 409.
- Orchestrator unavailable ⇒ explicit error message persisted to the chat (never a
  silent rule-router fallback for conversation; rule-router stays fallback for direct
  task submissions only).
- **The LLM is a consumer, not a context manager.** The runtime composes the per-turn
  system prompt; the LLM consumes what the runtime builds. The LLM never decides
  what enters its context window. The transcript system is what makes this
  enforceable.
- **Memory retrieval is server-curated, not LLM-driven.** When the runtime includes
  `memory_recall` in the per-turn prompt, the runtime curates the top-k entries
  (format, cap, anti-pollution). The LLM never sees free-form retrieval; it
  consumes a bounded list.

## The transcript system (interpretation #1)

The transcript system is the runtime-side context builder. The runtime becomes the
**determinism floor**: every chat turn sees a context the runtime built, not
whatever opencode happened to keep around. The LLM's view of the conversation
is bounded, auditable, and truncatable.

**Per-turn system prompt composition** (assembled by the runtime, not the LLM):
1. **Seed prompt** (orchestrator role + tool contracts; from `sweave/agents/orchestrator/config.yaml`).
2. **Server-curated `memory_recall`** of project-scoped decisions (top-k, format
   chosen by the runtime, anti-pollution filtered). Trigger rule: simple and
   auditable (e.g. "if the prior turn referenced any topic with stored
   memories, include the top-k"). The runtime decides; the LLM doesn't.
3. **Transcript** of the conversation so far, drawn from `Session.messages`,
   formatted as the runtime chooses (sliding window, structured summary of
   older turns + verbatim recent turns, etc.). The runtime picks the shape.
4. **Synthesis prompt** (when children are present), server-built with a token
   cap (default ~8K tokens total, config override) and per-child truncation.
5. **Current user message**.

**The opencode session's role** doesn't change — it remains the LLM execution
context. The runtime posts the composed prompt to it; opencode executes the
LLM call; the LLM generates a reply; the runtime captures it. The opencode
session's own session storage is no longer the system of record for the
conversation transcript; `Session.messages` is.

**The LLM is a tool user.** It picks the defer target. It requests `memory_recall`
if it thinks it needs more (the runtime curates the response). It generates the
user-facing reply. It never decides what enters its context window.

## Steps

### Step 1 — Per-Session orchestrator binding (the wrinkle) ~0.4
- `Session` gains `schema_version: int = 1` + `orchestrator_session_id: str | None`;
  `to_dict`/loader migrate legacy session files (absent fields → defaults) — the
  migrations policy extends to session storage.
- ServeRunner keying: orchestrator resolves to worktree = project root (no worktree
  creation for orchestrator — ever); registry key stays (specialist, worktree) so
  different sessions of the same project reuse one serve but NOT one session.
- `SpecialistRuntime` gains a session-resolution branch: specialists →
  `specialist.session_id`; orchestrator → `session.orchestrator_session_id`
  (create-on-first-use, persist on the Session record).
- Tests: two Sessions get two orchestrator sessions; binding survives restart
  (persisted); legacy session file migration; orchestrator submits create no worktree.

### Step 2 — Chat turn pipeline ~0.5
- `Delegation` gains `kind: str = "task"` (`"chat"` for orchestrator conversation
  turns) — additive field, from_dict default keeps v3 records loadable (no schema bump;
  drop-unknown-fields contract already covers it).
- `POST /api/sessions/{id}/messages` (role=user): persist message → create chat-turn
  Delegation (agent=orchestrator, kind=chat, depth=0, no worktree, parent_session_id,
  model per precedence chain) → JobRunner runs it like any delegation (trace, WS).
- **The runtime builds the per-turn system prompt** (per the transcript system
  section above) and posts it to the orchestrator's opencode session. The opencode
  session carries the LLM's working memory for one turn; the runtime's transcript
  (`Session.messages`) carries the conversation across turns.
- Assistant reply: final terminal text from the stream → persisted as
  `role=assistant` message (agent="orchestrator") → WS `message.added` event.
- Serial queue: per-session asyncio.Lock; messages arriving mid-turn wait; each
  becomes its own turn after the previous completes.
- Tests: end-to-end with mocked runtime — message in → turn delegation → reply
  persisted; queue ordering; orchestrator-unreachable → error message persisted (no
  silent fallback); per-turn system prompt composition is the runtime's job
  (assert: the LLM sees what the runtime built, not what opencode remembered).

### Step 3 — Synthesis loop (M1.6's deferred scope) ~0.4
- After chat-turn children all reach terminal: runtime builds a synthesis prompt —
  per child: `{specialist, task, status, output truncated to N chars}` (tiktoken-capped
  budget, default ~8K tokens total, config override) — sent as the next user message on
  the SAME orchestrator session; its terminal reply is the persisted assistant answer.
  The synthesis prompt is **server-built** (the runtime's job), not LLM-composed.
- Synthesis is skipped when the turn had no children (single-turn answer flows
  through unchanged).
- Chat turns auto-`done` after their final reply (implementation children still stop
  at `review` independently).
- Tests: synthesis prompt assembly + cap; no-children fast path; auto-done; children
  failing ⇒ synthesis still runs with failure noted (never hangs the conversation).

### Step 4 — Transcript system (the new piece, was interpretation #1) ~0.5
- `Session.messages` is the system of record for the conversation transcript. The
  opencode session's own session storage is no longer authoritative; the runtime's
  view of `Session.messages` is.
- **Transcript context builder**: the runtime's per-turn system prompt composer.
  Inputs: seed, server-curated `memory_recall`, transcript (drawn from
  `Session.messages`, formatted by the runtime), synthesis prompt (when
  children are present), current user message. The runtime owns the format
  and the caps; the LLM consumes the result.
- **Transcript format** (runtime's choice; v1 = sliding window with a token cap):
  - Recent N turns verbatim (default N=10, config override).
  - Older turns compressed to a structured summary (the runtime's
    responsibility, not the LLM's).
  - Per-turn token cap on the transcript portion of the system prompt
    (default ~16K tokens, config override). Oldest-truncated when over the cap.
  - The transcript is the runtime's view; opencode's session is no longer
    consulted for prior-turn context.
- **Server-curated `memory_recall`**:
  - Trigger rule (simple, auditable): the runtime inspects the prior turn's
    transcript + the current user message + recent tool calls, and decides
    whether to recall project-scoped memories.
  - Top-k cap (default 5 entries; config override).
  - Format chosen by the runtime (e.g. "decision: {summary} | {timestamp}").
  - Anti-pollution: the runtime drops entries that don't change the answer
    *before* the LLM sees the list.
  - Audit story: every recall's query, top-k entries, and what was actually
    included in the system prompt is logged to the trace. R6 compaction has
    a structural signal to compact against.
- **Memory contract in the orchestrator prompt** (the LLM-as-consumer framing):
  - "This conversation's history is `Session.messages` (server-curated)."
  - "Memory retrieval is server-curated. You may request `memory_recall(query)` for
    additional context; the runtime returns a bounded, formatted list."
  - "You don't choose what enters your context window. The runtime does."
- Tests:
  - Transcript format: sliding window, structured summary, oldest-truncated.
  - Per-turn system prompt composition: seed + memory + transcript + synthesis
    + user message, with token caps enforced.
  - Server-curated memory: top-k, format, anti-pollution filter, audit log.
  - Two Sessions, same project: each gets its own `Session.messages`; the
    runtime's transcript is per-Session, not per-orchestrator-specialist.
  - Opencode session restart: `orchestrator_session_id` persists; the
    runtime rebuilds the transcript from `Session.messages` on restart;
    the LLM sees the same context after a restart as before.

### Step 5 — Gates + live gate + docs ~0.3
- pytest (336 + ~22 new), `run.py --check` 13/13, `test_full.py` 40/40, loader green.
- **Live gate** (gmi, tiny): in a fresh session, chat "Ask the backend specialist to
  create hello.py printing OK, then summarize" → orchestrator defers (MCP) → child
  reaches review → synthesis reply lands in Chat as assistant message; second session
  same project gets its own orchestrator context (wrinkle proven live) AND its own
  transcript (transcript system proven live — second session's first chat doesn't see
  the first session's history). Trace + chat history inspected; per-turn system
  prompt composition logged (proves the LLM-as-consumer contract).
- Docs: DESIGN §4 (chat endpoint ⚠️→✅; transcript system ✅; runtime context builder
  ✅), R1 M1.7 ✅, §2.1 note (binding per-Session, implemented; transcript is the
  system of record), PROJECT_STATE progress; AGENTS gotcha if session-file
  migration bites.

## Explicit non-goals
- **Engine driver abstraction** (a separate side-project; see "Side-projects"
  below). M1.7 ships with opencode as the only engine. The transcript system
  is the structural prerequisite for the engine driver abstraction; the
  side-project builds on M1.7.
- Streaming output (M1.8). UI changes beyond what already exists (dogfood pass
  rules later). Parallel orchestrator turns per session (queue is the
  semantics). Memory writes from chat turns beyond the existing `memory_retain`
  (retention policy lands with R6 compaction).

## Side-projects (not R-numbered)
- **Custom agent engine** (side-project; scope refined after M1.7 lands).
  Motivation: a sweave-native LLM execution layer that doesn't depend on
  opencode. M1.7's transcript system is the structural prerequisite: the
  runtime owns the per-turn system prompt, so a custom engine only needs to
  implement "take a composed prompt, return a reply, optionally stream."
  Not committed to a roadmap slot; the side-project starts when the
  engine interface requirements are clear (after M1.7).

## Risks
- **LLM-driven memory pollution** (now bounded by the transcript system): the
  runtime's curated top-k + format + anti-pollution filter mean a noisy memory
  bank can't drown the signal. The audit story (every recall's query and
  included entries logged to the trace) means R6 compaction has a structural
  signal to compact against. The "functional, not just acceptable" framing:
  the memory system is runtime-owned with a real shape, not LLM best-effort.
- **Session-file write contention** (chat persists + bridge writes + turn
  completion) — all writes already go through ProjectManager's per-project lock
  (M1.prep).
- **Synthesis prompt bloat with many children** — hard cap + oldest-truncated.
- **Transcript format choice** (v1 = sliding window) is a runtime decision; if
  the chosen format doesn't carry enough context for long chats, the
  structured-summary step needs design iteration. The token cap is the
  binding constraint; the format is the dial.
- **Orchestrator session history grows unbounded per Session** — bounded
  by the transcript's per-turn token cap; the runtime truncates oldest.
  R6 compaction (when it lands) handles the deeper ceiling.

## Branch notes (vs the original M1.7 plan)
- **Step 4 (transcript system) is new** (~0.5 sessions). It absorbs the
  "transcript system as a clean M1.7 step" (interpretation #1 of the
  earlier thread) rather than as a separate M1.7.5 plan. The chat loop
  benefits from the transcript system landing with it; splitting would
  have meant two chat-loop designs.
- **The engine driver / custom engine / re-architecture conversation is
  side-project-scoped, not R-numbered.** The side-project starts after
  M1.7; its scope is bounded by the engine interface requirements that
  M1.7 surfaces.
- **The LLM's role is reduced.** The original M1.7 plan said the LLM
  "judges per turn whether to recall." The new framing: the runtime
  decides what enters the context window; the LLM consumes the result.
  The prompt contract reflects this (memory contract block).
