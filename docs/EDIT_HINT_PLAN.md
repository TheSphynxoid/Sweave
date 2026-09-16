# Edit close-match hint — plan of record

Status: **specified** (2026-09-16, this session — execution NOT
started). Predecessor reading: `sweave-engine/src/tools.js`
(`editPath`, `EXEC_TOOL_DEFS`, `capResult`), `docs/GOTCHAS.md`
(doc-editing discipline + encoding groups),
`docs/SUPERVISOR_PLAN.md` §10 (static guards stay exact-shape).

## 0. Motivating incident (2026-09-16, verified from trace)

Orchestrator planning turn `chat-295a73694c49` died at the
50-iteration ceiling with 76 tool calls. Forensics: 41 reads =
38 unique windows (legitimate coverage), but 5 `edit` calls
failed `oldString not found in file` and each dragged ~3–4
blind recovery iterations (grep + re-reads at nudged offsets
hunting invisible whitespace — the `PROJECT_STATE.md 348→350`
and `DESIGN.md 326→329` clusters). ≈15–20 of 50 iterations
burned on whitespace hunting, not work. The ceiling raise
(50→150, same day) covers the legitimate half; this plan kills
the waste half.

## 1. Rulings (PROPOSED 2026-09-16 — needs user lock)

1. **Hint-only, never auto-apply.** A near-miss stays a
   failure — the error text just names where and how close.
   Auto-fuzzy-apply risks editing the wrong region (the
   clobber class: PowerShell redirection mojibake, UTF-16 BOM
   writes — `docs/GOTCHAS.md` encoding groups). Fail-loud
   stands; the model re-issues with corrected context.
2. **No orchestrator bash, ever.** The read-inflation the
   forensics found (no-bash ⇒ every inspection is a tool
   call) is accepted cost. Bash edits bypass the exact-match
   gate entirely and re-open the clobber class (models are
   better at Git Bash than PowerShell, but `>`-redirection
   and here-docs still destroy multi-byte content). This plan
   makes the safe path cheaper instead of opening the
   dangerous one.
3. **Error text is the only channel.** No protocol bump, no new
   tool, no new trace verb — the hint rides the existing
   `tool.failed` error string (capped by `capResult` like any
   output).

## 2. Design (`editPath`, `tools.js` only)

On `count === 0`, before returning the bare failure:

1. Normalize line endings (`\r\n` → `\n`) on both sides and
   retry the count silently. Hit → fail with:
   `edit: oldString not found (line-ending mismatch — the file
   uses CRLF; match lines L1–L2 exactly as read, or resend with
   \r\n)`.
2. Else whitespace-insensitive search: collapse `[ \t]+` runs
   and compare per line; find the best-scoring window for the
   oldString's first line. Score above threshold → fail with:
   `edit: oldString not found; closest region lines A–B
   (differs in whitespace only):` + the region rendered with
   visible whitespace (`·` for space, `→` for tab, `␍` for CR),
   capped at ~20 lines / 2K chars.
3. Else keep today's bare `edit: oldString not found in file`
   (far-miss: the model is in the wrong place; a hint would
   mislead).

Multi-match behavior unchanged (`count > 1` rule runs on the
exact count only — normalized/whitespace matches never apply,
only hint). `replaceAll` path untouched. `write` untouched
(create-path has no match problem).

## 3. Steps

1. `editPath` hint paths (line-ending retry + whitespace
   close-match + caps) + unit-pin the exact-failure contract
   (today's strings preserved verbatim for far-miss).
2. Hermetic sidecar tests (`tests/test_engine_tools.py`
   pattern): CRLF near-miss names lines; whitespace-only
   near-miss shows the region; far-miss stays bare; exact
   single/multi semantics unchanged; hint capped.
3. Full pytest green + `run.py --check`; GOTCHAS pointer if a
   new whitespace shape is found en route.

## 4. Non-goals

No fuzzy apply; no `bash` for the orchestrator (ruling 2); no
protocol version bump; no trace/WS vocabulary; no charter
changes (the tool teaches at the point of failure — better
than another doctrine line the model sheds under load).

## 5. Risks

- A misleading hint costs more than a bare failure (model
  trusts it and burns iterations). Mitigate: threshold high,
  far-miss stays bare, region shown verbatim with visible
  whitespace so the model can verify rather than trust.
- Normalization must never leak into the write path — the
  retry in §2.1 is compare-only; any application reuses the
  exact-match branch on the raw bytes.

## 6. Gates

New hermetic tests green; existing engine suites green
(62/62); full pytest green; no marker-assert collisions
(`truncated` grep). Done-gate per series method (3× + live
check where called — no live check needed: fully hermetic).
