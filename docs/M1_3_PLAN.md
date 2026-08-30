# M1.3 — Shared serve + durable context (execution plan)

Status: planned, not started. Est. ~2.5–3 sessions (Step 0 ~0.5, build ~2, live gate
~0.5). Predecessors: M1.prep ✅, M1.0 ✅ (v2 API + per-message model done), M1.1 ✅,
M1.2 ✅ (Specialist.session_id exists, unused; M1.2 plan amendments K/L/M/N
applied below). This is R1's **risk sink** — the plan is deliberately
probe-first, and the provider-resolution gap surfaced in the M1.2
post-audit is the new sub-probe in step 0.

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

### Branch decision (after probes)
- **Branch A (expected)**: sessions persist globally; cwd binds to the serve process.
  ⇒ Architecture: **ServeRunner per busy specialist** (not per project) — lazily
  started, cwd = that specialist's *active* worktree, session persisted on the
  Specialist record and resumed across serve restarts. Process count = concurrent
  specialists (≤ pool), sequential within one specialist (§2.1 one-active-task rule).
  DESIGN §2.1's "one shared serve per project" is amended accordingly.
- **Branch B (fallback)**: sessions die with the serve ⇒ durable context becomes
  alive-serve-per-specialist + memory-replay on loss (context = memory bank recall +
  last-output summary). ~+1 session; §2.1 amendment different but same interface.

## Steps

### Step 0 — Probes (~0.5, needs user go for serve launch)
- Scripted probe (temp scratch dirs, file-logging launch path, gmi + ollama,
  ~10 tiny messages across 5 sub-probes):
  1. **Resume across restarts** (probe 1): create session → message → kill
     serve → restart in the SAME cwd → list + message to old id.
  2. **Resumed-session tool task** (probe 2): "run `pwd` (bash tool) and reply
     with its output" against a serve restarted in a DIFFERENT cwd.
  3. **Concurrency** (probe 3): two sessions on one serve, interleaved single
     messages.
  4. **Opencode session storage** (probe 4): locate the on-disk session store
     (file search after first create).
  5. **Provider resolution** (probe 5 — K-revised empirical question):
     a. **Catalog fetch**: `GET /config/providers` and `GET /provider` on a live
        serve. If neither path returns a JSON catalog, fall back to
        `opencode models --format json` (the CLI subcommand). Record the
        discovered shape so step 5 can build `GET /api/models/catalog` on
        the right primitive.
     b. **Bare-name empirical test**: send a message with
        `body["model"] = "MiniMaxAI/MiniMax-M3"` (no `gmi/` prefix) to a live
        serve, record the response status. If 2xx, the fallback is
        sufficient (warning only). If 4xx, the structured pair (K-revised)
        is required and the v2 protocol confirms provider identity must be
        explicit for non-default providers.
     c. **gmi round-trip**: send a message with
        `body["model"] = {providerID: "gmi", modelID: "MiniMaxAI/MiniMax-M3"}`,
        record the response and any provider log line. Confirms the
        multi-provider path works end-to-end.
- Output: findings recorded here + DESIGN §4 + §2.1 amendment; branch A/B
  declared; K-revised shape locked in (structured ModelRef) or downgraded to
  "soft warning on bare name" based on probe 5b result.

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
- **The M1.3 gate (live)**: delegate tiny task A to a test specialist (gmi) → done;
  delegate task B "what was task A about?" to the SAME specialist → answer proves
  durable context; `fresh: true` task C → no memory of A. Process count stable
  across all three; TTL shutdown verified.
- pytest full suite (207 + ~25 new), `run.py --check`, `test_full.py`, loader green.
- Docs: DESIGN §4 (OpenCode spawn path → ✅; Specialist runtime ✅), §2.1 amendment
  (Branch A: per-specialist serve runner), R1 M1.3 ✅, PROJECT_STATE progress,
  AGENTS gotchas (serve TTL note; orphan sweep behavior).

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
