// Durable engine session store (ruling 3: restarts without dropping
// sessions). JSON file under the engine data dir; doubles as the
// resume-from-partial journal. History entries carry engine message
// ids (msg_*) so POST /revert can name its target.
//
// Pointer semantics (opencode parity): revert records
// { to_message } — the listing still returns everything, and the NEXT
// /run builds history truncated after to_message (the prompt replaces
// the reverted tail).

import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
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
        for (const [id, s] of Object.entries(raw)) this.sessions.set(id, s);
      }
    } catch {
      // Corrupt journal degrades to empty (sessions recreate on
      // demand, same as the opencode 404-recreate path) — never boot-fail.
    }
  }

  save() {
    try {
      writeFileSync(this.file, JSON.stringify(Object.fromEntries(this.sessions)), "utf8");
    } catch {
      // Best-effort journaling; the in-memory session still serves.
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
    }
    return s;
  }

  /** History for the next prompt: full listing cut after the revert pointer. */
  historyForRun(session) {
    if (!session.revert) return session.messages;
    const idx = session.messages.findIndex((m) => m.id === session.revert.to_message);
    if (idx === -1) return session.messages;
    return session.messages.slice(0, idx + 1);
  }

  append(session, entry) {
    session.messages.push(entry);
    session.updated = Date.now();
    this.save();
  }
}
