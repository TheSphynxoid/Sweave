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
- **Five-minute onboard.** Open repo → stack detected → roster
  proposed → first delegation in minutes. The demo that sells dev
  tools. Seeds: agents loader, models sync, gallery, topology gen.
  First step: measure time-to-first-delegation, then drive it down.
- **Anti-differentiators (explained, not yet adopted).** Positioning by
  refusal — sentences competitors cannot say: never auto-merge to
  base; never silent exfiltration (fail-closed, explicit consent);
  never prompt-only guarantees (enforced or it doesn't ship); never
  unbounded autonomy (budgets + gates always); never train on user
  data by default. Candidate for a written list in DESIGN.md.

## 8. Risks

- Name drift: four kinds collapse back into `plugin` without a grep gate
  in Phase 0. Mitigation: the Phase 0 done-gate is a literal grep.
- Gallery becomes a preset dump: seed-quality bar + provenance
  (`forked_from` + reason, same as `fork_specialist`) or installs rot.
- Skills pilots reshape under R2: file layout is the spec, paths are not
  (TRACKING risk stands).
- Pluggables SDK tempts a framework that owns the loop (DESIGN §8 policy
  forbids it): registries stay thin, Sweave stays the orchestrator.
