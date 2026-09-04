# M1.7 — Orchestrator chat loop (execution plan)

Status: planned, not started. Est. ~1.5 sessions. Predecessors: M1.prep → M1.6 all ✅.
Inherits M1.6's deferred synthesis scope and fixes the §2.1 context-scope wrinkle as a
dedicated step. Supersedes the DESIGN.md §6 R1 M1.7 bullet.

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
- Orchestrator turn = one message POST on its durable session (full conversation
  context lives in that opencode session; no prompt-reassembly).
- Assistant reply: final terminal text from the stream → persisted as
  `role=assistant` message (agent="orchestrator") → WS `message.added` event.
- Serial queue: per-session asyncio.Lock; messages arriving mid-turn wait; each
  becomes its own turn after the previous completes.
- Tests: end-to-end with mocked runtime — message in → turn delegation → reply
  persisted; queue ordering; orchestrator-unreachable → error message persisted (no
  silent fallback).

### Step 3 — Synthesis loop (M1.6's deferred scope) ~0.4
- After chat-turn children all reach terminal: runtime builds a synthesis prompt —
  per child: `{specialist, task, status, output truncated to N chars}` (tiktoken-capped
  budget, default ~8K tokens total, config override) — sent as the next user message on
  the SAME orchestrator session; its terminal reply is the persisted assistant answer.
- Synthesis is skipped when the turn had no children (single-turn answer flows
  through unchanged).
- Chat turns auto-`done` after their final reply (implementation children still stop
  at `review` independently).
- Tests: synthesis prompt assembly + cap; no-children fast path; auto-done; children
  failing ⇒ synthesis still runs with failure noted (never hangs the conversation).

### Step 4 — Gates + live gate + docs ~0.3
- pytest (336 + ~18 new), `run.py --check` 13/13, `test_full.py` 40/40, loader green.
- **Live gate** (gmi, tiny): in a fresh session, chat "Ask the backend specialist to
  create hello.py printing OK, then summarize" → orchestrator defers (MCP) → child
  reaches review → synthesis reply lands in Chat as assistant message; second session
  same project gets its own orchestrator context (wrinkle proven live). Trace + chat
  history inspected.
- Docs: DESIGN §4 (chat endpoint ⚠️→✅), R1 M1.7 ✅, §2.1 note (binding per-Session,
  implemented), PROJECT_STATE progress; AGENTS gotcha if session-file migration
  bites.

## Explicit non-goals
- Streaming output (M1.8). UI changes beyond what already exists (dogfood pass rules
  later). Parallel orchestrator turns per session (queue is the semantics). Memory
  writes from chat turns (retention policy lands with R6 compaction).

## Risks
- Session-file write contention (chat persists + bridge writes + turn completion) —
  all writes already go through ProjectManager's per-project lock (M1.prep).
- Synthesis prompt bloat with many children — hard cap + oldest-truncated.
- Orchestrator session history grows unbounded per Session — acceptable for v1;
  compaction (R6) owns the ceiling later.
