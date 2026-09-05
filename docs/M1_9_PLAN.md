# M1.9 — Dogfood pass: funnel completion, visibility, hardening (execution plan)

Status: planned, not started. Est. ~2 sessions. Predecessors: M1.prep → M1.8 all ✅.
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
