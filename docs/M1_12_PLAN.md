# M1.12 Plan — Permission-aware turns: scoped roots + ask-handling + silence watchdog

Status: **done** (2026-09-10). Live gate green (3 scenes, `opencode-go/glm-5.3-flash`): scoped root passes silently; outside read → question → allow-once → real content; reject → loud abort. Rulings user-locked 2026-09-10:
1. Unknown outside-cwd paths **ask the human** (blocking, **no timeout** — the M1.11 ruling extends to permission prompts). Sweave controls the loop so Sweave controls the timer: opencode waits, Sweave waits, like `ask_human`.
2. **Allowlist roots pass silently**: project working dir + subfolders; worktrees (specialist: its own, orchestrator: all); global `~/.sweave` (required — the hung reads were global `agents.yaml`; per-project `.sweave` is inside the cwd, already covered).
3. **Transition**: blanket `external_directory: allow` (shipped, see starting point) stays UNTIL ask-handling works; then flip to scoped roots. Never leave a path resolving to `ask` with no handler.
4. **No sqlite scraping in production.** Liveness/permission signals come from the wire + HTTP API only (opencode's DB was forensics tooling, not a runtime seam).
5. Watchdog and turn timers **suspend while a human question is outstanding** (no-timeout ruling); a `doom_loop` and other ask-defaults keep defaults until one proves it hangs (each gets its own entry then, never a wildcard).

## Starting point (verified against code + live evidence)

**The incident.** 2026-09-10, session `Sweave-20260909-224620-1e451c` (stream-probe), opencode session `ses_f773d1fcaffeODlPHGSxu9cQbT`, model `openrouter/thinkingmachines/inkling:free+high`:
- `chat-f65e5774b8cb` (01:48): two tool steps complete, then `bash: cat ~/.sweave/agents.yaml …` stuck `status=running` forever → our 300s httpx trip → `[chat error: ReadTimeout: ]`.
- `chat-f94bd453c376` (01:53, pure retry, same engine session): steps complete, then `bash: Get-Content $env:USERPROFILE\.sweave\agents.yaml` stuck `running` → timeout.
- `chat-1389698ec0e2` (01:59): serve recorded NOTHING (session wedged behind the previous hung tool) → timeout.
- Reproduced live 2026-09-10 on a scratch `opencode serve` 1.18.29 (default policy, outside-cwd `Get-Content C:\Windows\win.ini`): tool `running`, zero bytes, zero tokens, never completes. Scratch serve destroyed after; no orphans (`taskkill /F /PID 15800`, verified).
- Mechanism (upstream issues #14473/#16367/#36762, same signature): headless `serve` has no UI, so any check resolving to `"ask"` waits forever. The `bash`/`read`/`edit`/`glob`/`grep` tools check `external_directory` (outside-cwd → default `ask`); both hung commands touched `~/.sweave/*`, every completed tool touched in-cwd files. No permission part/error ever surfaces (blocks inside the tool call).

**Already landed in the working tree (UNCOMMITTED — preserve it; the tree also holds a parallel session's changes, do not sweep them):**
- `sweave/harness/opencode.py`: httpx 300→1000s (turn_timeout binds, clear message wins).
- `sweave/runtime/specialist_runtime.py`: `info.error` surfacing (`[chat error: …]`, trace `info_error`); incomplete-turn error; `_split_json_stream` carry-over (`(pieces, leftover)`); stall watchdog (`STALL_TIMEOUT_SECONDS=300`, `stall_seconds` param, trace `stalled`, `[chat error: stalled after …]` marker); mirrored in `harness/opencode.py send()`.
- `sweave/chat/loop.py`: `STALE_SESSION_ERROR_MARKERS` + `_is_stale_session_error` + `session_rotated_after_stall` (fresh engine session after silence-class failures; auth/model errors keep the binding).
- `sweave-web/.../Thread.tsx`: `quietSeconds` + "quiet Ns" badge (≥10s) in the turn status bar; `CopyId` badges/buttons (session/delegation copy).
- `sweave/runtime/serve_runner.py` + `server.py` + `stop_server.py`: serve PID tracking (`~/.sweave/serves.json`), boot reclaim (dead-owner + port-probe verified), lifespan `shutdown_all`, 5-min idle sweeper, tree-kill shutdown + `/T` stop.
- `sweave/runtime/mcp_config.py`: managed top-level `{"external_directory": "allow"}` (`_ensure_top_level_permission`; user-owned blocks never overwritten, warned).
- Tests: 3 runtime (info_error/incomplete/split-glue) + stall + splitter-contract updates + rotation (3) + permission policy (3) + tracking/reclaim (6) + quiet (3). Gate at write time: 564 pytest (2 pre-existing models-registry env failures), 159 vitest, build green.

**Approval channel (PINNED live 2026-09-10, step-0 probe; see Execution summary 1):**
- **Listing surface = the `/event` bus ONLY.** Bridge event `permission.asked` (subscribe BEFORE the turn — live-fire only, no replay): `{"id": "per_…", "sessionID": "ses_…", "permission": "external_directory", "patterns": ["C:\\Windows\\*"], "metadata": {"command": "…", "directories": ["…"], "patterns": ["…"]}, "always": ["C:\\Windows\\*"]}`. NONE of the pending-list GET routes (`/session/{sid}/permissions`, `…/permission`, `/api/*`, `/api/permission`) exist in 1.18.29 serve — they all return the SPA HTML catch-all (200 HTML!). The 2026-09-10 `{"data":[]}` report matches; the bus is the only surface.
- **Reply**: `POST /session/{sid}/permissions/{rid}` body `{"response": "reject"}` / `{"response": "once"}` / (`"always"`) → 200 body `true`. Confirmations arrive on the bus as `permission.replied` `{sessionID, requestID, reply}`. (Key is `response`, NOT `reply`; other key → 400 `Missing key at ["response"]`.)
- **Resume-after-once works**: tool executes; terminal content lands as a **NEW assistant message** (pre-pause assistant message completes with EMPTY text at the pause; post-resume message carries the content, completed-ts set) — session goes `session.idle` on the bus. The ORIGINAL `POST /session/{id}/message` stream does not re-deliver the terminal frame: ended-stream + `session.idle` on the bus is the completion signal to handle in step 2 (fetch `GET /session/{sid}/message` for the final text — route exists, returns the full message list with parts).
- **Reject shape**: turn aborts; the assistant message persists with completed-ts and empty text (no loud tool error in the message list); `session.idle` on the bus. Treat reject as a failed turn.
- Probe script committed: `scripts/m1_12_permission_wire_probe.py` (retrying scene loop — models are not tool-compliant every turn; defaults to a live provider model via `/config/providers`, overridable `M1_12_PROBE_MODEL`).
- **Matcher semantics (supplementary pinning, from the installed binary):** the evaluate step is `rules.flat().findLast(...)` → **LAST matching rule wins** (catch-all first, specifics after — the plan's ordering was right). Checked patterns are generated as `path.join(dirname(file), "*")` with platform separators and `normalizePathPattern` only normalizes that `dir/*` form; rules must therefore use **platform separators** and BEST match the checked idiom. The render emits both `<root>{sep}*` and `<root>/**` per root (step-4 live gate proved the scoped root silently passes with this shape). Also: opencode's message stream may deliver a single newline-free JSON document — always parse with brace-depth splitting (`_split_json_stream`), never line-splitting (the live gate initially "saw nothing" because of this).

## Goal state

1. Rendered `opencode.json` carries **scoped** `external_directory` roots (orchestrator: cwd + all worktrees + `~/.sweave`; specialist: own worktree; + user-declared shared roots surface — location TBD in step 1) with user-owned blocks still never overwritten.
2. A pending permission for our session becomes a **blocking Sweave question** (M1.11 machinery: no timeout, skip, synthesis note, inline card) and the answer is POSTed back; the still-open stream resumes. Applies to **both roles** (ruling 2026-09-10: specialists route to the human too).
3. Stall watchdog + `turn_timeout` **suspend while the question is outstanding**; UI quiet badge already shows the wait.
4. Blanket `allow` flipped to scoped **only after 2–3 work**; live repro gate (outside read → question → allow-once → completes). User-declared shared roots live as a **project record field** (human-declared only), surfaced in step 1.

## Steps

0. **Wire-shape probe pinning** (~0.5 session). Scratch serve (default `ask` policy, temp cwd): trigger outside read, capture request ID via candidate list routes + fresh `/event` subscription (subscribe BEFORE the turn this time); reply `reject` with message, observe turn failure shape; reply `once` on a second turn, observe resume. Commit the working shapes as a repo probe script (`scripts/` — the 2026-09-10 probes lived in `%TEMP%\opencode\`, NOT in the repo; rewrite, don't reference temp). Gate: documented request/reply JSON for 1.18.29 in this plan (amendment) + a mock-transport test of the parser.
1. **Roots surface + scoped render** (~0.5 session). Decide where user roots live (recommend: project record field, orchestrator-only + named shared roots for specialists; do NOT reuse `worktree_base`). `mcp_config.py`: scoped patterns (`~` expansion per docs; last-match-wins, specifics after catch-all) + `_sweave_managed` refresh; user-owned blocks still win. Gate: render tests (scoped merge, user preservation, idempotence).
2. **Permission poller + question kind** (~1 session). On watchdog silence (and only then — no new background tasks): `GET` pending list for the turn's engine session; match by session (callID optional correlation). Hit → `EscalationStore.create(kind="permission", audience="human", timeout None)` carrying `{requestID, permission, patterns, tool, callID}`; ChatLoop treats it like a blocking question (reuse `_wait_for_escalation` by kind, or a parallel waiter — prefer reuse); answer/skip → POST reply (`once`/`always`→answer, `reject`→skip-with-message). New `kind` value = Delegation-schema-bump rule does NOT apply (escalation records, not Delegation — but check `escalation.py` versioning anyway). Gate: mock-transport tests (pending→question→reply→resume; skip→reject; no pending→unchanged stall path).
3. **Timer suspension + UI** (~0.5 session). `_send_message`: suspend stall countdown while a permission request for this turn is unresolved (asked-event or poll hit; cleared on stream resume). `_run_orchestrator_turn`: on `TimeoutError`, re-arm while the runtime reports an unresolved permission (bounded by nothing — user ruling). UI: permission question renders in the existing inline Question card (extend `TurnQuestions` by kind or add `TurnPermission` — prefer extension; WS-driven). Gate: tests (suspend-then-resume; timeout re-arm; card renders request summary + Allow once/Always/Deny).
4. **Flip + live gate + docs** (~0.5 session). Blanket→scoped render; scratch-serve live scene (outside read → card → allow-once → turn completes with file content; deny path fails loud); DESIGN §4/§8 (permission row), PROJECT_STATE M1.12 entry, GOTCHAS (ask-flow + reply shapes). Gates: full pytest, vitest, `npm run build`, `run.py --check`, suite 2× green.

## Explicit non-goals

- MCP `read_external` tool (the considered alternative): deferred — revisit only if ask-flow friction proves high in dogfood. Rationale recorded: MCP calls bypass opencode gates (deterministic, auditable, engine-agnostic), but cost model confusion + a second file-access path; the ask-flow keeps one path.
- `doom_loop` + other ask-defaults: untouched until one proves it hangs (own entry each, never a wildcard).
- Blanket `"*": "allow"` anywhere, ever.
- Sqlite polling of opencode's DB in production (forensics only).
- Auto-answering permissions without the human (no `always`-by-default; `always` only from an explicit user choice, persisted as opencode approved patterns).

## Open questions for the executor — RESOLVED by user ruling (2026-09-10)

1. Specialist permission prompts: **route to the human too** (user ruling, overriding
   the auto-deny recommendation). A specialist hitting an outside-root permission
   becomes a blocking human question the same as an orchestrator prompt.
2. User-declared shared roots: **project record field; human-declared only**
   (specialists never nominate roots). Orchestrator-only roots + named shared roots
   for specialists both live there.

## Risks

- ~~**v1/v2 permission store split**~~ RESOLVED by step 0: the bus is the sole listing surface and the reply route fires in serve mode (see pinned shapes). No v1 fallback needed. Remaining: unknown routes return SPA HTML (must distinguish "route exists" from "200 HTML" wherever Sweave GETs opencode).
- **Reply doesn't resume the hung tool** (serve bug class #36804: session stuck "busy" forever): mitigate with the existing stall-rotation (fresh session) as fallback — a replied-but-stuck turn still fails loud, never silent.
- **Approval fatigue**: every unlisted outside path now interrupts. Mitigate: `always` persists patterns; roots list grows from the questions asked (log them; consider a "bless this root" shortcut as follow-up, not this slice).
- **`always` persistence scope**: opencode stores approved patterns per project — a careless `always` widens silently. Mitigate: card labels `always` with its pattern list; audit event records the granted patterns.
- **Model confusion on reject**: reject-with-message surfaces as tool feedback; models may loop re-trying. Mitigate: message text directs to in-cwd alternatives; doom_loop default still guards true loops.

## Evidence appendix (repro anchors)

- Hung bash inputs: `cat ~/.sweave/agents.yaml 2>/dev/null || echo "No global agents.yaml"` (01:48:22, `ses_f773…`, call `call_3947…`); `Get-Content "$env:USERPROFILE\.sweave\agents.yaml"` (01:54:10, call `call_23e1c4…`). Both `state=running`, no end time; completed tools in the same session touched in-cwd files only.
- Delegations: `chat-f65e5774b8cb`, `chat-f94bd453c376`, `chat-1389698ec0e2` (all `[chat error: ReadTimeout: ]`, ~300.0s gaps in traces).
- Live repro 2026-09-10: scratch serve (default policy), `Get-Content C:\Windows\win.ini` → same signature; serve destroyed after.
- Timeout archaeology: httpx 300s (now 1000, `harness/opencode.py`), turn 900s (`chat/loop.py`), stall 300s (`specialist_runtime.py STALL_TIMEOUT_SECONDS`).

## Execution summary (2026-09-10)

1. **Step 0** — wire pinned (see the PINNED section above). Committed `bb4868f`.
2. **Step 1** — `render_external_directory` + Project.permission_roots (+ `PUT /api/projects/{name}/permission_roots`). Committed `a7cc9a1`.
3. **Step 2** — `runtime/permission_watch.py` (per-serve bus watcher, no new poller tasks; the standing SSE subscription is required because 1.18.29 has no pending-list route), the ask-dance in `SpecialistRuntime._send_message`'s stall branch (create kind=permission escalation w/ metadata requestID → unbounded wait → reply → idle-wait → recover via `GET /session/{sid}/message`), `escalation_store` metadata field. Mock tests: answer→always, skip→reject, no-pending→plain stall. Committed in `f03eb56` together with the M1.11 execution delta (user ruling: bundled).
4. **Incident note** — during step 2 the executor truncated `sweave/runtime/specialist_runtime.py` working-tree state to zero bytes (an unguarded identity rewrite). Recovered from HEAD (679 lines) + an opencode-transcript full read of the M1.11 state (854 lines) + re-applied step-2 edits; suite re-verified. Gotcha recorded in `docs/GOTCHAS.md`.
5. **Step 3** — turn-timer suspension (shielded re-arm while a pending human question exists; full budget restarts after each resolution) + permission kind on the inline question card ('always allow' grants exactly these patterns). Committed `ae21de1`.
6. **Step 4** — flip catch-all → ask; matcher semantics corrected from the binary (`findLast` last-match-wins confirmed; platform-separator + `dir/*` + `dir/**` root rules); live gate `scripts/m1_12_live_gate.py` GREEN with `opencode-go/glm-5.3-flash` (scene A: silent scoped-root pass; B: once → real content; C: reject → loud, tool bash error). Trailing suite: 579 pytest (+2 pre-existing env), 161 vitest, build green. Committed `6d9e8c2` + close-out.
7. **Explicit non-goals kept**: no MCP read_external tool; doom_loop/other ask-defaults untouched; no sqlite scraping; no auto-answer (always only from explicit user choice, patterns persisted opencode-side and shown on the card).

## Amendment 1 (user-locked 2026-09-10): in-band permission bridge via opencode plugin

**Incident that forced the amendment.** Session `Sweave-20260910-071906-787887` (dogfood, 07:24 local): two consecutive chat turns died with `[chat error: orchestrator turn exceeded 900s timeout]`. The orchestrator, investigating the global-config-pollution question, touched `C:\Users\user\.config\opencode\*` → `external_directory` → `ask` → `permission.asked` (ids `per_089fe28a2001JFuGT8UYG6mLM5` at 07:25:30 and `per_08a0e10ea0015G5M7tz3jo9jUy` at 07:42:52, opencode.log UTC+1). The designed step-2/step-3 recovery (300s stall watchdog → `_resolve_pending_permission` → blocking human question) **never fired**: no `stalled` trace event, no escalation record (`GET /api/delegations/{id}/escalation` → 404), no `turn_timer_suspended`. Root cause: the ask→question bridge was placed out-of-band (Sweave's Python loop) and gated on a *silence* watchdog that either never trips (stream trickling defeats byte-silence) or races the ask-dance; either way, an ask = an unattended 900s death.

**User ruling 2026-09-10 (this session):** we do not abandon ask-for-a-human, we do not add MCP file tools (the deferred `read_external` stays deferred), and we hijack the ask so a permission question always reaches the Sweave UI. Since omnigent runs headless with allow/deny policies and can't ask (their PR #1776 notes an ASK verdict in headless has no human), our harness *can* now ask — the bridge belongs inside the opencode process, not in Sweave's loop.

**Change: preference order inverts.** The per-project rendered opencode.json now provisions a plugin at `.opencode/plugins/sweave-permission.ts` (project-level plugin dir; auto-loaded at serve startup, same render as opencode.json). The plugin subscribes `permission.asked` in-process and resolves the ask synchronously:

1. POST `{base_url}/api/permission/hijack` (Sweave's own server, token-protected) with `{directory, sessionID, requestID, permission, patterns, metadata}`.
2. Sweave scope-evaluates against the project record (cwd subtree, worktrees, `~/.sweave`, `Project.permission_roots`) — the project record becomes the single source of scope truth, replacing out-of-band pattern math in the render.
3. In-scope → plugin replies `once` (in-scope reads should rarely reach the plugin because the scoped `external_directory` render still allow-silently-passes; the plugin is the safety net, not the gate).
4. Out-of-scope → blocking human question, kind=permission, no timeout (M1.11 ruling) → the answer lands in the plugin → POST `response` to the pinned wire `POST /session/{sid}/permissions/{rid}`. `reject` surfaces as a tool error to the model (loud, recoverable), never a silent hang.
5. The out-of-band ask-dance (stall watchdog → `_resolve_pending_permission`) is RETAINED as fallback for the plugin-absent / plugin-crashed case; it's no longer the primary path (noted flaw: the same failure mode above still gates it — repair follow-up if incidents recur).

**Steps.**
1. Plugin generator in `mcp_config.py` (template + ensure-on-render, `_sweave_managed` marker) + plugin SDK contract (packet content) → `permission.asked` subscribe → POST to sweave → POST pinned reply. Writes to the project dir next to opencode.json.
2. Endpoint in `web/server.py`: `POST /api/permission/hijack` (mcp-token guarded) — resolve project by directory; create escalation (kind=permission, no timeout); map answer: allow → `once` (or `always` per user answer), skip → `reject`. Reuses EscalationStore (M1.11). Tests: mock-store mock/mock; scope logic unit tests; plugin render idempotence.
3. Gates: pytest (>= 579 pass rate), `run.py --check`; vitest + build only if UI touched (it is NOT). Fallback: if live verification impossible on 1.18.29 pinned serve, plan a dedicated M1.12.5 step-5 live gate following `scripts/m1_12_live_gate.py` pattern (3 scenes).
4. Docs: DESIGN §4 permission row amended (in-band bridge added); PROJECT_STATE + GOTCHAS out-of-band-bridge flakiness + incident record.

**Status: DONE (2026-09-10, amendment 1 executed).** Plugin bridge shipped
(`sweave/runtime/permission_bridge.py` + `.ts`, `OPENCODE_CONFIG_DIR` island
injection in `ServeRunner.start`, `POST /api/permission/hijack` in
`web/routers/mcp.py`, session registry in `SpecialistRuntime`); gates:
587 pytest pass (+2 pre-existing env fails), `run.py --check` 13/13.
Commits `0113ec8` (amendment) + `9c0aa87` (execution). Live gate for the
bridge path (hijack-route scenes on a pinned 1.18.29 serve, extending
`scripts/m1_12_live_gate.py`) is the remaining follow-up.

## Amendment 2 (user-locked 2026-09-10): defer-beacon liveness + turn-cap extension + effort-variant preservation

Trigger: session `Sweave-20260910-092707-3b37fd` — the backend child ran
~15 min of real streamed work opencode-side (150 assistant steps in the
serve DB) but sweave's 900s cap killed it mid-task and the chain gate
then rejected the re-dispatch. Two user rulings ride along:
(a) the `+variant` effort selection is NEVER silently dropped — the
bare-suffix form is invalid on provider catalogs (live probe:
structured `variant` field → 200; `model+suffix` → 500/NotFound);
the legacy spec path now parses the string via the same ModelRef
parser and emits the structured wire (live probe verified). Legacy
spawn env strips the suffix instead of delivering a dead default.
(b) opencode gives sweave NO streaming liveness mid-turn, but a
`defer` call from a running turn IS observable: submit_task_v2 now
appends a `child_deferred` beacon to the CALLER's trace file
(schema-free) + emits it on the WS bus; JobRunner's turn cap
(`_bounded_turn`) re-arms a full budget instead of killing when a
beacon landed inside the beacon window (max 3 extensions), shielded
so the in-flight work is never cancelled mid-extension.
Re-dispatch gate (chain-active check must exclude terminal states)
+ UI overwrite of the pre-deferral assistant message remain open
follow-ups (owner: M1.7/ChatLoop hardening).

