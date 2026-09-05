# Gotchas — burned us once, don't relearn

Each group below is one branch of work. When a group's trigger fires (the trigger
table in `AGENTS.md` points here), read the whole group before you start. New
gotchas land here — grouped by branch, not appended as a numbered list.

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

## Opencode harness & wire protocol

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

## Paths & config

1. The M1.prep-era `agents.yaml` was **CWD-relative** (`Path("agents.yaml")`) — M1.2
   anchored it to **`Path.home() / ".sweave" / "agents.yaml"`** (via
   `AppState._anchored_agents_path`). Code that wants the file reads
   `state.dynamic_agents_path` (which carries the anchored path), never a fresh
   `Path("agents.yaml")` from CWD.

## sweave-web UI

1. **React StrictMode + WS connections in dev** (R4 step 1). The WSProvider
   (`sweave-web/src/context/WSProvider.tsx`) holds the WebSocket in a ref + opens it
   from a `useEffect` that returns a cleanup. StrictMode double-invokes the effect in
   dev; the cleanup re-runs, the connect re-runs, the new socket replaces the old.
   The provider is idempotent: a single ref holds the connection; the cleanup only
   closes when the provider actually unmounts. Any new global-side-effect provider
   (EventSource, long-poll) follows the same pattern: ref + idempotent connect +
   cleanup that closes on real unmount, not on StrictMode's double-invoke.

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
