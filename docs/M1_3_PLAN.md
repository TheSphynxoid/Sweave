# M1.3 — Shared serve + durable context (execution plan)

Status: step 0 done (2026-08-30; step-0 findings folded into this
plan — the raw 15 MB probe dump was stripped pre-push 2026-09-15).
Est. ~2 sessions (build ~1.5, live gate ~0.5). Predecessors: M1.prep ✅,
M1.0 ✅ (v2 API + per-message model done), M1.1 ✅, M1.2 ✅
(Specialist.session_id exists, unused; K-revised is now locked in
per the step-0 probe). This is R1's **risk sink** — the plan is
deliberately probe-first, and the provider-resolution gap surfaced in
the M1.2 post-audit is now closed by the step-0 findings.

## Tier framing carried from M1.2

Orchestrator (per-project singleton, flag on Specialist) / Specialist (persistent,
named, user-creatable) / SubAgent (M1.1's SubAgentRun, out of scope here).
Model precedence: `task_override > specialist.current_model > role_ref hint >
orchestrator.default`; role_ref is optional and unknown values fall through.

## Model identity across custom providers (amended 2026-08-30)

The user's `~/.config/opencode/opencode.json` declares custom providers
(ollama + gmi, in this install) on top of opencode's default provider set.
M1.2 ships `Specialist.current_model` as a plain string, and the v2
body construction in `harness/opencode.py:155-161` only recognises
`provider/model` slash pairs — bare names fall through to the opencode
default provider, which is the wrong routing for custom-endpoint models
like `MiniMaxAI/MiniMax-M3` (gmi) or `qwen3:8b` (ollama).

The full catalog-driven UI is R4 phase 2 (per DESIGN.md: "model combobox
grouped by provider with live/catalog badges"); the catalog endpoints
(`GET /api/models/catalog`, the models.dev + opencode-introspection
merge) ship there. **M1.3's job is to make sure the data shape is
correct so R4's UI can be built without a refactor** — i.e. the
Specialist record carries a structured `ModelRef` so the v2 body
shape is correct under all configurations, and the harness parser is
tidied up.

Plan amendments in this doc (numbered to match the chat):
- **K-revised**: `Specialist.current_model` becomes a structured
  `ModelRef {provider, model_id}`. Backwards-compatible via a
  `schema_version=2` migration: v1 records (bare string) are
  loaded as `ModelRef(provider=None, model_id=<the bare string>)`,
  the harness falls back to the unqualified-name path when
  `provider is None` and the opencode serve does the lookup. The
  empirical question — does opencode's serve accept a bare name
  from a non-default provider? — is step 0 sub-probe 2; the answer
  decides whether the fallback is a soft warning or a hard error.
- **L-revised**: `harness/opencode.py:_parse_provider_model` rewrite.
  Drop the dead `elif model_id:` branch (unreachable: the parser
  returns `(None, None)` for any input without `/`, so `model_id`
  is always `None` at that line). The new shape: accept either a
  structured `ModelRef` or a `provider/model` string; emit
  `body["model"] = {providerID, modelID}` when provider is known,
  else `body["model"] = model_id` (existing fallback). No behaviour
  change for the existing M1.0 tests (which all use `provider/model`
  strings); new path is purely additive.
- **M-revised**: step 0 probe — three sub-probes.
  1. **Catalog fetch**: `GET /config/providers` and `GET /provider`
     on a live serve. If neither path returns a JSON catalog, fall
     back to `opencode models --format json` (the CLI subcommand).
     Record the discovered shape so step 5 can build
     `GET /api/models/catalog` on the right primitive.
  2. **Bare-name empirical test**: send a message with
     `body["model"] = "MiniMaxAI/MiniMax-M3"` (no `gmi/` prefix) to
     a live serve, record the response status. If 2xx, the fallback
     is sufficient (warning only). If 4xx, the structured pair is
     required and the v2 protocol confirms provider identity must
     be explicit for non-default providers.
  3. **gmi round-trip**: send a message with
     `body["model"] = {providerID: "gmi", modelID: "MiniMaxAI/MiniMax-M3"}`,
     record the response and any provider log line. Confirms the
     multi-provider path works end-to-end.
- **N**: Branch A note. The cwd-vs-session-persistence decision is
  independent of provider identity. Each serve has all configured
  providers from the user's `opencode.json`; multi-provider
  (ollama + gmi + opencode default) is the user's opencode
  configuration, not Sweave's. The provider-resolution gap is
  addressed by K-revised (Specialist.current_model becomes
  ModelRef), not by the cwd vs session-persistence decision.

## Starting point (do NOT rebuild)
- Per-delegation lifecycle today: every `JobRunner._run` calls `harness.spawn(spec)` →
  one `opencode serve` per task (cwd = that task's worktree), system prompt, task
  message, stream read to terminal. No reuse, no session continuity.
- M1.0 assets: resolved exe (shim→opencode.exe), log-file port discovery (no pipes),
  v2 API (`/session`, `/session/{id}/message`), `parts[].text` body, per-message model
  `{providerID, modelID}`, chunked-stream reader with **terminal detection**
  (assistant object with parts ⇒ turn complete; provider errors surfaced verbatim).
- `Specialist.session_id` persisted (M1.2), currently always None; `attach()` still
  respawns. JobRunner comments already anticipate "the runtime" filling worktree paths.
- Derived specialist status (idle/running) from open delegations (M1.2).

## The three open probes — and a fourth discovered one

1. **Resume across serve restarts** (M1.0 leftover): does a NEW serve, started in the
   same cwd, see and accept messages for a session created by a previous serve?
   (`GET /session` lists old ids? `POST /session/{id}/message` succeeds?) Where does
   opencode store session state (global `~/.local/share/opencode` vs per-project
   `.opencode/`)?
2. **Completion signal semantics**: current terminal detection = assistant object with
   parts. Confirm: does the stream for ONE message POST cover the agent's whole turn
   (tool calls included) until the final assistant message? Any partial-turn traps?
3. **cwd binding for tool execution** (decides the architecture): opencode tools
   (bash/read/edit) execute in the serve process's cwd. If cwd binds to the *serve*,
   a "one shared serve per project" hosting specialists in DIFFERENT worktrees breaks
   tool calls (specialist A's tools would run in specialist B's worktree). Probe: task
   "run pwd and report" against a resumed session whose serve now runs in a different
   cwd — which cwd do tools see?
4. **NEW — provider resolution under multi-provider opencode config** (decides
   K-revised shape): the user's `opencode.json` declares custom providers (ollama
   + gmi in this install) on top of the opencode default. M1.2 ships
   `Specialist.current_model` as a bare string; the v2 body construction in
   `harness/opencode.py:155-161` only recognises `provider/model` slash
   pairs. Bare names fall through to the opencode default provider. The
   opencode v2 protocol's behavior on a bare model name from a non-default
   provider is not documented in this repo and not tested. Probe: see
   step 0 sub-probes 2 + 3 below. If the bare-name path fails, the
   structured `ModelRef` (K-revised) is required.

### Branch decision (after probes — closed 2026-08-30)
**Findings** (full report in `docs/M1_3_PROBE_RESULTS.md`):
1. **Probe 1 (in-process resume)**: sessions persist for the lifetime of the
   serve; `GET /session` returns the list.
2. **Probe 2 (cwd test)**: a freshly-spawned serve in a different cwd has
   `pwd` resolve to **the new serve's cwd**, not the worktree the session
   was created in. Confirms **Branch A: cwd binds to the serve process**.
   Per-specialist ServeRunner is required so each serve's cwd matches the
   active worktree of the specialist it serves.
3. **Probe 3 (concurrency)**: one serve, two sessions, one message each —
   works fine, no cross-talk.
4. **Probe 4 (storage)**: storage root is `~/.local/share/opencode`.
   The probe's "0 new files" finding was **misleading** — opencode
   persists sessions in the pre-existing SQLite file
   `opencode.db` (the probe's `_snapshot_storage` saw the file
   before and after, correctly, but missed that a *new row* was
   inserted into the existing table). Confirmed by direct query
   (May 2026 amendment): the `session` table holds the created
   record, the `message` table has rows linked by FK, and the
   `path.cwd` field records the worktree at message time. **Sessions
   DO survive opencode process restarts** (the SQLite DB is
   persistent; the `opencode serve` process is just the in-memory
   request handler). **Implication for M1.3**: the Branch A "1
   serve per (specialist, worktree) + session reused on subsequent
   delegations" decision still holds for cwd isolation, but the
   reuse is more powerful than originally sketched — the persisted
   session_id in `opencode.db` means a *fresh* `opencode serve` can
   resume the conversation by GET-ing the same `session_id`. The
   404-recreate path in `_ensure_session` covers the rare case
   where a session has been deleted (or the worktree path changed).
5. **Probe 5a (catalog fetch)**: `GET /config/providers` returns a
   `providers` array (7 providers: `zai`, `zai-coding-plan`, `openrouter`,
   `opencode`, `opencode-go`, `nvidia`, `gmicloud` — note: the user's
   earlier `ollama` + `gmi` are now `gmicloud` and friends, a richer
   multi-provider setup). The catalog is rich and JSON-typed; step 5
   can build `GET /api/models/catalog` on top of `/config/providers`.
6. **Probe 5b (bare-name empirical test)**: `body["model"] =
   "MiniMaxAI/MiniMax-M3"` (no `gmicloud/` prefix) → **400 BadRequest**:
   `Expected object | null, got "MiniMaxAI/MiniMax-M3"`. The v2 protocol
   **rejects bare strings** outright. **K-revised is REQUIRED, not
   optional.** The structured `ModelRef` is the only way to route to
   non-default-provider models.
7. **Probe 5c (gmi round-trip)**: `body["model"] = {providerID: "gmi",
   modelID: "MiniMaxAI/MiniMax-M3"}` → 500 (provider not connected
   in the probe env, but the wire format is accepted). The structured
   pair works at the v2 protocol layer; provider health is a separate
   concern.

**Decisions**:
- **Architecture**: ServeRunner per busy specialist (cwd per worktree, as
  Branch A says). Each serve is a fresh `opencode serve` started in the
  specialist's current worktree.
- **Session storage**: opencode persists sessions in
  `~/.local/share/opencode/opencode.db` (a SQLite database). Sessions
  DO survive opencode process restarts (the SQLite DB is
  persistent; the `opencode serve` process is the in-memory request
  handler). The `session_id` we persist on the Specialist record is
  therefore more useful than originally sketched in the plan: a
  *fresh* `opencode serve` started by the runtime can GET the
  stored `session_id` and resume the conversation. The
  404-recreate path in `_ensure_session` still covers the rare
  case where a session has been deleted (or the worktree path
  changed). Note: cwd still binds to the serve process
  (Branch A's architectural decision), so we still want a
  per-specialist ServeRunner — the cross-restart reuse is a
  *bonus* on top of the cwd-isolation rationale.
  the session is gone and we create a new one — the durable context
  comes from the worktree re-injection preamble (the new session
  inherits the system prompt + model + conversation history *if we send
  it*; this is the M1.7 / R6 territory for full replay).
- **K-revised is locked in**: `Specialist.current_model` becomes
  `ModelRef {provider, model_id}` (or stays a string with a parallel
  `current_provider` field). v1 records (bare string) load as
  `ModelRef(provider=None, model_id=<bare string>)`; the harness falls
  back to today's unqualified-name path with a warning when
  `provider is None` AND the bare string isn't a default-provider model
  (probe 5b proved the fallback would 400 on non-default providers; the
  warning is the only graceful path). Endpoints accept either a
  structured `{provider, model_id}` body or a `provider/model_id` string
  (parsed by `_parse_provider_model`).
- **L-revised locked in**: `harness/opencode.py:_parse_provider_model`
  rewrite (drop the dead `elif model_id:` branch; accept structured
  ModelRef or `provider/model` string; emit `{providerID, modelID}` when
  provider is known, else the unqualified-name path with a warning).
- **Catalog data flow**: `GET /config/providers` (M1.3 step 5) +
  models.dev (R4 phase 2 dropdown UI). The probe (5a) saw 7 providers
  in the user's current `opencode.json`: `zai`, `zai-coding-plan`,
  `openrouter`, `opencode`, `opencode-go`, `nvidia`, `gmicloud`. The
  `opencode` provider block (the opencode-default bundled models) is
  empty in this config — every model the user uses comes from a
  custom provider. R4 phase 2 builds the dropdown UI on top of the
  opencode catalog + models.dev intersection.
- **Branch B discarded**: no fallback path needed (probe 5b says we
  must always provide the structured pair; we don't have a graceful
  degrade to "let the serve figure it out").

## Steps

### Step 0 — Probes ✅ done 2026-08-30
- Probe script `probe_m1_3_step0.py` (run from repo root) executes
  5 sub-probes against a real `opencode serve` in a temp dir with
  a copy of the user's `~/.config/opencode/opencode.json` (so
  ollama + gmi — now gmicloud, zai, openrouter, etc. — are present).
  The npm shim is resolved to the real `.exe` per
  `sweave/harness/opencode.py:_resolve_command` (the shim itself
  isn't directly executable by Python's subprocess).
- Findings recorded in `docs/M1_3_PROBE_RESULTS.md` and summarised
  in the "Branch decision (after probes)" section above. The K-revised
  shape is locked in (structured ModelRef) per probe 5b's empirical
  finding. The bare-name fallback for v1 records survives with a
  warning (probe 5b's failure is for a non-default-provider model
  the user never typed as bare; the warning path is for v1 records
  that reference a default-provider model by bare name).

### Step 1 — ServeRunner (per-specialist serve lifecycle) ~0.75
- `runtime/serve_runner.py`: one `ServeRunner` per specialist with an open delegation.
  Lazy start via existing harness spawn internals (resolved exe, log-file port
  discovery); `health()` = `GET /session` 200; auto-restart on dead process;
  **idle TTL shutdown** (default 30 min — long-lived Bun processes are a known freeze
  risk; §7) — config knob.
- **Orphan sweep** (psutil, §8 adoption): on server startup, find opencode processes
  whose cmdline contains our serve log marker / whose cwd is a `.worktrees/` path and
  whose parent is dead → kill. Never touch the user's own interactive opencode
  processes (heuristic: no `.worktrees/` in cmdline/cwd AND parent alive).
- Lifecycle events on WSEventBus: `serve.started|stopped|restarted {specialist, port}`.
- Tests: mocked subprocess (no live serve) for start/health/restart/TTL; orphan
  heuristic unit tests with synthetic process tables.

### Step 2 — Session resume + worktree re-injection ~0.75
- `SpecialistRuntime.run(delegation, worktree_path, fresh: bool = False)`:
  1. resolve ServeRunner (start/reuse)
  2. session: if `fresh` or no stored session → `POST /session` → persist id on
     Specialist; else verify stored id via `GET /session/{id}` (404 ⇒ recreate +
     warn trace) — **Branch A resume: just POST to the stored id**
  3. worktree re-injection: first message of EVERY delegation includes a context
     preamble: "Task working directory: {worktree_path} (absolute). All file
     operations happen here." (conversational context never assumes cwd)
  4. system prompt sent once per session (not per delegation); per-delegation
     model via `body["model"]` — structured `ModelRef` when K-revised is in
     place (K-revised has a v1→v2 migration in `from_dict` so legacy bare
     strings keep working). The provider is sent explicitly via `{providerID,
     modelID}` when known; falls through to the bare-name path when
     `provider is None` (the warning fires once per session, not per message).
- `harness.attach()` becomes real: delegates to the runtime (kills the respawn stub).
- Trace events: `serve_started`, `session_resumed|session_created`, `worktree_set`,
  `model_resolved` (records the provider/model pair that was actually sent —
  aids R6's dispatch training).
- Tests: mocked — resume path, 404-recreate path, fresh flag, preamble injection,
  session_id persistence, ModelRef routing, bare-name fallback, single-active-task
  queue (second submit waits).

### Step 3 — JobRunner integration ~0.5
- `_run` calls `SpecialistRuntime.run` instead of `harness.spawn` per delegation;
  status transitions unchanged (queued→running→review/done/failed per M1.1 store).
- Delegation record gains `serve_port`/`session_resumed` in trace only (no schema
  bump — trace is the observability surface, records stay stable).
- Tests: end-to-end with mocked harness — two sequential delegations to the same
  specialist share session id; different specialists get different runners.

### Step 4 — Stuck detection v1 + hardening ~0.5
- Stream timeout: no terminal within `config.harness.turn_timeout` (default 15 min)
  ⇒ delegation failed with explicit error + serve recycled (restart on next use).
  (Heartbeat/staleness upgrades deferred to R6 — the resolution-skill consumer.)
- Review status: on stream success the delegation enters `review` (not done) —
  human/cross-review promotes to done (M1.4 finalizes promotion rules).
- Tests: timeout path with a hanging stream mock; review-transition path.

### Step 5 — Live gate + docs ~0.5
- **The M1.3 gate (live)**: the probe results show sessions are
  in-memory in this opencode version — Branch A's "resume across
  restarts" doesn't apply in the way the original sketch imagined. The
  live gate is therefore: **two sequential delegations to the same
  specialist within one ServeRunner lifetime share a session and
  accumulate context** (the per-specialist serve reuses the
  Specialist's `session_id` for the duration of the serve's life; a
  second message after a `running` delegation can continue the
  conversation). Process count stable across the run; TTL shutdown
  verified (idle ServeRunner is killed after `config.harness.ttl`).
- The original sketch's "kill serve → restart → resume" test (Branch
  A proof) becomes "session dies with serve" — the design's session
  reuse only works within one serve lifetime. Document this in
  DESIGN §2.1; don't try to fake it.
- The M1.3 unit + integration test suite (269/269 pytest, +62
  since M1.2 finished) covers the runtime lifecycle, the ModelRef
  K-revised wire shape, the per-specialist queue serialisation, the
  per-key serve runner identity, the structured-vs-bare model
  encoding, the system-prompt-on-session-create path, the 404
  recreate path, the turn timeout, and the review transition. The
  end-to-end live gate against a real opencode requires a configured
  provider (ollama + qwen3:8b, or gmicloud + gmi/*, etc.) reachable
  from the test environment; the M1.3 step 0 probe is the manual
  proof. A scripted live gate is documented in
  `tests/test_m1_3_step5_live_gate.py` (skipped in this env; run by
  hand when a working opencode + provider is available).
- pytest full suite (207 + ~62 new = 269), `run.py --check`,
  `test_full.py`, loader green.
- Docs: DESIGN §4 (OpenCode spawn path → ✅; Specialist runtime ✅,
  per-specialist ServeRunner), §2.1 amendment (Branch A: per-specialist
  serve runner; **sessions are persisted in opencode.db, so the
  stored `session_id` survives opencode restarts** — the 404-recreate
  path covers deletion / worktree-change), R1 M1.3 ✅, PROJECT_STATE
  progress, AGENTS gotchas (serve TTL note; orphan sweep behavior;
  the opencode.db session table is the source of truth for the
  session_id we persist on the Specialist).

## Explicit non-goals
- Parallel tasks within one specialist (queue lands with M1.6 DelegationManager).
- Model-switch enforcement semantics (M1.5 — M1.3 already passes per-task model).
- Encoder-assisted selection, memory replay (Branch B only), multi-project serve
  sharing, cross-vendor anything.
- **Catalog UI (dropdown) — R4 phase 2** (per DESIGN.md: "model combobox grouped by
  provider with live/catalog badges"). M1.3 ships the *data shape* (the
  `ModelRef` field, the catalog-fetch sub-probe, the structured `body["model"]`
  in the harness); R4 ships the dropdown UI on top. M1.3's job is the
  foundation; R4's job is the surface.

## Risks
- **Probe outcome risk**: Branch B costs ~+1 session (buffered in estimate).
- Bun long-lived-process stability (the freeze incident) — mitigated by TTL shutdown,
  log-file I/O, and orphan sweep; never hold serve pipes.
- Session storage is user-global: sessions from other opencode usage coexist — we key
  by our own ids and never list-guess (verify by id only).
- Windows firewall prompts on new listen ports per serve — same as today (one port per
  delegation already happens); unchanged exposure.
- gmi probe costs: ~6 tiny messages, negligible (user-approved provider).
