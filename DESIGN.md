# Sweave — System Design

> Rebuilt 2026-08-29 after the original design was lost. Lineage: Sweave began as a
> **Polly fork** (Polly = Omnigent's multi-agent coding orchestrator). Decision: Sweave is
> now a **standalone, Polly-style orchestrator** — Omnigent's agent YAML spec is a
> compatibility target, not a dependency. Status markers: ✅ wired · ⚠️ stub · ❌ dead ·
> 📐 planned. Source of truth for "what runs today": PROJECT_STATE.md + the table in §4.

## 1. Vision

Sweave orchestrates a supervisor conversation (the **Session**) that delegates work to
persistent **specialist agents** (backend / frontend / reviewer / custom), each running in
an isolated **git worktree**, opening a **PR as its deliverable**. A human always merges.
Long-term memory is hierarchical: global → project → session.

Non-negotiable principles (inherited from Polly):
1. **The orchestrator never writes code.** It decomposes, delegates, synthesizes.
2. **Worktree isolation.** Every delegated task runs in `.worktrees/{task_id}/{agent}`.
3. **The PR is the deliverable.** Agents never merge; cross-review before "done".
4. **Human merges.** `git merge` / `gh pr merge` are human-only actions.
5. **Harness-agnostic specs.** Agents are declared in YAML; the executor is swappable
   (OpenCode today; Claude Code / Codex on the roadmap).

Distinct from Polly (our additions): project-scoped everything (agents, memory, sessions
per folder on disk), hierarchical memory banks, model routing by role + rules, Windows-first.

## 2. Core concepts

```
Project   a folder on disk, opened via backend file browser; owns agents, sessions, memory
Session   an orchestrated conversation inside a project (parent = supervisor)
ChildSession  one delegated agent run: own worktree, own context, own memory scope
Agent     persistent specialist declared in YAML (Omnigent-spec compatible) or UI-created
Harness   executor adapter implementing AgentProcess (spawn/send/wait/terminate)
Worktree  git isolation unit, branch sweave/{task_id}/{agent}, PR via gh or REST
Memory    hindsight-backed banks: global / project-{name} / session-{id}
Router    pattern → (agent, model) decision, templates like {{models.backend.default}}
```

## 3. Architecture

```
┌──────────┐   ┌─────────────────────────────────────────────┐
│ CLI      │   │ Web SPA (vanilla JS, no build)              │
│ sweave … │   │ chat · children · agents · memory · settings│
└────┬─────┘   └────────────────────┬────────────────────────┘
     │ typer                        │ REST + WS (/ws)
     ▼                              ▼
┌─────────────────────────────────────────────────────────────┐
│ FastAPI  sweave/web/server.py                               │
├─────────────────────────────────────────────────────────────┤
│ Services (internal, sweave/tools + sweave/*):               │
│   RuleRouter      pattern→agent/model   ✅ (llm fallback ⚠️)│
│   DelegateTaskTool worktree+spec+spawn  ✅                  │
│   WorktreeManager git wt + PR (gh/REST) ✅                  │
│   MemoryTool      recall/retain/reflect ✅ (hindsight)     │
│   ProjectManager  projects/sessions/msgs✅ (JSON store)     │
├─────────────────────────────────────────────────────────────┤
│ Harness layer (sweave/harness)                              │
│   AgentSpec → Harness.spawn → AgentProcess(send/wait/term)  │
│   OpenCode: `opencode serve --port 0` + HTTP  ✅            │
│   claude/codex: detect-only 📐 (no spawn)                   │
└─────────────────────────────────────────────────────────────┘
```

Data flow of a delegated task (today, real):
`POST /api/tasks` → RuleRouter.route → DelegateTaskTool.execute →
WorktreeManager.create_worktree → AgentSpec(prompt, model, worktree) →
OpenCodeHarness.spawn (`opencode serve`, cwd=worktree) → HTTP message → result string.

## 4. Component status (audit 2026-08-29)

| Component | Status | Notes |
|---|---|---|
| WorktreeManager (create/remove/list/PR) | ✅ | server, CLI, delegate all call it |
| RuleRouter matching + `{{templates}}` | ✅ | first-match regex/keyword |
| RuleRouter `_llm_fallback` | ⚠️ | keyword heuristic, no LLM call |
| Task delegation → opencode serve | ✅ | real subprocess + HTTP session |
| Agent lifecycle (wait/terminate/attach) | ⚠️ | send returns response; no completion tracking; `attach` respawns; `_active_agents` never cleaned |
| Chat message endpoint | ⚠️ | persist-only, no agent reply (orchestrator loop missing) |
| Agent definitions `sweave/agents/*/config.yaml` | ❌ | never loaded; prompts **hardcoded** in tools/__init__.py:120-150 |
| Claude Code / Codex harnesses | 📐 | detect-only (which/--version), no spawn |
| `/ws` realtime | ⚠️ | route exists (server.py:201) but client saw 404 — re-verify after restart |
| `sweave doctor`, `models`, `rules`, `route` | ✅ | `models --reset` ⚠️ stub |
| Web UI v1 | ✅ | 40/40; welcome-mode gating fixed; see test_sidebar_nav.js |
| Git history | ❌ | **zero commits** — all knowledge unversioned |

## 5. Locked decisions

1. **Standalone, Polly-style** — no omnigent dependency (remove `omnigent[hindsight]` from
   pyproject). Keep Omnigent agent-YAML *shape* as our agent spec.
2. **OpenCode first** — the only spawn-capable harness for milestone 1; claude/codex
   adapters are documented roadmap (R3), not day-one code.
3. **Docs split** — this file = architecture/design; AGENTS.md = how agents work in this
   repo; PROJECT_STATE.md = runtime state + session history.
4. Vanilla-JS no-build SPA; FastAPI serves static + REST + WS.
5. Windows-first (detached servers via scripts, backend-driven file browser).

## 6. Roadmap

### R0 — Hygiene (before any feature)
- **First git commit** (the design was lost once; unversioned = lost again).
- Agent YAML loader: `sweave/agents/loader.py` parses `agents/*/config.yaml`
  (spec_version, executor, prompt, os_env, tools.builtins) → replaces hardcoded prompts
  in DelegateTaskTool; keep hardcoded dict as fallback for missing fields.
- Fix `/ws` 404; fix CLI bug `router.router._llm_fallback` (main.py:68).
- Remove dead deps from pyproject (`omnigent`, `asyncio-mqtt` if unused).

### R1 — Agent runtime completion (make delegation trustworthy)
- AgentProcess lifecycle: completion detection (opencode session status / idle timeout),
  terminate on done, cleanup `_active_agents`, real `attach`.
- ChildSession records worktree/branch/PR URL + status transitions (queued→running→
  review→done/failed) — mirrors Polly's `.polly/registry.json` as per-project registry.
- Orchestrator chat loop: messages endpoint routes through the supervisor model instead
  of persist-only.

### R2 — Orchestrator skills (Polly's core loop)
- `/fanout`: parallel-safe subtasks → one worktree + one child each → PR per child.
- `/cross-review`: implementer's diff → *different-role* reviewer child; blocking issues
  loop back as fixes. (Same-vendor rule becomes: reviewer model ≠ implementer model.)
- `/investigate`: read-only children (no worktree commit), synthesized findings.
- Skills = skills/{name}/SKILL.md conventions + delegation presets; human merges, always.

### R3 — Multi-harness
- `ClaudeCodeHarness`, `CodexHarness` implementing AgentProcess (subprocess/headless),
  register in harness_registry; per-agent `harness:` field already in AgentSpec.
- Cross-vendor review then = reviewer on a different harness than implementer.

### R4 — Web UI v2 (folded from UI_PLAN.md)
- Phase 1: statusline topbar (project + visible CWD), session dropdown w/ search,
  Ctrl+K palette, `GET /api/models/catalog` (live harness models + models.dev cache).
- Phase 2: model combobox grouped by provider with live/catalog badges.
- Phase 3: agents workbench (two-pane, ▶ Run → child, activity, pulse via /ws).
- Regression harness: Playwright+Edge scripts (see test_sidebar_nav.js pattern).

### R5 — Packaging
- `pipx install sweave`, versioned releases, first public README pass.

## 7. Risks / open questions
- OpenCode serve port discovery (probe 4096-4199) is fragile — revisit with `--port 0`
  stdout parsing only.
- No completion tracking yet: "task done" currently = HTTP response received (R1 risk).
- JSON-file storage for projects/sessions is fine single-user; SQLite migration deferred.
- models.dev catalog cache staleness → refresh endpoint in R4.
- Windows subprocess lifecycle (orphaned opencode.exe on crash) — add PID sweep in R1.
