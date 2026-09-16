# Git read-only tool (engine-native) — plan of record

Status: **done** (2026-09-16) — steps 1–3 executed 2026-09-15
(d02e4a8 / 47770ed / ee9ada7); step 4 fix-up executed 2026-09-16
(gates green, GOTCHAS entry 8 completed after a concurrent-workstream
clobber, plan doc landed). Sequencing note resolved: the turn-timeout
fix (supervisor thread) + stats polish both landed before execution.

## 0. Rulings (user-locked 2026-09-15)

1. **Native `git` exec-tool, argv-exec, verb allowlist, no shell.**
   Not bash-with-git (shell strings need parsing; `git log; rm -rf`
   defeats prefix checks), not a new MCP tool (opencode-only +
   stdio hop + 30s timeout workaround). Engine-is-spec: land
   engine-first; opencode needs nothing (reads already allowed
   through the `bash` ferry).
2. **Offered to the orchestrator only** (joins `ORCHESTRATOR_TOOLS`
   alongside `edit`/`write`/`todo` under the 2026-09-15 widening).
   Specialists keep `bash`; no change.
3. **Verb list locked at build**: `log show status diff branch
   ls-files rev-parse`. Adding `stash`/`clean`/`checkout` later is
   a new ruling, never scope creep.
4. **Maintenance accepted**: git CLI read verbs are
   decade-stable; cost is ~100 lines + ~5 hermetic tests (same
   order as `grep`/`glob`). No protocol version bump (additive).

## 1. Starting point (re-verified 2026-09-15 against code)

- Engine tools: `sweave-engine/src/tools.js` — `EXEC_TOOL_DEFS`
  schema list (`read/edit/write/bash/glob/grep/todo`), the
  `executeTool` switch, `matchTarget`, `permissionKey`
  (`write`→`edit`), `capResult` 32K hygiene. `read` defaults to
  2000 lines (`DEFAULT_READ_LIMIT`, opencode parity); `bash`
  tail-cuts via `runBash` (`exec`, cwd + timeout + SIGKILL abort).
- Loop offering: `sweave-engine/src/loop.js: toolDefsFor` filters
  `EXEC_TOOL_DEFS` by `body.tools` + role sweave tools;
  orchestrator always loops (`needsLoop`).
- Wire acceptance: `sweave/engine/protocol.py: TOOL_BASELINE`
  (7 names) + `validate_run_request` rejects unknown tools (loud
  `bad_request` on mismatch). Sidecar mirrors via `KNOWN_TOOLS` +
  `serve.js: validateRun`. `TOOL_BASELINE` doc comment requires a
  user ruling + trace-use audit per addition — **this plan §0 is
  that ruling; the audit is the archaeology use-case** (commit
  archaeology, branch verification, stale-pin checks — every
  planning round hits these and currently can't run them).
- Orchestrator scope: `ORCHESTRATOR_READONLY_TOOLS =
  ("read","grep","glob")` + `ORCHESTRATOR_TOOLS = (read, grep,
  glob, edit, write, todo)` (2026-09-15 `.md` widening,
  `specialist_runtime.py:122-140`); `ORCHESTRATOR_MD_WRITE_MAP =
  {"*": "deny", "*.md": "allow"}` structural on the engine
  (`:923-924`, `:937`). Opencode side denies git-mutation bash
  patterns only (`agent_permission.py:
  ORCHESTRATOR_BASH_DENY`) — `git log/show/status/diff` already
  pass. Still no bash on the engine, ever.
- Test pattern: `tests/test_engine_tools.py` (node-gated,
  sidecar spawn, scripted turns, deny/ask/loop guards).

## 2. Goal state

`git` joins the engine exec tools as a first-class read-only
tool. The planner (and any future orchestrator turn) runs commit
archaeology without asking the user; specialists are untouched.

## 3. Steps

### Step 1 — Sidecar `tools.js` (~0.3 session)
- Schema def (~20 lines, terse-description doctrine):
  `{verb: enum[log, show, status, diff, branch, ls-files,
  rev-parse], args: string[]}`.
- Executor `runGit`: `spawn("git", [verb, ...sanitizedArgs],
  {cwd})` — argv, never a shell string. Unknown verb →
  `rejected:` before spawn (doom-guard pattern, typed error the
  model adjusts to, never an ask).
- Flag denylist (structural, not a parser): reject any arg
  starting with `-` except `--stat --oneline -n --name-only
  --porcelain`; deny `--upload-pack --exec -c --config`
  explicitly.
- Defaults: `log` → `-n 20 --oneline` unless args say otherwise
  (same doctrine as `read`→2000: paged by default, explicit
  wins).
- Wiring: `matchTarget("git")` → `{target: verb, isPath:
  false}`; `permissionKey("git")` → `"git"` (rides `gateToolCall`
  untouched; default-allow like `read`, future-deniable).
  `capResult` reuse (32K path).
- Done-gate: hermetic node tests — unknown verb rejected w/o
  spawn; `--upload-pack`/`-c`/`--exec` denied; `log` defaults
  `-n 20 --oneline`, explicit wins; non-repo cwd fails loud
  naming git; oversized output tail-cut + marker; small output
  byte-identical.

### Step 2 — Wire acceptance (~0.1)
- `protocol.py: TOOL_BASELINE += "git"` + sidecar `KNOWN_TOOLS`
  + serve `validateRun` accept.
- No version bump (additive; old-Python never sends `git`;
  mismatch fails loud as `bad_request`, never cryptic).
- Done-gate: `validate_run_request` accepts `["git"]`; document
  the old-sidecar mismatch shape in test.

### Step 3 — Python offering (~0.1)
- `ORCHESTRATOR_TOOLS += ("git",)`. Assert orchestrator
  `body["tools"]` carries it; specialist default doesn't
  (baseline-inherit is a build-time decision, default no).
- Done-gate: contract tests on the offered set per role.

### Step 4 — Regression + docs (~0.1)
- Full `pytest` + `npm run build` green; grep `truncated` for
  marker-assert collisions.
- Follow-up (same change, doc commits allowed): DESIGN §4 row
  (git read-only tool ✅), GOTCHAS entry (argv-exec rationale),
  CUSTOM_ENGINE_PLAN tool-table line.

## 4. Explicit non-goals

- No write verbs (`commit/merge/push/checkout/apply`;
  `checkout` excluded as mutation-adjacent).
- No structured JSON output v1 (porcelain/oneline text only).
- No opencode changes (ferry exists).
- No protocol version bump.
- No `bash` changes; no prompt/response text collection.
- No scheduler, no new MCP slots.

## 5. Risks

- `git.exe` absent / non-PATH on Windows → fail loud with an
  install hint, never silent.
- Non-git cwd → loud `not a git repository`, no fallback.
- Large history → default `-n` + `capResult`; never whole-log.
- Parallel threads own `sweave/*` worktrees — read-only verbs
  can't disturb them, but `status`/`diff` output may surprise;
  scope reads to the turn cwd (default) unless args say otherwise.

## 6. Execution record

- 2026-09-15: plan detailed (planning session).
- 2026-09-15, step 1 (`d02e4a8`): sidecar `runGit` — argv-exec,
  `GIT_VERBS` allowlist (log/show/status/diff/branch/ls-files/
  rev-parse), structural flag gate (`GIT_FLAG_ALLOW` +
  `GIT_FLAG_DENY_PREFIX` + fused-`-n<N>`/`--flag=value` regex; bare
  `-` args denied otherwise), `log` default `-n 20 --oneline`
  (explicit wins), pre-spawn `rejected:` typed errors, loud
  git-missing/non-repo failures, `matchTarget`→verb,
  `permissionKey`→`git` (default-allow), schema def in
  `EXEC_TOOL_DEFS`. Build evidence locked a delta vs the plan's
  simpler denylist sketch: the deny-prefix check alone passed
  `-n20`/`--foo=bar` fused forms carrying arbitrary payloads, so
  the fused-allow shape was added (audited by the `d02e4a8` test
  block's deny cases).
- 2026-09-15, step 2 (`47770ed`): `TOOL_BASELINE += "git"`
  (`sweave/engine/protocol.py`, doc comment carries the trace-use
  audit), sidecar `TOOL_BASELINE`/`KNOWN_TOOLS` (`providers.js`),
  `serve.js validateRun` accepted it via the shared `KNOWN_TOOLS`
  gate. Wire test: `test_run_request_accepts_git_and_names_unknown_
  sidecar_mismatch` (accept + old-sidecar `bad:tools (unknown: git)`
  shape). No version bump.
- 2026-09-15, step 3 (`ee9ada7`): `ORCHESTRATOR_TOOLS += "git"`
  (+ comment naming the plan; specialists untouched —
  `get_default_tools()` unchanged), orchestrator-tools tuple pin
  extended.
- 2026-09-16, step 4 (fix-up, this session): a dependency thread had
  already written the step-1 tests + this plan doc but timed out
  before committing them. Gates re-run fresh on the committed
  implementation + the uncommitted tests: 5/5 git sidecar tests
  (`node` v24.18.0, `git` 2.55.0.windows.5), protocol + orchestrator
  suites 62/62, full pytest 1190 passed (1 deselected:
  `test_m1_9_step2_chat.py::test_chat_surface_files_present` — the
  documented pre-existing UI-thread red, SessionPicker deleted by
  `f3f9dab`, not engine-related), `npm run build` green, run.py
  `13/13`. Marker grep (`truncated`) clean — no assert collision
  (`test_git_oversized_head_cut_and_small_byte_identical` shares
  only the `test_read_output_truncated_before_history` shape and
  both assert their own outputs). Docs: DESIGN §4 git row verified
  accurate (includes the deliverable list this record repeats);
  GOTCHAS entry 8 COMPLETED — a concurrent workstream (`0c1d590`
  supervisor step 5) had inserted only the title line and the body
  was lost; body rebuilt engine-first from the verified `tools.js`
  code and entries 8/9 restored to commit order. CUSTOM_ENGINE_PLAN
  tool-table line added at the step-2 row. Committed as
  "git-read tool step 4: ..." (plan doc + test block + docs fix-up).
