# M1.11 Plan — Replace native `question` with Sweave Q&A + specialist escalation + Children audit

Status: **done** (2026-09-10). Rulings user-locked 2026-09-10:
1. Native opencode `question` tool is replaced (denied on both managed agents) — Sweave owns the Q&A surface.
2. `ask_human` = blocking orchestrator→human **question**, **no timeout** (waits indefinitely).
3. Skip = opencode-Esc equivalent, but guarded by a **system-issued "are you sure?"** (UI confirm dialog, not LLM text) against fat-finger.
4. New `escalate` = specialist→orchestrator, non-blocking notice (handles the "specialist needs orchestrator" path the old wildcard-deny made impossible).
5. Children tab = **global audit log** (keep global scope; make rows meaningful).

## Starting point (verified against code)

- MCP tools: `defer` / `list_specialists` / `ask_human` (`sweave/mcp/__init__.py:374 build_server`). `ask_human` POSTs to `/api/delegations/{id}/escalate` and returns `escalated: <id> (deadline=…)` immediately — fire-and-forget.
- `EscalationStore` (`sweave/runtime/escalation.py:92`): `{escalation_id, delegation_id, question, options, status: pending|answered|timeout, deadline_at}`; `timeout_seconds=900` default, `force_timeout` test seam; **no background sweep wired** (timeout never auto-fires in production).
- Permissions (`sweave/runtime/agent_permission.py:57`): orchestrator `{task: deny, bash(globs): deny}`; specialist `{task: deny, sweave_*: deny}`. Native `question` neither allowed nor denied → opencode default applies (unowned surface).
- Contradiction: `DESIGN.md:35` + `escalation.py:3` say specialists escalate via `ask_human`, but `sweave_*: deny` + `mcp_config.py:127` charter forbid it.
- ChatLoop (`sweave/chat/loop.py:180 _wait_for_children`): waits for child delegations only; never waits for escalations; first turn's reply becomes final answer while escalation stays pending detached.
- Thread (`Thread.tsx:384`): only `TurnDelegations` (children by `parent_task_id`); no escalation Q fetch, no inline answer. `question-prompt.tsx` component exists, unwired.
- Children (`Children.tsx:33`, `LiveTree.tsx:245`, `DetailView.tsx`): global list, escalation lane shows task snippet only (no question preview/kind), detail modal has no escalation section.

## Goal state

1. **Native `question` denied** on `sweave-orchestrator` + `sweave-specialist` (`{"question": "deny"}`). LLM must use Sweave tools; native path never fires.
2. **`ask_human(question, options?, caller_delegation_id)` = blocking human question.** MCP returns immediately (`escalated: … (no deadline — waits for answer)`); **ChatLoop holds the turn open** (no assistant persist) until `answered | skipped`, then runs synthesis with the answer injected. No deadline, no auto-timeout.
3. **Skip with system confirm.** `POST /api/delegations/{id}/skip {confirmed: true}` → `status=skipped`, clears `needs_attention`, emits `specialist.escalation_resolved{status: skipped}`. UI: Skip → `window.confirm("…proceed with best judgment?")` (system dialog, not LLM) → POST. Unconfirmed POST → 409. Synthesis treats `skipped` like "proceed with best judgment".
4. **`escalate(message, caller_delegation_id)` = specialist→orchestrator.** Non-blocking; record `{kind: escalation, audience: orchestrator}`; sets `needs_attention`; visible in Children audit + TurnDelegations `• needs input` + synthesis context. Specialists allowed `sweave_escalate` only (explicit denies replace the `sweave_*` wildcard).
5. **Children = global audit log.** Escalation lane shows kind badge (`Q`/`ESC`) + question/message preview; DetailView gains Escalation section (Q/options/status/response/deadline-or-"waits"); TurnDelegations expanded card shows escalation preview + answer/skip link. Thread gains inline Question card for the turn's own pending question (answer input + Skip-confirm, WS-driven).

## Steps

1. **Backend: permissions + EscalationStore** (~0.5 session). `agent_permission.py`: both profiles add `question: deny`; specialist replaces `sweave_*: deny` with explicit `sweave_defer/sweave_list_specialists/sweave_ask_human: deny` (allow `sweave_escalate` by omission). `escalation.py`: `create(kind, audience, timeout_seconds=None)`; `deadline_at: str|None`; `status += skipped`; `skip()`; `answer()` unchanged; `force_timeout` kept as manual/test seam. Gate: new pytest (deny matrix, no-deadline create, skip flow, kind/audience persist).
2. **MCP + router** (~0.5 session). `mcp/__init__.py`: `ask_human` posts `{kind: question, audience: human}` + returns no-deadline line; new `escalate` tool + dispatcher + list entry. `routers/delegations.py`: `EscalateRequest{kind, audience}`, `POST …/skip`, `GET …/escalation` returns full record. `server.py`: store constructed with `timeout_seconds=None`. Gate: MCP tool-set test updated (`{defer, list_specialists, ask_human, escalate}`), stdio round-trip, router skip/confirm tests.
3. **ChatLoop hold-open** (~0.5 session). `ChatLoop` gains `escalation_store` param (wired in `server.py`); `_wait_for_escalation(delegation_id)` polls store with no deadline (0.5s interval); `_run_turn_body`: after children settle, if own escalation pending → wait → synthesis includes `Human answer: …` / `Human skipped — proceed with best judgment`. Gate: new chat-loop escalation tests (answer → synthesis contains answer; skip → synthesis contains skip note; no assistant persisted before resolution).
4. **UI: inline Q + audit** (~1 session). `types` + `client.skipEscalation`; `TurnQuestions.tsx` (own-delegation pending Q, WS `specialist.escalated/resolved`, options buttons + text + Answer + Skip-confirm); mount in `Thread.tsx` assistant block; `TurnDelegations` expanded shows escalation preview; `LiveTree` lane shows kind badge + Q preview; `DetailView` escalation section; `wsInvalidations` maps the two escalation events to `delegations` + `escalation` keys. Gate: vitest (card render/answer/skip-confirm/audit badge) + `npm run build`.
5. **Docs + gates** (~0.5 session). DESIGN §4/R1, PROJECT_STATE, GOTCHAS (permission-split + no-timeout + skip-confirm patterns); `run.py --check`, full pytest, vitest, build; suite 2× green.

## Explicit non-goals

- MCP long-poll (tool call stays open): rejected — ChatLoop-level hold keeps the 30s MCP timeout irrelevant.
- Turn-timeout suspension: `turn_timeout` still bounds LLM streaming turns; the escalation wait itself is unbounded (separate poll, no deadline).
- Per-question deadlines / auto-timeout sweep: removed for questions; `force_timeout` stays as manual/test seam only.
- Session-scoped Children rewrite: stays global per ruling; filters beyond kind badge are follow-up.
- Writable specialist chat / fork work (M1.10): untouched.

## Risks

- **Infinite-wait wedging a chat turn**: user walks away mid-question → turn never finalises, session lock held. Mitigate: Skip-confirm is one click; rerun/edit path can cancel (documented); future: server-restart reaps via persisted pending record + `list_open`.
- **Opencode permission-key drift** (`question` rename): mitigate with live `GET /config` check + test pinning the rendered agent map.
- **Specialist `escalate` abuse (spam)**: audit-visible by design; cap/rate-limit is follow-up on evidence, not speculation.

## Execution summary (2026-09-10)

Shipped as planned, one deviation: `wsInvalidations.ts` untouched — delegation/escalation events are subscribed locally (ChildrenPage, TurnDelegations, TurnQuestions), not via the AppProvider map, so no map change was needed.

- Backend: `question: deny` both roles; specialist explicit denies (`sweave_defer/list/ask_human`, `sweave_escalate` allowed); `EscalationStore` kind/audience/skip/nullable deadline; `POST …/skip`; store default `timeout_seconds=None`.
- ChatLoop: `escalation_store` param wired in `server.py` lifespan; `_wait_for_escalation` (unbounded) + `_escalation_note`; synthesis carries Q&A + child escalation notices.
- UI: `TurnQuestions.tsx` (+4 vitest), Thread mount, TurnDelegations preview, LiveTree kind badges + previews + Skip-confirm, DetailView escalation section, `skipEscalation` client.
- Gates: 11 new pytest green; full suite 547 passed (2 pre-existing models-registry env failures, unrelated — generated `models.yaml` default not in registry); 13/13 `run.py --check`; 148+4 vitest; `npm run build` green.
