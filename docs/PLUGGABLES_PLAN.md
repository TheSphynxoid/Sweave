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

## 7. Open: differentiation (left open, not decided)

Per user ruling 2026-09-11 this section stays divergent. Pillar-lock
(delivery guarantees / human-gated refine / harness-agnostic / local-first)
was presented and explicitly deferred — "not what I meant by brainstorming".
The next planning round runs an open brainstorm on developer-first jobs
vibe tools won't do, then returns with options + trade-offs for rulings.
No DESIGN.md behavior change lands from this plan until those rulings lock.

## 8. Risks

- Name drift: four kinds collapse back into `plugin` without a grep gate
  in Phase 0. Mitigation: the Phase 0 done-gate is a literal grep.
- Gallery becomes a preset dump: seed-quality bar + provenance
  (`forked_from` + reason, same as `fork_specialist`) or installs rot.
- Skills pilots reshape under R2: file layout is the spec, paths are not
  (TRACKING risk stands).
- Pluggables SDK tempts a framework that owns the loop (DESIGN §8 policy
  forbids it): registries stay thin, Sweave stays the orchestrator.
