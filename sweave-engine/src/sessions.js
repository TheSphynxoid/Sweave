// Durable engine session store (ruling 3: restarts without dropping
// sessions). One JSON file per session under `<dataDir>/sessions/`
// (journal surgery 2026-09-20: the old single `sessions.json` grew to
// 22MB and was rewritten synchronously on EVERY append — every tool
// call of every iteration blocked the whole sidecar on a 22MB sync
// write). Sessions load lazily (boot indexes ids only) and appends
// flush debounced (250ms); `ensure` still persists immediately (the
// harness binding depends on it) and every turn end flushes
// synchronously. A legacy single-file journal is imported once on
// first boot (non-empty sessions only — 116 zero-msg dead entries
// stayed behind in the live 22MB file) and renamed
// `sessions.json.migrated`. History entries carry engine message ids
// (msg_*) so POST /revert can name its target.
//
// Pointer semantics (opencode parity): revert records
// { to_message } — the listing still returns everything, and the NEXT
// /run builds history truncated after to_message (the prompt replaces
// the reverted tail).

import { readFileSync, writeFileSync, mkdirSync, existsSync, renameSync, readdirSync, unlinkSync } from "node:fs";
import { join } from "node:path";

let counter = 0;

export function newMessageId(prefix) {
  counter += 1;
  return `${prefix}_${Date.now().toString(36)}_${counter}`;
}

export class SessionStore {
  constructor(dataDir) {
    this.dir = dataDir;
    this.shardDir = join(dataDir, "sessions");
    // Legacy single-file journal: migration source only (see below —
    // never written after the first sharded boot).
    this.file = join(dataDir, "sessions.json");
    // In-memory materializations (lazily loaded — boot indexes ids
    // only, so a 22MB journal costs one readdir, not a full parse).
    this.sessions = new Map();
    this.known = new Set();
    // Shards with unwritten appends + the debounce timer. The timer
    // is unref'd: a pending flush never holds the process open, and
    // every turn end flushes synchronously (serve.js finish()).
    this.dirty = new Set();
    this.saveTimer = null;
    try {
      mkdirSync(dataDir, { recursive: true });
      mkdirSync(this.shardDir, { recursive: true });
      this.migrateLegacy();
      for (const f of readdirSync(this.shardDir)) {
        if (typeof f === "string" && f.endsWith(".json")) {
          try {
            this.known.add(decodeURIComponent(f.slice(0, -5)));
          } catch {
            this.known.add(f.slice(0, -5));
          }
        }
      }
      this.gcEmpty();
    } catch {
      // Corrupt journal degrades to empty (sessions recreate on
      // demand, same as the opencode 404-recreate path) — never boot-fail.
    }
  }

  shardPath(id) {
    return join(this.shardDir, `${encodeURIComponent(id)}.json`);
  }

  writeShard(id) {
    const s = this.sessions.get(id);
    if (!s) return;
    try {
      const tmp = `${this.shardPath(id)}.tmp`;
      writeFileSync(tmp, JSON.stringify(s), "utf8");
      renameSync(tmp, this.shardPath(id));
    } catch (e) {
      // Loud, not silent: a lost journal means turns succeed and
      // then vanish on restart. The in-memory session still serves.
      try {
        process.stderr.write(`sweave-engine: journal save failed: ${(e && e.message) || e}\n`);
      } catch {}
    }
  }

  // Immediate checkpoint (the old save() contract: saveSession
  // callbacks, /revert, tests). Flushes dirty shards AND every
  // in-memory session — direct mutations (todoWrite's `s.todos =`,
  // the revert pointer) bypass append()'s dirty mark, so a
  // dirty-only flush would silently drop them. Turn-hot appends
  // still ride the debounced path; this runs at turn boundaries,
  // reverts, and todo saves (rare, small loaded set — never the
  // whole journal).
  save() {
    if (this.saveTimer) {
      try {
        clearTimeout(this.saveTimer);
      } catch {}
      this.saveTimer = null;
    }
    const ids = new Set([...this.dirty, ...this.sessions.keys()]);
    for (const id of ids) this.writeShard(id);
    this.dirty.clear();
  }

  // Turn-end flush (serve.js finish() calls this on every turn end —
  // the debounce window never outlives the turn that filled it).
  flush() {
    this.save();
  }

  scheduleSave() {
    if (this.saveTimer) return;
    try {
      this.saveTimer = setTimeout(() => {
        this.saveTimer = null;
        this.save();
      }, 250);
      if (this.saveTimer && typeof this.saveTimer.unref === "function") {
        this.saveTimer.unref();
      }
    } catch {
      this.saveTimer = null;
    }
  }

  // One-time import of a pre-shard single-file journal. Non-empty
  // valid sessions become shards (kept OUT of memory — get() loads
  // them on demand); empty/corrupt entries stay behind; an existing
  // shard always wins (it was written after any migration). The
  // legacy file is renamed away exactly once, so this never re-runs.
  migrateLegacy() {
    let raw;
    try {
      if (!existsSync(this.file)) return;
      raw = JSON.parse(readFileSync(this.file, "utf8"));
    } catch {
      try {
        renameSync(this.file, `${this.file}.corrupt-${Date.now()}`);
      } catch {}
      try {
        process.stderr.write("sweave-engine: legacy journal unparseable, quarantined\n");
      } catch {}
      return;
    }
    if (!raw || typeof raw !== "object") {
      try {
        renameSync(this.file, `${this.file}.corrupt-${Date.now()}`);
      } catch {}
      return;
    }
    let moved = 0;
    let skipped = 0;
    for (const [id, s] of Object.entries(raw)) {
      if (s && typeof s === "object" && "approvals" in s) delete s.approvals;
      if (!s || typeof s !== "object" || !Array.isArray(s.messages)) {
        skipped += 1;
        continue;
      }
      // Dead weight stays behind — unless it carries state (todos,
      // revert) worth keeping despite having no messages yet.
      const carriesState =
        s.messages.length > 0 ||
        (Array.isArray(s.todos) && s.todos.length > 0) ||
        s.revert;
      if (!carriesState || this.known.has(id)) {
        skipped += 1;
        continue;
      }
      this.sessions.set(id, s);
      this.writeShard(id);
      this.sessions.delete(id);
      this.known.add(id);
      moved += 1;
    }
    try {
      renameSync(this.file, `${this.file}.migrated`);
    } catch {}
    try {
      process.stderr.write(`sweave-engine: migrated legacy journal (${moved} sessions, ${skipped} empty/invalid skipped)\n`);
    } catch {}
  }

  // Drop dead-weight shards (hygiene 2026-09-20): every real turn
  // appends its user message immediately after ensure, so a shard
  // with no messages, no todos, and no revert pointer is a
  // validation/auth failure that minted a session and died — the
  // 116/358 class in the live journal. Anything carrying state
  // (todos, revert) survives regardless. Boot-only; turns never
  // create empties anymore (serve.js ensures after validation).
  gcEmpty() {
    let dropped = 0;
    const isEmpty = (s) =>
      Array.isArray(s.messages) &&
      s.messages.length === 0 &&
      (!Array.isArray(s.todos) || s.todos.length === 0) &&
      !s.revert;
    for (const id of [...this.known]) {
      let s = this.sessions.get(id);
      if (!s) s = this.loadShard(id, { retain: false });
      if (s && isEmpty(s)) {
        try {
          unlinkSync(this.shardPath(id));
        } catch {}
        this.sessions.delete(id);
        this.known.delete(id);
        dropped += 1;
      } else if (s) {
        this.sessions.delete(id); // index-only; reload on demand
      }
    }
    if (dropped > 0) {
      try {
        process.stderr.write(`sweave-engine: dropped ${dropped} empty sessions\n`);
      } catch {}
    }
  }

  // Read one shard into memory (null when absent/unusable — the
  // caller recreates on demand). Corrupt shards are quarantined
  // (renamed, never re-read) so one torn write can't poison every
  // later turn on that session.
  loadShard(id, { retain = true } = {}) {
    let s;
    try {
      s = JSON.parse(readFileSync(this.shardPath(id), "utf8"));
    } catch {
      return null;
    }
    if (s && typeof s === "object" && "approvals" in s) {
      delete s.approvals;
      if (retain) this.dirty.add(id);
    }
    // Shape validation (hygiene B5): a torn/hand-edited entry used
    // to poison every later append/historyForRun with a TypeError.
    // Quarantine it loudly — one corrupt session recreates on
    // demand, never bricks the boot.
    if (!s || typeof s !== "object" || !Array.isArray(s.messages)) {
      try {
        process.stderr.write(`sweave-engine: dropping corrupt session ${JSON.stringify(id)}\n`);
      } catch {}
      try {
        renameSync(this.shardPath(id), `${this.shardPath(id)}.corrupt-${Date.now()}`);
      } catch {}
      this.known.delete(id);
      return null;
    }
    if (retain) this.sessions.set(id, s);
    return s;
  }

  get(id) {
    const mem = this.sessions.get(id);
    if (mem) return mem;
    if (!this.known.has(id)) return null;
    return this.loadShard(id);
  }

  ensure(id) {
    let s = this.sessions.get(id);
    if (!s && this.known.has(id)) s = this.loadShard(id);
    if (!s) {
      s = { id, messages: [], revert: null, created: Date.now() };
      this.sessions.set(id, s);
      this.known.add(id);
    } else if (s && typeof s === "object" && "approvals" in s) {
      delete s.approvals;
    }
    // Immediate (not debounced): the harness binding persists this
    // id before the turn runs — a crash must not lose the creation.
    // One small file, never the whole journal.
    this.writeShard(id);
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
    // Turn-hot path (every assistant/tool message of every
    // iteration): mark dirty + debounced flush. A crash inside the
    // window loses at most 250ms of tail — the read-time sanitize
    // (not write-time repair) is the load-bearing poison fix, and
    // every turn end flushes synchronously (serve.js finish()).
    if (session && session.id) {
      // Alias guard: persist the object the caller actually
      // mutated, never a stale stored twin.
      if (this.sessions.get(session.id) !== session) {
        this.sessions.set(session.id, session);
        this.known.add(session.id);
      }
      this.dirty.add(session.id);
    }
    this.scheduleSave();
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
 * content) or is dropped when empty. A second pass drops orphan
 * tool outputs (a result whose call has no surviving assistant
 * entry — the context ceiling severs pairs oldest-first, and
 * replaying the output half alone 400s both flavors
 * deterministically). Failed assistant entries are already
 * excluded downstream; this covers the non-failed dangling
 * case. Write-time repair is deliberately NOT attempted: a
 * crash/restart poison can never be fixed at write time (the process
 * is gone), so the read-time sanitize is the load-bearing fix.
 *
 * ORDERING: callers must cap FIRST (capHistory) and sanitize
 * SECOND — capping a sanitized history severs pairs the sanitize
 * had just validated.
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
  // Orphan-output pass: a tool result whose call has no surviving
  // assistant entry (ceiling-severed, or an id-less stray) replays
  // as an unattributed function_call_output / tool message — a
  // deterministic 400 on both flavors. Drop it; an output that
  // cannot be attributed is meaningless downstream.
  const keptIds = new Set();
  for (const m of out) {
    if (m && m.role === "assistant" && !m.failed && Array.isArray(m.toolCalls)) {
      for (const tc of m.toolCalls) {
        if (tc && tc.id) keptIds.add(tc.id);
      }
    }
  }
  return out.filter(
    (m) => !(m && m.role === "tool" && !keptIds.has(m.toolCallId))
  );
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
// Per-turn reasoning persistence bound (matches the transcript
// reader's cap): thinking is replayed from the journal, so it must
// be bounded like every other history bytes class.
export const REASONING_MAX_CHARS = 8000;

function entryChars(m) {
  let n = 0;
  if (m && typeof m.content === "string") n += m.content.length;
  if (m && typeof m.reasoning === "string") n += m.reasoning.length;
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

export function historyTruncationNote(omittedMessages, droppedChars, omittedTurns) {
  const turns = omittedTurns > 0 ? ` across ~${omittedTurns} earlier turn(s)` : "";
  return (
    `[sweave history note: ${omittedMessages} message(s) omitted ` +
    `to fit context (${droppedChars} chars${turns}; tool calls without ` +
    `answers are never replayed); earlier work is out of scope — ` +
    `continue from what is shown]`
  );
}

export function capHistory(entries) {
  const list = Array.isArray(entries) ? [...entries] : [];
  let chars = list.reduce((n, m) => n + entryChars(m), 0);
  let droppedMessages = 0;
  let droppedChars = 0;
  let droppedTurns = 0;
  // Charter pin: index 0 is the session's charter+task anchor (the
  // role prompt rides the first user message, never repeated). Drop
  // from index 1 while more than two messages remain; the anchor and
  // the live prompt are never capped away from the model.
  while (
    (list.length > HISTORY_MAX_MESSAGES || chars > HISTORY_MAX_CHARS) &&
    list.length > 2
  ) {
    const m = list.splice(1, 1)[0];
    droppedMessages += 1;
    if (m && m.role === "user") droppedTurns += 1;
    const c = entryChars(m);
    droppedChars += c;
    chars -= c;
  }
  return { entries: list, droppedMessages, droppedChars, droppedTurns };
}
