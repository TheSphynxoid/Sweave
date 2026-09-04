# M1.8 — Streaming (execution plan)

Status: planned, not started. Est. ~1 session. Predecessors: M1.prep → M1.7 all ✅.
Supersedes the DESIGN.md §6 R1 M1.8 bullet. Scope ruling (2026-09-04): v1 streams
**assistant-reply tokens + status transitions only**; specialist intermediate/tool
output stays in the JSONL traces (surfaced by the M1.9 detail view) — the chat tab
stays quiet while specialists work.

## Starting point (do NOT rebuild)
- Reply capture today: `SpecialistRuntime`/harness reads the opencode chunked stream
  server-side and returns the **full text at once** (`aiter_text` → text parts →
  terminal detect; opencode.py:444, specialist_runtime.py:444). Nothing streams to
  clients mid-turn.
- `ChatLoop.run_turn` (sweave/chat/loop.py:258): composes prompt → runs orchestrator
  turn (`_run_orchestrator_turn`:496) → `_finalise_turn`:542 persists assistant
  message → fires `message.added`. The user sees nothing until this end.
- `WSEventBus.publish(event, data)` (web/events.py:70); UI handler
  `app.js:795 ws.onmessage` dispatches by event name; chat renders via
  `renderSessionMessages` (full re-render from session data).
- Delegation status transitions already stream (`delegation.status_changed`, M1.prep).

## Goal state
A user typing in Chat sees the orchestrator's reply render **incrementally** as the
model generates it, plus live status transitions for the turn and its children.
Specialist tool noise stays out of the chat; the trace keeps everything.

## Steps

### Step 1 — Harness streaming callback ~0.3
- `AgentProcess.send(message, on_chunk=None)`: optional async callback invoked per
  text chunk as it leaves the opencode stream (contract note: claude/codex adapters
  implement per-chunk or fall back to single-shot — contract stays optional).
- `SpecialistRuntime.run`/`_run_orchestrator_turn` accept and forward `on_chunk`;
  default None preserves today's accumulate-only behavior (all existing tests pass
  unchanged).
- Tests: mock stream → callback receives ordered chunks; no callback ⇒ old behavior.

### Step 2 — ChatLoop wire-up + throttle ~0.3
- `run_turn(..., on_chunk)` threads the callback; ChatLoop wraps it to publish
  `chat.delta {session_id, delegation_id, text}` on WSEventBus, **coalesced** (flush
  at most every ~100ms or ~64 chars — per-token WS frames are noise; config knob
  `chat.stream_coalesce_ms`).
- Terminal handling unchanged: the full final text still lands via `message.added`
  (clients finalize the partial with the authoritative message — idempotent by
  message id).
- Status transitions already flow; assert `chat.delta` events carry the live turn's
  delegation_id so the UI can scope them.
- Tests: coalescing (many chunks → few events), final message authoritative,
  event payload shape.

### Step 3 — UI incremental rendering ~0.3
- app.js: handle `chat.delta` — if the current Chat tab shows the turn's session,
  append/patch a single "streaming" bubble (create-once, update textContent; no full
  re-render per delta); on `message.added` for the same id, replace the partial with
  the persisted message.
- Keep the `switchTab`/render invariants: streaming never triggers
  `renderSessionMessages` (that's the re-render storm this must avoid); only the one
  bubble node mutates.
- Tests (playwright pattern): send message with mocked multi-chunk reply → partial
  bubble appears before final; final replaces partial; no console errors; existing
  40 UI checks still green.

### Step 4 — Gates + live gate + docs ~0.2
- pytest (381 + ~10 new), `run.py --check`, `test_full.py`, loader green.
- **Live gate** (gmi): a chat turn whose reply streams visibly (multiple `chat.delta`
  frames in the WS log before `message.added`; bubble renders incrementally in the
  headless browser). Delegation status events for children still fire during the turn.
- Docs: DESIGN §4 (streaming row ✅; chat row note), R1 M1.8 ✅, PROJECT_STATE
  progress; AGENTS gotcha only if coalescing bites.

## Explicit non-goals
- Specialist intermediate/tool-output streaming (trace-only; M1.9 detail view reads it).
- SSE fallback (WS is the only transport; SSE only if WS proves unreliable in
  dogfooding).
- Streaming into Children tab (status transitions already cover it); typing indicators;
  multi-turn interruption/cancel.

## Risks
- Coalescing too aggressive ⇒ "streaming" looks batched; too loose ⇒ WS spam. The
  knob defaults conservative; tune in M1.9 dogfooding.
- opencode chunk boundaries split UTF-8 sequences — the existing `_split_json_stream`
  already handles partial JSON; text-part deltas are string-appended (safe).
- Full-suite WS test flakiness — tests use the bus directly (no real sockets), the
  playwright test is the only socket user (same as current UI tests).
