# Pluggables Plan — taxonomy + application extensions

Status: planned (2026-09-11). Plan of record for the next execution session.
Supercedes: the one-liners in `docs/R4_PLAN.md:54` (gallery) and
`docs/TRACKING_PLAN.md` Phase C (first skills). Does not replace them —
it gives them names so they stop colliding.

## 1. Reality evidence (measured, not assumed)

- No `skills/` dir anywhere in the repo (TRACKING T4, re-verified 2026-09-11
  via glob `skills/**/*` + `sweave/**/skills/**` — both empty).
- Specialist store exists and works (`sweave/runtime/specialist_store.py:1-37`):
  global `~/.sweave/agents.yaml` + per-project `{project}/.sweave/agents.json`
  + seed templates; resolution project → global → seed; `fork_specialist`
  + `fork_policy`; `{{var}}` prompt templates; per-seed model overrides.
- Bridges exist and are internal only:
  `sweave/runtime/permission_bridge_plugin.ts` → island
  `~/.sweave/opencode/plugins/` injected as `OPENCODE_CONFIG_DIR`
  (`sweave/runtime/permission_bridge.py:10-28`). Project-dir
  `.opencode/plugins/` rejected by ruling (code auto-executes).
- Extension points that already exist as Protocols/registries:
  - Harness: `AgentProcess` Protocol + `Harness` ABC + `harness_registry`
    (`sweave/harness/base.py:110-190`). Only `opencode` spawns today;
    claude/codex detect-only; custom engine per `docs/CUSTOM_ENGINE_PLAN.md`.
  - Memory: `MemoryBackend` Protocol (`recall/retain/reflect/health_check`)
    + `MemoryFactory.create` (`sweave/memory/backends.py:42-60,306`).
    Default `hindsight/embedded_slim` unusable out of the box (R4.4 audit);
    local-first file backend is the re-cut plan.
- MCP surface is small and deliberate: `defer` + `list_specialists` +
  `ask_human` + `escalate` (+ `fork_specialist`); native `question` denied
  on both managed agents. No saturation rule so far except TRACKING's
  "zero new always-visible MCP tools" for skill reads.

## 2. Taxonomy (user-locked 2026-09-11)

Four kinds. The word `plugin` means exactly one of them.

1. **Skills** — markdown + file-backed procedures agents READ.
   Shape: `skills/{name}/SKILL.md` + records under
   `{project}/.sweave/{tickets,plans}/*.md`. Read via existing
   `read/glob` (opencode `skill`+`read`, custom engine native).
   No code execution by Sweave itself. First pilots: `skills/plan`,
   `skills/tickets` (TRACKING Phase C). Standalone `customize-sweave`
   stays post-MVP.
2. **Specialist presets** — starter identities agents ARE.
   Shape: Omnigent-spec YAML (same as `sweave/agents/*/config.yaml`).
   Install target: the existing specialist store (project or global scope).
   Gallery: browse/install/import/export (R4 one-liner, now specified here).
3. **Bridges** — opencode TS code Sweave injects to close a wire gap.
   Example: permission bridge. Island-isolated, versioned with Sweave,
   never user-installed, never in project dirs. The word `plugin` in
   opencode docs maps here, nowhere else.
4. **Pluggables (= Plugins)** — Python extensions that extend Sweave itself.
   This is what `plugin` means unqualified. V1 surface (all already
   Protocols/registries, so this is productization, not invention):
   - Harness adapters (`Harness` + `harness_registry`)
   - Memory backends (`MemoryBackend`: Embedder + VectorStore + Logic
     per DESIGN §2.3; `MemoryFactory` fails closed)
   - Resolution-queue consumers (v1 rule-based + reviewer; v2 small-model)
   - (deferred) escalation notifiers, detail-view projectors, CLI commands

## 3. Rulings locked 2026-09-11

- Names above are binding in code, docs, and UI. No new use of `plugin`
  for skills or presets.
- Refine gating: **auto-local, promote-global, rollback everywhere**.
  Session-local harness edits auto-apply; project/global promotion needs
  human promote (same endpoint discipline as `review→done`).
  Rollback is first-class at every scope. Note: for local work the
  worktree itself acts as the snapshot — global scope is where versioned
  rollback needs real design.
- Skill reads add zero new always-visible MCP tools (TRACKING rule stands).
- Bridges never live in project dirs (permission-bridge ruling stands).
- Defer wait-set (locked 2026-09-11): `defer` stays non-blocking
  (`queued: <id>` at tool level, `sweave/mcp/__init__.py:244-287`);
  a `blocking` flag puts that child in the synthesis join set
  (`ChatLoop._wait_for_children`, `sweave/chat/loop.py:346-384),
  `false` = fire-and-forget into the Children lane. No tool-call
  holding (kills fanout, burns the turn timeout on one child).
- Review-request record (locked 2026-09-11): a finishing specialist
  yields status `review` + a review-request (reviewer-role hint, diff
  pointer, manifest/confidence); the orchestrator resolves it
  explicitly (`defer(target=reviewer)`) or batched when the wait-set
  settles. Verdicts flow through `POST /api/delegations/{id}/promote`
  only. Specialists never spawn reviewers (one-authority rule stands).
  Detail design lands with R2 cross-review.
- Per-specialist tool enforcement (locked 2026-09-11): one `Specialist`
  record is the source of truth for tool policy. Opencode translates
  it by rendering `agent.sweave-spec-{name}` entries (Option A;
  system prompt stays a session-level send there — transitional).
  The custom engine consumes it natively per run
  (`tools[] + permission_map`, `docs/CUSTOM_ENGINE_PLAN.md:81`).
  Opencode render quirks stay quarantined in `mcp_config.py` +
  `harness/opencode.py` per the wire-drift doctrine.
  Still owed: new-server default, reviewer locked-vs-template,
  allow/deny-only values (proposed: default-off, locked, no `ask`).

## 4. Goal state

- A user can browse preset gallery → install `backend-orders` into their
  project → see it in the resolver → defer to it, with zero new concepts.
- A user can read `skills/plan/SKILL.md` and understand how plans persist
  across sessions on both harnesses, without learning MCP.
- A third party can ship a `Harness` or `MemoryBackend` behind a manifest
  (name, version, scopes, permissions) with install/enable/disable,
  health check, and rollback — without forking Sweave.
- `sweave doctor` reports pluggable health alongside serves and models.

## 5. Phases (each shippable, each gated)

### Phase 0 — Names + inventory (this plan, no code)
Land this file + a taxonomy note in DESIGN §8 (one paragraph, no behavior
change). Done-gate: grep `plugin` in docs resolves to exactly one meaning
per file; TRACKING Phase C + R4 gallery reference this file.

### Phase A — Specialist presets gallery (next execution)
Gallery over Omnigent YAML: browse (seed + installed), install to
project/global scope via existing store writes, import/export round-trip,
seed-shadow guard preserved (saver still skips `scope == "seed"`).
Explicit non-goal: executable skills, bridges, pluggable SDK.
Done-gate: install → resolve → list → defer round-trip via API + UI;
pytest +N (store/gallery), vitest +M (gallery cards), build green.

### Phase B — Skills pilots (`skills/plan`, `skills/tickets`)
File layout is the spec (`skills/{name}/SKILL.md` + per-project `.md`
mirrors); read-path demonstrated on both harnesses (opencode `skill`+`read`,
custom native); orchestrator files, specialists escalate (no new tools
for them — TRACKING ruling). Done-gate: skill-driven read survives session
close on both harnesses; no new MCP slots; docs/GOTCHAS entry.

### Phase C — Pluggables SDK v1 (Harness + Memory + Resolution consumer)
Manifest + discovery (project → global → builtin), enable/disable,
health (`doctor` + Settings), versioned rollback, fail-closed factory.
Harness adapters ship probe gates per the wire-drift doctrine
(`docs/CUSTOM_ENGINE_PLAN.md` Step 0); memory backends ship the three
retention badges + secret tag-and-vault boundary (R4.4 rulings).
Explicit non-goal: general Python plugin sandbox (bridges stay bundled;
third-party code runs with user permissions + explicit trust, same as
Prime's warning — documented, not sandboxed in v1).

## 6. Explicit non-goals

- No skill execution runtime (skills are read, not run, in v1).
- No project-dir bridges or user-supplied opencode plugins.
- No scheduler/cron construction (TRACKING Phase D stands — visual only).
- No `customize-sweave` here (standalone, post-MVP).
- No differentiation lock-in (left open per 2026-09-11 — see §7).

## 7. Explored: differentiation (explored 2026-09-11, NOT locked)

Prior art noted honestly: promptfoo (open-source prompt eval/regression)
and DSPy (optimize LM programs against metrics) are the ancestors of any
"CI for the harness" idea; LangSmith/Langfuse cover output observability;
closed labs co-design harness+model invisibly. None wires evals as
promotion gates inside a live orchestrator fed by its own delivery
records — that composition is the opening. Prime's Factorio cheat
(reward hack preserved as a skill) is the cautionary tale for
improvement-without-gates.

Five directions explored with the user, none locked. Each seeds from
machinery already in the repo. The sixth (training ground) is a
parallel side-direction, not a minor — tracked alongside, not after.

- **Runs on cheap models.** Prime's RLM needs frontier models (their own
  paper: Flash-Lite variants underperformed the baseline — capability
  floor). Sweave's protocols (structured defer, typed manifests,
  promotion endpoints, wait-sets) are built so a cheap model can PM
  with strong engineers (R6 thesis). First step is a benchmark, not a
  feature: same task, cheap vs frontier orchestrator, delegation
  success compared.
- **Agent estimation.** Estimate tokens/time at defer time, track
  estimate-vs-actual per specialist from trace `tokens_used` + cost
  records, calibrate. Nobody does this. First step: record only.
- **Contract-first fanout.** Agree interface contracts (API shapes, file
  boundaries) before parallel implementation; sides code against the
  contract; cross-review checks conformance. Moves semantic conflicts
  (frontend calls an API backend never added) from review time to plan
  time. Seeds: Stage-0 overlap check + manifests + resolution queue.
  First step: a contract record type on the parent delegation.
- **CI for the harness.** Golden task sets per project; prompt / routing /
  tool-policy changes evaluated before promotion, rollback on red.
  Seeds: `override_log.jsonl` gold labels (M1.2), traces, live-gate
  scripts. First step: golden-set format + one eval runner script.
- **Audit export.** Actor+reason log for merges, promotes, grants,
  refines — exportable for regulated shops. Seeds: traces +
  override log + escalation store already record most of it. First
  step: a projection endpoint on the `detail_view.py` pattern.
- **Training ground (parallel side-direction).** Sweave as the harness
  labs train against instead of building in-house. Two tracks: (a) RL
  environment — fresh worktree = reset, delegation = episode, test
  evidence + promote verdicts = verifiable reward, constrained action
  space = clean credit assignment (vs free-form REPL); needs a formal
  Env API + parallel scale story. (b) Trajectory datasets — traces +
  reviewer verdicts + promote/demote + override gold labels as SFT /
  preference data; needs curation/export + opt-in consent (local-first
  is the privacy answer). Interoperate, don't compete: export in
  `verifiers`-compatible shape. Composes with CI (golden tasks = train
  distribution) and cheap-models (small models train well here).
  Risks: single-machine JobRunner vs hundred-env scale; reward gaming
  (verifiable tests as reward, never LLM-judged success).
- Deferred without exploration: learn-from-the-human (human-action
  distillation). May return.

### Remix round (2026-09-11, explored, NOT locked)
- **Dogfood loop.** Track-2 trajectory exports train Sweave's own R6
  encoder heads (intent/dispatch/resolution/mediation). Sweave becomes
  its own first training customer; opted-in user trajectories improve
  dispatch for everyone. Closes training-ground × cheap-models × R6.
- **Embedder decomposition.** R6 heads grow beyond dispatch: split tasks
  by similarity to past delegations, assign reviewers by embedding
  distance, match subtasks to existing contracts.
- **Failure postmortems.** Failed delegations yield structured records
  (tried / failed / hypothesis), retrieved on similar tasks, feeding
  estimates and CI golden-negatives.
- **Skills with tests.** Presets/skills ship golden tests; install runs
  their evals; verified badges carry proof. Marketplace with teeth.
- **Replay debugger.** Step through a trace turn-by-turn, inspect the
  composed prompt at each step, fork a turn with a different model.
  The plan board grows into an ops console.
- **Model query planner.** Like a SQL planner picking joins from table
  stats: at defer time Sweave estimates the task and picks the cheapest
  model meeting the project's cost/latency policy. Needs estimation
  first; `models.yaml` role buckets are the tiers.
- **Training-ground extension: shared test-bed + aggregated DB.**
  Beyond per-lab use: a shared environment suite for general ML
  research (swarm-style multi-agent setups) plus an aggregated,
  opt-in, anonymized trajectory database pooled from consenting users.
  Always opt-in, never a default; local-first stays the posture.

### Round 3 (2026-09-11, explored, NOT locked)

- **Flywheel map.** Outputs feeding inputs: traces → estimation
  calibration → query planner → cheaper runs → more runs → more
  traces; verdicts + override logs → gold labels → dogfood heads →
  better dispatch → more usage → more labels; postmortems → lore →
  fewer failures; golden tasks → train distribution → better heads.
  Compounding core = the record layer. Rule: every feature emits
  records; records are the product.
- **Provenance ledger.** Actor+reason+hash for every commit, PR,
  promote, grant, refine, verdict. Seeds: `Sweave-Delegation` trailers
  + manifests + traces. Supply-chain for agent work; the compliance
  wedge and the training-provenance answer in one.
- **Multi-human teams.** Roster, lore, plans shared across humans;
  presence; human-to-human handoff on the same protocol as delegation.
  Key user insight (locked as design intent): the collaboration
  protocol IS the training protocol — human↔human, human↔Sweave and
  Sweave↔Sweave interactions all emit the same records, so scaling
  collaboration scales training (multiplexing). One protocol, three
  uses.
- **Sweave-to-Sweave.** Delegations callable across machines/repos:
  remote execution, cross-repo contracts, capability advertisement,
  budget escrow across trust boundaries. `defer` semantics generalize
  to a remote roster. Unlocks the team-server and multi-repo stories.
  Extended 2026-09-11: the same plane carries TOTAL project
  orchestration/automation — sprints planned and executed across
  sessions, multiple orchestrators coordinating, the `/plan` kanban
  as the shared state.
  Coordination design (explored 2026-09-11, NOT locked):
  - **Spearhead (scoped single-writer, rotation deferred).** The valuable
    core is the single-writer principle — it generalizes the existing
    one-authority doctrines (orchestrator-only spawn §2.1, human-only
    merge, promote endpoint as sole path to `done`). Headship is
    scoped (per sprint/epic/lane), not global: only the head closes
    the sprint / merges the integration branch / files cross-project
    defers. Token rotation across peers is deferred — it rebuilds
    Raft-lite (leases, handoff, failure detection) before static
    scoping has been shown to hurt. Rotation may return as a
    load-balancing policy across sprints.
  - **Cross-boundary wait reuses the wait-set.** Pass-and-wait vs
    fire-and-forget across projects is the locked `blocking` flag at
    wider scope: `true` joins at the reunion, `false` runs async
    against contracts. No new primitive. New failure modes only:
    remote stall (bounded wait + existing escalation machinery),
    partial results.
  - **Reunion (bounded sync ritual) + mediation (three-tier).** Default
    is NO waiting — orchestrators work async against contracts;
    the reunion is the scheduled wait (barrier with an LLM-readable
    agenda), triggered by milestone (wait-set settled) or escalation
    threshold, not wall-clock (scheduler stays deferred per TRACKING
    Phase D). Mediation on disagreement: automated contract-conformance
    first, spearhead breaks ties, human for the rest — mirrors the
    review pipeline. Every reunion emits a decision log into lore.
  Open tension (not resolved): the orchestrator is today a per-project
  singleton (DESIGN §2.2) — federation needs either
  orchestrator-per-workstream, a meta-orchestrator tier, or
  cross-project delegation. Decision owed before any spec.

  Meta-view (2026-09-11): today the HUMAN is the spearhead — creates
  sessions, passes messages, orchestrates everything. The automated
  spearhead unifies all sessions under one orchestration umbrella the
  user interacts with (the funnels, unified); the human moves up one
  level to policy + exceptions + merges. Three planes vs prime-agent's
  two (daemon + workers): execution (sessions), coordination
  (orchestrators, reunions, spearhead), governance (promotions,
  review phasing, contracts, audit). Reunion + mediation improves the
  existing worktree/diff/review phasing rather than replacing it.
  Temporal phasing: reunion-v1 runs runtime-driven on opencode (the
  runtime holds barrier state and wakes orchestrators with composed
  prompts — the synthesis pattern generalized; no new primitive).
  Full temporal coordination (schedules, heartbeats, cross-session
  messaging, sleep/wake) needs the custom engine — opencode's gaps
  are concrete: turns are bounded request/response (waiting = holding
  HTTP open), no inter-session messaging, no scheduled wakeups. So
  the design does NOT block on the custom engine; the engine removes
  polling/timeout fragility later.

  Group memory (explored 2026-09-11, NOT locked): a fourth bank scope
  `group-{id}` beside global / project / session, owned by the
  spearhead scope (sprint/epic) and visible to member sessions only.
  Context sharing is dynamic and curated, not broadcast: sessions
  escalate memory candidates, the spearhead promotes them into the
  group bank — the promotion discipline a fourth time (review→done,
  refine local→global, review-request→review, candidate→group).
  Unmediated shared banks decay into mush; curation is load-bearing.
  Retrieval across own + group + project banks is ranked by a
  COORDINATION embedding (relevance-to-my-contract, not generic
  text similarity), trainable from usage signals (what retrieved
  memory actually got used downstream) — another flywheel loop, and
  a natural first customer for the dogfood loop. Lifecycle: born
  with the sprint, archived into project lore with provenance at
  reunion close.

  Spearhead-as-scrum-master (explored 2026-09-11, NOT locked): the
  role mapping is nearly 1:1 — spearhead creates sprints and runs
  reunions (standup + retro combined), mediation clears impediments
  (escalations ARE impediments), tickets are the backlog, the kanban
  is the board, estimation grows toward velocity, postmortems are
  retros, the human is the product owner (priorities + acceptance via
  merges/promotes). Lean into the mapping where it clarifies (names:
  sprint, backlog, reunion, impediment); don't force it where the
  medium differs (async agents: milestone cadence, not daily;
  spearhead also holds technical authority — assignment + conformance
  — which Scrum splits across team/PO). Value: any engineering
  manager understands the product in one paragraph.
- **Five-minute onboard.** Open repo → stack detected → roster
  proposed → first delegation in minutes. The demo that sells dev
  tools. Seeds: agents loader, models sync, gallery, topology gen.
  First step: measure time-to-first-delegation, then drive it down.
- **Anti-differentiators (stance adopted 2026-09-11; NOT the moat).**  Positioning by refusal — sentences competitors cannot say: never
  auto-merge to base; never silent exfiltration (fail-closed, explicit
  consent); never prompt-only guarantees (enforced or it doesn't ship);
  never unbounded autonomy (budgets + gates always); never train on user
  data by default. User ruling: these are stance (mine, by extension the
  project's) and belong written in DESIGN.md — but stance alone is not
  differentiation; the moat must come from capabilities. Candidate for
  a DESIGN.md principles list; no behavior change.

### Prior session extraction (Sweave-20260910-053752-57e465, "Themes",
## 2026-09-10 — 30 msgs, explored, NOT locked)

Already covered elsewhere (no action): roster self-upgrade loop
(= refine), embedder delegation ladder A–C (= R6 dispatch + dogfood),
diff review gate (= R2 cross-review), timeline visualizer (= M1.9
detail view), model routing tiers (= ModelRef). New extractions:
- **Chain-state injection** (agent-side, cheap, high-value): runtime
  knows depth/budget/siblings/elapsed — the child doesn't. One
  `chain-context` line per turn ("depth 1/2, budget 40% left, 3
  siblings active") so agents wrap up instead of tripping blind caps.
  Single-file change in `SpecialistRuntime` prompt assembly.
- **Verified gate vocabulary**: per-turn prompt instructs run-gate +
  report-exact-counts; synthesis flags unverified claims. Turns the
  audit-trust-per-claim rule into infrastructure. Pairs with
  postmortems (unverified → suspect).
- **Reasoning-loop detector** (user's embedder idea #2): signature is
  failure-streak + semantic sameness of intent across *varying*
  attempts — not repeated identical calls. Intervention at turn
  boundaries only (no mid-turn inject seam; `reject` aborts).
  Dogfood acceptance gate: run the detector on the trace log of the
  session that built it — must flag its own loops. M1.12 incident
  traces are seed data. Nudge-only mode first.
- **Tripwire badges** (UI): live delegation cards showing tokens
  spent, depth, elapsed from existing server-side fires.
- **R4-thread UI backlog**: semantic icon indirection over lucide
  (4th token family after fonts/radius); curated layout list MVP
  (not customizable — bounded test matrix); focus mode + browser
  fullscreen; Electron wrappability (backend+SPA already ideal);
  keyboard-first pane nav (Ctrl+pages/panes, arrows+enter, no modal
  state). Theme engine/fonts work is partly landed (37 tokens);
  the rest waits on the R4 thread.
- **Self-diagnose skill (explored 2026-09-11, NOT locked).** A skill
  that pulls a small HERMITIC diagnose-suite from git (pinned to the
  installed version's tag/commit, hash-checked, fail-closed on skew)
  and runs it on the user's machine — chat-driven `sweave doctor`.
  Constraints: not the 74-file dev corpus (assumes repo layout + dev
  deps); code-download executes under the bridges ruling (explicit
  trust, never silent); offline degrades to the bundled `doctor`.
  Packaging twin: exclude `tests/` from the shipped artifact (R5
  packaging) — installs stay lean either way. Natural pilot for
  skills-with-tests.
- **Standing: context auditing (adopted 2026-09-12).** Baseline
  measured: MCP surface 2,880 chars, orchestrator prompt 4,390,
  permission profiles <200, per-turn memory avg 0 (backend down),
  synthesis avg 0 (join turns only). A `scripts/context-audit`
  (per-role, per-turn in/out from traces + static surfaces, budgets
  per surface pinned so overhead can't silently regrow) feeds
  estimation attribution + the query planner's pricing. Memory
  section will rise when R4.4 lands — expected, inside its 2K cap.

## 8. Risks

- Name drift: four kinds collapse back into `plugin` without a grep gate
  in Phase 0. Mitigation: the Phase 0 done-gate is a literal grep.
- Gallery becomes a preset dump: seed-quality bar + provenance
  (`forked_from` + reason, same as `fork_specialist`) or installs rot.
- Skills pilots reshape under R2: file layout is the spec, paths are not
  (TRACKING risk stands).
- Pluggables SDK tempts a framework that owns the loop (DESIGN §8 policy
  forbids it): registries stay thin, Sweave stays the orchestrator.
