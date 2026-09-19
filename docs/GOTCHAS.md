# Gotchas — burned us once, don't relearn

Each group below is one branch of work. When a group's trigger fires (the trigger
table in `AGENTS.md` points here), read the whole group before you start. New
gotchas land here — grouped by branch, not appended as a numbered list.

## Opencode harness & wire protocol

1. **An engine `permission.asked` is answerable ONLY via the escalation JSON its round-trip keys to — and that JSON is invisible to every UI surface** (2026-09-18, fix child `42a848a03cf0`). Two transports exist and they are NOT symmetric:
   - opencode serves → in-band plugin ferries `permission.asked` to `POST /api/permission/hijack` → `resolve_hijack_request` creates the blocking escalation (kind=permission) and waits/polls on it;
   - the native engine sidecar → `loop.js` emits the SSE event and calls `callEnginePermission` (`sweave.js`) → `POST /api/engine/permission` (`wait:false`) + `GET /api/delegations/{id}/escalation` polling.
2. Symptom when the round-trip is missed: the SSE `permission.asked` rides the trace (looks handled), `waiting_with_progress` holds the turn open with `holds: 0` (supervisor sees quiet, no pending question anywhere), the delegation store has no escalation record under any project (the ask was keyed to the bare delegation id, not a project store), and `~/.sweave/escalations/{id}.json` is the ONLY place the live ask exists. The UI answer card never renders (the Children lane reads project stores), so the human sees a stuck turn with no button — and reads the run as discarded when it is WEDGED, not dead. The turn does NOT end: the engine-side approval cache holds it open indefinitely (no deadline per M1.11).
3. **Keying: engine asks key the escalation under the delegation id string only** (`resolve_engine_permission` takes `payload.delegation_id`), while the opencode bridge keys through `lookup_session` and resolves project + roots for scope. A bare-keyed record is invisible to surfaces that scan project stores for `kind=permission` pending asks.
4. **Verify by effect, never by trace presence**: a `permission.asked` trace event proves the ask was MADE, never that it is ANSWERABLE. Check `~/.sweave/escalations/{id}.json` (status `pending` + `response` null) + `GET /api/delegations/{id}/escalation` before assuming the ask is live for the human — and remember the GET is flat-keyed too: it finds the record, the UI's per-project scan does not.
5. **Recovery meanwhile (manual):** `POST /api/delegations/{id}/answer` (or `/skip` with confirmed) against the escalation the engine created; the child is still running underneath (engine-side approval map holds the turn open — nothing died) and proceeds on the answer. A deny/skip forces the tool call to fail, which is the honest path for an out-of-scope scratch write (`C:\tmp\...`): the fixer writes in its own worktree or fails, never escapes.

## Server ops — start/stop/restart, ports, config reload

1. `start_server.py`/`stop_server.py` resolve `web.pid` **relative to CWD** — run them
   from the repo root only, or they silently no-op while an old server keeps serving.
2. **A stale server blocks `start_server.py` silently** (M1.2 + M1.8 lessons). A previous
   `python start_server.py` that wasn't stopped leaves port 8100 bound. The new
   `start_server.py` still prints "Server started" and writes a PID, but fails to bind
   ("ERROR: [Errno 10048] only one usage of each socket address" in `web_err.log`); the
   chat runtime then hits a real opencode on `127.0.0.1:4096` instead of the mock,
   producing 500s and HTTPStatusError tracebacks. Always run `python stop_server.py`
   before `start_server.py`, verify `python` processes are actually gone (kill by PID
   if not) and that `Get-NetTCPConnection -LocalPort 8100` is empty. The mock behavior
   (`SWEAVE_MOCK_OPENCODE=1`) is gated on the **server's** environment, not the
   script's — a stale server without the env var will silently consume the test.
3. `ProjectManager` is **AppState-owned and loaded once at server startup** — editing
   `~/.sweave/config.json` while the server runs does nothing until restart.
4. Restart semantics: the SPA is served from disk (`sweave-web/dist`), so **Python
   changes need a server restart**; UI changes need `npm run build` + hard-reload
   (Ctrl+Shift+R).
5. **Stopping/restarting the server used to orphan every opencode serve**
   (2026-09-10: 13 stale serves, ~4.8GB, `serve --port 0` with cwd = project root).
   Three stacked causes: nothing ever called `sweep_idle` / `shutdown_all` /
   `sweep_orphan_serves` (all defined, zero callers); `stop_server.py` killed only
   the python PID (no `/T`); and `find_orphan_serves` requires `.worktrees` in
   cmdline/cwd, which project-root orchestrator serves never contain (plus psutil
   was never installed, so it no-op'd anyway). Fix: PID tracking in
   `~/.sweave/serves.json` + boot reclaim (dead-owner + port-probe verified) +
   lifespan `shutdown_all` + 5-min idle sweeper + `taskkill /F /T` in
   `stop_server.py`. Concurrent second servers are safe (live-owner entries are
   skipped). Your own `opencode` TUI / `:4123` serve are never touched (only
   tracked PIDs are ever killed).
6. **Ephemeral test servers: isolated home + never the user's port**
   (2026-09-13). A scratch server for live gates boots with an
   ISOLATED home (`$env:HOME`/`$env:USERPROFILE` = tmp dir — the
   child inherits process env; a hashtable built but never passed to
   `Start-Process` does nothing, and the server silently uses the
   REAL home, sweeping real state). Verify isolation by effect
   (token file appears under the tmp home), never by assumption.
   Never bind the user's live port; never `start_server.py` for a
   scratch instance (it shares `web.pid`/`web.log`); PID-scope every
   kill and confirm the user's server answers after.
7. **Backend fixes are invisible until THAT server restarts + never
   mutate live stores out-of-process** (2026-09-14, two live bites).
   (a) The user's server (PID 27388, booted 01:12) predates the
   working tree: `GET /api/harnesses` still listed only `opencode`
   and new endpoints 404d while the UI (vite dev, live source)
   already showed the new surfaces. Symptom pattern: UI offers what
   the API doesn't have. Fix is a restart (no turns were running;
   check `/api/delegations?status=running` first), never parallel
   editing. (b) A scratch create/delete probe via the API left
   `zz-debug-harness` in server MEMORY after the file was cleaned
   directly — the server re-lists it until restart; file edits lose
   to in-memory stores on the next upsert. Probe live state with
   no-op reads; when a write probe is unavoidable, do it through
   the API and delete through the API. (c) Mis-scoped records:
   `Sweave/.sweave/agents.json` holds full `scope="global"` copies
   (pre-2026-09-11 shape) — the list shows "global", but a
   scope-hinted global lookup skips the project file, falls to the
   seed view, and PUT 400s "seed view ... cannot be edited here".
    `SpecialistResolver.locate()` (project -> global -> seed by FILE
    LOCATION) is the single lookup for PUT/DELETE write-back; never
    trust the scope label for routing a write. (d) A "restart" is not
    a restart until the NEW process owns the port (2026-09-15: the
    stats page served the pre-1b payload shape after a restart —
    `undefined.toFixed` blank-screened the UI — because the old
    process was still serving while the new one died on the port;
    `web.log` even showed the new banner AND the old process's
    requests in one file). Verify by effect: process start time newer
    than the code commit + the response shape actually changed
    (`GET /api/stats/summary` keys, not just HTTP 200).
8. **Seed views vs raw overrides + cross-harness session ids**
   (2026-09-14). (a) The global store holds hollow `scope="seed"`
   override records (old ones with stale baked `harness` defaults):
   `list_resolved`/`resolve` must serve the MERGED `_seed_view`,
   never the raw record — and `harness_override=None` means "inherit
   the YAML", the only state that distinguishes "user chose" from
   "never chose". Both setters (`set_seed_model`/`set_seed_harness`)
   must carry the other dimension over, or they wipe it. (b) Engine
   sessions are `eng_*`, opencode `ses_*`: attaching a foreign id
   mints a charter-less fresh session under that string (silent
   amnesia) — `_engine_session_resume()` spawns instead. History
   never transfers across harnesses by design. (c) The opencode side
   needs the mirror: `_ensure_session` must ignore non-`ses_*`
   stored ids WITHOUT verifying (a foreign id can answer non-404
   and then die in `_send_message`'s guard — live 2026-09-14:
   `eng_*` via the engine→opencode fallback), and only HTTP 200
   reuses (404 + anything else recreates). Fixture note: mock ids
   must be `ses_*` with underscore — `ses-foo` (hyphen) is foreign
   by the same rule and silently takes the create path. (d) The
   automatic engine→opencode retry is REMOVED (2026-09-14, clarity
   over obscurity): an engine-selected turn that fails fails loud,
   full stop — a retry would start a history-less session, bill
   twice, and misattribute the error (all three observed live the
   same night).
9. **Stopping/restarting the server used to orphan the engine sidecar
    too** (2026-09-14: a pile of `node.exe` "Node.js" entries in Task
    Manager surviving server stops). Same shape as item 5 but a second
    leak with its own causes: the lifespan shutdown reaped opencode
    serves and never touched the sidecar, and `CREATE_NO_WINDOW`
    children aren't console-attached, so closing the terminal (or
    Ctrl+C, which runs lifespan WITHOUT a sidecar teardown) left the
    node process behind while every restart spawned a fresh one.
    Killing the strays by hand is safe (sessions persist in
    `~/.sweave/engine/sessions.json`; the next engine turn spawns a
    fresh sidecar lazily). Fix mirrors the serve reclaim:
    `~/.sweave/engine-sidecar.json` PID tracking (written on spawn)
    + boot sweep (adopt a live version-matching sidecar, else kill a
    stale pid ONLY when its command line is our `serve.js` — never
    kill on pid alone: recycled pids and other Node apps are left
    alone) + lifespan `shutdown_sidecar` (terminate, force on
    timeout; adopted/URL sidecars untouched). `stop_server.py`'s
    `taskkill /F /T` already covers the tree-kill path.

## Opencode harness & wire protocol

0. **M1.12 permission wire (2026-09-10, pinned + live-gated).** The ONLY pending-
   permission surface in 1.18.29 serve is the `/event` bus — every candidate list GET
   returns the SPA HTML catch-all with 200 (filter on `content-type`/`<` prefix, never
   trust 200). Reply is `POST /session/{sid}/permissions/{rid}` body
   `{"response": "once"|"always"|"reject"}` (key `response`, NOT `reply`; other key →
   400 `Missing key at ["response"]`) → 200 `true`. Root-rule patterns: last-match-wins
   (`findLast` in the binary), checked patterns are `path.join(dirname(file), "*")`
   with platform separators; render rules with `<root>{sep}*` (+ `**`), never
   forward-slash on Windows. After an allow reply the tool runs and its terminal
   content lands as a NEW assistant message — the old stream doesn't re-deliver
   terminal; wait for bus `session.idle`, then `GET /session/{sid}/message`. And
   opencode may deliver the WHOLE message stream as one newline-free JSON document —
   ALWAYS parse with brace-depth splitting (`_split_json_stream`), never `"\\n"`
   line splitting.    Free-model providers can hang entirely (zero stream bytes for
   minutes on ANY prompt) — don't diagnose that as a permission hang; permission
   hangs always come with a `permission.asked` event.

0b. **M1.12 amendment 1 — the ask→question bridge is IN-BAND now
   (2026-09-10)** (corrects 0/6's assumption that the out-of-band stall
   branch is reliable). Incident: session `Sweave-20260910-071906-787887`
   — two 900s turn deaths on a LIVE `permission.asked` with NO `stalled`
   trace and NO escalation record. Why the old net failed is still
   undetermined (byte-silence watchdog never tripped, or scheduled
   branch never resumed) — DON'T trust silence-based ask recovery.
   The primary path: plugin `sweave/runtime/permission_bridge_plugin.ts`
   → island `~/.sweave/opencode/plugins/` → injected ONLY into sweave
   serves via `OPENCODE_CONFIG_DIR` (`ServeRunner.start`). Standalone
   opencode NEVER executes it (user ruling: a plugin is code —
   project-dir `.opencode/plugins/` would auto-run in every session and
   could block standalone asks on a dead sweave server; agents/MCP
   config entries are inert clutter and may stay in the repo).
   Plugin contract: ferry only, never reply itself (`event` hook,
   fire-and-forget fetch to `POST /api/permission/hijack`, token via
   `process.env.SWEAVE_MCP_TOKEN`, sweave URL via
   `SWEAVE_HOST`/`SWEAVE_PORT`); the reply POST is sweave's job
   (it owns the serve URL from the session registry
   `permission_bridge.register_session` — added in
   `SpecialistRuntime` after `_ensure_session`).    Scope decision =
   project record (`Project.permission_roots`) evaluated in
   `resolve_hijack_request`; in-scope → auto `once`, out-of-scope →
   blocking human question (the runtime's stall branch stays as
   FALLBACK only). Gotchas if you touch it: the island is derived at
   call time from `Path.home()` (`_island_dir()`) — never cache it in a
   module constant (tests redirect home); a plugin copy failure
   degrades silently to the out-of-band path (logged, never raises).

0c. **Loading the bridge plugin on 1.18.29 (live-gated 2026-09-10).**
   Two pins that CONTRADICT current opencode docs:
   (1) `OPENCODE_CONFIG_DIR` custom directory does NOT load its
   `plugins/` subdir on 1.18.29 (config.plugin stayed empty); the
   seam that works is the `plugin` config ARRAY carrying the plugin's
   absolute path (`["<abs path .ts>"]`, POSIX slashes) delivered via
   the `OPENCODE_CONFIG` env var pointing at the island
   `~/.sweave/opencode/opencode.json`. both env vars are injected
   by `ServeRunner` per spawn (from `ensure_permission_bridge`).
   (2) plugin `console.*` output does NOT appear in the serve stdout
   or opencode's own log — the observable channel is the SDK
   `client.app.log({body:{service, level, message}})` (messages land
   in `~/.local/share/opencode/log/opencode.log` with
   `message="..."`). (3) any FastAPI route defined in a file with
   `from __future__ import annotations` MUST import its annotation
   types (e.g. `Request`) at MODULE level — a function-local import
   leaves the annotation string unresolved and FastAPI silently
   reinterprets the parameter as a required query field (422
   `missing query field "request"`). Live gate: `scripts/
   m1_12_bridge_gate.py` — 3 scenes GREEN (in-scope auto-allow via
   plugin; out-of-scope → escalation → `allow once` → content;
   deny → loud abort). The older `scripts/m1_12_live_gate.py` was
   committed BROKEN (line 318 concatenated `return` onto the
   `print`; compile check now part of the gate routine).

1. **Sessions persist in `~/.local/share/opencode/opencode.db` (SQLite)** (corrects the
   M1.3 step 0 probe — "0 new files" was misleading: a new row was inserted into the
   `session` and `message` tables). The `session_id` persisted on the `Specialist`
   record survives opencode process restarts. The 404-recreate path in
   `_ensure_session` covers a deleted session or changed worktree path. The worktree
   re-injection preamble is still required (cwd binds to the serve process; sessions
   are tied to the cwd at create time via `message.path.cwd`).
2. **Multi-provider config** (M1.3 K-revised): the v2 `POST /session/{id}/message` body
   requires `body["model"]` to be `null` or a structured `{providerID, modelID}`
   object — **bare model names are rejected with 400**. The harness always emits the
   structured pair when the `ModelRef` is complete; the v1 record's bare-string path
   falls back to the unqualified name (with a warning). After rotating providers, run
   `opencode models` to see canonical names.
3. **Mock + wire fixtures MUST emit `info.time.completed` + `info.finish` on every
   response** (M1.9 step 1). The old "parts + role==assistant" heuristic in
   `OpenCodeProcess.send` fired on a delta that happened to include a parts list
   before the assistant message was done — that was the bug. Terminal detection
   requires both flags. New tests that build a stream directly use the
   `_parts_model_stream` helper (sets the terminal flag by default). Any new mock of
   the opencode v2 wire must include the terminal flag from the start or it hits the
   "no terminal flag set" error path.
4. **`SpecialistRuntime._send_message` reads `info.error`, not just parts**
   (2026-09-10 rerun incident). The runtime bypasses `OpenCodeProcess.send` and
   parses the stream itself — it collected `text` parts only, so an upstream
   rejection on a 200 stream (401 CreditsError after a model switch, zero text
   parts) returned `""` as SUCCESS and the chat loop persisted an empty assistant
   message (turn "did nothing"; the error was visible only in opencode's sqlite
   `message.data.error`). `_send_message` now mirrors the harness: `info.error`
   (any turn) → `[chat error: <name>: <message>]`, no-text + no-terminal →
   incomplete-turn error. Ground truth for "what did the serve actually record"
   is `~/.local/share/opencode/opencode.db` (`message.data.error`, `part` rows).
5. **httpx `client.stream()` yields nothing under MockTransport with a manually
   assigned `ByteStream`** (found while testing #4). To unit-test
   `_send_message` parsing, fake the stream context manager directly (see
   `_send_message_proc` in `tests/test_specialist_runtime.py`), not the transport.
6. **Any permission resolving to `"ask"` hangs a headless serve forever**
   (2026-09-10 stream-probe: two 5-min ReadTimeouts, then a third turn that
   got nothing on the wedged session). The `bash` tool's
   `external_directory` check fires on outside-cwd paths and defaults to
   `ask`; with no UI to answer, the tool sits at `status=running` and
   NOTHING surfaces (no part, no error -- the DB just shows a `start`
   timestamp with no end). Fix is config, not timeouts: the rendered
   per-project `opencode.json` carries a managed top-level
   `{"external_directory": "allow"}` (`_ensure_top_level_permission`;
   user-owned blocks are never overwritten, at most warned about).
   Deliberately NOT `"*": "allow"` -- danger gates stay in the
   per-agent profiles (`runtime/agent_permission.py`). Other
   ask-defaults (`doom_loop`, ...) keep defaults until one proves it
   hangs; each gets its own entry, never a wildcard.
7. **TurnStatusBar hooks order**: every hook (and the pure message
   reads feeding the quiet tracker) must sit above the
   `if (!isRunning) return null` early return -- the bar mounts idle
   and starts later, so anything below the return is a Rules-of-Hooks
   violation that only explodes when a turn starts (caught by the
   ChatLab stream tests, not by idle renders).

## Writing runtime tests

1. **Tests that drive `SpecialistRuntime.run` end-to-end MUST set
   `SWEAVE_MOCK_OPENCODE=1`** (the existing seam) via a module-scoped autouse fixture.
   Without it, `ServeRunner.start()` spawns a real `opencode serve` subprocess, which
   occasionally fails to bind (`RuntimeError: opencode serve exited early`) under
   full-suite load and turns the test order-dependent. The fixture is wired in
   `tests/test_m1_3_step3_job_runner_integration.py`,
   `tests/test_m1_4_5_step1_model_ref_contract.py`, and
   `tests/test_m1_4_5_step2_switch_semantics.py`; a regression test
   (`test_runtime_runner_is_mocked_no_real_subprocess`) pins the invariant. Copy the
   fixture from one of those three into any new runtime-path test file.

2. **Never assert against the real `~/.sweave` from tests — and never
   assume `Path.home` patches are enough** (2026-09-09: every
   `POST /api/projects` in the suite landed `p-*` / `proj-*` junk in
   the REAL `~/.sweave/projects`, 130+ dirs, and hijacked the
   `active_project` pointer). The HTTP stack runs on the import-time
   `project_manager` singleton (`sweave/projects.py:617`), early-bound
   by `sweave/api/projects.py` and `sweave/web/server.py` — an
   already-built object no home patch can redirect. Cover comes from
   the autouse `_isolate_project_manager_singleton` fixture in
   `tests/conftest.py` (tmp-backed singleton, all three bindings,
   per test). If you add a FOURTH early-bound `from sweave.projects
   import project_manager`, extend that fixture. Env-only home
   fixtures (`HOME`/`USERPROFILE` without a `Path.home` patch) are
   banned for home-reading tests — the autouse patch wins and the
   planted files diverge (see `test_m1_9_step4_visibility.py`
   `home_dir`).

3. **Stubs replacing ``SpecialistRuntime._send_message`` must accept
   every optional callback** (2026-09-09: adding ``on_reasoning``
   broke 23 tests across 10 files — every ``fake_send`` with the old
   ``(self, body, trace, on_chunk=None)`` signature). The runtime
   always passes both callbacks; a stub missing one turns every
   turn into ``[chat error: TypeError...]``. When adding a callback
   to ``_send_message``, update all ``fake_send*`` stubs in the
   same change (grep ``async def fake_send`` under ``tests/``).
    3b. **System-send doubles must invoke ``on_chunk``** (2026-09-13).
    ``_bounded_system_send`` waits for the first streamed byte
    (``PRE_MODEL_TIMEOUT_SECONDS``, prod 950s, conftest shrunk to
    1s) before letting the send run on — a double that never calls
    ``on_chunk`` (early ``test_prompt_template.spy_send`` returning a
    bare ``MagicMock``) trips the bound and reads as a stall. The
    real harness invokes ``on_chunk`` on text parts; doubles should
    too (call it once then return).
    3c. **``run()`` doubles must accept the step-4 kwargs + every
    ``run()`` caller resolves the harness FIRST** (2026-09-13).
    ``run(harness=, project_dir=, permission_roots=)`` broke the
    narrow ``_StubSpecialistRuntime.run`` double in
    ``test_job_runner.py`` (same rule as 3: update doubles in the
    same change). And files driving ``run()`` without the item-1
    mock (e.g. ``test_specialist_runtime.py``) silently routed at
    the REAL sidecar after the default flip — green-but-slow via
    fallback, except the timing-sensitive parallel test which went
    red. Symptom: unexpected `harness_selected`/`fallback_used`
    warnings in captured logs + suite slowdown.

4. **Test answerers for escalation flows must be gated, never
   fire-once** (2026-09-11: a fire-once answerer answered the
   pre-created hold in ~10ms — before the 50ms stall even engaged —
   routing the test into supersede-create instead of the reuse path,
   and hanging the suite when the new card had no answerer left).
   Gate on the trace marker that proves the path under test ran
   (``permission_reused`` / ``permission_hold_wait`` /
   ``turn_soft_limit_asked``), then answer. A pure-creation path
   (late-answer) needs no answerer at all. Symptom of getting this
   wrong: the suite hangs with zero output (faulthandler shows an
   idle loop — coroutine frames are invisible).

## MCP surface

1. **The auth token is the only seam between the sweave MCP server and the API**
   (M1.6). Both the stdio server (`sweave/mcp/`) and the API router
   (`/api/mcp/specialists`) read the shared token at `~/.sweave/mcp_token`
   (auto-generated on first call, mode 0o600 best-effort). It is **localhost-only** —
   the only thing standing between the MCP surface and the endpoint. End-to-end tests
   on the MCP path MUST set `Path.home` to a temp dir (the token path derives from
   `Path.home()`; don't overwrite the real user's token). The TestClient fixtures in
   `tests/test_m1_6_step1_mcp_server.py` and `tests/test_m1_6_step3_orchestrator_wiring.py`
   already do this. The token is also passed via `SWEAVE_MCP_TOKEN` env var (set by the
   per-project opencode.json `environment` block) so opencode-spawned subprocesses read
   it without touching the home file.
2. **Per-project opencode.json plumbing is idempotent** (M1.6). `ensure_mcp_config(project_dir)`
   writes/refreshes the project's `opencode.json` so the orchestrator's serve session in
   that cwd sees the sweave MCP server. The `_sweave_managed` marker inside the
   `mcp.sweave` entry is the contract: presence = sweave wrote it, absence = user wrote
   it (leave it alone). New fields for the sweave MCP entry go in `_sweave_mcp_entry`
   in `runtime/mcp_config.py` — the single source of truth. `M1.6_DISABLE_MCP_PLUMBING=1`
   is the test/CI kill switch.
3. **MCP handlers register the *params* models, never the request models**
   (2026-09-09: every `tools/call` failed with -32602 "Invalid request
   parameters", so `defer` / `list_specialists` / `ask_human` NEVER worked
   over the wire). The lowlevel runner validates the incoming `params`
   member against the registered type: `CallToolRequest` has a required
   `params` field, so `{"name": ...}` never validates. Register
   `PaginatedRequestParams` for `tools/list` and `CallToolRequestParams`
   for `tools/call`; the dispatcher reads `params.name` /
   `params.arguments`. `tools/list` only worked by accident (all-default
   fields). The stdio round-trip test (`test_mcp_server_stdio_round_trip`)
   pins `tools/call` over the wire — never sidestep it again (the old
   "SDK requestState mismatch" comment was this same bug misdiagnosed).
4. **The lifespan exports `SWEAVE_MCP_TOKEN` into the server env**
   (2026-09-09). The per-project opencode.json sets
   `environment.SWEAVE_MCP_TOKEN = "{env:SWEAVE_MCP_TOKEN}"` (opencode
   expands `{env:...}` in file config); the expansion source is the
   serve's inherited env, so the server must export the canonical token
   or the expansion is empty/stale and every tool 401s. The MCP server
   itself prefers the env var and falls back to `~/.sweave/mcp_token`.
   Tests that boot the lifespan must expect the export (it is restored
   on shutdown); the stdio round-trip test passes the tmp token to the
   child explicitly.
5. **Tool gating lives on opencode-native agents, not in the MCP
   block** (2026-09-09 full cutover). The managed `agent` map
   (`sweave-orchestrator` / `sweave-specialist`) is rendered from
   `sweave/agents/*/config.yaml` + `runtime/agent_permission.py` on
   every project activation; `SpecialistRuntime` pins each turn via
   `body["agent"]`. The orchestrator prompt's single source of truth
   is the YAML (the legacy one-off system send is skipped for the
   orchestrator; specialists keep it for role flavor). Serve-side
   config discovery walks UP the directory tree (the `mcp list` CLI
   does not), so worktree serves see the project file -- agent
   permissions, not file placement, are the isolation boundary.
6. **M1.11: blocking questions replace the native `question` tool;
   specialists escalate via `escalate`, not `ask_human`** (2026-09-10).
   Both managed agents deny `question` (the headless serve cannot
   answer it and Sweave never intercepts it). Specialists deny
   `sweave_defer` / `sweave_list_specialists` / `sweave_ask_human`
   EXPLICITLY (never restore the `sweave_*` wildcard — it would
   re-deny `sweave_escalate`, the only sweave tool specialists may
   call). Questions have NO deadline (`deadline_at=None`); the
   ChatLoop hold-open (`_wait_for_escalation`, unbounded poll) is
   what keeps the turn running — NOT an MCP long-poll (the 30s MCP
   timeout is irrelevant by design). Skip is `POST …/skip
   {confirmed: true}` (409 when unconfirmed); the UI's
   `window.confirm` is the system-issued guard, never LLM text.
7. **Unprovisioned MCP servers list zero tools** (2026-09-12:
   standalone opencode discovers the same per-project opencode.json
   via upward resolution and paid full tool-schema context for tools
   that cannot work without the Sweave API). `_managed_session()`
   (`SWEAVE_MCP_TOKEN` env present-nonempty) gates `tools/list`
   (empty, silent — no MCP-error spam) + `tools/call` (clean
   rejection). Managed spawns always carry the env (lifespan export
   -> opencode.json `environment` block); `{env:...}` expanding to
   empty still gates correctly via `bool()`. Tests asserting the
   managed surface must `monkeypatch.setenv` (never bare
   `os.environ` — leaks mask the unprovisioned pins).

## Windows console flashes + locale I/O

1. **Every subprocess spawn goes through `sweave/platform.py`**
   (2026-09-09: each chat turn flashed 2-3 CMD windows — the transcript
   snapshotter's `git` calls). Console-subsystem children (`git.exe`,
   `python.exe`, `opencode.exe`, `gh`, `docker`) flash a window unless
   spawned with `CREATE_NO_WINDOW`. Use `run_no_window` /
   `check_output_no_window` (sync) and `creationflags_no_window()` for
   `asyncio.create_subprocess_exec`. Children opencode spawns itself
   (the MCP server) can't take our flags: the opencode.json command
   uses `pythonw_executable()` (`pythonw.exe`, windowless; stdio pipes
   work identically).
2. **JSON reads must be `encoding="utf-8"` wherever writes are**
   (2026-09-09: a live assistant reply containing an emoji crashed
   every lifespan `load()` on cp1252-locale systems with an uncaught
   `UnicodeDecodeError` — the next server restart would not boot).
   `atomic_write_json_sync` writes utf-8; `ProjectManager.load` read
   with the locale default. Fixed + pinned by
   `test_load_reads_utf8_session_content`; the load's except clauses
   also catch `UnicodeDecodeError` so one bad file still can't poison
   the boot.

## Delegation & Session schema

1. **Schema-version field gate** (M1.7 + M1.9). `test_v3_field_set_includes_all_m1_1_plus_m1_6_fields`
   in `tests/test_delegation_store.py` pins the public Delegation field set — any new
   field requires bumping `SCHEMA_VERSION` in `runtime/delegation_store.py` AND adding
   a migration helper (`_migrate_vN_to_vN+1`) that defaults the new field for older
   records. (M1.7 step 2's "no schema bump" reasoning was wrong — the gate is real;
   bumping 3 → 4 + `_migrate_v3_to_v4` was the fix, and 4 → 5 + `_migrate_v4_to_v5`
   for `needs_attention`.) Session schema is looser: new fields get
   `data.get("...", default)` in `Session.from_dict` so legacy files load without a
   helper. Check both before adding a field.

2. **M1.13 archive sub-state (2026-09-11 ruling, ARCHIVE-not-delete).** Delegation
   records carry an additive `archived: bool` + `archived_at` timestamp (schema v6,
   `_migrate_v5_to_v6` defaults False/None; v4 records pass through BOTH v4->v5 and
   v5->v6 migration steps — the chain is per-version, not just `if < SCHEMA_VERSION`).
   The boot sweep (`sweave/runtime/delegation_archive.py::sweep_archive_delegations`,
   wired in the lifespan right after `_recover_interrupted_delegations`) archive-flags
   every record whose `project_name` is unregistered or whose workdir is gone;
   records with `project_name=None` (the fallback store's own rows) are NEVER swept.
   The project/session delete endpoints cascade the same flags
   (`archive_project_scope` / `archive_session_scope`) — hooks run BEFORE the
   registry entry is removed so the workdir is still resolvable. Per-project
   aggregates persist at `~/.sweave/archived/{slug}.json`; `source` is derived at
   READ time ("store" rows win over "index" rows), never written into the index.
   GET /api/delegations: `?archived=false|true|all` (default false = archived
   hidden) + `?include_archived=true` adds the `archived_projects` aggregate rows.
   Idempotency gotcha: a second server process (e.g. `run.py --check`) sweeps and
   archives, but the LIVE server still holds its own in-memory copy of the same
   `delegations.json` and rewrites the un-archived records on its next persist —
   the flags re-apply on the next real server restart. Don't "fix" this by
   restarting the live server mid-flight.

## Lifecycle & engine contracts

1. **Chat-loop auto-done is the chat path's job, not JobRunner's** (M1.7 step 3).
   `JobRunner._run` sets `final_status = "review"` on success for implementation
   delegations (M1.4+M1.5 ruling). ChatLoop overrides the chat delegation to `"done"`
   after the final reply — the chat path is the only place that knows it should be
   `done`. Implementation children of a chat turn still stop at `review`
   independently. Any new orchestrator surface (e.g. a `/investigate` API) keeps the
   same split: the surface that owns the lifecycle owns the auto-done.
2. **The runtime transcript composer is the runtime's view, not the engine's**
   (M1.7 step 4). The composed prompt is what the LLM sees from the runtime; the
   engine (opencode today) carries its own session memory on top. The sweave-internal
   engine (side-project, future) is where the runtime fully owns the transcript.
   When adding a new engine, decide: external (composed + engine view) or internal
   (composed only).
3. **Three timers bound a turn — know which one you are changing**
   (incident 2026-09-11: a 17-min silent turn died on httpx's 1000s
   client timeout with a bare ReadTimeout; neither Sweave timer
   fired). Fuse (`_bounded_turn` deadline, 4h default since
   supervisor P3 — pulses govern, the fuse bounds uncertain
   silence); stall watchdog (`_send_message`,
   300s) bounds wire silence incl. header wait; httpx (1000s) bounds
   the socket. Invariant to preserve: a *silent* turn must die on
   the stall bound with a truthful message, never ride out to
   httpx. After changing any of the three values, re-check the
   ordering (stall < httpx < total keeps each failure attributed
   to the right layer).
4. **Escalation records are single-slot per delegation — concurrent
   creators must claim atomically** (incident 2026-09-11: the
   in-band bridge and the stall branch both called `create()`
   unconditionally, and `create()` overwrites — the second finder
   destroyed the first's live record or its recorded answer).
   Same-ask races go through `EscalationStore.create_or_reuse` with
   the ask's request id; exactly one finder owns the reply POST
   (the other waits + recovers). Never add a third `create()`
   caller on the permission path without the reuse key.
5. **Two waits, one rule — never let the settled-sets drift again**
   (M2.1, the `job_runner.py:898` mismatch: `ChatLoop.
   _wait_for_children` settled on done/failed/review while
   `JobRunner._wait_for_children` settled only on done/failed, so a
   child sitting in `review` wedged its parent until
   `turn_timeout`). Both waits now share `JOIN_SETTLED_STATUSES` +
   `in_join_set` / `is_join_settled` (`runtime/delegation_store.
   py`) — the wait-set flag (`blocking`) scopes the JOIN set and
   `review` is join-terminal in both. Any future change to what
   counts as "settled" goes in the shared helpers, never in one
   call site.
6. **`git diff <base>` never shows untracked files — a review built
   on it alone reads as "nothing to review" for exactly the
   delegations that created new files** (review Phase 1: the
   truncation probe caught an empty bundle on a new-file-only
   change). The bundle builder appends `??` entries as marked
   `/dev/null`-vs-file sections (binary → one-line note, never
   content; per-file 32KB cap, global 256KB cap). Staged-but-
   uncommitted tracked content DOES show (worktree vs base) —
   only fully untracked paths are invisible. Any future diff
   surface must cover the untracked set, not just the diff.
7. **Store-boundary side effects bypass router guards — the
   endpoint tests can't see it** (review Phase 1: `EscalationStore.
   answer/skip/force_timeout` clear `needs_attention` through the
   injected flagger, and the production flagger cleared blindly —
   wiping the flag on a review-owed delegation before the
   router's review-aware loop ran, which breaks early without
   restoring it; the endpoint tests pass because their store
   wires no flagger). The production rule is now one function
   (`make_attention_flagger` + `_review_owes_promotion` in
   `web/routers/delegations.py`) shared by the flagger and the
   router loops, pinned by `tests/test_review_trigger.py` (real
   factory + real `answer/skip/force_timeout`). When adding a new
   clear site, route it through the same rule — and wire the
   flagger in the test state, or the test proves nothing.
8. **Native git inspection is argv-exec with a verb allowlist,
   never a shell string** (2026-09-15, `docs/GIT_READ_TOOL_PLAN.md`).
   `runGit` spawns `git [verb, ...args]` — no shell means `git log;
   rm -rf` can't ride the call. Verbs are allowlisted (log/show/
   status/diff/branch/ls-files/rev-parse) and args go through a
   structural flag gate: bare `-` args are DENIED unless they ride
   `GIT_FLAG_ALLOW` (`--stat/--oneline/-n/--name-only/--porcelain`)
   or the fused `(-n<N>|--<flag>=<value>)` shape; `--upload-pack/
   --exec/-c/--config` are denied even fused. Both rejections happen
   BEFORE spawn (`rejected: ...` typed errors — doom-guard pattern,
   the model self-corrects; never a permission ask). `log` pages
   `-n 20 --oneline` by default, explicit args win (read→2000
   doctrine). Adding a verb is a user ruling, never a model ask
   (plan §0.3).

9. **The engine `bash` tool names its shell per-boot — models
   cannot guess it** (incident b8544168fa59: 12 min of Unix pipes
   on Windows CMD, 24 failures, then a streak trip — the model was
   never told). `detectUnixShell` prefers explicit Git locations
   (PATH order grabs WSL/Store stubs — verified) with a
   `SWEAVE_BASH_PATH` override; the dynamic `bashDescription()`
   is the model's only shell contract (charters stay static).
   When the shell changes, update the description builder, never
   per-call prompts — and re-run the slow-burst pin
   (`test_slow_streak_survives_burst_rule`, ~122s by construction).

10. **The specialist transcript is a JOIN of two sources — never
   one** (2026-09-16, view step 2a). The engine sidecar journal
   (`~/.sweave/engine/sessions.json`) holds prompt + text + tool
   calls, but NOT reasoning or tokens; the delegation trace holds
   reasoning chunks (`reasoning`, per turn delimited by
   `engine_user_message`) + `tokens_used`. Both degrade
   independently: a trace-less block still projects (empty
   reasoning/tokens), a journal-less delegation projects NO key
   content (opencode turns, pre-change records). Adding a new
   block field means deciding which source owns it — never a
   second write of the same data (sizes bound growth: prompt/
   text/result 20K, reasoning 8K, preview 200).
11. **Trace prompt capture is sizes + bounded preview, never full
   text** (2026-09-16, view step 2b). Long-turn prompts re-
   represented verbatim in the trace would re-bill every
   remaining iteration sized like the exec-tool bloat class
   (the 32K cap lesson). `wire_prompt` carries sizes (preamble/
   task/total/render) + a 200-char preview; the FULL task text
   already rides `prompt_sent` (the record-side capture
   predating this), so there is exactly one full copy per turn.
12. **An unknown wire part is a ROW, never a crash** (2026-09-16,
   view step 2c). Both stream readers' type switches must end
   in an else that traces `unknown_part {type, raw}` once per
   type per turn — a version bump that renames parts degrades
   to unknown rows and the turn still completes. A silently-
   ignored fall-through is the drift failure mode: nothing in
   the trace, nothing in the UI, code assumes nothing changed.

13. **Never hold one socket open for a human-timescale wait**
    (incident 2026-09-16: the engine permission wait held
    `POST /api/engine/permission` open; Node's ~300s fetch idle
    timeout killed it at ~307s as `provider_error: fetch failed`
    while the server kept waiting — the user's later answer
    landed nowhere). Waits resolve by create-and-poll: the POST
    creates (or reuses) the escalation and returns it at once
    (`{"wait": false}`), the answer arrives via `GET`
    polling — the same shape as `ask_human`'s `waitEscalation`.
    Any new blocking wait goes through the poll shape; a
    held-open socket is a 5-minute time bomb with a misleading
    error label (the failure reads as provider trouble, never
    as what it is: an unanswered question outliving the socket).

14. **Cancel reads the STORE mid-run — settle-time writes come
    too late** (incident 2026-09-17: `engine_session_id` rode
    only the settle write, so Stop found `None`, the abort never
    fired, and the specialist committed 3 min after the user
    stopped it). Anything cancel needs (session ids, abort
    handles) persists at BIND time via the `on_session_bound`
    hook — both harnesses plus the chat path; the settle write
    stays as fallback/forensics. Pin the mid-run record in
    tests, not just the settled one (`test_stop_session_bind.py`:
    the hooked id must be on the store row while the turn runs).

15. **A turn that dies mid-tool-loop poisons its session for every
    later turn — sanitize at read time** (incident 2026-09-19:
    two fix-round delegations failed loud with
    `engine_failed_before_work: provider_error: provider 400` on
    BOTH flavors; the 200-char trace cut read as a model outage
    ("[inval..."), while the journal held the truth: responses
    "No tool output found for function call ...", chat
    "assistant message with 'tool_calls' must be followed by tool
    messages..."). The assistant entry is appended BEFORE tools
    execute, so an abort/timeout/crash between the two leaves an
    unanswered call in the journal — and sessions resume across
    delegations, so every later turn replays the poison and 400s
    before any work (live scan: 17/358 journals, zero orphans).
    `sanitizeHistory()` (`sweave-engine/src/sessions.js`) keeps
    only calls with a matching tool output, applied in BOTH
    history mappers (loop.js chat + responses.js) — the single
    choke point covering loop and single-shot paths. Write-time
    repair is deliberately absent: crash poison can never be
    fixed at write time (the process is gone). Pinned by
    `tests/test_engine_history_hygiene.py` (pre-seeded poisoned
    journal, real sidecar, both flavors serve + answered pairs
    preserved; fails on the pre-fix mappers). Companion fix the
    same night: the `engine_failed_before_work` cut is 200→500
    chars so the upstream reason survives into traces.


## Paths & config

1. The M1.prep-era `agents.yaml` was **CWD-relative** (`Path("agents.yaml")`) — M1.2
   anchored it to **`Path.home() / ".sweave" / "agents.yaml"`** (via
   `AppState._anchored_agents_path`). Code that wants the file reads
    `state.dynamic_agents_path` (which carries the anchored path), never a fresh
    `Path("agents.yaml")` from CWD.

2. **`routing.turn_timeout_s` (2026-09-10) is read at THREE sites — keep them in
   sync**: `JobRunner.turn_timeout` (set in `web/server.py` lifespan from
   `get_routing().turn_timeout_s`), the `ChatLoop.turn_timeout` (constructed from
   `state.job_runner.turn_timeout`), and the ConfigManager reload callback
   `_apply_turn_timeout` (pushes new values into BOTH at runtime). After adding
   a new turn-bounded surface, register it in the reload callback or it keeps
   the boot-time value forever. The reload callback only fires on
   **config.yaml** edits (the `ConfigReloader` watches `config_path.name`);
   a rules.yaml-only edit does NOT trigger reload — restart (or touch
   config.yaml) after editing rules.yaml. The default lives in two places
    intentionally: `RoutingConfig.turn_timeout_s` (14400.0, authoritative,
    validated 0 < v <= 14400) and `JobRunner.DEFAULT_TURN_TIMEOUT` (4*3600,
    the un-configured fallback — test
    `test_turn_timeout_config_field_default_and_bounds` pins both). Since
    supervisor P3 the value is a runaway fuse (pulses govern); tune
    per-project via the routing overlay for patient work, not the
    global default.
   failure string stays `turn_timeout_exceeded_{timeout}s` so the UI
   failure-taxonomy can parse the `turn_timeout_exceeded_<N>s` substring with
   whatever value is configured.

3. **Generated artifact + user state in one file = three-writer
   clobber** (fast-track 2026-09-11: models.yaml carried the
   hand-set `default` AND the generated providers, written by
   `set_default_model`, `sync_registry`, and any stale reader —
   the stray `default: opencode/muse-spark-...` was the live
   exhibit). Rule: generators write providers-only; user
   selections live in config.yaml (`models.default`,
   config > customs > legacy precedence). If you add a new
   generated file, decide on day one which writer owns every key.
   Related: `SweaveConfig.to_yaml` dumps the MERGED config
   (providers + routing included) — never use it to persist one
   key or the registry pollutes config.yaml; the surgical
   `_set_models_default_line` + `atomic_write_text_sync` path is
   the precedent for single-key config writes.

4. **Never splice `yaml.safe_dump(scalar)` into a larger document**
   (2026-09-11: broke config.yaml mid-suite). PyYAML appends a
   `...` document-end marker to a bare scalar
   (`"<v>\n...\n"`) that `.strip()` does NOT remove — the splice
   inserted a stray `...` line and every load 500'd with
   `ParserError: expected '<document start>'`. Allowlist-match
   plain-safe values and emit them verbatim; dump-and-take-
   first-line only as a fallback.

5. **config.yaml / models.yaml / models.meta.json are LIVE user
   state — untracked, never stash, checkout, or restore them**
   (user ruling: working artifacts, not repo work —
   `config.example.yaml` is the tracked template; the registry is
   regenerated via `sweave models sync`). History: 2026-09-13, a
   `git stash` + `pop` round-trip collided with live edits and a
   `checkout -- config.yaml` destroyed the live default; recovery
   was byte-compare against a TEMP backup. Untracking removes the
   git half of the hazard; the test half is closed separately —
   no test may load the repo CWD (bare `ConfigManager()` rewrote
   the live file via load-time legacy adoption; use the
   `repo_config_pair` helper in `tests/conftest.py`, which copies
   or plants a synthetic seed). Rules: treat these files as
   read-only; verify with byte hashes, never `git status` alone;
   conflicting content always resolves in favor of the live file,
   never HEAD.

6. **Task worktrees: base, git dir, and lifecycle.** The effective
   base is the project override when absolute, anchored at the
   project dir when relative, else `{project}/.worktrees` — always
   resolved absolute. The git dir is ALWAYS the project dir
   (`WorktreeManager(base, git_dir=project)`), because
   `create_worktree` inherits the process CWD otherwise and a
   multi-project server would plant trees in the wrong repo.
    Creation failure fails the delegation loud (non-git projects
    must init — no silent in-tree fallback). Removal centralizes in
    `JobRunner._transition` on done/failed (normal, timeout, cancel)
    plus the promote endpoint; settle commits stray WIP first
    (`commit_wip`, step 4 2026-09-15 — plain `remove` refuses dirty
    trees, the `a5884977` leak; the kept branch preserves the work;
    never force-remove uncommitted work); crash-recovery
    (`recover_interrupted`, boot sweep) does NOT remove — trees
    orphaned by a crash need manual `git worktree prune` + dir
    removal (a sweep is future work). Tests driving `_run` inject
    the conftest fake lifecycle (`fake_worktree_manager_factory`,
    now with `async_commit_wip`); the real-git proofs (clean +
    dirty settle) live in `test_task_worktrees.py`.

7. **Agent scratch goes to the OS temp dir, never repo root**
    (2026-09-15: `test_out*.txt`, `test_full.txt`, `theme_fail.txt`
    vitest logs in repo root — several written from INSIDE a
    worktree, so the habit travels with the agent, not the cwd).
    Cause: engine `bash` head-truncation taught models to redirect
    output to files (fixed by tail-cut + paged `read`, step 2).
    Rule: throwaway runner output + ad-hoc scripts belong in the OS
    temp dir; the observed shapes are gitignored (see the Agent
    scratch block in `.gitignore`); seeds carry the discipline line
    and the reviewer audits strays as a non-blocking finding.

8. **A seed YAML that doesn't parse is silently skipped — live
    charters fossilize** (2026-09-15: `orchestrator/config.yaml`
    had col-0 list items inside the prompt block scalar, breaking
    the mapping; the loader logged a warning and skipped the file,
    so no auto-seed was possible and live orchestrator charters
    were fossilized/empty while the file *looked* fine). Rule: any
    seed edit must re-run the loader (`test_seed_agents.py` — the
    4-role test is the tripwire; it was red on HEAD the whole
    time, proving the break). Suspect this first when a charter
    change has no effect.

## sweave-web UI

1. **React StrictMode + WS connections in dev** (R4 step 1). The WSProvider
   (`sweave-web/src/context/WSProvider.tsx`) holds the WebSocket in a ref + opens it
   from a `useEffect` that returns a cleanup. StrictMode double-invokes the effect in
   dev; the cleanup re-runs, the connect re-runs, the new socket replaces the old.
   The provider is idempotent: a single ref holds the connection; the cleanup only
   closes when the provider actually unmounts. Any new global-side-effect provider
   (EventSource, long-poll) follows the same pattern: ref + idempotent connect +
    cleanup that closes on real unmount, not on StrictMode's double-invoke.

2. **Pulling agent-elements via shadcn CLI hits the npm 12 `--allow-scripts` gate**
   (R4.2/R4.3, 2026-09-08). `npx shadcn@latest add https://agent-elements.21st.dev/r/<name>.json`
   runs `npm install <deps>` and **fails with `EALLOWSCRIPTS`** because the CLI passes
   `--allow-scripts`, which npm 12 forbids for project installs ("add the entries to the
   `allowScripts` field in package.json, or to .npmrc, instead"). The global `~/.npmrc`
   `allow-scripts=opencode-ai` is also in force. Working recipe:
   - Add an `"allowScripts"` **array** to `sweave-web/package.json` listing the pulled
     deps' runtime deps that run install scripts (e.g. `ai`, `@tabler/icons-react`,
     `@pierre/diffs`, `shiki`, `@shikijs/*`, `diff`, `esbuild`, `hast-util-to-html`,
     `@pierre/theme`, `@pierre/theming`, `lru_map`). `esbuild`'s postinstall is the one
     that actually fires.
   - Use **`npx shadcn@4.20.0`** (not `@latest`): 4.20.0 doesn't pass `--allow-scripts`,
     so it relies on the package.json `allowScripts` field and succeeds. `@latest` hard-fails.
   - Known-good slugs: `bash-tool`, `text-shimmer`, `input-bar`, `edit-tool`. `tool-card`,
     `tool-ui`, `agent-thought` return 404 (not valid registry names).
   - The CLI's `.npmrc allow-scripts=*` is ignored for project installs in npm 12; only
     the package.json field works. Don't waste time on a project `.npmrc`.
    - After the pull, check `src/components/agent-elements/agent-ui.css`:
      pre-2026-09-09 it needed a `[data-theme="dark"]` selector next to
      `.dark` (the CLI re-import drops it). Post-2026-09-09 the file no
      longer carries per-theme color blocks at all — its `--an-*`
      variables bridge to the theme tokens (`var(--color-*)`), and the
      runtime toggles `.dark` for every dark-mode preset — so there is
      nothing to re-apply; just don't reintroduce hardcoded per-theme
      blocks. `edit-tool` already ships its own `[data-theme="dark"]`
      rules, so only the BashTool token block needs the patch.

3. **The global `*:focus-visible` outline beats Tailwind's
   `focus:outline-none` on text fields** (2026-09-09). The rule in
   `src/styles/animations.css` is unlayered, and unlayered author CSS
   beats Tailwind v4's layered utilities regardless of specificity —
   so the chat textarea rendered a 2px outline *inside* the composer
   box despite `focus:outline-none`. Text-entry elements
   (`textarea/input/select/[contenteditable]`) are now explicitly
   opted out in the same file and carry their own affordance (the
   composer box glows via `focus-within:`). Same trap as the deleted
   universal margin/padding reset — keep global element rules out of
   unlayered CSS unless they are meant to beat every utility.

4. **`needs_attention` means "answer OR promote" — gate Answer buttons
    on a pending escalation** (2026-09-12). Review entry sets the flag
    with NO escalation record, so `AnswerInline` (`LiveTree.tsx`) used
    to render a dead Answer button next to Mark done on every review
    row (click → `POST …/answer` → 404). The button now GETs the
    escalation first and renders only for `status === "pending"`
    (resolved-but-unpromoted also hides: the remaining action is
    promotion), refetching on `specialist.escalated/resolved` for
    late-arriving permission asks. Rule: any new attention affordance
   must verify its backing record exists; never render from the flag
   alone.

5. **No-FOUC inline theme script: regenerate after touching tokens.ts /
   fontScale.ts, and NEVER put a literal closing script tag inside it**
   (2026-09-13). `sweave-web/index.html` embeds a generated pre-paint
   script (preset table + font-scale map) between the
   `SWEAVE-THEME-INLINE` markers — source of truth stays in
   `src/lib/theme/tokens.ts` / `fontScale.ts`. After any change there,
   run `cd sweave-web && node scripts/gen-theme-inline.mjs` (uses the
   repo's own tsc; needs `npm install` done once). `inline.test.ts`
   pins the embedded table against the live modules, so drift fails
   the gate. Trap seen live: the generator's own code comment
   contained the literal sequence that closes a script element — the
   parser ended the element early and dumped the remaining JS as
   visible page text starting mid-comment. The generator now fails
    loudly if the body contains that sequence, and the pin test asserts
    the same; write "closing script tag", never the literal, in or near
    the template.

6. **Radix Tabs activate on focus — `fireEvent.click` alone never
    switches tabs in jsdom** (2026-09-14). The Radix trigger activates
    on focus (automatic mode) and `fireEvent.click` dispatches mouse
    events without focusing, so the panel never mounts and the test
    fails on a missing testid — looking exactly like the component
    didn't render. Recipe (see `SettingsAppearance.test.tsx`
    `activateTab`): `el.focus(); fireEvent.click(el);` then assert.
    The unfocused-click failure also emits `act(...)` warnings from
    Tabs/RovingFocus, which are noise, not the bug.

7. **Optimistic updates must mirror the server record model**
    (incident 2026-09-17: edit swapped the new text onto the
    ORIGINAL row while the server appends a revision row — the
    edited text rendered twice once `message.added` arrived; a
    reload healed it, which is why it hid). Shape rule: the
    target keeps its content + `superseded` flag, the new text
    rides an optimistic `local-` revision with `fork_from`
    linkage, and the existing id-swap reconcile converges the
    two — never a shape the server would never emit. Pinned by
    the edit-reconcile test in `runtime.test.ts` (duplicate text
    fails the gate).

## Opencode harness & wire protocol

1. **The mock must match the real v2 wire** (R4.0, 2026-09-05). The
   `SWEAVE_MOCK_OPENCODE=1` mock in `sweave/harness/opencode.py::_spawn_mock`
   must faithfully emulate the real opencode v2 HTTP API:
   - `POST /session` returns `{id: "ses_..."}` (the v2 wire format).
   - `POST /session/{id}/message` returns 200 only when `id` starts
     with `ses_` AND equals the id `POST /session` issued; otherwise
     404 (matches the real serve's unknown-session behaviour).
   - `GET /session/{id}` returns 200 for the issued id; 404 otherwise.
   - Mock id format: `ses_mock_{name}` (underscore; v2-faithful), not
     `ses-mock-{name}` (hyphen; pre-R4.0). The runtime's `_send_message`
     asserts `process._session_id.startswith("ses_")` before posting —
     any mock that emits a non-`ses_` id silently masks bugs.
   - `_StubStreamResponse` must expose `.text` (the harness reads it in
     the `HTTPStatusError` catch path: `e.response.text`).
2. **Two sources of truth for one id is a bug** (R4.0). The runtime
   owns TWO ids for the same opencode session: the external binding
   (`Session.orchestrator_session_id` or `Specialist.session_id`) and
   `process._session_id`. Both must be the same value at the moment
   `_send_message` runs, or the wire gets a placeholder
   (`chat-{hex}` / empty string / stale value) and the real serve
   returns 500. `_ensure_session` writes both in all three paths
   (create / 404-recreate / reuse); `_send_message` refuses to post
   when they disagree. Pin this in tests — see
   `tests/test_r4_0_wire_shape.py`.
3. **`SWEAVE_MOCK_OPENCODE=1` test fixtures must use the `ses_`
   prefix**. Pre-R4.0 fixtures used `sid-*`, `chat-*`, or other
   arbitrary ids; the runtime's `ses_` assertion now rejects those.
   When porting a test to the mock, change the fixture id to
   `ses_whatever` and the wire-shape mock will pass.

## Playwright e2e (R4.1, 2026-09-06)

1. **Chromium build is version-pinned per Playwright release**.
   `sweave-web/e2e/` runs via `npx playwright test`; the test driver
   needs the exact chromium build that ships with the locked
   `@playwright/test` version. `npx playwright install chromium` is
   required when the Playwright version is bumped, OR when the
   test environment doesn't have a matching build. The wave-1 +
   R4.1 suites both pin a chromium build; local environments
   without internet access (or with older browsers cached at a
   different revision) will see the tests fail with
   ``Executable doesn't exist at .../chromium_headless_shell-NNNN/...``.
   The local gates (pytest + run.py --check + vitest + npm run
   build) are the source of truth; the e2e suite is CI-time.
   When the chromium version mismatches, the wave-1 e2e is the
   simplest reproduction.

 2. **The e2e dev server is bound to ``127.0.0.1``**. The
    ``playwright.config.ts`` webServer URL is ``http://127.0.0.1:3000``;
    Vite by default binds to ``localhost`` (IPv6). When starting
    the dev server manually (not via the webServer config), pass
    ``--host 127.0.0.1`` so the URL check succeeds.

3. **Bounded scroll regions need ``min-h-0`` on the whole flex chain**
   (R4.2, 2026-09-08). A flex child's default ``min-height: auto``
   refuses to shrink below its content, so a sidebar section wrapping a
   ``max-h-[40vh]`` list pushed the nav + status rows BELOW the
   viewport (content "overflowing underneath the limit"; the active
   session's tick/delete affordances sat under the fold). The fix is
   structural: make the scroll-owning section ``flex-1 min-h-0`` inside
   an ``overflow-hidden`` column, let the radix ``ScrollArea`` flex
   (drop fixed ``max-h-*`` caps), and pin everything else
   (``shrink-0``). Verify with the geometry probe: ``sidebarBottom ==
   innerHeight`` and the active row's rect inside the tree rect
   (pattern in the ui-chat-probe family).

## React Query invalidation map (R4.1, 2026-09-06)

1. **The five WS events invalidate the smallest scope of
   query keys**. The mapping lives in
   ``sweave-web/src/context/wsInvalidations.ts`` (a pure
   function; 9 vitest pin the contract). The AppProvider
   subscribes once per event and iterates the returned key
   list. Adding a new WS event is a 3-line change (one event
   name in the switch, one entry in the AppProvider subscribe
   loop, one vitest). The invalidation map must never
   cross-invalidate: a session event for project p1 must not
   touch project p2's session list. (Tests cover this in
   ``wsInvalidations.test.ts``.)
2. **``active_session.changed`` has a side effect beyond
   query invalidation**: the AppProvider refetches
   ``/sessions/active`` and patches its own state (the
   topbar statusline + sidebar active-pill read it). The
   invalidation map returns an empty list for this event;
   the AppProvider's subscribe loop handles the refetch
   directly (not through the map) so the side effect stays
   in one place.

## Tailwind v4 token format (R4.1 step 1c, 2026-09-06)

1. **Each ``--color-*`` variable in the ``@theme`` block carries
   a full ``rgb()`` value**, not a bare ``<r> <g> <b>`` tuple.
   Tailwind v4 generates ``bg-<name>`` utilities that resolve to
   ``var(--color-<name>)``; a bare tuple is not a valid CSS color
   value. The runtime theme writer (``tokensToCssVariables`` in
   ``src/lib/theme/tokens.ts``) wraps each preset tuple in
   ``rgb()`` on write. Consumers reference the variable directly
   (no ``rgb()`` wrapper): ``background-color: var(--color-background)``.
2. **The runtime override path is RGB tuples** (the same shape
   the preset tokens use). The custom-color editor writes via
   ``hexToRgbTuple(hex)`` so the merged map is consistently
   RGB-tuple. Pre-R4.1 step 1c the override was a hex string
   (``#rrggbb``); post-migration the override is ``<r> <g> <b>``.
   The localStorage round-trip tests reflect the new contract.
3. **``dark:`` / ``.dark`` follow the preset mode, not the preset
   name** (2026-09-09). ``applyThemeToDocument`` toggles the
   ``dark`` class + ``color-scheme`` from ``preset.mode``, and
   ``@custom-variant dark`` keys off ``.dark`` — so every dark
   preset (dracula, nord, ...) gets dark styling. Never gate
   dark-only CSS on ``[data-theme="dark"]`` (matches one preset).
   The 18 extended tokens (status/chrome/chat/code/link/selection)
   are mode-aware per preset; new presets go through
   ``definePreset`` in ``tokens.ts`` (core 19 required, extended
   fall back to card/muted derivations + mode generics), and new
   ``--color-*`` utilities need a matching ``@theme`` default in
   ``globals.css`` or Tailwind won't generate the class.

## Opencode serve CMD window stays open after a turn crash (R4.2, 2026-09-07)

1. **Symptom**: When the opencode serve dies mid-stream (e.g. the
   connection is reset or the process crashes), the user sees a
   black CMD window that stays open indefinitely. The chat turn
   appears "frozen" -- the assistant bubble never finalizes.

2. **Root causes** (two independent bugs):

   a. **Error-prefix mismatch** (fixed in R4.2 step 0 hotfix):
      The runtime's error sentinel was ` [error: ...] ` but the
      chat loop checked for ` [chat error: ` (see
      `sweave/chat/loop.py` lines 492, 549). The first-turn error
      didn't match, so the chat loop fell through to a wasteful
      synthesis turn (another full orchestrator call) that also
      failed. The user waited ~30-60s for the turn to finish.
      **Fixed**: the runtime now returns ` [chat error: ...] `.

   b. **Periodic `sweep_idle` never runs** (still open):
      The opencode serve process is never reaped because the
      `ServeRunnerRegistry.sweep_idle()` method exists but is
      never called. The M1.3 design intended a periodic task to
      shut down idle serves; the periodic task was never wired.
      The CMD window (the opencode serve process) lives on.

3. **Workarounds** (until the sweep is wired):
   - Manual cleanup: `python stop_server.py` (calls
     `ServeRunnerRegistry.shutdown_all`).
   - Or `taskkill /IM opencode.exe /F` in an admin shell.

4. **Tracking**: The periodic sweep is tracked as a follow-up
   (not in the R4.2 scope per the "no backend change" non-goal,
   but the root cause of the stray CMD windows). When the sweep
   lands, the idle TTL (default 5 min) will clean up stray
   serves automatically.

## Opencode model provider config (R4.2, 2026-09-08)

1. **Symptom**: Chat API returns 500 with `HTTPStatusError: Server error '500 Internal Server Error' for url 'http://127.0.0.1:XXXX/session/ses_.../message'`. The opencode serve log shows the request received but the model provider is unknown.

2. **Root cause**: `models.yaml` used provider names (`tokengo`, `subconscious`, `nano-gpt`, `qiniu-ai`, etc.) that opencode doesn't recognize. The model resolution chain falls back to these unqualified names, and the opencode serve rejects the structured model with an unknown provider.

3. **Fix**: Use the **opencode** provider (built-in to opencode) with models that opencode actually serves:
   ```yaml
   models:
     orchestrator:
       default: opencode/nemotron-3-ultra-free
       aliases:
       - opencode/deepseek-v4-flash
       - opencode/gemini-3.5-flash
       - nvidia/nemotron-3-ultra-550b-a55b
       provider: opencode
   ```
   Run `opencode models --provider opencode` and `opencode models --provider nvidia` to see available models.

4. **Testing**: After updating models.yaml, restart the server (`python stop_server.py && python start_server.py 8100 127.0.0.1`) and test the chat endpoint.

## React rendering error with assistant-ui ThreadMessageLike (R4.2, 2026-09-08) — SUPERSEDED

The `extractText()` workaround below this note described the step-1
Thread, which rendered `message.content` directly. R4.2 step 2-pre
(2026-09-08) rebuilt the Thread on `MessagePrimitive.Parts` slots, so
the failure mode is gone — see the "assistant-ui 0.15 primitives"
group below for the traps that REPLACE this one.

1. **Symptom**: Uncaught Error: `Objects are not valid as a React child (found: object with keys {type, text})`. The UI flashes and disappears when sending a message.

2. **Root cause**: `ThreadMessageLike` from `@assistant-ui/react` expects `content` to be `Part[]` (array of `{type: "text", text: string}`) for ALL messages (both user and assistant). The adapter's `projectEntry` correctly wraps all messages in this format, but the `UserMessage` component rendered `message.content` directly — when `content` is an array, React tries to render each part object as a child.

3. **Fix**: Add an `extractText()` helper in the `UserMessage` component to handle both string and `Part[]` content formats:
   ```typescript
   function extractText(content: string | { type: string; text: string }[]): string {
     if (typeof content === "string") return content;
     return content.filter((p) => p.type === "text").map((p) => p.text).join("");
   }
   ```
   Use `extractText(message.content)` for rendering and for the copy-to-clipboard action.

4. **Note**: The `AssistantMessage` component already handles `Part[]` correctly via `AssistantTextPart` which extracts text from parts.

## assistant-ui 0.15 primitives (R4.2 step 2-pre, 2026-09-08)

1. **`ThreadPrimitive.Messages` with a children render function renders
   the function PER MESSAGE**. The canonical anatomy is
   `{({ message }) => <UserOrAssistant />}` — one call per message,
   each ambient-scoped to that message. The step-1 Thread rendered the
   ENTIRE message list inside the function: N messages rendered the
   list N times (3 API messages → 9 DOM roots, 4 visible user rows,
   4 empty phantom rows). If row counts don't match the API, check
   this first; the probe (`sweave-web/scripts/ui-chat-probe.mjs`)
   dumps `[data-message-id]` per row to catch it.
2. **ThreadMessageLike normalization is strict on the no-convertMessage
   path**: assistant entries need a top-level `status` (terminal =
   `{ type: "complete", reason: "stop" }`; running = `{ type:
   "running" }`) or part-state computation crashes with
   `Cannot read properties of undefined (reading 'type')` in
   `normalizePartStatus`. Text parts carry their own `status`
   (`{ type: "complete" }` — no `reason` on PART status; the reason
   lives on message status only). `useExternalStoreRuntime` requires
   `convertMessage` for the ThreadMessageLike[] flavor (identity
   `(m) => m` is what our hook uses).
3. **`ActionBarPrimitive.Root` with `hideWhenRunning` +
   `autohide="not-last"` UNMOUNTS on non-last messages and while
   running** — it renders `null`, no data attribute is emitted. Do
   NOT add hover-reveal CSS on top (`opacity-0 group-hover:...`);
   that hides the bar that the primitive intentionally shows on the
   last message.
4. **`ThreadPrimitive.Viewport` owns auto-scroll**: pass `autoScroll`,
   `turnAnchor="bottom"`, `scrollToBottomOnRunStart/Initialize/
   ThreadSwitch` as PROPS. The `useThreadViewportAutoScroll` hook is
   for custom scroll containers, not for the primitive's viewport.
5. **jsdom lacks `ResizeObserver`** — the viewport needs it; the vitest
   setup (`src/test/setup.ts`) stubs it. Any new test that mounts the
   Thread depends on that stub.
6. **Our `ui/tooltip` has NO implicit provider** — a bare `<Tooltip>`
   outside `<TooltipProvider>` throws at render. `TooltipIconButton`
   self-wraps; hand-rolled tooltips (e.g. the composer stop button)
   must wrap themselves.
7. **Tailwind v4: unlayered CSS beats `@layer utilities` regardless of
   specificity.** An unlayered `* { padding: 0; margin: 0 }` reset in
   globals.css silently disabled EVERY `p-*`/`px-*`/`m-*` utility
   app-wide (topbar clipped at the window edge, composer flush,
   bubbles cramped) while everything still built and tests passed.
   Tailwind's preflight already resets margins in `@layer base` —
   never add an unlayered universal reset. (Also: never write `p-*/`
   inside a CSS comment — the `*/` terminates it.)
 8. **PowerShell round-trips destroy UTF-8 source files**: a
    `Get-Content` + `Set-Content -Encoding UTF8` pass on a UTF-8 (no
    BOM) file reads it as cp1252 and writes back double-encoded
    (`—` → `â€"`) plus a BOM. This bit a bulk string-replace in this
    round (em-dashes in .tsx docstrings became mojibake). For any
    byte-level edit of source files use `python -c` in binary mode;
    the repair is `raw.decode('utf-8').encode('cp1252').decode('utf-8')`
    after stripping a leading `\xef\xbb\xbf`.
9. **A bare Radix popover hangs jsdom in this dependency tree**
   (R4.2 picker polish, 2026-09-08): rendering `Popover` +
   `PopoverTrigger` + `PopoverContent` and clicking the trigger under
   `@testing-library/react` puts the vitest worker into a sync loop
   (no testTimeout fires, the whole process hangs, zero output). Real
   browsers are fine. Consequence: any test that needs a popover-
   mounted surface must mount the panel component DIRECTLY — keep
   popover-bodied widgets split into a testable panel export (see
   `PathPickerBrowser`) and verify the popover shell via the
   screenshot probes (`scripts/ui-picker-probe.mjs`). Never commit
   hang-probe scratch tests — a leftover one silently hangs the whole
   suite (files are listed by name in the failure output; anything
   with `__min`/scratch naming is suspect).
 10. **Tall popovers must respect ``--radix-popper-available-height``**
     (note the name: it is the POPPER var; there is no
     ``--radix-popover-content-*`` var in this radix version). Without
     ``max-h-[var(--radix-popper-available-height)]`` + ``overflow-hidden``
     + internal flex, a popover anchored near the viewport edge overflows
     the screen (the picker's breadcrumbs rendered off-screen). The
     scrolling region must carry ``min-h-0 flex-1 overflow-y-auto`` —
     the same ``min-height: auto`` flex trap as the sidebar; without
     ``min-h-0`` the list refuses to shrink and never scrolls. Footer
     rows inside the popover take ``shrink-0``.

## Chat turn lifecycle — refresh + restart (2026-09-10 recovery contract)

1. **Chat turns are DETACHED from the HTTP handler** (see
   `ChatLoop.run_turn` → `_turn_owner_runner`): the request coroutine
   only spawns + `asyncio.shield`-awaits the turn task, so a client
   refresh/disconnect cancels the POST but NOT the turn — the reply
   is still persisted + emitted when it finishes. Never re-bind the
   turn body to the request's task tree: the pre-hardening code let
   uvicorn's disconnect cancellation kill mid-flight turns, leaving
   the delegation phantom-`running` forever.
2. **The M1.7 lock-as-queue semantics are GONE — don't restore
   them.** A second POST while a turn is active raises
   `TurnActiveError` (→ HTTP 409 carrying the active-turn snapshot).
   The old code queued the second message behind the per-session
   lock, silently hanging the client's HTTP request for up to
   `turn_timeout`. New tests that send two turns on one session must
   await the first turn's completion (or expect the 409).
3. **Boot recovery ordering is load-bearing**: the lifespan calls
   `_recover_interrupted_delegations` immediately after building
   `PerProjectDelegationStores` because at that instant no task can
   be running — anything non-terminal on disk is by definition from
   a dead process. Don't move the recovery after the runners /
   sweeper start, don't call `recover_interrupted` while the server
   is live (it would fail live turns).
4. `chat.stream_persist_interval` (default 2.0s) is how often the
   partial reply is persisted onto the delegation `output`; the
   final `_finalise_turn` write is always authoritative and
   OVERWRITES it (error turns end with `output=""` — the partial
   tail survives only on interrupted/crashed records + in the trace
   log). Registry snapshots (`GET /api/sessions/{id}/turn`) are
   in-memory only: they cover refreshes of the SAME process, never
   express them as durable state.
