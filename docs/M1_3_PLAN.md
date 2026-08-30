# M1.3 — Shared serve + durable context (execution plan)

Status: planned, not started. Est. ~2.5–3 sessions (Step 0 ~0.5, build ~2, live gate
~0.5). Predecessors: M1.prep ✅, M1.0 ◐ (v2 API + per-message model done), M1.1 ✅,
M1.2 ✅ (Specialist.session_id exists, unused). This is R1's **risk sink** — the plan
is deliberately probe-first.

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

## The two open probes — and a third discovered one

1. **Resume across serve restarts** (M1.0 leftover): does a NEW serve, started in the
   same cwd, see and accept messages for a session created by a previous serve?
   (`GET /session` lists old ids? `POST /session/{id}/message` succeeds?) Where does
   opencode store session state (global `~/.local/share/opencode` vs per-project
   `.opencode/`)?
2. **Completion signal semantics**: current terminal detection = assistant object with
   parts. Confirm: does the stream for ONE message POST cover the agent's whole turn
   (tool calls included) until the final assistant message? Any partial-turn traps?
3. **NEW — cwd binding for tool execution** (decides the architecture): opencode tools
   (bash/read/edit) execute in the serve process's cwd. If cwd binds to the *serve*,
   a "one shared serve per project" hosting specialists in DIFFERENT worktrees breaks
   tool calls (specialist A's tools would run in specialist B's worktree). Probe: task
   "run pwd and report" against a resumed session whose serve now runs in a different
   cwd — which cwd do tools see?

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
- Scripted probe (temp scratch dirs, file-logging launch path, gmi, ~6 tiny messages):
  1. create session → message → kill serve → restart → list + message to old id
  2. resumed-session tool task: "run `pwd` (bash tool) and reply with its output"
     against a serve restarted in a DIFFERENT cwd
  3. two sessions on one serve, interleaved single messages (concurrency sanity)
  4. locate opencode session storage on disk (file search after first create)
- Output: findings recorded here + DESIGN §4; branch A/B declared; §2.1 amendment
  drafted if Branch A.

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
  4. system prompt sent once per session (not per delegation); per-delegation model
     via existing `body["model"]`
- `harness.attach()` becomes real: delegates to the runtime (kills the respawn stub).
- Trace events: `serve_started`, `session_resumed|session_created`, `worktree_set`.
- Tests: mocked — resume path, 404-recreate path, fresh flag, preamble injection,
  session_id persistence; single-active-task queue (second submit waits).

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

## Risks
- **Probe outcome risk**: Branch B costs ~+1 session (buffered in estimate).
- Bun long-lived-process stability (the freeze incident) — mitigated by TTL shutdown,
  log-file I/O, and orphan sweep; never hold serve pipes.
- Session storage is user-global: sessions from other opencode usage coexist — we key
  by our own ids and never list-guess (verify by id only).
- Windows firewall prompts on new listen ports per serve — same as today (one port per
  delegation already happens); unchanged exposure.
- gmi probe costs: ~6 tiny messages, negligible (user-approved provider).
