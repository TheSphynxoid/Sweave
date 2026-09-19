// Durable engine session store (ruling 3: restarts without dropping
// sessions). JSON file under the engine data dir; doubles as the
// resume-from-partial journal. History entries carry engine message
// ids (msg_*) so POST /revert can name its target.
//
// Pointer semantics (opencode parity): revert records
// { to_message } — the listing still returns everything, and the NEXT
// /run builds history truncated after to_message (the prompt replaces
// the reverted tail).

import { readFileSync, writeFileSync, mkdirSync, existsSync, renameSync } from "node:fs";
import { join } from "node:path";

let counter = 0;

export function newMessageId(prefix) {
  counter += 1;
  return `${prefix}_${Date.now().toString(36)}_${counter}`;
}

export class SessionStore {
  constructor(dataDir) {
    this.file = join(dataDir, "sessions.json");
    this.sessions = new Map();
    try {
      mkdirSync(dataDir, { recursive: true });
      if (existsSync(this.file)) {
        const raw = JSON.parse(readFileSync(this.file, "utf8"));
        let scrubbed = false;
        for (const [id, s] of Object.entries(raw)) {
          // Always-grants are memory-only (per-run ruling 2026-09-15):
          // they live in loop.js's sessionApprovals map, never here.
          // Scrub legacy `approvals` arrays so a restart wipes them
          // even for journals written before the ruling.
          if (s && typeof s === "object" && "approvals" in s) {
            delete s.approvals;
            scrubbed = true;
          }
          // Shape validation (hygiene B5): a torn/hand-edited entry
          // (string, array, null, or object without a messages
          // array) used to poison every later append/historyForRun
          // with a TypeError. Drop it loudly — one corrupt session
          // recreates on demand, never bricks the boot.
          if (!s || typeof s !== "object" || !Array.isArray(s.messages)) {
            try {
              process.stderr.write(`sweave-engine: dropping corrupt session ${JSON.stringify(id)}\n`);
            } catch {}
            scrubbed = true;
            continue;
          }
          this.sessions.set(id, s);
        }
        // Persist the scrub so the stale bytes don't linger either.
        if (scrubbed) this.save();
      }
    } catch {
      // Corrupt journal degrades to empty (sessions recreate on
      // demand, same as the opencode 404-recreate path) — never boot-fail.
    }
  }

  save() {
    try {
      // Atomic write (tmp + rename): a crash mid-write must never
      // tear the whole journal (which would read back as corrupt and
      // silently amnesia every session on the next boot).
      const tmp = `${this.file}.tmp`;
      writeFileSync(tmp, JSON.stringify(Object.fromEntries(this.sessions)), "utf8");
      renameSync(tmp, this.file);
    } catch (e) {
      // Loud, not silent: a lost journal means turns succeed and
      // then vanish on restart. The in-memory session still serves.
      try {
        process.stderr.write(`sweave-engine: journal save failed: ${(e && e.message) || e}\n`);
      } catch {}
    }
  }

  get(id) {
    return this.sessions.get(id) || null;
  }

  ensure(id) {
    let s = this.sessions.get(id);
    if (!s) {
      s = { id, messages: [], revert: null, created: Date.now() };
      this.sessions.set(id, s);
      this.save();
    } else if (s && typeof s === "object" && "approvals" in s) {
      delete s.approvals;
    }
    return s;
  }

  /** History for the next prompt: full listing cut after the revert pointer.
   *
   * No-rotation invariant (user ruling): sessions are immortal — a
   * revert rewrites history in place, never discards the session.
   * `exclusive: true` drops the named message itself too (edit =
   * history rewrite: the old user prompt must not survive alongside
   * its replacement). Absent/unknown target keeps everything (never
   * truncate blindly on a bad id).
   */
  historyForRun(session) {
    if (!session.revert) return session.messages;
    const idx = session.messages.findIndex((m) => m.id === session.revert.to_message);
    if (idx === -1) return session.messages;
    return session.messages.slice(0, session.revert.exclusive ? idx : idx + 1);
  }

  append(session, entry) {
    session.messages.push(entry);
    session.updated = Date.now();
    this.save();
  }
}

/**
 * Drop unanswered tool calls from replayed history (both flavors).
 * A turn that dies mid-tool-loop (abort, timeout, crash/restart after
 * the assistant entry was appended but before every tool output
 * landed) leaves an assistant function_call/tool_calls entry with no
 * matching tool output in the journal. Replaying it verbatim makes
 * the NEXT turn on that session fail deterministically before any
 * work: Console Go 400s the chat flavor ("assistant message with
 * 'tool_calls' must be followed by tool messages...") and the
 * Responses flavor ("No tool output found for function call ...").
 * Sessions are immortal and resumed across delegations, so one
 * poisoned turn bricks the session for every future specialist
 * (2026-09-19: 17/358 journals poisoned, two fix-round delegations
 * failing loud with truncated "[inval..." errors).
 *
 * Rule: keep only calls that have a matching tool output. An
 * assistant entry left with zero calls keeps its text (as plain
 * content) or is dropped when empty. Failed assistant entries are
 * already excluded downstream; this covers the non-failed dangling
 * case. Write-time repair is deliberately NOT attempted: a
 * crash/restart poison can never be fixed at write time (the process
 * is gone), so the read-time sanitize is the load-bearing fix.
 *
 * Lives here (not loop.js) so both mappers — loop.js chat and
 * responses.js — share one choke point without an import cycle.
 */
export function sanitizeHistory(entries) {
  const answered = new Set();
  for (const m of entries || []) {
    if (m && m.role === "tool" && m.toolCallId) answered.add(m.toolCallId);
  }
  const out = [];
  for (const m of entries || []) {
    if (
      m &&
      m.role === "assistant" &&
      !m.failed &&
      Array.isArray(m.toolCalls) &&
      m.toolCalls.length > 0
    ) {
      const kept = m.toolCalls.filter((tc) => tc && answered.has(tc.id));
      if (kept.length === m.toolCalls.length) {
        out.push(m);
      } else if (kept.length > 0) {
        out.push({ ...m, toolCalls: kept });
      } else if (m.content) {
        const { toolCalls: _dropped, ...rest } = m;
        out.push({ ...rest, toolCalls: [] });
      }
      // else: empty + fully unanswered — drop the message entirely.
    } else {
      out.push(m);
    }
  }
  return out;
}

// Pre-flight history ceiling (hygiene B5): immortal sessions grow
// forever (live 1000–2395 msgs observed) and every loop iteration
// re-sends full history, so an old session eventually 400s on
// context length — deterministically, every turn, recoverable only
// by manual /revert. Cap the MAPPED history (journal truth is
// untouched): drop oldest first, keep at least the newest message
// (the live prompt always rides), and report what was cut so the
// mappers can name it honestly instead of silently narrowing
// context. Budgets are deliberately message+byte (tokenizers are a
// dependency the sidecar refuses).
export const HISTORY_MAX_MESSAGES = 400;
export const HISTORY_MAX_CHARS = 500000;

function entryChars(m) {
  let n = 0;
  if (m && typeof m.content === "string") n += m.content.length;
  if (m && Array.isArray(m.toolCalls)) {
    for (const tc of m.toolCalls) {
      try {
        n += JSON.stringify(tc && tc.args ? tc.args : {}).length;
      } catch {}
      if (tc && typeof tc.name === "string") n += tc.name.length;
    }
  }
  return n;
}

export function historyTruncationNote(droppedMessages, droppedChars) {
  return (
    `[sweave history note: ${droppedMessages} oldest message(s) ` +
    `(${droppedChars} chars) omitted to fit context; earlier work is ` +
    `out of scope — continue from what is shown]`
  );
}

export function capHistory(entries) {
  const list = Array.isArray(entries) ? [...entries] : [];
  let chars = list.reduce((n, m) => n + entryChars(m), 0);
  let droppedMessages = 0;
  let droppedChars = 0;
  while (
    (list.length > HISTORY_MAX_MESSAGES || chars > HISTORY_MAX_CHARS) &&
    list.length > 1
  ) {
    const m = list.shift();
    droppedMessages += 1;
    const c = entryChars(m);
    droppedChars += c;
    chars -= c;
  }
  return { entries: list, droppedMessages, droppedChars };
}
