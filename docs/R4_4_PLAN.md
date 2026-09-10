# R4.4 — Memory + Agents workbench + Settings (re-cut from reality, 2026-09-10)

Status: **re-cut planned** (supersedes the 2026-09-05 pre-dogfood strawman below;
the strawman is preserved at the bottom for lineage).

## Intervention note (user-locked 2026-09-10 — read first)

The wave-1 web UI was judged a failure by the user. All subsequent UI work was
**manually derived by the user under their own judgement, not built by executors
from plans**. Consequences for this plan:

- The R4_4 strawman (three panes behind a wave-1 dogfood gate) never described
  reality: the Memory / Agents / Settings pages already exist in
  `sweave-web/src/pages/` (shipped via manual derivation), while the backend
  they sit on does not work (see audit). Plans below are cut from the shipped
  UI + verified backend behavior, not from the strawman.
- The wave-1 dogfood gate is void: the user has driven the UI directly and the
  friction verdict is already in (this re-cut IS the friction list).
- Executor duty narrows to backend + conformance: make the shipped pages work,
  cover them with tests, and record deltas. No UI redesign from plans without
  an explicit user ruling per pane.

## Reality audit (verified 2026-09-10 against code + live server)

**Shipped UI (manual):**

- `Memory.tsx`: bank cards (global / project / session) with Recall + Retain
  tabs; header text also promises reflect, but there is **no reflect tab**,
  no `reflectMemory` client method (`api/client.ts:357` has banks/recall/
  retain only), no `memory.changed` WS event, results render as raw JSON.
- `Agents.tsx`: specialist cards by scope + edit dialog + model switch
  (commits `b1072ce`, `57907bc`).
- `Settings.tsx`: model/routing surfaces (registry work `b5073aa`).

**Backend (verified live 2026-09-10, TestClient):**

1. `POST /api/memory/recall|retain|reflect` with a JSON body → **422**
   (`sweave/web/routers/memory.py:18` declares scalar query params;
   the UI posts JSON bodies). The Memory page's Recall/Retain buttons
   cannot succeed as wired.
2. `hindsight_client` is not installed here, and no hindsight server runs;
   correctly-shaped calls raise `RuntimeError` (`backends.py:116`). The
   default config (`config.yaml: memory.backend=hindsight/embedded_slim`)
   is therefore unusable out of the box; chat degrades silently to empty
   memory sections (`transcript.py:378` try/except — by design, but it
   means memory contributes nothing anywhere).
3. Zero coverage: no pytest touches recall/retain/reflect; no vitest
   touches the Memory page (only transcript timestamps + seed tool
   names are pinned).

## Rulings (user-locked 2026-09-10)

1. **Local-first default.** A file-backed backend (naive recall now,
   sqlite-vec path per R7 later) becomes the default; hindsight turns
   opt-in. Memory must work with zero infra.
2. **Hosted embeddings opt-in** (answers the "no local hosting" need).
   Policy ownership sits with OpenRouter: the allowlist is fetched from
   `GET https://openrouter.ai/api/v1/endpoints/zdr` (auto-updated) and
   pinned at sync time; per-request enforcement is `provider: {zdr: true,
   data_collection: "deny"}` with `allow_fallbacks: false` (fail closed —
   an outage errors instead of silently routing to a retaining endpoint).
   Unknown/absent = locked (mirrors OpenRouter's own conservative stance).
   The consent names OpenRouter as policy owner and states policies may
   change. Gated on a probe confirming embedding-endpoint coverage of the
   ZDR list.
3. **Retention badges, three states:** verified-ZDR / retain-for-abuse
   (allowed-with-disclosure, distinct badge) / trains-or-unknown (locked).
   Abuse-scanning retention is not a blocker; training use is.
4. **Secret tag-and-vault.** Local detect (regex + entropy; LLM classifier
   second layer) → redact-and-vault (secret value to the OS credential
   store, tag like `[SECRET:aws_prod#1]` in memory text) → tagged entries
   pinned local-only (excluded from hosted batches) → at-rest encryption
   for the local store. (Encrypt-then-embed is impossible: ciphertext has
   no semantics to search; hence redact at the boundary, not after.)
5. **Factory fails closed** (`MemoryFactory.create`): hosted embeddings
   require allowlisted provider + persisted consent; the UI cannot
   silently exfiltrate by bug.

## Steps

1. **Contract fix + first tests** (~0.5 session). Pydantic body models for
   recall/retain/reflect (JSON bodies, matching the UI client); keep query-
   param compat only if a consumer needs it (none known — verify by grep).
   Add `reflectMemory` client method + reflect tab. Gate: endpoint tests
   (recall/retain/reflect round-trip incl. 422-shape regression) + Memory
   page vitest (render/tabs/answer paths) + `run.py --check`.
2. **Local-first backend** (~1 session). File-backed `MemoryBackend`
   (per-bank JSONL under `~/.sweave/memory/`; naive keyword retrieval;
   `ts` stamps per the M1.7 contract); default `backend: local`
   (hindsight opt-in via existing modes); health endpoint surfaced in the
   Memory pane header. Gate: backend unit tests + factory tests (fail-
   closed hosted path) + live page check (retain → recall round-trip).
3. **Memory pane v1 conformance** (~0.5 session). Reflect tab, health
   indicator, human-readable results (not raw JSON), empty/error states.
   Gate: vitest + build.
4. **Hosted-embeddings opt-in** (~1 session, gated on ZDR probe).
   Retention map fetched from `/endpoints/zdr` at sync time (+ date);
   consent dialog naming OpenRouter as policy owner; factory enforcement;
   badge per provider from the same map (single source of truth).
   Pre-step probe: ZDR-list embedding coverage + NIM embedder minimum
   requirements. Gate: probe report + consent/allowlist tests.
5. **Secret tag-and-vault** (~1 session, after step 2). Detect → vault →
   pin-local → at-rest encryption. Gate: redaction tests (known shapes +
   entropy), pin-local tests (tagged entries never in hosted batches),
   round-trip test (retain secret → recall returns tag, value resolves
   locally only).
6. **Docs + close-out** (~0.5 session). DESIGN §4/R4.4 + §2.3, PROJECT_STATE,
   GOTCHAS (contract-shape + fail-closed + tag patterns); full gates.

## Explicit non-goals

- Changing the shipped page layouts (user-owned; deltas only by ruling).
- Bank-level locality scoping (e.g. session banks always local) — sensible,
  follow-up on evidence.
- Hindsight removal (stays opt-in; `setup_hindsight.py` + doctor check stay).
- R6 encoder heads / compaction cadence (unchanged roadmap).
- Custom-engine memory API (the composer already abstracts the backend;
  engine-agnostic by construction).

## Risks

- **Scope creep into a vault product.** The OS credential store call is one
  function; a built-in vault UI is explicitly out. If the OS store proves
  awkward on any platform, fall back to encrypted local file (at-rest key
  from OS store) — same boundary, less surface.
- **ZDR list gaps for embeddings.** If the probe shows embeddings uncovered,
  step 4 blocks on manual curation (source + date per provider) rather than
  shipping on an assumption — in the dangerous direction, never.
- **Detection false negatives.** Regexes miss novel formats; that is why the
  LLM classifier is layer two and tagged-pin is default-deny for hosted
  batches (untagged-but-sensitive is still exposed — documented, not solved).

---

## Superseded strawman (2026-09-05, preserved for lineage)

> Three panes, sequenced Memory → Agents workbench → Settings behind a
> wave-1 dogfood gate (~3 sessions of daily driving); pre-dogfood spec
> identifying surfaces while dogfood determines ship order. Steps as
> originally written: read-only Memory pane (recall/reflect/retain forms +
> `memory.changed` WS), Agents workbench (scope groups, status pills, model
> switch, run-task affordance), Settings (Models/Routing/Catalog), cutover +
> dogfood-handoff docs. Non-goals: TUI, mobile, i18n, parallel-turn UI,
> specialist stream-follow, fanout/cross-review, agent-loop overhaul. Risks
> as originally noted (pane/chat ownership duplication, workbench-as-backstop,
> catalog staleness, scope creep). The gate never fired — the user derived
> the UI manually instead — and the backend audit above replaces the
> strawman's assumptions.
