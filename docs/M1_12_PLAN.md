# M1.12 Plan — Permission-aware turns: scoped roots + ask-handling + silence watchdog

Status: **planned** (2026-09-10). Rulings user-locked 2026-09-10:
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
