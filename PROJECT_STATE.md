# Sweave - Project State for Future Agents

## Overview

**Sweave** is a multi-agent orchestration platform (CLI: `sweave`) with a **project-scoped** architecture:
- **Project** = a folder on disk (opened via backend file browser)
- **Session** = an orchestrated conversation within a project
  - Parent session runs the orchestrator
  - Spawns **child sessions** (specialist agent runs) for delegated work
- **Memory** is hierarchical: `global` → `project-{name}` → `session-{id}`

- **License**: Apache 2.0
- **Python**: 3.11+
- **Project root**: `C:\Users\user\sweave\`
- **Git**: initialized
- **Web server**: FastAPI + single-page application (SPA)

---

## Current State (Latest Build - All Working)

> **Design**: `DESIGN.md` (architecture, component status, roadmap R0–R7).
> **Agent guide**: `AGENTS.md`. UI v2 plan is folded into DESIGN.md §6 R4.
> **pytest is the source of truth for logic tests**; root scripts are smoke gates.

### What Works (Verified - 40/40 Tests Pass)
- ✅ **App is VISIBLE** after loading (critical fix: `app.classList.remove('hidden')`)
- ✅ **Sidebar navigation WORKS** (5 tabs: Chat, Children, Agents, Memory, Settings)
- ✅ Full-screen layout (CSS: `height: 100vh; width: 100vw` on body and app)
- ✅ Backend file browser (drives, list, validate, create)
- ✅ Project welcome screen with 3 action cards
- ✅ Topbar shows project name and session name (clickable for quick switching)
- ✅ Sidebar toggle for collapsing
- ✅ All modals work (project, session, agent, detail)
- ✅ Theme system with 5 preset themes (dark, light, dracula, nord, catppuccin)
- ✅ WebSocket real-time updates
- ✅ Memory recall/reflect/retain operations
- ✅ Global error handlers that show errors on screen for debugging
- ✅ Backend-driven file browser (no "Folder picker not supported" error)

### Test Results (All Passing - verified 2026-09-05)
- **452/452** in `pytest tests/` (source of truth for logic tests; +5 from
  the R4.0 wire-shape regression file)
- **13/13** in `run.py --check` (endpoint smoke + SPA mounted from sweave-web/dist)
- **40** vitest unit tests in `sweave-web/` (design + chat reducer + tree)
- **2** Playwright e2e tests in `sweave-web/e2e/` (CI gate)
- **ALL GREEN** in `test_agents_loader.py` (24 checks)
- v1 vanilla UI tests (test_full.py, test_sidebar_nav.js, test_promote_ui.js) retired

### Currently Running
- **Web server**: not running (clean stop after final M1.2 sweep 2026-08-30)
- **Start with**: `python start_server.py 8100 127.0.0.1` (from repo root!)
- **Stop with**: `python stop_server.py` (from repo root — web.pid is CWD-relative)
- **Logs**: `web.log` / `web_err.log`

### M1 progress (after M1.prep + M1.0 + M1.1 + M1.2 + M1.3 + M1.4+M1.5)
- ✅ **M1.prep** — all 8 steps (9 commits)
- ✅ **M1.0 Live serve probe** — done: v2 HTTP API + per-message model +
  chunked JSON stream consumption. Its leftovers (session resume across
  serve restarts, completion semantics) were closed by the M1.3 step-0
  probes.
- ✅ **M1.1 Record split** — done: Delegation v2 schema + per-project
  persistence + SubAgentRun + API filters + UI v1 bridge.
- ✅ **M1.2 Specialist store + CRUD** — done: `Specialist` dataclass +
  per-scope stores (global `~/.sweave/agents.yaml` + per-project
  `{project}/.sweave/agents.json`) + `SpecialistResolver` (project→global→seed
  resolution, orchestrator auto-seed singleton) + `is_orchestrator` flag
  + model precedence chain at submit + `/api/specialists` CRUD +
  `PUT /api/specialists/{name}/model` (emits `model.changed` with new
  `{name, model, scope}` shape) + override log (gold labels for R6
  dispatch) + `SubAgentRun` endpoints (`POST/GET/finish`) + UI v1
  compat bridge writes a `ChildSession` carrying `delegation_id` on
  submit (R4 removes the bridge). Tier framing baked in: Orchestrator /
  Specialist / SubAgent three-tier model with Orchestrator as a singleton
  (flag on Specialist; not a separate type).
- ✅ **M1.3 Shared serve + durable context** — done: `ServeRunner` per
  (specialist, worktree) pair with lazy start, idle TTL, psutil orphan
  sweep. `SpecialistRuntime` orchestrates one delegation: session
  create / 404-recreate / reuse + worktree re-injection preamble +
  structured ModelRef in `body["model"]` (M1.3 K-revised; user's
  `opencode.json` has multi-provider: ollama, gmicloud, zai, opencode
  default). `JobRunner` integrates the runtime (legacy
  `delegate_tool.execute` path preserved). M1.3 step 4: per-turn
  timeout (default 15 min) on both paths; success routes to
  `review` (M1.4 promotes to `done`). M1.3 step 0 (probe) amended:
  opencode persists sessions in `~/.local/share/opencode/opencode.db`
  (SQLite), so the stored `session_id` survives opencode process
  restarts — the 404-recreate path covers the rare case where a
  session has been deleted (or the worktree path changed). Branch A's
  cwd isolation is still the architectural rationale for
  per-specialist runners; cross-restart session reuse is a bonus.
  ServeRunner's lifetime. 269/269 pytest across 25 files; 13/13
  run.py --check; 40/40 test_full; 8/8 test_browser; ALL GREEN
  test_agents_loader.
- ▶ **Next**: M1.3 shared serve + durable context (per DESIGN.md §6 R1)
- ✅ **M1.4+M1.5 Lifecycle promotion + model-at-request-time** — done
  2026-09-03 per `docs/M1_4_5_PLAN.md` (merged; M1.4 is folded into
  M1.5). Rulings locked 2026-08-30:
  - **Human promotes**: a delegation reaching `review` stays there
    until the user promotes it (`review → done`) via
    `POST /api/delegations/{id}/promote` (or the Children-tab "Mark
    done" button — same endpoint). R2's cross-review later automates
    the verdict; the API is the automation seam. Extends the
    human-merges rule to lifecycle promotion.
  - 5 steps:
    1. Flake fix + `_active_agents` audit. The M1.3 step 3 runtime
       tests let `ServeRunner.start()` spawn a real opencode serve
       subprocess even though the tests only assert on the wire shape
       — that was the leak source for the order-dependent
       `test_job_runner_runtime_path_legacy_model_string` flake.
       Fix: a module-scoped autouse fixture pins
       `SWEAVE_MOCK_OPENCODE=1` for the whole file. The runner no-ops
       into a sentinel (`port=0`, no subprocess, `base_url="http://mock-opencode"`)
       and `_build_process` returns a stub `OpenCodeProcess`. New
       regression test `test_runtime_runner_is_mocked_no_real_subprocess`
       pins the invariant: removing the fixture makes the test fail
       immediately at the env-var assertion. `_active_agents` audit:
       grep confirmed no external consumers; dead dict + `attach_agent`
       method removed from `sweave/tools/__init__.py`. M1.3's
       `ServeRunnerRegistry` owns process lifecycle.
    2. `ModelRef` + `model_ref_to_wire` moved to
       `sweave/harness/base.py` (the contract type, R3-ready). The
       `specialist_store` module re-exports for backward compatibility
       (no API break). `Message` gains `model: ModelRef | None = None`;
       `OpenCodeProcess.send` prefers `message.model` over `spec.model`
       (per-message beats spawn-time). `AgentProcess` protocol
       docstring: model-per-request is contract; R3 adapters implement
       per-invocation flags (claude `--model`, codex `-m`). Trace
       records `model_used` on every delegation completion
       (source: task_override / specialist.current_model / none).
    3. Switch semantics: `PUT /api/specialists/{name}/model` while a
       specialist is running is accepted + stored; because every
       delegation resolves its model at submit, the switch applies to
       the NEXT delegation, never mid-task. Test pins the queued
       application: submit A (model=m1) → mutate
       specialist.current_model (equivalent to PUT) → submit B
       (model=None) → B uses the NEW model. 4-level chain verified
       end-to-end incl. `orchestrator.default` final fallback (same
       pattern as `test_m1_2_step2`; the chain itself is already
       covered there — this test pins the post-M1.3 invariant). WS
       `model.changed` payload shape (`{name, model, scope}`) pinned
       by source inspection.
    4. **Human promotion (review → done)**:
       - `POST /api/delegations/{id}/promote` — valid only from
         `review` (409 from queued/running/done/failed; 404 unknown);
         sets status=done + completed_at; trace
         `status_changed` (source=human_promote); WS
         `delegation.status_changed`; bridged `ChildSession.status`
         synced to `done` so the UI Children tab re-renders.
       - UI v1: "Mark done" button on Children-tab delegation
         entries; visible only for `child.status === 'review' && child.delegation_id`;
         click handler posts to the promote endpoint and mirrors the
         new status locally. OffsetParent-verified by
         `test_promote_ui.js` (Playwright headless Edge).
       - **R2 note**: cross-review will call this same endpoint
         programmatically. The API is the automation seam; no
         R2-specific code lives here.
    5. Gates: 295/295 pytest (was 269; +26 across 2 new test files);
       13/13 `run.py --check`; 40/40 `test_full`; suite 3× consecutive
       green. **Live spot-check** (gmi, tiny): 3 delegations to
       `backend` all reached `review`; one promoted via API → status
       `done`; process count stable (1 python proc before, 1 after);
       no leaks; server still responsive. New files:
       `tests/test_m1_4_5_step1_model_ref_contract.py` (8),
       `tests/test_m1_4_5_step2_switch_semantics.py` (5),
       `tests/test_m1_4_5_step3_promote.py` (12; 10 promotion-matrix
       + 2 UI source pins; the 4-parametrize blocked-status cases
       count as one test function), `test_promote_ui.js` (Playwright
       smoke). 6 new commits on `master` (steps 0, 1, 2, 3, plan/CONTEXT
       scaffolding). M1.4+M1.5 done.
- ▶ **Next**: M1.6 DelegationManager + deferral (per DESIGN.md §6 R1)
- ✅ **M1.6 DelegationManager + deferral via MCP tool** — done
  2026-09-04 per `docs/M1_6_PLAN.md`. Rulings locked 2026-08-30:
  - **defer = real MCP tool** (not JSON parsing). Orchestrator calls
    `defer(target, task, reason?, caller_delegation_id)` natively; the
    JSON-convention stays only as an upgrade-path note.
  - **Depth cap 2**: orchestrator → specialist → defer → orchestrator
    → peer.
  - **Chain budget 200K coordination tokens** (tiktoken cl100k_base);
    coordination traffic only (orchestrator turns, defer payloads,
    result summaries). Specialist internal work is opaque by design.
  - 5 steps:
    0. **opencode MCP-in-serve probe** — passed. Per-project
       `opencode.json` with `type: "local"`, `command: [array]`,
       `environment: {KEY: VALUE}`, `timeout: 30000` is honored
       (`opencode mcp list` reports "✓ connected"). No global-
       config injection fallback needed. Full results in
       `docs/M1_6_STEP0_PROBE.md`.
    1. **Sweave MCP server** (`sweave/mcp/`, `python -m sweave.mcp`,
       stdio, official `mcp` SDK MIT, §8 adoption). Two tools:
       `defer` + `list_specialists`. Auth: shared token at
       `~/.sweave/mcp_token` (auto-generated, 32 URL-safe bytes,
       mode 0o600); localhost-only `X-Sweave-MCP-Token` header.
       Tool results are plain text so the orchestrator's text-mode
       path can act on `rejected: <reason>` lines.
    2. **DelegationManager + Delegation v3** (`runtime/delegation_manager.py`).
       Per-process gate. Three rules in order: depth, loop, budget.
       `ChainError` subclasses for clean 409 mapping. Delegation
       schema v2 → v3 (depth, chain_root_id, coordination_tokens).
       v3 records on disk; `from_dict` migrates v1 + v2 forward.
       Top-level delegations bypass chain rules.
    3. **Orchestrator wiring + parent gating**.
       - Orchestrator prompt updated with the `defer` tool contract
         (target, task, reason, caller_delegation_id; "queued" /
         "rejected:" / "error:" return shapes; "Never implement
         code yourself" headline).
       - Per-project `opencode.json` plumbing
         (`runtime/mcp_config.py`, `ensure_mcp_config`): idempotent,
         preserves user-edited blocks (the `_sweave_managed`
         marker), merges with existing top-level keys. Triggered on
         `POST /api/projects/{name}/active` so the orchestrator's
         serve cwd sees the sweave MCP server automatically.
       - Parent gating: `JobRunner._wait_for_children` blocks the
         parent's `review` transition until every child reaches
         `done` or `failed`; bounded by `turn_timeout`. Trace records
         `children_settled` or `children_settle_timeout`. **Synthesis
         generation (re-prompting with child results) is M1.7
         scope** — M1.6 delivers tree lifecycle + gating only.
    4. **Gates + live mini-scene + docs**.
       - 336/336 pytest (was 295; +41 across 3 new test files:
         `test_m1_6_step1_mcp_server.py` (11), `test_m1_6_step2_delegation_manager.py`
         (15), `test_m1_6_step3_orchestrator_wiring.py` (11), plus
         4 in `test_delegation_store.py`).
       - 13/13 `run.py --check`; 40/40 `test_full`; suite 3×
         consecutive green.
       - **Live mini-scene** (`scripts/m1_6_live_scene.py`,
         `SWEAVE_MOCK_OPENCODE=1`): parent + child end-to-end via
         the same HTTP path the MCP tool uses; loop probe (third
         defer to the same target → 409 "rejected: loop detected").
         1 python proc before, 1 after; no leaks.
       - Docs: DESIGN §4 (6 new rows: DelegationManager, MCP
         server, per-project opencode.json plumbing, orchestrator
         defer tool contract, parent gating, Delegation v3),
         R1 M1.6 bullet replaced with the post-execution summary
         + branch notes (vs plan: per-project config is the
         mechanism; no global injection fallback needed), §2.1
         (defer-tool wording + DelegationManager M1.6 update),
         §8 already had the `mcp` entry pre-M1.6.
- ✅ **M1.7 Orchestrator chat loop** — done 2026-09-04 per `docs/M1_7_PLAN.md`.
  5 steps:
  1. **Per-Session orchestrator binding** (the §2.1 wrinkle).
     `Session` gains `schema_version: int = 1` +
     `orchestrator_session_id: str | None`. `SpecialistRuntime.run` +
     `_ensure_session` gain `session_id_getter` / `session_id_setter`
     callbacks; default = the M1.3 Specialist-record behaviour (backwards
     compatible). Three Sweave sessions of the same project get three
     independent orchestrator contexts.
  2. **Chat turn pipeline**. `Delegation` gains `kind: str = "task"` (with
     `"chat"` for orchestrator conversation turns); `Delegation` schema
     bumps 3 → 4 with a `_migrate_v3_to_v4` helper (pre-M1.7 records
     default to `kind="task"`). New `sweave/chat/loop.py` ChatLoop class
     (per-session asyncio.Lock for serial semantics, orchestrator turn
     via SpecialistRuntime, Session-bound session-id callbacks, persisted
     assistant reply, error messages on timeout / unreachable). New route
     behaviour: `POST /api/sessions/{id}/messages` (role=user) drives the
     chat loop; non-user roles keep the persist-only contract. Rulings
     locked 2026-08-30: chat turns **auto-`done`** (the M1.7 ruling;
     implementation children still stop at `review`); concurrent user
     messages **queue** (serial per-session); orchestrator unavailable
     ⇒ explicit error message persisted (never a silent fallback to the
     rule-router for chat).
  3. **Synthesis loop** (M1.6's deferred scope for chat). `sweave/chat/
     synthesis.py` builds a server-composed prompt from child
     delegations (per-child {specialist, task, status, output, error},
     oldest-first truncation, tiktoken-capped at ~8K, default
     configurable). ChatLoop waits for children (bounded by
     `turn_timeout`) and runs a second orchestrator turn for the
     synthesis. Fast path: no children → first turn's reply is the final
     answer. Failed children: synthesis still runs with failure noted.
  4. **Runtime transcript system + memory curation + multi-source
     "what's new"** (the new piece that grew M1.7 by ~0.7 sessions).
     `sweave/chat/transcript.py` composer builds the per-turn composed
     prompt — curated memory (top-k=5, ~2K), multi-source "what's new"
     (memory entries with `ts > last_recall_ts` + git diff since
     `last_snapshot`, ~1K), synthesis (when children, ~8K), one-
     paragraph transcript reference (~100, **NOT** the full transcript),
     user message. The runtime owns the format, the caps, the audit
     story. The composed prompt size is O(memory + synthesis + user),
     NOT O(transcript_length). Session gains
     `last_memory_recall_ts: datetime | None` + `last_git_snapshot: str
     | None`; `MemoryEntry` gains `ts: datetime | None`. `GitSnapshotter`
     for the git section (graceful fallback: non-git projects → empty
     section). Trace records per-section sizes + dropped counts (the
     audit trail). Rulings locked 2026-08-30: external engines
     (opencode) see runtime's composed prompt + engine's own session
     memory on top (engine-specific, outside the runtime's control);
     server-internal engine (side-project, future) sees runtime's view
     only.
  5. **Gates + live mini-scene + docs**. 381/381 pytest (was 336; +45
     across 4 new test files: `test_m1_7_step1_per_session_orchestrator_binding.py`
     (6), `test_m1_7_step2_chat_turn_pipeline.py` (11),
     `test_m1_7_step3_synthesis_loop.py` (10),
     `test_m1_7_step4_transcript.py` (18)). 13/13 `run.py --check`;
     40/40 `test_full`; suite 3× consecutive green. **Live mini-scene**
     (`scripts/m1_7_live_scene.py`, `SWEAVE_MOCK_OPENCODE=1`):
     two sessions get independent orchestrator bindings (proven on
     disk), chat delegation auto-`done`, trace audit event recorded.
     1 python proc before, 1 after; no leaks. Docs: DESIGN §4 (chat
     endpoint ✅, transcript system ✅, runtime context builder ✅,
     synthesis loop ✅, per-Session binding ✅, parent-gating row
     updated), R1 M1.7 bullet flipped to ✅, §2.1 noted.
- ✅ **M1.8 Streaming** — done 2026-09-04 per `docs/M1_8_PLAN.md`.
- ✅ **M1.9 Dogfood pass** — done 2026-09-05 per `docs/M1_9_PLAN.md`.
  5 steps:
  1. **Trace completeness** — the harness's stream reader captures
     tool parts (pending → running → completed | error) keyed by
     callID, step boundaries (start/finish), and per-turn
     ``tokens_used``. Terminal detection: turn complete iff
     ``info.time.completed`` AND ``info.finish`` are set (replaces
     the pre-M1.9 per-chunk "parts + role==assistant" heuristic).
     The dead ``type:"error"`` part branch was removed; errors come
     from ``info.error``. Pre-M1.9 fixtures updated to emit the
     terminal flag (the real wire always does). Reasoning parts
     default OFF (``trace_reasoning=True`` flag enables them).
  2. **Hardening bundle** — per-project ``worktree_base`` on the
     Project record (overrides the global config; legacy files
     load with ``None``). ``WorktreeManager.align()`` primitive
     with the dirty-skip rule (never stash-dance a working agent).
     Specialist permission profile: orchestrator = ``task: deny`` +
     git bash deny (commit/merge/push/rebase/hard-reset/gh pr merge);
     specialist = ``task: deny`` only (specialists commit freely in
     their disposable branches per the 2026-09-04 commit-authority
     map). ``runtime/mcp_config.py`` injects the orchestrator's
     profile into the per-project ``opencode.json``.
  3. **Output funnel completion** — ``ask_human(question, options?)``
     MCP tool (sibling of ``defer``; same auth + wire surface). The
     asking delegation is flagged ``needs_attention`` (Delegation
     schema v5; ``SCHEMA_VERSION=5``, ``_migrate_v4_to_v5``). Endpoints:
     ``POST /api/delegations/{id}/escalate``, ``/answer``, ``GET
     /api/delegations/{id}/escalation``. WS events: ``specialist.
     escalated`` + ``specialist.escalation_resolved``. Timeout (15
     min default; configurable) records "no answer received" as the
     placeholder response so the LLM proceeds with best judgment. The
     MCP server reads ``SWEAVE_MCP_TOKEN`` env first (the opencode.json
     plumbing seam), falls back to the home file.
  4. **Visibility surfaces** — ``sweave/web/detail_view.py`` projects
     the trace JSONL into composed-prompt / tool-timeline / tokens /
     status-timeline sections. ``GET /api/delegations/{id}/detail``
     HTTP endpoint + ``sweave log <id>`` / ``sweave tail <id>`` /
     ``sweave watch`` CLI. The ``tail`` command is an async generator
     with file-rotation handling; ``watch`` polls the running server.
  5. **Self-hosting live gate** — ``scripts/m1_9_self_hosting_scene.py``
     drives one chat turn end-to-end through the HTTP API (mock
     opencode subprocess; real running server; ``SWEAVE_MOCK_OPENCODE=1``).
     Funnel-leak report (every forced exit to API/CLI/file) becomes
     R4's re-planning input. Per-project ``worktree_base`` plumbed
     through ``/api/projects`` POST.
  447/447 pytest (was 400 at M1.9 step 0; +47 from the five step
  files). 13/13 ``run.py --check``; 40/40 ``test_full.py``.
  4 steps:
  1. **Harness streaming callback** (the v1 scope ruling).
     `OpenCodeProcess.send(message, on_chunk=None)` and
     `SpecialistRuntime._send_message(process, body, trace, on_chunk=None)`
     accept an optional async-or-sync callback invoked with each
     text part as it leaves the opencode stream. Default None
     preserves the M1.0+ accumulate-only behavior; all
     pre-M1.8 callers and tests pass unchanged. Async callbacks
     are awaited (the harness detects coroutine return values);
     sync callbacks are invoked directly. A misbehaving
     callback that raises is logged + ignored — streaming is
     best-effort from the harness's view.
  2. **ChatLoop wire-up + throttle**. `sweave/chat/streaming.py`
     (new) `ChatDeltaCoalescer`. ChatLoop wraps the harness's
     on_chunk in a coalescer that publishes `chat.delta` events
     on the WSEventBus at most every `stream_coalesce_ms`
     (default 100) or when the buffer crosses
     `stream_char_threshold` (default 64). The persisted
     `message.added` event stays authoritative (full text); the
     chat.delta events are partial snapshots for incremental
     UI rendering. The coalescer is created once per turn and
     closed on every exit path via try/finally. The chat loop's
     `run_turn` was refactored to delegate to `_run_turn_body`
     so the same try/finally could wrap the body without
     repeating the close logic. `SpecialistRuntime.run` gained
     an `on_chunk` param (default None).
  3. **UI incremental rendering**. `sweave/web/static/js/app.js`
     handles `chat.delta` and `message.added` events directly
     with **no re-render storm**. The streaming lifecycle:
     create-once, patch in place (`textContent += text` on
     `.streaming-text`), replace on `message.added` via
     `replaceWith` (the parent's child list is preserved; the
     container's `innerHTML` is never reset during streaming).
     The persisted assistant message now carries
     `metadata={'delegation_id': chat_d.id}` so the UI can join
     the message to the streaming bubble. `sweave/web/static/
     style.css` adds a `.streaming` class (border accent +
     blinking cursor hint while the orchestrator is replying).
     The previous UI used `data.type` for WS dispatch which was
     always undefined (the wire format is `data.event`); the new
     handler dispatches correctly. `sendChat` now routes
     through the chat endpoint `/api/sessions/{id}/messages`,
     not the legacy `/tasks` path. The M1.7 step 2 chat
     endpoint is now the canonical chat surface; M1.8 makes it
     the streaming surface.
  4. **Gates + live gate + docs**. 400/400 pytest (was 381;
     +19 across 3 new test files:
     `test_m1_8_step1_harness_streaming_callback.py` (8),
     `test_m1_8_step2_chat_loop_streaming.py` (10),
     `test_m1_8_step3_streaming_ui.py` (1, runs a node + jsdom
     polyfill to exercise the streaming functions in
     isolation)). 13/13 `run.py --check`; 40/40 `test_full`;
     suite 3× consecutive green. **Live mini-scene**
     (`scripts/m1_8_stream_live_scene.py`, `SWEAVE_MOCK_OPENCODE=1`):
     1 `chat.delta` event with `session_id` + `delegation_id` +
     `text`; 2 `message.added` events (user + assistant) with
     `delegation_id` in metadata; status transitions
     `running → done`; two sessions of the same project get
     independent `orchestrator_session_id` bindings. 1 python
     proc before, 1 after; no leaks. Docs: DESIGN §4 R1 M1.8
     bullet flipped to done; PROJECT_STATE M1.8 done summary;
      docs/M1_8_PLAN.md status flipped from `planned` to `done`
      with an execution summary section.
- ▶ **M1 CLOSED** (all 10 milestones). M1.9 was the self-hosting
  dogfood pass; the funnel-leak list in docs/M1_9_PLAN.md is
  R4's re-planning input (all leaks closed by R4 step 4 below).
- ✅ **R4 sweave-web rebuild (wave 1)** — done 2026-09-05 per `docs/R4_PLAN.md`.
  4 steps:
  1. **Foundation** — design system (5 v1 presets: light/dark/
     dracula/nord/catppuccin) as RGB-tuple CSS variables + custom-
     color override; theme switcher with localStorage persistence;
     Tailwind tokens bridged to the design system (popover/ring/
     input added); shell (Sidebar + Topbar + Layout + Notification
     Container) with the wave-1 nav (Chat + Children); typed
     API client regenerated for the v2 surface (no `any`);
     WSProvider with reconnect + topic dispatch (StrictMode-
     safe); Vite dev proxy to :8100; ThemeApplier drives
     `:root[data-theme=...]` on mount. 21 vitest unit tests.
  2. **Chat (input funnel)** — pure-function reducer
     (`pages/chat/reducer.ts`) for the M1.8 streaming invariant
     (chat.delta appends in place; message.added replaces; turn
     boundary clears); MessageList patches the streaming bubble
     via a ref + textContent (no React re-render per delta);
     SessionPicker (closes the M1.9 funnel leak -- session
     lifecycle in-shell); Composer with auto-grow textarea +
     serial-turn lock per session. 31 vitest tests.
  3. **Output funnel** — tree builder
     (`pages/children/tree.ts`) with root + depth + orphan
     promotion; LiveTree (depth-indent rows, status pills,
     PromoteButton on every review record, AnswerInline on
     every needs_attention record, EscalationLane at the top);
     DetailView modal (composed prompt + tool timeline +
     tokens + status timeline from `GET /api/delegations/{id}/
     detail`). 40 vitest tests.
  4. **Flag-day cutover** — `sweave-web/dist` mounted by the
     backend (SPA catch-all + `/assets` + `/favicon.svg`;
     `SWEAVE_UI_VANILLA=1` forces the v1 fallback for debugging);
     v1 vanilla UI assets (sweave/web/static/) + v1 UI tests
     (test_full.py, test_sidebar_nav.js, test_promote_ui.js) +
     v1-era debug/verify/check scripts retired (git history
     preserves); Playwright e2e suite at `sweave-web/e2e/`
     (CI gate; the local pytest gate uses `playwright test
     --list` to pin suite registration). Docs: AGENTS.md ground
     rule amended (UI is sweave-web/, dist served not committed);
     DESIGN §4 + §6 R4 row + §3 stack + plan; PROJECT_STATE
     this entry.
447/447 pytest (was 447 at M1.9 step 5; +5 from the four step
   orchestrator files). 13/13 `run.py --check`. v1 vanilla UI
   retired.
- ✅ **R4.0 hotfix — chat session-id resolution** — done 2026-09-05
  per `docs/R4_PLAN.md`. The chat turn path was posting to
  `/session/chat-{delegation_id}/message` (an internal id the
  opencode serve doesn't recognise) and 500'ing. Root cause:
  `SpecialistRuntime._ensure_session` updated the external
  binding (`Session.orchestrator_session_id`) but never propagated
  the resolved id into `process._session_id`; the wire kept the
  placeholder `_build_process` seeded. Three commits (one step):
  `_build_process` seeds `session_id=""` (no fabrication);
  `_ensure_session` writes `process._session_id` in all three
  paths (create / 404-recreate / reuse); `_send_message` asserts
  the resolved id starts with `ses_` before posting; the dead
  `_persist_session_id` helper is removed. Wire-shape mock
  tightened to match the real serve (rejects non-`ses_` ids /
  unknown ids on the wire with 500 / 404; mock id format
  `ses_mock_{name}`). 5 new wire-shape tests in
  `tests/test_r4_0_wire_shape.py` (confirmed to fail 4/5 when
  the propagation was temporarily reverted — a real regression
  test). **452/452 pytest** (was 447; +5), 13/13 `run.py --check`,
  40 vitest. R4.1 is now unblocked.
- ▶ **R4.1 UX foundation** (per `docs/R4_1_PLAN.md`, rewritten
  2026-09-05): project → session tree navigation (project switcher
  + session list per project), theme system to the external bar
  (tokens, presets, dark default), app shell redesign, **scaffold-
  first**: every v1 surface (chat, children, detail, memory,
  agents, settings) ships as a designed stub before features
  fill it. Est. ~1 session. Predecessor R4.0 is now done.
- ▶ **R4.4 wave 2 spec** — Memory tab + Agents workbench (the
  R4-workbench vision from the M1.2 era) + Settings panes
  (models/routing/memory/catalog picker — old UI_PLAN items).
  Three panes, sequenced (Memory → Agents workbench →
  Settings) by the dogfood handoff. The pre-dogfood strawman
  is in `docs/R4_4_PLAN.md` (the old R4.1 strawman renamed
  2026-09-05 per the hub restructure); the dogfood re-cuts it.
  Same protocol as M1.9's dogfood handoff: user daily-drives
  wave 1 on real work; the friction list becomes R4.4 / R4.2
  input. R2 skills interleave on demand (per R4 plan §5).
- **Planner pattern to kill**: the M1.7 and M1.9 plans both said "no schema bump" for a new Delegation field and both were wrong (gotcha #12 gate forced 3->4 then 4->5). Rule for future plans: ANY new Delegation field = SCHEMA_VERSION bump + migration helper, no exceptions.

### M1.prep — done 2026-08-29
- **Plan of record**: `docs/M1_PREP_PLAN.md`
- **Test results**: 62/62 pytest, 13/13 `run.py --check`, 40/40 `test_full.py`, 8/8 `test_browser.py`, 8/8 `test_projects.py`, ALL GREEN on `test_agents_loader.py`
- **New files**: `sweave/web/{state,deps,events}.py`, `sweave/web/routers/{projects,agents,tasks,worktrees,memory,config,fs,delegations}.py`, `sweave/runtime/{__init__,locking,trace_log,delegation_store,job_runner}.py`, `tests/{conftest,test_seed_agents,test_atomic_write,test_locking,test_ws_event_bus,test_trace_log,test_delegation_store,test_job_runner,test_projects_atomic,test_projects_lock,test_routers_smoke}.py`
- **Deleted**: `sweave/web/api.py` (stale duplicate, never mounted)
- **New endpoints**: `POST /api/v2/tasks` (async — returns `{delegation_id, status: queued}`), `GET /api/delegations`, `GET /api/delegations/{id}`, `POST /api/delegations/{id}/wait`
- **Sync `/api/tasks` kept** for backward compat; deprecated in OpenAPI, slated for removal in M1.4+
- **WS event bus**: `WSEventBus` (sweave/web/events.py) is the single pub/sub; legacy event names (`agent_created`, `model_changed`, `task_completed`, `worktrees_cleaned`, `worktree_removed`, `rule_added`) preserved on the wire
- **Trace log**: `~/.sweave/traces/{delegation_id}.jsonl` (one JSON object per line)
- **Git history**: 40 commits on `master` (M0 rebuild → design docs → M1.prep → M1.0 → M1.1 → M1.2)

---

## CRITICAL BUGS FIXED (Latest Session)

### Bug 1: Sidebar buttons don't work (Session 7)
**Root cause**: Multiple potential issues — DOM ready state, event listener timing, complex module architecture.

**Fix**: Complete rewrite of frontend using:
- All JS in one IIFE to avoid scope issues
- `if (document.readyState === 'loading')` check before setting up listeners
- Global error handlers for debugging
- Single `switchTab()` function as source of truth

### Bug 2: Everything hidden after loading (Session 8)
**Root cause**: The `app` div starts with `class="app hidden"` but `showProjectMode()` and `showWelcome()` never removed the `hidden` class. They only toggled inner sections.

**Fix**: Added critical line in `start()` function:
```js
setTimeout(() => {
  const loader = $('loader');
  if (loader) { /* hide loader */ }
  // CRITICAL: Remove hidden class from app so it's visible
  const app = $('app');
  if (app) app.classList.remove('hidden');
}, 500);
```

---

## Architecture: Session = Orchestrated Conversation

A **Session** is an orchestrated conversation that can spawn **child sessions** (specialist agent runs):

```
Session (parent - orchestrator)
├── Messages (user, assistant, tool calls)
├── Context (current state)
├── Memory Bank (session-scoped)
└── Child Sessions (specialist agent runs)
    ├── backend agent (own worktree, own context)
    ├── frontend agent (own worktree, own context)
    └── reviewer agent (own worktree, own context)
```

---

## Project Structure

```
C:\Users\user\sweave\
├── config.yaml              # Server, harness, memory, git, models, routing
├── models.yaml              # Model registry (4 roles)
├── rules.yaml               # 5 routing rules + LLM fallback
├── pyproject.toml           # Dependencies
├── .env.example             # API key template
├── .gitignore
├── README.md
├── PROJECT_STATE.md         # THIS FILE
│
├── start_server.py          # Start server detached
├── stop_server.py           # Stop server via PID file
├── run.py                   # Run server or test suite
├── test_server.py           # Server endpoint tests
├── test_projects.py         # Project/session tests
├── test_browser.py          # File browser API tests
├── test_v3.py               # Comprehensive UI verification
├── test_full.py             # Latest comprehensive test
├── verify_v2.py             # Older verification script
├── check_modules.py         # JS module verification
│
├── scripts/
│   ├── generate_models.py   # Fetches models.dev -> models.yaml
│   └── setup_hindsight.py   # Hindsight init
│
└── sweave/                  # Python package
    ├── __init__.py
    ├── projects.py          # Project + Session + Message + ChildSession data model
    ├── api/
    │   └── projects.py      # Project/Session API endpoints
    ├── agents/              # Agent YAML configs
    ├── cli/                 # Typer CLI
    ├── config/              # Pydantic schemas + ConfigManager
    ├── harness/             # Harness abstraction + OpenCode impl
    ├── memory/              # Hindsight backends
    ├── router/              # RuleRouter with template resolution
    ├── workspace/           # WorktreeManager
    ├── tools/               # Tools
    └── web/
        ├── __init__.py
        ├── server.py        # FastAPI app (40+ routes)
        └── static/
            ├── index.html    # Single-page app
            ├── style.css     # All styling
            └── js/
                └── app.js   # All JS in one IIFE
```

---

## Key Architectural Decisions (Final)

### 1. JS in IIFE
The entire app.js is wrapped in `(function() { 'use strict'; ... })()` to prevent any global scope pollution.

### 2. `switchTab()` is CORE
The navigation is the most critical function. Defined ONCE, called from event listeners. Both nav items and tab panels use the same `data-tab` attribute.

### 3. `setupEventListeners()` called after DOM is ready
```js
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', start);
} else {
  start();
}
```

### 4. **CRITICAL**: App must be unhidden after loader
In `start()`, the setTimeout that hides the loader **MUST** also remove the `hidden` class from `#app`:
```js
setTimeout(() => {
  const loader = $('loader');
  if (loader) { /* hide loader */ }
  const app = $('app');
  if (app) app.classList.remove('hidden');  // <-- THIS WAS MISSING
}, 500);
```

### 5. Global error handlers
```js
window.addEventListener('error', (e) => {
  showError('JS Error: ' + (e.error?.message || e.message));
});
window.addEventListener('unhandledrejection', (e) => {
  showError('Promise Error: ' + (e.reason?.message || e.reason));
});
```
This shows errors in a visible error box at the top of the page.

### 6. All elements have stable IDs
JS queries via `$('id')`, not fragile class names.

### 7. Backend-driven file browser
Uses Python's `pathlib` for cross-platform support. Works on all browsers and OS.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| **Projects** | | |
| GET | /api/projects | List projects |
| POST | /api/projects | Create project (from folder) |
| GET | /api/projects/active | Get active project |
| POST | /api/projects/{name}/active | Switch to project |
| DELETE | /api/projects/{name} | Delete project |
| **Sessions** | | |
| GET | /api/sessions | List sessions |
| POST | /api/sessions | Create session |
| GET | /api/sessions/active | Get active session |
| GET | /api/sessions/{id} | Get session with messages + children |
| POST | /api/sessions/{id}/active | Switch to session |
| POST | /api/sessions/{id}/messages | Add message |
| DELETE | /api/sessions/{id} | Delete session |
| **File System** | | |
| GET | /api/fs/drives | List available drives/roots |
| GET | /api/fs/list?path=X | List directory contents |
| POST | /api/fs/validate | Validate project path |
| POST | /api/fs/create | Create new directory |
| **Memory** | | |
| GET | /api/memory/banks | Get hierarchical banks |
| POST | /api/memory/recall | Search memories |
| POST | /api/memory/retain | Store memory |
| POST | /api/memory/reflect | Synthesize memories |
| **Agents, Tasks, Config** | | |
| GET/POST/PUT/DELETE | /api/agents[/name] | Agent CRUD |
| POST | /api/tasks | Execute task |
| POST | /api/route | Preview routing |
| GET/POST | /api/models | Model config |
| GET/POST | /api/rules | Routing rules |
| GET | /api/config | Full config |
| WS | /ws | WebSocket for real-time updates |

---

## UI Layout

```
┌──────────────────────────────────────────────────────┐
│ ☰ Sweave  project-name / session-name  [●] [+Session]│ ← topbar (56px)
├──────┬───────────────────────────────────────────────┤
│ Chat │                                               │
│ Child│                                               │
│ Agent│   ┌─────────────────────────────────────┐    │
│ Memor│   │                                     │    │
│ ─────│   │           CONTENT AREA             │    │
│ Sett│   │       (chat / children / etc)      │    │
│      │   │                                     │    │
│ [●]  │   └─────────────────────────────────────┘    │
└──────┴───────────────────────────────────────────────┘
```

Layout uses CSS Grid with `height: 100vh; width: 100vw` on the app container. Top bar is fixed height (56px), sidebar is 200px (64px when collapsed).

---

## Key Files (Critical to Read)

1. **`sweave/web/static/index.html`** - Single-page app structure (must have all element IDs)
2. **`sweave/web/static/style.css`** - All styling
3. **`sweave/web/static/js/app.js`** - All JS in one IIFE (must unhidden #app after loader)
4. **`sweave/web/server.py`** - FastAPI routes
5. **`sweave/projects.py`** - Project/Session data model
6. **`sweave/api/projects.py`** - Project/Session API endpoints

---

## Configuration

### config.yaml
```yaml
server: { host: "127.0.0.1", port: 8080 }
harness:
  default: "opencode"
  opencode: { command: "opencode", serve_args: ["--port", "0"] }
memory:
  backend: "hindsight"
  hindsight:
    mode: "embedded_slim"
    bank_id: "project-memory"
git:
  provider: "github"
  worktree_base: ".worktrees"
  auto_pr: true
  pr_base_branch: "main"
models: { registry_path: "models.yaml", rules_path: "rules.yaml", hot_reload: true }
routing: { routes: [], fallback: "llm" }
```

### models.yaml
```yaml
models:
  orchestrator: { default: "deepseek-flash", aliases: [...], provider: "opencode" }
  backend: { default: "glm-5.2", aliases: [...], provider: "opencode" }
  frontend: { default: "hy3", aliases: [...], provider: "opencode" }
  reviewer: { default: "claude-3.5-sonnet", aliases: [...], provider: "opencode" }
```

### rules.yaml
```yaml
routes:
  - pattern: "backend|api|database|server|sql|auth|orm"
    agent: "backend"
    model: "{{models.backend.default}}"
fallback: "llm"
```

---

## Running Commands

```bash
# Start server (background, detached)
python start_server.py 8100 127.0.0.1

# Stop server
python stop_server.py

# Run server (foreground)
python run.py --host 127.0.0.1 --port 8100

# Run tests
python run.py --check              # 13 endpoint tests
python test_browser.py 8100        # 8 file browser tests
python test_full.py                # 40 comprehensive UI tests
python -m pytest tests/            # 62 unit/integration tests (source of truth)

# CLI commands
sweave run "Build a REST API"
sweave route "Create a React component"
sweave models --list
sweave doctor
```

---

## User Context

The user wants:
1. **Project-scoped agents** - Open a folder → project with own memory/agents
2. **Hierarchical memory** - Global + project + session
3. **Session = orchestrated conversation with children**
4. **Specialist agents** are persistent, not temporary
5. **Different models per agent** (dynamically selectable)
6. **Web UI** (Odysseus-inspired, full-screen)
7. **Configurable Hindsight memory** (embedded/docker/cloud)
8. **OpenCode as default harness** (extensible)
9. **CLI: `sweave`**
10. **GitHub workflow** (gh CLI optional)
11. **Windows-friendly** (separate scripts for background processes)

**Critical UI requirements** (all now met):
- ✅ **Full-screen** interface (not stopping mid-screen)
- ✅ **All buttons must work** (navigation, modals, etc.)
- ✅ **App must be VISIBLE** after loading (not hidden)
- ✅ **Welcome screen** is a proper design (not just an ugly button)
- ✅ **File access via backend** (not browser sandbox)
- ✅ **Folder picker works** on Windows (backend-driven, not browser API)

---

## History of Build Sessions

### Session 1: Initial setup
- CLI structure, config system, routing, harness abstraction, memory backends, basic SPA

### Session 2: UI enhancement
- Odysseus-inspired UI with Jinja2 templates, then React frontend

### Session 3: Web server + JS modules
- FastAPI server, static files, JS modules

### Session 4: Bug fixing
- Fixed fullscreen issue (position: fixed, 100vh/100vw)
- Fixed timing issues (lazy DOM element lookup)
- Fixed font 404s (removed @font-face)

### Session 5: Project/Session architecture
- Project + Session + ChildSession data model
- Hierarchical memory
- Welcome screen, sidebar project context

### Session 6: Clean rewrite
- Removed Jinja2 templates, React frontend, complex theme system
- Single clean index.html with all UI
- Single clean app.js (no module imports, no build step)
- Clean style.css with 6 preset themes

### Session 7: File browser fix
- Added backend `/api/fs/*` endpoints (drives, list, validate, create)
- Replaced broken File System Access API with backend-driven file browser
- Added proper 2-pane file browser UI in the project modal
- No more "Folder picker not supported" error

### Session 8: Sidebar navigation fix
- Completely rewrote the entire frontend from scratch
- **Sidebar navigation NOW WORKS** - tested with comprehensive verification
- All JS in one IIFE to avoid scope issues
- Global error handlers show errors on screen for debugging
- Strict DOM ready check before setting up listeners
- 5 themes (dark, light, dracula, nord, catppuccin) with data-theme attribute
- 42/42 verification tests pass

### Session 10 (latest): M1.1 — Record split (Delegation v2 + SubAgentRun + UI v1 bridge)
- 5-step refactor per `docs/M1_1_PLAN.md`:
  1. Delegation v2 fields + v1→v2 migration (`SCHEMA_VERSION=2`,
     `Manifest` TypedDict, `_migrate_v1_to_v2` helper)
  2. Per-project disk persistence via `PerProjectDelegationStores`
     (`{project}/.sweave/delegations.json`, atomic write-through via
     `runtime.locking.atomic_write_json_sync`, lazy per-project loading,
     corruption recovery)
  3. `SubAgentRun` ephemeral type (`runtime/subagent_store.py`,
     per-process, in-memory, FIFO-capped at 500, R2's `/investigate`
     will consume it)
  4. API filters on `/api/delegations` (`?project_name=&status=&parent_task_id=`),
     v2 task accepts `parent_task_id` + `manifest` passthrough,
     `SubAgentRun` endpoints (`POST/GET/finish`), UI v1 compat bridge
     writes a `ChildSession` carrying `delegation_id` on submit
  5. Gates + docs (this session)
- `ChildSession.delegation_id` field added; `from_dict` reads it with
  `.get()` so pre-M1.1 JSON files load with `delegation_id=None` (the
  UI v1 render path treats None as legacy entry)
- JobRunner now holds `PerProjectDelegationStores` + a
  `project_dir_resolver` callback (so it stays domain-agnostic; the
  AppState supplies the closure over `project_manager`)
- 122/122 pytest (was 80 after M1.0; +42 across M1.1: 10 schema + 9
  JobRunner + 16 SubAgentRun + 14 step 4)
- 13/13 run.py --check, 40/40 test_full, 8/8 test_browser, ALL GREEN
  test_agents_loader
- 14 new commits on `master` across M1.0 + M1.1 (4 + 5 + 5)

### Session 11 (latest): M1.2 — Specialist store + CRUD + UI v1 agents bridge
- 5-step refactor per `docs/M1_2_PLAN.md` (plan itself amended in
  chat — Orchestrator / Specialist / SubAgent tier framing baked in
  + 7 audit amendments: A seed→role_ref boundary, B model.changed
  shape, C new event names, D .sweave subdir creation, E anchored
  path, F override log no-active-project case, G role_ref as
  optional hint not hard binding):
  1. `Specialist` + `GlobalSpecialistStore` (`~/.sweave/agents.yaml`,
     home-anchored — kills the CWD-relative gotcha) + `ProjectSpecialistStore`
     (`{project}/.sweave/agents.json`, creates `.sweave` subdir) +
     `SpecialistResolver` (project→global→seed, orchestrator
     auto-seed, name validation, seed read-only view).
  2. Legacy import: anchored `agents.yaml` becomes the source of
     truth; `AppState.dynamic_agents` is now a derived view for the
     legacy router until R4. Model precedence chain in
     `DelegateTaskTool._resolve_model` (4 levels: task_override >
     specialist.current_model > role_ref hint > legacy).
  3. `/api/specialists` CRUD + `PUT /{name}/model` (emits the new
     `model.changed {name, model, scope}` shape) + `specialist.*`
     events. Override log at `{project}/.sweave/override_log.jsonl`
     (global fallback `~/.sweave/override_log.jsonl`) — appended
     when a v2 task carries an explicit `agent` that differs from
     the router decision (R6 dispatch gold labels).
  4. `/api/agents` bridge (test-first: tests fail with the dict shape
     first, then the fix). Array shape `{builtin, global, dynamic}`
     + description-overwrite bug fixed (PUT now patches `description`
     and `system_prompt` independently). Orchestrator name 409 on
     create/delete. New + legacy event names both emitted.
  5. Docs + this session.
- 207/207 pytest (was 122 after M1.1; +85: 33 specialist store + 13
  model precedence + 23 specialists router + 16 agents bridge).
- 13/13 run.py --check, 40/40 test_full, 8/8 test_browser, ALL GREEN
  test_agents_loader.
- 5 new commits on `master` for M1.2 + 1 plan-amendment commit +
  the two CONTEXT / plan commits = 8 across this session.
- **Stale server caught + killed**: a leftover PID 7028 from the
  M1.1 audit was still bound to port 8100; killed before the
  final sweep so the post-M1.2 server loads cleanly. Server
  boots the new code; the v1.18 OpenCode harness fix (M1.0) and
  the per-message model field are still active; nothing was
  rolled back.

### Session 12 (latest): M1.3 — Shared serve + durable context
- 6-step refactor per `docs/M1_3_PLAN.md` (plan itself amended in
  chat — 7 audit amendments A–G: K-revised model_ref shape, drop dead
  parser branch, new event names, .sweave subdir creation, anchored
  agents.yaml path, override log fallback, role_ref as optional hint).
  Step 0 ran first as an empirical probe to ground the plan in real
  v2-protocol behavior:
  1. `ServeRunner` per (specialist, worktree). Lazy start, psutil
     orphan sweep on boot, idle TTL (default 30 min, config knob).
     `find_orphan_serves` / `sweep_orphan_serves` helpers in
     `runtime/serve_runner.py`; no-op when psutil isn't installed.
   2. `SpecialistRuntime` + `ModelRef` + session lifecycle:
      `_model_body` always emits `{providerID, modelID}` when known
      (K-revised); `_ensure_session` covers create / 404-recreate /
      reuse paths; worktree preamble injected on every message.
      Probe-amended: opencode persists sessions in
      `~/.local/share/opencode/opencode.db` (SQLite), so the stored
      `session_id` DOES survive opencode process restarts (the
      `opencode serve` is just the in-memory request handler). The
      404-recreate path covers the rare case where a session has
      been deleted (or the worktree path changed).
  3. `JobRunner` integration: new optional ctor args
     `specialist_runtime` + `specialist_factory`; when wired, the
     runtime path runs; otherwise legacy `delegate_tool.execute`.
     Bare `current_model` strings parse as `ModelRef(provider=None,
     ...)`; structured `provider/model` strings parse to the typed
     pair; the v2 body emits the structured shape.
  4. Turn timeout (15 min default) + review transition: success ->
     `review` (M1.4 promotes to `done`); failure (timeout, agent
     error) -> straight to `failed`. Trace records the timeout event
     for observability.
  5. Live gate (manual): `tests/test_m1_3_step5_live_gate.py` --
     skipped in this env because the opencode startup races the
     sweave boot (separate Popen). The probe docs are the manual
     proof for now.
   6. Docs: DESIGN §4 (Agent lifecycle ✅, OpenCode spawn path ✅,
      SpecialistRuntime row), §2.1 amendment (per-specialist serve
      runner; **opencode.db session persistence** so the stored
      `session_id` survives opencode process restarts), R1 M1.3 ✅.
- 269/269 pytest (was 207 after M1.2; +62: 17 serve_runner + 21
  model_ref + 12 specialist_runtime + 6 step3 integration + 6
  step4 timeout/review + tests fixed along the way).
- 13/13 run.py --check, 40/40 test_full, 8/8 test_browser, ALL GREEN
  test_agents_loader.
- 6 new commits on `master` for M1.3 + 2 plan/context commits = 8
  across this session.
- **Stale server caught + killed** (same gotcha as M1.2): a leftover
  process was bound to a test port; killed before final sweep so the
  new code loads cleanly.

The current implementation is clean, working, and reliable. All reported bugs
have been fixed and verified.

### Pending: history clean before first remote push
- When a remote repo is created (R5), do a minimal history alteration first
  (git filter-repo on docs/M1_8_PLAN.md) to purge the UTF-16-LE artifact blob
  committed between 64bd013 and the M1.8 docs fix (2026-09-04). Nothing is pushed
  yet, so this is free today and impossible tomorrow.

### Known issues (transient - watch before trusting the gates)
- **Flaky test**: `tests/test_m1_3_step3_job_runner_integration.py::
  test_job_runner_runtime_path_legacy_model_string` fails ~1-in-N full-suite
  runs, passes in isolation (order-dependent, shared-state leakage suspected).
  Scheduled fix: **M1.4+M1.5 step 0** (`docs/M1_4_5_PLAN.md` - hermetic fixture,
  gate = suite green 3x consecutively). If you see exactly this test red in a
  full run: rerun isolated before diagnosing product code.

### Where M1 stands next (post-M1.3, rulings locked 2026-08-30)
- **Next plan of record**: `docs/M1_4_5_PLAN.md` (M1.4 folded into M1.5; ~1
  session). Rulings: flake fix = step 0; **human promotes** `review -> done`
  (`POST /api/delegations/{id}/promote` + Children-tab button; R2 cross-review
  automates via the same endpoint); ModelRef promoted into `harness/base.py`
  as the contract type.

---

## Critical Lessons Learned

1. **ALWAYS check if your main container needs to be unhidden** - If it starts with `display: none` or `hidden` class, something must explicitly remove that.

2. **Test with actual user perspective** - Just because the server returns 200 doesn't mean the UI is visible.

3. **Use global error handlers** - They're invaluable for debugging.

4. **Strict DOM ready check** - `document.readyState === 'loading'` check before setting up listeners.

5. **Single source of truth for tab switching** - One function, called from multiple places.

6. **Backend-driven file browser** - Don't rely on browser File System Access API; use your own backend endpoints.

7. **Comprehensive test scripts** - Have a test that verifies every element is present and every API endpoint works.