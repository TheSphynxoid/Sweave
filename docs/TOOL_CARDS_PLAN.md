# Tool cards enrichment + ask_human batching — plan of record

Status: planned (2026-09-16). Plan of record for the execution session(s).
Thread: transparency follow-up (chat tool rows + specialist detail + Q&A).
Parent context: `docs/SPECIALIST_VIEW_PLAN.md` (Amendment 2026-09-16), `sweave/chat/tools.py`, `sweave-engine/src/tools.js` + `loop.js`, `sweave/mcp/__init__.py`, `sweave-engine/src/sweave.js`, `sweave/runtime/escalation.py`, `sweave/web/routers/delegations.py`, `sweave-web/src/components/thread/Thread.tsx`, `sweave-web/src/pages/children/detail/sections.tsx`.

## Rulings (user-locked 2026-09-16)

- **F1 — Enrich the row (Option A).** Additive capped fields on the persisted `metadata.tools[]` row + `chat.tool` / `specialist.tool` WS payloads. Persistence is the design (reload-safe, no extra fetch, offline-capable).
- **F2 — Bash (Option A).** Command always visible; response behind a collapsed expander (inline cap ~2K, full text in the detail surface). Critical for security review + thread scannability.
- **F3 — Read window = show the parameters.** `Read <path> - lines X-Y [of N] - limit L offset O`. "Brittle" clarification: the engine footer `(Showing lines X-Y of total…)` is free text — executor must emit a STRUCTURED window (`{offset, limit, total, shownFrom, shownTo}`) in tool state and render from that, never parse the footer. Same file + same offset/limit repeated → visible "same window" affordance.
- **F4 — Write = Option B.** Real overwrite diff (old-capture for small files, capped at 200K, fail-safe) rendered green/red with side-by-side comparison UX (reuse the existing `EditTool` diff card; polish, don't rebuild). Create (no old) renders preview + stats (`+N lines`).
- **F5 — Ask batching = additive `questions[]`, max 5.** Single `question` stays compat (normalized to 1-elem). One record, one hold-open, one card with stacked sections. All-answered-before-continue. `multiSelect`: probe decides (default OUT for v1 unless the opencode baseline shows it load-bearing).
- **F6 — Enrich every badge where useful, scan-safe, minimal clutter.** Explicit: `grep` shows pattern (+ path/include, match count) — never result content. Same doctrine elsewhere: parameters + counts + excerpts, never full dumps. Opencode batching surface is the probe baseline; adopt what earns its keep.
- **F7 — Execution order: strict sequence** (probe → backend contract → UI binding → ask batch). Frontend binding starts only after the backend shape locks (no rework). No parallel backend/frontend window. Multi-turn: NO new infra needed (see §1).

## 0. Starting point (re-verified 2026-09-16 against code, not older bullets)

- Pipeline: engine `loop.js:649-695` emits `tool.started/completed/failed` with `state={status,input,output|error}` → Python harnesses (`harness/engine.py:640-`, `harness/opencode.py:533-`) normalize via `chat/tools.py:tool_event` → `compact_tool_record` persists `metadata.tools[]={callID,tool,status,summary,title,input,round}` → `chat.tool` WS live + `specialist.tool` for child turns (`runtime/job_runner.py:1730-`) → UI `Thread.tsx:TurnTools/ToolRow` + `SegmentedBody`, detail `detail_view.py:tool_timeline` + `sections.tsx:ToolTimelineRow` (bash/edit/agent cards).
- Lossy today BY DESIGN (`tools.py:_capped_input` keeps edit/write input only; reads persist `input=None`; bash command lives only in the 160ch `summary`; output/error stay trace-only). Detail/transcript already carry bash output + edit inputs — chat inline is the gap.
- Engine facts: bash tail-cuts 32K (`tools.js:truncateTail`); read pages 2000 (`DEFAULT_READ_LIMIT`) + teaches offset; edit/write return short strings only (`edited/wrote …`); grep caps 100 matches; `matchTarget`/`permissionKey` already name per-tool targets.
- Q&A today: single `{question, options?}` → one `EscalationStore` record per `delegation_id` (create overwrites; `create_or_reuse` only for permission requestIDs) → `ChatLoop` holds open (no assistant persisted) → `TurnQuestions.tsx` single card. Endpoints: `POST …/escalate`, `/answer {response}`, `/skip {confirmed:true}`, `GET …/escalation`. Native `question` denied both roles. No `questions[]` anywhere (MCP, engine `SWEAVE_TOOL_DEFS`, store, router, UI).
- Composer never reads `metadata.tools` (regression-pinned in `tests/test_chat_tools.py`) — enriching rows is UI-safe by construction.

## 1. Multi-turn execution — the asked question, answered

The system ALREADY executes multi-turn: the engine loop runs up to 150 (orchestrator) / 300 (specialist) iterations per turn with doom/streak/volume guards + supervisor pulses, and chat turns re-invoke as synthesis follow-ups (blocking defers join by default). A batch of 5 questions needs none of that extended: it is ONE tool call → ONE record → ONE hold-open → ONE synthesis injection (`Human answers: 1) … 2) …`). No new turn machinery, no polling, no second funnel. If a future batch wants answer-as-you-go partial progress, that is a deliberate extension — explicitly OUT for v1 (all-at-once keeps the hold + audit trivially correct).

## 2. Goal state

- Every tool badge (chat inline AND specialist/detail) shows what the user needs to audit the turn at a glance: read window, write/edit diff, bash command + collapsed response, grep/glob/git/todo parameters + counts. Scan-safe (caps everywhere), no clutter (one-liner default, expanders for the rest), reload-safe (persisted rows, not live-only).
- `ask_human` accepts additive `questions[]` (≤5): one wait, one record, one stacked card; single-question callers byte-identical to today.
- Opencode parity probed first; adopted surface documented in-plan.

## 3. Steps

### Step 0 — Probe: opencode baseline + payload inventory (~0.25 sess)

1. Capture the installed opencode `question` tool schema (params: `questions` array? per-item `question/header/options/multiSelect`? answer keying?) — table in-plan; `multiSelect` in/out locks here.
2. Inventory current per-tool `state.input/output` payloads on BOTH paths (engine SSE + opencode v2 parts) for read/write/edit/bash/grep/glob/git/todo.
3. Done-gate: inventory + schema tables committed to this plan (§6); step 1 branches on them, never on memory. Keep the probe as a drift script where cheap.

### Step 1 — Backend enrichment contract (~0.75 sess)

1. `sweave/chat/tools.py`: per-tool `detail` builder (additive key on the row) + `output_excerpt` (bash, 2K cap) — generic over input+output so BOTH harnesses benefit, not engine-only:
   - read: `{filePath, offset, limit, total?, shownFrom, shownTo, sameWindow?}`. Engine emits structured window in state (never footer-parsing); opencode derives what the parts carry (missing total = absent, never guessed).
   - write: `{mode: create|overwrite, linesAdded, linesRemoved?, preview}` + old-capture for overwrite (small files only, capped at 200K, fail-safe None → falls back to preview+stats). Edit keeps full old/new (already).
   - bash: `{command, exit?, output_excerpt(2K), truncated}` — command ALWAYS carried (even on failure), exit structured where known (engine adds it; opencode parses nothing — absent stays absent).
   - grep: `{pattern, path?, include?, matchCount}` — NEVER match content. glob: `{pattern, path?, count}`. git: `{verb, args}`. todo: `{titles[]}`. defer/list/ask/escalate: today's one-liners unchanged.
2. Engine (`tools.js`/`loop.js`): emit the structured fields above in tool state (window for read, old-capture/stats for write, exit for bash, count for grep/glob). Additive only; provider-visible text outputs unchanged.
3. WS (`chat.tool`, `specialist.tool`) carries the enriched row verbatim; `compact_tool_record` caps: detail JSON ~2K, excerpt 2K, existing `INPUT_JSON_CHARS`/`SUMMARY_CHARS`/`MAX_TOOLS_PER_MESSAGE` discipline held.
4. Done-gate: pytest (per-tool detail unit matrix incl. garbage-never-raises, caps pinned, opencode-parts path, engine-state path, reload round-trip, composer-isolation regression), `run.py --check`, suite 3× where the repo demands.

### Step 2 — Chat + detail binding (~0.75 sess)

1. `Thread.tsx` (`ToolRow` + `SegmentedBody`): one-liner default (`Read path - Lx–y/z`, `Bash $cmd`, `Grep "pat" in p (n)`, …) + expanders: bash response collapsed (2K inline), write/edit green-red side-by-side diff (reuse `EditTool`), read same-window badge. Minimal-clutter rule: the collapsed row never grows taller than today.
2. `sections.tsx` (`ToolTimelineRow` + Tools tab): same enriched fields (full bash output already there — keep; add window/counts/stats rows).
3. `types/index.ts` (`ChatToolRow.detail`, `output_excerpt` additive, optional → old servers degrade to today's one-liners).
4. Done-gate: vitest (row-per-tool render matrix, expander open/close, same-window badge, legacy-row degrade), `npm run build` green, Playwright only if the repo gate demands.

### Step 3 — ask_human batch (~1 sess)

1. Contract (additive, all layers same shape): `ask_human` accepts `questions: [{question, options?}≤5]` alongside legacy `question`; legacy normalizes to 1-elem server-side. `>5` → `rejected:` line (not a crash). `multiSelect` only if step 0 locked it in.
2. Store (`escalation.py`): record gains `questions[]` + `answers[]` (additive; single-question records project as 1-elem). One record per delegation still (overwrite discipline unchanged). Answer path accepts `{answers: string[]}` alongside `{response}` (single); status flips only when ALL answered (all-at-once) — partial posts persist without resolving (or are rejected with a clear line; executor picks + pins by test).
3. Router (`routers/delegations.py`): `EscalateRequest.questions?`, `AnswerRequest.answers?` (validation: lengths match, non-empty, ≤5). MCP (`mcp/__init__.py` `_ask_human`) + engine (`sweave.js` `callAskHuman` + `SWEAVE_TOOL_DEFS`) mirror the shape with IDENTICAL `rejected:` strings. Orchestrator charter (`agents/orchestrator/config.yaml` defer-contract section) documents batch + max-5.
4. UI (`TurnQuestions.tsx`): stacked per-question sections (options as buttons per question, per-question input, single Send-all + per-question answer where trivially consistent with the store choice). Skip stays single + system-confirmed (skip = whole batch → best judgment).
5. `ChatLoop` hold unchanged (waits on record status); synthesis injects all Q/A pairs. Permission asks untouched (never batched).
6. Done-gate: pytest (compat single, batch-5, 6-rejected, partial semantics, skip-whole-batch, legacy-record projection, hold-then-synthesize incl. engine `waitEscalation` shape), `run.py --check`; vitest (stacked card, option buttons per question, legacy single degrade); live gate (real turn asks 2, answers both, synthesis quotes both).

## 4. Explicit non-goals

- No full outputs inline (2K excerpt rule stands; detail holds the rest).
- No `multiSelect` v1 unless step 0 proves load-bearing.
- No batching beyond `ask_human` (read/glob/grep multi-call are follow-ups).
- No second input funnel (pane stays read-only + abort/answer).
- No composer reads of rows (UI-only contract holds; no test relaxed).
- No new dependency (§8 untouched).

## 5. Risks

- Row bloat → mitigated by per-field caps pinned in tests (the 2026-09-14 607K-read lesson).
- Write old-capture IO/race → small-file cap + read-before-write best-effort; failure degrades to preview+stats, never fails the turn.
- Batch partial-answer ambiguity → locked in step 3 by test before UI binds.
- Opencode schema drift → probe table + drift script; degrade (unknown keys ignored, never crash).
- Two-surface divergence (chat vs detail) → one `detail` builder serves both; vitest pins both renderers against the same fixtures.

## 6. Execution record (executor appends; probe tables land here)

### Step-0 probe results (2026-09-16, drift scripts: `scripts/probe_tool_payloads.py`, `scripts/probe_question_schema.py`)

**A. Opencode `question` tool schema (1.18.31, installed binary)**

| Probe surface | Result |
|---|---|
| JSON schema route | NONE: `/doc/json`, `/openapi.json`, `/doc/openapi.json`, `/doc.json` all serve the SPA HTML (v2 API exposes no OpenAPI). `/tool*`/`/config/tools`/`/tool/ids`... are SPA catch-alls (200 HTML). |
| `/agent` profiles | 7 agents (build/compaction/explore/general/plan/summary/title); `has_question=True` on ALL (the word appears in tool gating/profile JSON) but NO tool schema in the profile; `tools` preview empty on every profile |
| `/permission` | pending-list surface (`[]`); the write lever is `/session/{sid}/permission(s)/{rid}` per M1.12 jump table (our `permission_bridge.py` rides it) |  
| binary grep | tool schemas are Go structs, not greppable strings (Bun/Go bundle; Emacs/JS noise drowns the table) |
| LIVE turn (schema capture) | **BLOCKED — free-tier quota**: openrouter 429 `free-models-per-day` on both `:free` models (`lfm-2.5-2.6b:free`, then `gemma-4-26b-a4b-it:free`); per plan the probe exits BLOCKED without a retry loop |

**Lock:** `multiSelect` stays OUT for v1 (F5 default; the probe did NOT show it load-bearing — no surface carried it at all). The opencode `question` tool is NOT the batching carrier for Sweave: our ask path rides `EscalationStore` (`POST …/escalate` + `/answer`/`/skip`), and step 3's batch shape is OUR record shape — the opencode-schema dependency drops to "opencode parity is the probe baseline when a live turn is affordable" (see amendment block below).

**B. Per-tool payload inventory — ENGINE path (live journal `~/.sweave/engine/sessions.json`, 2026-09-16; result_chars_max = longest single tool result char count)**

| tool | results | result_chars_max | input persistence today (loop.js emit sites) |
|---|---|---|---|
| bash | 1531 | 32,901 (32K tail-cut marker rides) | `input=call.args` on tool.completed/failed — command IS in state.input (the row drops it: `_capped_input` keeps edit-like only) |
| read | 1009 | 607,551 pre-cap; 32,796 post-cap | `input=call.args`; the window is FOOTER TEXT inside `output` (`(Showing lines X-Y of total. Use offset=…)`) — no structured window |
| write | 93 | 82 | `input=call.args` — but `_capped_input` DOES persist write (edit-like match), full old/new never computed (writePath returns just `wrote {filePath}`; old bytes not captured pre-write) |
| edit | 139 | 152 | input persisted (edit-like); result string `edited {filePath} (N replacements)` |
| grep | 229 | 30,094 | `input=call.args` (pattern/path/include); match COUNT only implicit (result line count, capped 100) |
| glob | 74 | 32,796 | `input=call.args`; no count field |
| git | 90 | 32,796 | `input=call.args` (verb/args); no count/window |
| todo | 70 | 1,186 | input persisted (edit-like match, includes `todos[]`) |
| defer | 34 | 175 | input dropped; `escalate` folded same |
| ask_human | 1 | 273 | input dropped |
| list_specialists | 10 | 512 | input ({}) — nothing to persist |

**C. Per-tool payload inventory — OPENCODE v2 parts (recorded shapes, free-tier probe 0 + M1.9 parts-model suite)**

| field | shape on the wire |
|---|---|
| part keys | `callID, id, messageID, metadata, sessionID, state, tool, type` |
| state keys | `status, input, output, error, title, time, metadata` |
| statuses | `pending → running → completed \| error` (permissive: pending may be absent) |
| input per tool | read `{filePath, offset?}`, edit `{filePath, oldString, newString, replaceAll?}`, write `{filePath, content}`, bash `{command, timeout}`, grep `{pattern, path?, include?}`, glob `{pattern, path?}`, git n/a (engine-native) |
| output per tool | read/carries the FOOTER text too (`(Showing lines X-Y of N. Use offset=…)`); bash carries tail-cut text + exit code only in the ERROR string (`exit {code}:`) on failure; edit/write return short strings (`edited…`/`wrote…`); grep/glob list lines |
| opencode totals | NO structured window/count/exit — absent stays absent (the projector never guesses) |

**Step-1 shape decision (locked):** the `detail` builder derives what the WIRE carries (input fields + output presence) on both paths; the ENGINE additionally gains structured state fields (window/exit/count/old-capture) so the engine path is exact; the opencode path projects to the same keys whenever its parts carry them, ABSENT otherwise (never parsed out of footer text on either path). `sameWindow` compares `(filePath, offset, limit)` against the previous read row.

### Amendment 2026-09-16 — probe block (schema) reshapes step 3's carrier lock

**Finding (closes tool cards enrichment as planned) — the opencode `question`
tool is not the batch carrier, and the probe block justifies the change:**

1. The installed 1.18.31 serve exposes NO tool-schema surface: every doc
   route (`/doc/json`, `/openapi.json`, `/doc/openapi.json`, `/doc.json`)
   serves the SPA HTML; `/tool/ids`-family and `/config/tools` are SPA
   catch-alls; `/agent` carries profiles only. Binary grep finds no
   extraction (tool schemas are Go structs drowned by bundled JS).
2. The live free-tier capture is BLOCKED (openrouter 429
   `free-models-per-day` on both `:free` models) — per plan that exits
   BLOCKED, never a retry loop.
3. Step 3's carrier was OUR surface already (post `/session/{sid}/escalate`
   → `EscalationStore` + `/answer`,`/skip`), NOT the opencode `question`
   tool; the probe result just says that lock likewise stands on OUR
   shape (questions[]/answers[]) — a row that never depended on
   opencode's internals.

**Lock: `multiSelect` stays OUT for v1 (F5 default; the probe showed no
carrier for it at all). Step 3 proceeds exactly as written against the
EscalationStore + MCP + engine `sweave.js` parity — the plan's levels are
txt-compatible with the INSTALLING carrier. `questions[]`'s opencode-side
live turn drags the batch only if its schema is later captured
(drift script re-run when affordable).** The starting-point claim it
amends: "opencode batching surface is the probe baseline; adopt what
earns its keep" — the probe baseline is a BLOCKED one, so the sweep
falls to our own surface (which is what the plan already targeted).
