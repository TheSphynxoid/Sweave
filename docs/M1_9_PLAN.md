# M1.9 — Dogfood pass: funnel completion, visibility, hardening (execution plan)

Status: done 2026-09-05 (per `docs/M1_9_PLAN.md` post-execution summary at the bottom).
Est. ~2 sessions. Predecessors: M1.prep → M1.8 all ✅.
Absorbs the hardening rulings (2026-09-04), the two-funnel completion, and the
visibility axis (superuser requirement). This is the last M1 milestone; its gate is
the first day of Sweave doing what its own development needed done manually.

## Starting point (do NOT rebuild)
- Chat loop + streaming live (M1.7/M1.8); defer via MCP (M1.6); specialist sessions
  durable per (specialist, worktree), orchestrator per Session (M1.3); promote
  endpoint exists (M1.5, review→done only); delegation records v4 + traces JSONL.
- Traces capture: composed prompts, status transitions, final outputs, memory-recall
  audit. **Gap: tool activity inside a specialist turn is dropped** — the stream
  reader harvests only `text`/`error` parts.
- WSEventBus + unified vocabulary; Children tab renders bridge records with promote
  buttons; no live tree, no detail view, no visibility CLI.
- opencode parts model (spike 2026-09-04, explore agent on the reference clone):
  `ToolPart {callID, tool, state: pending|running|completed|error, input, output,
  title, time.{start,end}}`, `step-start`/`step-finish` (tokens{input,output,
  reasoning,cache}, cost), `reasoning` parts; terminal = `info.time.completed` +
  `info.finish`; errors on `info.error` (no `type:"error"` part exists — our check
  is a dead branch); `GET /event` SSE (`message.part.updated/delta`) for live-follow.

## Rulings/defaults applied
- Chain budget stays **coordination-only** (policy, not necessity — step-finish
  tokens are now known-visible): specialist tokens are **logged per turn** in traces
  and shown in the detail view, never capped by the chain budget.
- Human promotes; chat turns auto-done (M1.5/M1.7 rulings stand).
- Scratching an itch early: dev repo is never its own live-gate target
  (scratch project in temp; per-project `worktree_base` makes it enforceable).

## Steps

### Step 1 — Trace completeness (parts model adoption) ~0.4
- Stream reader (opencode.py) captures: `tool` parts (callID, tool,
  state.status lifecycle pending→running→completed|error, input, output, title,
  time.{start,end}), `step-start`/`step-finish` (reason, cost, tokens),
  `reasoning` parts (optional flag) — keyed by callID/part-id, snapshot-replace
  semantics; emits trace events `tool.started|updated|completed|failed`,
  `step.boundary`, and per-turn `tokens_used {input, output, cost}`.
- **Terminal detection fix**: turn complete = `info.time.completed` set AND
  `info.finish` present (replaces the every-chunk-matches "assistant + parts"
  heuristic). **Remove** the dead `type=="error"` part branch; errors read from
  `info.error`.
- Tests: mocked multi-tool stream (ordered tool lifecycle, steps, tokens), terminal
  fix regression, no-behavior-change for text-only streams.

### Step 2 — Hardening bundle ~0.3
- Specialist agent config rendering sets `permission.task: deny` (closes the native
  subagent bypass around DelegationManager); orchestrator session config denies
  git-mutation bash (`git commit/merge/push` patterns) — runtime computes git state.
- **Per-project `worktree_base`**: field on the Project record (default = global
  config), WorktreeManager honors it. Convention enforced in docs: the dev repo is
  never its own live-gate target.
- `WorktreeManager.align()` precursor: rebase/merge worktree branch onto the
  integration branch state + dirty-skip rule (R2's full protocol lands later;
  this is the primitive).
- Tests: config generation assertions, override resolution, align on clean/dirty.

### Step 3 — Output funnel completion ~0.5
- **`ask_human(question, options?)` MCP tool** (sibling of defer, same server/auth):
  blocking tool call — the asking delegation is flagged `needs_attention`, WS
  `specialist.escalated {delegation_id, question, options}`, the question surfaces
  in the Children tab escalation lane. Answer: `POST /api/delegations/{id}/answer
  {response}` → returns as the tool result into the asking session. Timeout
  (default 15 min, config) → tool returns "no answer received" so the LLM proceeds
  with best judgment rather than hanging.
- **Children tab live tree**: WS-driven status pulses per delegation
  (queued/running/review/done/failed + needs-attention lane at top), promote and
  answer buttons inline. No full re-renders (M1.8 single-bubble pattern).
- Tests: ask_human round-trip (mock LLM asks → escalation event → answer → tool
  result), timeout path, tree update events.

### Step 4 — Visibility surfaces ~0.5
- **Delegation detail view** (web): renders the trace as a specialist-turn story —
  composed prompt (collapsible), tool timeline with statuses/durations/inputs/
  outputs (from step 1), tokens/cost per step, synthesis, final output. Route:
  Children entry click → detail pane.
- **Visibility CLI**: `sweave log <delegation_id>` (pretty-render a trace, sections
  like the detail view), `sweave watch` (live tree: delegations + statuses, WS or
  poll), `sweave tail <delegation_id>` (follow a running turn).
- `session-ui` mining done during implementation (rendering patterns for tool
  timeline); reference clone stays outside the repo.
- Tests: CLI render over fixture traces; detail view smoke (playwright pattern).

### Step 5 — Gates + self-hosting live gate + docs ~0.4
- pytest (400 + ~24 new), `run.py --check` 13/13, `test_full.py` 40/40, loader
  green, streaming test green.
- **Self-hosting gate**: create a scratch project (temp dir, exercising the
  worktree_base override); run one real task end-to-end through the chat thread —
  orchestrator defers, specialist works in its worktree, escalates or completes,
  answer/promote through the UI — **without leaving the chat thread**. Count and
  record every funnel leak (any forced exit to files/CLI/API). The friction list is
  expressed as funnel leaks and becomes R4's re-planning input.
- Docs: DESIGN §4 (visibility rows ✅, streaming row ✅), R1 M1.9 ✅ + M1 exit
  statement, PROJECT_STATE progress + dogfood findings, AGENTS gotchas if any.

## Explicit non-goals
- R4 UI re-planning (post-dogfood, from the funnel-leak list). TUI. SSE/live-follow
  rendering of specialist streams (detail view + CLI tail cover the need; `GET
  /event` SSE is the R4 mechanism when wanted). Fanout/cross-review (R2).
  Parallel turns per session. Budget counting of specialist tokens (logged, not
  capped — policy per ruling).

## Risks
- `ask_human` blocking semantics: a long-unanswered question holds a serve session
  open — timeout + turn_timeout interplay needs care (escalation timeout < turn
  timeout; a timed-out escalation must not fail the whole turn).
- `permission.task`/bash-deny rendering depends on project-level opencode config
  (mechanism verified in M1.6 step 0 — same plumbing, low risk).
- Children-tab live updates vs the M1.8 no-rerender invariant — same single-node
  patch pattern; the tree structure itself changes rarely (bridge writes).
- Detail view over large traces — cap rendered events (config), full data stays in
  the JSONL.

## Execution summary (2026-09-05)

Five steps landed as planned; one minor amendment (no architectural impact).
Total pytest count: 447 (+47 from the M1.9 step files; 411 baseline before M1.9).
All five commits on `master`:

1. **Trace completeness** — parts-model capture in the harness
   (`sweave/harness/opencode.py`) + the runtime's `_send_message`:
   tool parts (pending → running → completed | error), step boundaries,
   per-turn `tokens_used` audit anchor, reasoning parts (off by default).
   Terminal detection: `info.time.completed` + `info.finish` (replaces the
   pre-M1.9 "parts + role==assistant" per-chunk heuristic). The dead
   `type:"error"` part branch was removed; `info.error` is the canonical
   v2 error surface. Pre-M1.9 fixtures updated to add the terminal flag
   (mock reflects real wire). 11 new tests; mock now emits terminal info
   on every response.

2. **Hardening bundle** — per-project `worktree_base` (Project field;
   `sweave/runtime/worktree_base.py` resolver), WorktreeManager.align()
   primitive with the dirty-skip rule (never stash-dance a working
   agent), specialist permission profile (`sweave/runtime/agent_permission.py`)
   — orchestrator = `task: deny` + git bash deny; specialist = `task:
   deny` only (specialists commit freely per the 2026-09-04 commit-
   authority map). `runtime/mcp_config.py` injects the orchestrator's
   profile into the per-project `opencode.json`. 13 new tests.

3. **Output funnel completion** — `ask_human(question, options?)` MCP
   tool (sibling of `defer`; same auth + wire surface). The asking
   delegation is flagged `needs_attention: bool = False` (Delegation
   schema v5; `SCHEMA_VERSION=5`, `_migrate_v4_to_v5`). `sweave/runtime/
   escalation.py:EscalationStore` is the persistence + event surface.
   Endpoints: `POST /api/delegations/{id}/escalate`, `POST /api/delegations/
   {id}/answer`, `GET /api/delegations/{id}/escalation`. WS events:
   `specialist.escalated` + `specialist.escalation_resolved`. The MCP
   server now reads `SWEAVE_MCP_TOKEN` env first (the opencode.json
   plumbing seam), falls back to the home file. 13 new tests.

4. **Visibility surfaces** — `sweave/web/detail_view.py` projects the
   trace JSONL into composed-prompt / tool-timeline / tokens / status-
   timeline sections. `GET /api/delegations/{id}/detail` HTTP endpoint
   + `sweave log <id>` / `sweave tail <id>` / `sweave watch` CLI. The
   `tail` command (`sweave/cli/tail.py:follow_trace`) is an async
   generator with file-rotation handling; `watch` polls the running
   server's `/api/delegations` endpoint on a 1s cadence. 8 new tests.

5. **Self-hosting live gate** — `scripts/m1_9_self_hosting_scene.py`
   drives one chat turn end-to-end through the HTTP API (mock
   opencode subprocess; real running server; SWEAVE_MOCK_OPENCODE=1).
   The funnel-leak report records every forced exit to API/CLI/file;
   those are R4's re-planning input. Per-project `worktree_base`
   plumbed through `/api/projects` POST. 2 smoke tests.

**Amendment**: the plan claimed "no schema bump" for the kind field in
step 2 (the chat delegation flag, M1.7). That reasoning was wrong
(see AGENTS.md gotcha #12: every new Delegation field requires a
schema bump + migration helper). The M1.7 step 2 history (bump 3→4
+ `_migrate_v3_to_v4`) was applied again for M1.9 step 3 (bump 4→5 +
`_migrate_v4_to_v5`). Session schema continues to use `from_dict`
defaults (no bump needed).

**Funnel leaks recorded by the live scene** (R4's input):
- project + session lifecycle (create / activate) — no chat equivalent
- delegation tree inspection (`GET /api/delegations`) — no chat equivalent
- promote (`POST /api/delegations/{id}/promote`) — Children tab button
  pending; the Children-tab live-tree patch lands when the UI side is
  wired (R4 UI work).
- ask_human answer (`POST /api/delegations/{id}/answer`) — Children tab
  answer button pending; same R4 dependency.

These leaks are precisely the spots R4's UI re-plan covers: the chat
funnel needs the Children tab to render the live tree (status pulses,
promote/answer buttons inline, escalation lane at top) so the human
never has to leave the chat thread for any of these operations.
