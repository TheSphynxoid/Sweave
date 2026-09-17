// Execution-tool implementations (step 2). Zero dependencies.
//
// Permission model (opencode parity, enforced blindly from the
// orchestrator-rendered map — the engine never invents policy):
//   map = { tool: "allow"|"ask"|"deny" | { pattern: action } }
//   - string form = blanket verdict for the tool
//   - object form = pattern rules, LAST match wins (opencode rule)
//   - unknown tool key = "allow", except external_directory = "ask"
// Match targets mirror opencode: read/edit/write -> file path,
// glob -> pattern, grep -> regex, bash -> full command string,
// todo -> "*". Paths resolving OUTSIDE the turn cwd additionally
// consult the external_directory entry (default ask).
//
// ask -> ctx.askPermission({ permission, patterns, detail }) which
// the loop implements via SSE permission.asked + the Sweave
// POST /api/engine/permission round-trip. "Always" grants live in
// loop.js's memory-only per-session map (per-run + per-specialist;
// the journal never persists them).

import { exec, spawn } from "node:child_process";
import { promises as fsp, existsSync, statSync } from "node:fs";
import { join, resolve, relative, sep } from "node:path";

export const DEFAULT_BASH_TIMEOUT_MS = 120000;
export const PER_TOOL_BUDGET_MS = 1200000; // proposed 1200s, view-plan parity
export const MAX_OUTPUT_CHARS = 32768;
// Opencode parity: an omitted read limit pages (never whole-file).
export const DEFAULT_READ_LIMIT = 2000;

// Shell grounding (2026-09-15, incident b8544168fa59: a model
// emitted Unix pipes on Windows CMD for 12 minutes — 24 failures
// — because nothing named the shell). Best-offer Git Bash:
// explicit Git locations first (PATH order would grab WSL/Store
// stubs, verified live); SWEAVE_BASH_PATH overrides;
// non-Windows keeps the platform shell. Null = platform default
// (cmd.exe on Windows). Resolved once at boot, injected
// read-only for tests.
export function detectUnixShell({ platform, env, exists } = {}) {
  const plat = platform ?? process.platform;
  if (plat !== "win32") return null;
  const E = env ?? process.env;
  const candidates = [];
  if (E.SWEAVE_BASH_PATH) candidates.push(E.SWEAVE_BASH_PATH);
  const pf = E.ProgramFiles || "C:\\Program Files";
  const pfx = E["ProgramFiles(x86)"] || "C:\\Program Files (x86)";
  candidates.push(`${pf}\\Git\\bin\\bash.exe`, `${pfx}\\Git\\bin\\bash.exe`);
  const isThere = exists ?? existsSync;
  for (const c of candidates) {
    try {
      if (isThere(c)) return c;
    } catch {
      // next candidate
    }
  }
  return null;
}
export const UNIX_SHELL = detectUnixShell();

// The bash description names the shell (the model's only shell
// contract — charters stay static, this is per-boot truth).
// Pure over the detected shell + platform so tests pin both
// variants deterministically.
export function bashDescriptionFor(shell, platform) {
  const tail =
    "Oversized output keeps the tail (most recent); redirect to a file only for logs you will grep, and prefer the OS temp dir.";
  if (shell)
    return `Run a shell command in the turn cwd via Git Bash on Windows (Unix syntax: pipes, grep, head all work; prefer relative paths). ${tail}`;
  if ((platform ?? process.platform) === "win32")
    return `Run a shell command in the turn cwd via cmd.exe on Windows (no head/tail/grep pipes; prefer the read/grep tools for inspection). ${tail}`;
  return `Run a shell command in the turn cwd. ${tail}`;
}
export function bashDescription() {
  return bashDescriptionFor(UNIX_SHELL);
}

function globBody(pattern) {
  // Pragmatic glob (*, ?, **) -> regex body. Mirrors the shapes
  // opencode renders ("git commit*", "<root>\*", "<root>/**").
  let out = "";
  for (let i = 0; i < pattern.length; i++) {
    const c = pattern[i];
    if (c === "*") {
      if (pattern[i + 1] === "*") i++;
      out += ".*";
    } else if (c === "?") {
      out += ".";
    } else {
      out += c.replace(/[.+^${}()|[\]\\]/, "\\$&");
    }
  }
  return out;
}

function patternMatches(pattern, target, isPath) {
  if (pattern === "*") return true;
  try {
    const ci = isPath && process.platform === "win32";
    return new RegExp(`^${globBody(pattern)}$`, ci ? "si" : "s").test(target);
  } catch {
    return false;
  }
}

/**
 * Evaluate one permission. Returns "allow"|"ask"|"deny".
 * @param {object} map permission map from /run
 * @param {string} tool tool name
 * @param {string} target match target (path / command / pattern / "*")
 * @param {boolean} isPath whether target is a filesystem path
 */
export function evaluatePermission(map, tool, target, isPath) {
  const entry = map ? map[tool] : undefined;
  const defaults = tool === "external_directory" ? "ask" : "allow";
  if (entry === undefined) return defaults;
  if (typeof entry === "string") {
    return entry === "allow" || entry === "ask" || entry === "deny" ? entry : defaults;
  }
  if (entry && typeof entry === "object") {
    let verdict = null;
    for (const [pattern, action] of Object.entries(entry)) {
      if (patternMatches(pattern, target, isPath)) verdict = action;
    }
    if (verdict === "allow" || verdict === "ask" || verdict === "deny") return verdict;
  }
  return defaults;
}

function isOutsideCwd(cwd, absPath) {
  const rel = relative(cwd, absPath);
  return rel === "" ? false : rel.startsWith("..") || resolve(rel) === rel;
}

/**
 * Gate one tool call. Returns { verdict, permission?, patterns? }.
 * Path tools additionally consult external_directory when the
 * resolved path escapes the turn cwd.
 */
export function gateToolCall(map, tool, target, { isPath = false, cwd = null, absPath = null } = {}) {
  const verdict = evaluatePermission(map, tool, target, isPath);
  if (verdict !== "allow") {
    return { verdict, permission: tool, patterns: [target] };
  }
  if (isPath && cwd && absPath && isOutsideCwd(cwd, absPath)) {
    const ext = evaluatePermission(map, "external_directory", absPath, true);
    if (ext !== "allow") {
      return { verdict: ext, permission: "external_directory", patterns: [absPath] };
    }
  }
  return { verdict: "allow" };
}

function ok(output) {
  return { ok: true, output: String(output === undefined ? "" : output) };
}

function fail(error) {
  return { ok: false, error: String(error) };
}

function truncateOutput(text) {
  if (text.length <= MAX_OUTPUT_CHARS) return { text, truncated: false };
  return {
    text: text.slice(0, MAX_OUTPUT_CHARS) + `\n... [truncated ${text.length - MAX_OUTPUT_CHARS} chars]`,
    truncated: true,
  };
}

/**
 * Tail-cut for streaming-style outputs (bash): failures and verdicts
 * live at the END, so an oversized result keeps the most recent
 * bytes and names the head cut. Opencode-parity direction.
 */
function truncateTail(text) {
  if (text.length <= MAX_OUTPUT_CHARS) return { text, truncated: false };
  const cut = text.length - MAX_OUTPUT_CHARS;
  return {
    text: `... [truncated ${cut} chars from the start — showing the tail]\n` + text.slice(cut),
    truncated: true,
  };
}

async function readPath(cwd, filePath, offset, limit) {
  const abs = resolve(cwd, filePath);
  let st;
  try {
    st = statSync(abs);
  } catch {
    return fail(`read: no such file or directory: ${filePath}`);
  }
  if (st.isDirectory()) {
    const entries = await fsp.readdir(abs, { withFileTypes: true });
    const lines = entries.map((e) => `${e.isDirectory() ? e.name + "/" : e.name}`);
    return ok(lines.join("\n"));
  }
  if (st.size > 4 * 1024 * 1024) return fail("read: file too large (>4MB)");
  const raw = await fsp.readFile(abs, "utf8");
  if (raw.includes("\0")) return fail("read: binary file");
  const lines = raw.split("\n");
  const total = lines.length;
  const start = Math.max(0, (offset || 1) - 1);
  // Opencode parity: omitted limit pages (default window), it never
  // means whole-file — the 2026-09-14 incident was a limit-less read
  // dumping 607K chars into history. Explicit limits still win.
  const effLimit = limit || DEFAULT_READ_LIMIT;
  const slice = lines.slice(start, start + effLimit);
  let text = slice.join("\n");
  const last = start + slice.length;
  if (last < total) {
    text += `\n\n(Showing lines ${start + 1}-${last} of ${total}. Use offset=${last + 1} to continue.)`;
  }
  return ok(text);
}

async function editPath(cwd, filePath, oldString, newString, replaceAll) {
  const abs = resolve(cwd, filePath);
  let raw;
  try {
    raw = await fsp.readFile(abs, "utf8");
  } catch {
    return fail(`edit: no such file: ${filePath}`);
  }
  if (typeof oldString !== "string" || !oldString) return fail("edit: oldString must be non-empty");
  const count = raw.split(oldString).length - 1;
  if (count === 0) {
    // Close-match hint (EDIT_HINT_PLAN, 2026-09-16): a near-miss
    // stays a failure — the text just names where and how close,
    // with visible whitespace so the model verifies instead of
    // blind re-reading. Compare-only: normalization never reaches
    // the write path (exact application below still runs on raw
    // bytes). Far-miss keeps the bare contract string verbatim.
    const hint = editCloseMatchHint(raw, oldString);
    return fail(hint || "edit: oldString not found in file");
  }
  if (count > 1 && !replaceAll) {
    return fail(`edit: oldString matches ${count} times; use replaceAll or add context`);
  }
  const next = replaceAll ? raw.split(oldString).join(newString) : raw.replace(oldString, newString);
  await fsp.writeFile(abs, next, "utf8");
  return ok(`edited ${filePath} (${count} replacement${count === 1 ? "" : "s"})`);
}

// Visible-whitespace rendering for the edit close-match hint:
// the model verifies the shown region against its text instead
// of trusting a claim. Non-ASCII markers ride the error string
// (tool results already carry UTF-8; capResult bounds the size).
function showWhitespace(text) {
  return String(text).replace(/\r/g, "␍").replace(/\t/g, "→").replace(/ /g, "·");
}

function normWsLine(line) {
  return line.replace(/[ \t]+/g, " ").trim();
}

function lineOf(text, index) {
  let n = 1;
  for (let i = 0; i < index && i < text.length; i++) {
    if (text[i] === "\n") n++;
  }
  return n;
}

// Best-effort near-miss explanation for an edit with zero exact
// matches. Returns a hint string or null (far-miss → caller keeps
// the bare contract string). Never applies anything.
function editCloseMatchHint(raw, oldString) {
  const rawLf = raw.replace(/\r\n/g, "\n");
  const oldLf = oldString.replace(/\r\n/g, "\n");
  // 1) Line-ending retry (compare-only): LF-sent oldString against
  // a CRLF file. Line numbers are identical on both sides (the
  // fold drops no newlines).
  if (rawLf !== raw || oldLf !== oldString) {
    const idx = oldLf ? rawLf.indexOf(oldLf) : -1;
    if (idx >= 0) {
      const l1 = lineOf(rawLf, idx);
      // Trailing newlines don't extend the region (an oldString of
      // "line two\n" matched line 2, not "lines 2-3").
      const l2 = l1 + oldLf.replace(/\n+$/, "").split("\n").length - 1;
      return `edit: oldString not found (line-ending mismatch — the file uses CRLF around lines ${l1}-${l2}; resend oldString with \\r\\n endings, copied exactly as read)`;
    }
  }
  // 2) Whitespace-insensitive window search: every normalized
  // oldString line must equal the file window's normalized lines
  // ("differs in whitespace only" — the high threshold; anything
  // looser would mislead more than a bare failure). Bounded: huge
  // files and huge oldStrings keep the bare failure.
  if (raw.length > 1000000) return null;
  const HINT_MAX_LINES = 20;
  const HINT_MAX_CHARS = 2000;
  const rawLines = raw.split("\n");
  const normed = rawLines.map(normWsLine);
  const normOld = oldLf.split("\n").map(normWsLine);
  if (normOld.length > HINT_MAX_LINES) return null;
  // Trailing blank lines don't extend the region (an oldString of
  // "foo\n" names line N, not "lines N-N+1").
  while (normOld.length > 1 && normOld[normOld.length - 1] === "") normOld.pop();
  const anchor = normOld.findIndex((l) => l !== "");
  if (anchor < 0) return null;
  for (let s = 0; s + normOld.length <= normed.length; s++) {
    if (normed[s + anchor] !== normOld[anchor]) continue;
    let all = true;
    for (let k = 0; k < normOld.length; k++) {
      if (normed[s + k] !== normOld[k]) { all = false; break; }
    }
    if (!all) continue;
    const a = s + 1;
    const b = s + normOld.length;
    const region = rawLines.slice(s, s + normOld.length).join("\n").slice(0, HINT_MAX_CHARS);
    return `edit: oldString not found; closest region lines ${a}-${b} (whitespace differs — space=· tab=→ CR=␍; copy exactly as read):\n${showWhitespace(region)}`;
  }
      return null;
}

// TOOL_CARDS 2b (2026-09-17): the overwrite diff needs the PRE-WRITE
// bytes. Captured here (the site that owns the write window, racing
// nothing else in this turn), so toolStateExtra can project
// mode/linesRemoved/old_capture WITHOUT a second read. Best-effort:
// a missing file means a create, an unreadable or too-big old file
// means NO capture (the detail degrades to preview + stats), and
// NOTHING here can fail the turn (writePath still writes).
const WRITE_OLD_CAPTURE_CHARS = 200000;

async function captureOldWrite(abs) {
  try {
    const pre = await fsp.readFile(abs, "utf8");
    if (!pre) return null;
    return pre.length <= WRITE_OLD_CAPTURE_CHARS ? pre : null;
  } catch {
    // No pre-existing file (or unreadable) -> the write is a create
    // (or degrades to preview + stats). Never fails the turn.
    return null;
  }
}

async function writePath(cwd, filePath, content) {
  const abs = resolve(cwd, filePath);
  // Pre-write old-capture BEFORE the bytes change (never after).
  const old = await captureOldWrite(abs);
  await fsp.mkdir(join(abs, ".."), { recursive: true });
  await fsp.writeFile(abs, content === undefined ? "" : String(content), "utf8");
  return { ...ok(`wrote ${filePath}`), _old: old };
}

function runBash(cwd, command, timeoutMs, signal) {
  return new Promise((resolvePromise) => {
    // No-rotation invariant: a kill must actually kill. An aborted
    // turn leaves no blind work behind — the child dies here, not at
    // its own timeout.
    if (signal && signal.aborted) {
      resolvePromise({ ok: false, error: "bash: aborted before start" });
      return;
    }
    const timeout = Math.max(1000, timeoutMs || DEFAULT_BASH_TIMEOUT_MS);
    const onAbort = () => {
      try {
        child.kill("SIGKILL");
      } catch {}
      // The callback above may never fire after a kill on some
      // platforms — settle explicitly so /abort never waits.
      settle({ ok: false, error: "bash: aborted (killed on turn stop)" });
    };
    const settle = (value) => {
      if (signal) {
        try {
          signal.removeEventListener("abort", onAbort);
        } catch {}
      }
      resolvePromise(value);
    };
    const child = exec(
      command,
      {
        cwd,
        timeout,
        maxBuffer: 4 * 1024 * 1024,
        windowsHide: true,
        // Best-offer shell (2026-09-15): Git Bash on Windows when
        // detected (verified live: pipes + exit codes propagate;
        // abort still kills the direct child — grandchildren may
        // orphan exactly as under cmd, no worse). Undefined keeps
        // the platform default.
        ...(UNIX_SHELL ? { shell: UNIX_SHELL } : {}),
      },
      (error, stdout, stderr) => {
        const out = truncateTail((stdout || "") + (stderr ? `\n[stderr]\n${stderr}` : ""));
        if (error) {
          if (error.killed && (error.signal === "SIGTERM" || error.signal === "SIGKILL")) {
            settle({
              ok: false,
              error: `bash: timed out after ${timeout}ms (partial output kept)`,
              partial: out.text,
            });
          } else {
            settle({ ok: false, error: `bash: exit ${error.code}: ${out.text.slice(-2000)}` });
          }
        } else {
          settle(ok(out.text));
        }
      }
    );
    if (signal) {
      signal.addEventListener("abort", onAbort, { once: true });
    }
    void child;
  });
}

async function globSearch(cwd, pattern, root) {
  // Pure-JS glob over **, *, ?. Returns paths sorted by mtime desc
  // (opencode GlobTool parity: modification-time order).
  const base = root ? resolve(cwd, root) : cwd;
  const hasDoubleStar = pattern.includes("**");
  const results = [];
  async function walk(dir, rel) {
    let entries;
    try {
      entries = await fsp.readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      if (e.name === "node_modules" || e.name === ".git") continue;
      const relPath = rel ? `${rel}/${e.name}` : e.name;
      if (e.isDirectory()) {
        if (hasDoubleStar) await walk(join(dir, e.name), relPath);
        else if (!pattern.includes("/")) await walk(join(dir, e.name), relPath);
      } else {
        const name = hasDoubleStar ? relPath : e.name;
        if (patternMatches(pattern, name, true) || patternMatches(pattern, relPath, true)) {
          results.push({ relPath, abs: join(dir, e.name) });
        }
      }
    }
  }
  await walk(base, "");
  const withTime = [];
  for (const r of results.slice(0, 500)) {
    try {
      withTime.push({ ...r, mtime: statSync(r.abs).mtimeMs });
    } catch {
      withTime.push({ ...r, mtime: 0 });
    }
  }
  withTime.sort((a, b) => b.mtime - a.mtime);
  return ok(withTime.map((r) => r.relPath).join("\n"));
}

async function grepSearch(cwd, pattern, path, include) {
  let re;
  try {
    re = new RegExp(pattern);
  } catch (e) {
    return fail(`grep: invalid regex: ${e.message}`);
  }
  const base = path ? resolve(cwd, path) : cwd;
  const matches = [];
  async function walk(dir) {
    if (matches.length >= 100) return; // ripgrep-100 cap parity
    let entries;
    try {
      entries = await fsp.readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      if (matches.length >= 100) return;
      if (e.name === "node_modules" || e.name === ".git") continue;
      const abs = join(dir, e.name);
      if (e.isDirectory()) {
        await walk(abs);
      } else {
        if (include && !patternMatches(include, e.name, true)) continue;
        let st;
        try {
          st = statSync(abs);
        } catch {
          continue;
        }
        if (st.size > 1024 * 1024) continue;
        let raw;
        try {
          raw = await fsp.readFile(abs, "utf8");
        } catch {
          continue;
        }
        if (raw.includes("\0")) continue;
        const lines = raw.split("\n");
        for (let i = 0; i < lines.length; i++) {
          if (re.test(lines[i])) {
            matches.push(`${relative(cwd, abs)}:${i + 1}:${lines[i].slice(0, 300)}`);
            if (matches.length >= 100) return;
          }
        }
      }
    }
  }
  await walk(base);
  return ok(matches.join("\n"));
}

const TODO_STATUSES = new Set(["pending", "in_progress", "completed", "cancelled"]);
const TODO_PRIORITIES = new Set(["high", "medium", "low"]);

function todoWrite(session, todos) {
  if (!Array.isArray(todos)) return fail("todo: todos must be a list");
  for (const t of todos) {
    if (!t || typeof t.content !== "string" || !t.content) {
      return fail("todo: every item needs a non-empty content string");
    }
    if (!TODO_STATUSES.has(t.status)) {
      return fail(`todo: bad status ${JSON.stringify(t.status)} (pending|in_progress|completed|cancelled)`);
    }
    if (!TODO_PRIORITIES.has(t.priority)) {
      return fail(`todo: bad priority ${JSON.stringify(t.priority)} (high|medium|low)`);
    }
  }
  session.todos = todos.map((t) => ({ content: t.content, status: t.status, priority: t.priority }));
  return ok(JSON.stringify(session.todos, null, 2));
}

// Read-only git inspection (argv-exec, never a shell string).
const GIT_VERBS = new Set(["log", "show", "status", "diff", "branch", "ls-files", "rev-parse"]);
// Args starting with "-" are denied except this allowlist (structural,
// not a parser). Dangerous git flags are denied explicitly below.
const GIT_FLAG_ALLOW = new Set(["--stat", "--oneline", "-n", "--name-only", "--porcelain"]);
const GIT_FLAG_DENY_PREFIX = ["--upload-pack", "--exec", "-c", "--config"];

function runGit(cwd, verb, args, signal) {
  return new Promise((resolvePromise) => {
    if (!GIT_VERBS.has(verb)) {
      resolvePromise({ ok: false, error: `rejected: unknown git verb ${JSON.stringify(verb)}` });
      return;
    }
    const rawArgs = Array.isArray(args) ? args : [];
    for (const a of rawArgs) {
      if (typeof a !== "string") {
        resolvePromise({ ok: false, error: "rejected: git args must be strings" });
        return;
      }
      if (GIT_FLAG_DENY_PREFIX.some((d) => a === d || a.startsWith(d + "="))) {
        resolvePromise({ ok: false, error: `rejected: git flag denied: ${a}` });
        return;
      }
      // Bare "-n"/"--flag value" splits ride as separate argv entries;
      // "-n20"/"--flag=value" fused forms carry their payload inline.
      const fused = a.startsWith("-") && !GIT_FLAG_ALLOW.has(a) && !/^(-n\d+|--[A-Za-z-]+=.+)$/.test(a);
      if (fused) {
        resolvePromise({ ok: false, error: `rejected: git flag denied: ${a}` });
        return;
      }
    }
    // Default paging (read->2000 doctrine): `log` pages -n 20
    // --oneline unless args say otherwise; explicit wins.
    let finalArgs = [...rawArgs];
    if (verb === "log") {
      const hasN = finalArgs.some((a) => /^-n(\d+)?$/.test(a) || a === "--max-count");
      if (!hasN) finalArgs = ["-n", "20", "--oneline", ...finalArgs];
    }
    if (signal && signal.aborted) {
      resolvePromise({ ok: false, error: "git: aborted before start" });
      return;
    }
    const settle = (value) => resolvePromise(value);
    let child;
    try {
      const spawnOpts = { cwd, windowsHide: true };
      if (signal && typeof AbortSignal !== "undefined" && signal instanceof AbortSignal) {
        spawnOpts.signal = signal;
      }
      child = spawn("git", [verb, ...finalArgs], spawnOpts);
    } catch (e) {
      resolvePromise({ ok: false, error: `git: failed to start (${e && e.message ? e.message : e}) — is git installed and on PATH?` });
      return;
    }
    let out = "";
    let err = "";
    const onData = (buf, acc) => {
      const s = String(buf);
      return acc + s;
    };
    if (child.stdout) child.stdout.on("data", (d) => { out = onData(d, out); });
    if (child.stderr) child.stderr.on("data", (d) => { err = onData(d, err); });
    child.on("error", (e) => {
      const msg = e && e.code === "ENOENT"
        ? "git: git executable not found — install git and ensure it is on PATH"
        : `git: failed to start (${e && e.message ? e.message : e})`;
      settle({ ok: false, error: msg });
    });
    child.on("close", (code) => {
      const text = (out + (err ? `\n[stderr]\n${err}` : "")).trim();
      if (code === 0) {
        settle(ok(text));
      } else if (/not a git repository/i.test(text)) {
        settle({ ok: false, error: `git: not a git repository (${cwd})` });
      } else {
        settle({ ok: false, error: `git: exit ${code}: ${text.slice(-2000)}` });
      }
    });
  });
}

// OpenAI function schemas. Descriptions stay reference-tight: the
// orchestrator prompt already teaches the contract (tool-context
// budget standing rule). The todo discipline rides here because no
// prompt teaches it — the documented budget exception.
export const EXEC_TOOL_DEFS = [
  {
    name: "read",
    description:
      "Read a file (offset/limit, 1-based; omitted limit pages 2000 lines — use offset to continue) or list a directory.",
    parameters: {
      type: "object",
      properties: {
        filePath: { type: "string", description: "Path relative to the turn cwd" },
        offset: { type: "number" },
        limit: { type: "number", description: "Max lines (default 2000)" },
      },
      required: ["filePath"],
    },
  },
  {
    name: "edit",
    description: "Exact-string file edit (oldString must match verbatim).",
    parameters: {
      type: "object",
      properties: {
        filePath: { type: "string" },
        oldString: { type: "string" },
        newString: { type: "string" },
        replaceAll: { type: "boolean" },
      },
      required: ["filePath", "oldString", "newString"],
    },
  },
  {
    name: "write",
    description: "Create or overwrite a file (gated by the edit permission).",
    parameters: {
      type: "object",
      properties: {
        filePath: { type: "string" },
        content: { type: "string" },
      },
      required: ["filePath", "content"],
    },
  },
  {
    name: "bash",
    description: bashDescription(),
    parameters: {
      type: "object",
      properties: {
        command: { type: "string" },
        timeout: { type: "number", description: "Timeout in ms" },
      },
      required: ["command"],
    },
  },
  {
    name: "glob",
    description: "Find files by pattern (sorted by modification time).",
    parameters: {
      type: "object",
      properties: {
        pattern: { type: "string" },
        path: { type: "string" },
      },
      required: ["pattern"],
    },
  },
  {
    name: "grep",
    description: "Regex search across files (max 100 matches).",
    parameters: {
      type: "object",
      properties: {
        pattern: { type: "string" },
        path: { type: "string" },
        include: { type: "string" },
      },
      required: ["pattern"],
    },
  },
  {
    name: "todo",
    description:
      "Session task list (full-list replace). Mark in_progress exactly one at a time; mark completed only after verification.",
    parameters: {
      type: "object",
      properties: {
        todos: {
          type: "array",
          items: {
            type: "object",
            properties: {
              content: { type: "string" },
              status: { type: "string" },
              priority: { type: "string" },
            },
            required: ["content", "status", "priority"],
          },
        },
      },
      required: ["todos"],
    },
  },
  {
    name: "git",
    description:
      "Read-only git inspection in the turn cwd (log/show/status/diff/branch/ls-files/rev-parse). Defaults: log pages -n 20 --oneline.",
    parameters: {
      type: "object",
      properties: {
        verb: {
          type: "string",
          enum: ["log", "show", "status", "diff", "branch", "ls-files", "rev-parse"],
        },
        args: { type: "array", items: { type: "string" } },
      },
      required: ["verb"],
    },
  },
];

/**
 * TOOL_CARDS step 1 (2026-09-16): structured per-tool state
 * extras — additive fields on the tool.* emit `state` payloads
 * so the Python side's `detail` builder projects them WITHOUT
 * parsing the free-text output (the footer stays for the model;
 * the chat cards render from these keys — ruling F3's
 * "never parse the footer" bar). Additive only: provider-visible
 * text outputs unchanged; unknown keys ignored downstream (the
 * degrade contract holds on both harnesses).
 */
export function toolStateExtra(name, args = {}, settled) {
  const a = args || {};
  const r = settled || {};
  const extras = {};
  switch (name) {
    case "read": {
      if (r.ok && typeof r.output === "string") {
        const m = r.output.match(/\(Showing lines (\d+)-(\d+) of (\d+)\. Use offset=(\d+)/);
        if (m) {
          extras.window = {
            shownFrom: Number(m[1]),
            shownTo: Number(m[2]),
            total: Number(m[3]),
            nextOffset: Number(m[4]),
          };
        }
      }
      break;
    }
    case "write": {
      const content = typeof a.content === "string" ? a.content : "";
      // 2b: the pre-write capture rides `settled._old` (see writePath).
      // Overwrite = old present (mode flips, linesRemoved + the diff
      // payload name it); create = nothing else is carried.
      const old = r && typeof r._old === "string" ? r._old : null;
      if (old) {
        extras.mode = "overwrite";
        extras.linesRemoved = old.split("\n").length;
        extras.old_capture = old;
      } else {
        extras.mode = "create";
      }
      extras.linesAdded = content ? content.split("\n").length : 0;
      if (content) extras.preview = content.slice(0, 200);
      break;
    }
case "edit": {
      const oldText = typeof a.oldString === "string" ? a.oldString : "";
      const newText = typeof a.newString === "string" ? a.newString : "";
      extras.linesAdded = newText.split("\n").length;
      extras.linesRemoved = oldText.split("\n").length;
      break;
    }
    case "bash": {
      extras.command = a.command || "";
      const err = settled && settled.error ? String(settled.error) : "";
      const m = /\bexit (-?\d+)/.exec(err);
      extras.exit = m ? Number(m[1]) : settled && settled.ok ? 0 : null;
      if (settled && typeof settled.output === "string" && settled.output) {
        extras.output_excerpt = settled.output.slice(-2000);
        extras.truncated = settled.output.length > 2000;
      }
      break;
    }
    case "grep": {
      if (r.ok && typeof r.output === "string") {
        extras.matchCount = r.output.split("\n").filter(Boolean).length;
      }
      break;
    }
    case "glob": {
      if (r.ok && typeof r.output === "string") {
        extras.count = r.output.split("\n").filter(Boolean).length;
      }
      break;
    }
    case "git":
    case "todo":
      break;
    default:
      break;
  }
  return extras;
}

/**
 * Execute one execution tool (permission already decided by the
 * caller — pass the gate verdict for ask flows).
 * @returns { { ok, output?|error?, partial? } }
 *
 * Output hygiene (token-bloat guard, 2026-09-14): every exec-tool
 * result is capped at MAX_OUTPUT_CHARS before it enters session
 * history — a limit-less `read` of a 600KB file once dumped 607K
 * chars into history and re-billed it on all ~20 remaining
 * iterations (~2M of a 5.4M-token turn from ONE read). `bash`
 * truncates at the source (tail-cut), so it is excluded here (a
 * second pass would stack truncation markers). `read` pages by
 * default (DEFAULT_READ_LIMIT, opencode parity) and teaches
 * `offset` continuation, so models page instead of redirecting
 * test output to files (review-hardening step 2, 2026-09-15).
 */
export async function executeTool(name, args, execCtx) {
  const { cwd, session, signal } = execCtx;
  const a = args || {};
  let result;
  switch (name) {
    case "read": {
      const abs = resolve(cwd, a.filePath || "");
      result = await readPath(cwd, a.filePath || "", a.offset, a.limit).then((r) => ({ ...r, _abs: abs }));
      break;
    }
    case "edit": {
      const abs = resolve(cwd, a.filePath || "");
      // write-equivalent: gated by the edit permission key (opencode parity).
      result = await editPath(cwd, a.filePath || "", a.oldString, a.newString, a.replaceAll).then((r) => ({ ...r, _abs: abs }));
      break;
    }
    case "write": {
      const abs = resolve(cwd, a.filePath || "");
      result = await writePath(cwd, a.filePath || "", a.content).then((r) => ({ ...r, _abs: abs }));
      break;
    }
    case "bash":
      return runBash(cwd, a.command || "", a.timeout, signal);
    case "glob":
      result = await globSearch(cwd, a.pattern || "", a.path);
      break;
    case "grep":
      result = await grepSearch(cwd, a.pattern || "", a.path, a.include);
      break;
    case "todo":
      result = await todoWrite(session, a.todos);
      break;
    case "git":
      result = await runGit(cwd, a.verb, a.args, signal);
      break;
    default:
      return fail(`unknown execution tool: ${name}`);
  }
  return capResult(result);
}

/**
 * Cap one tool result at MAX_OUTPUT_CHARS (output and error text).
 * Small results pass through byte-identical; oversized ones keep a
 * head + an honest `... [truncated N chars]` marker naming the cut.
 */
export function capResult(result) {
  if (!result || typeof result !== "object") return result;
  const out = result.output;
  if (typeof out === "string" && out.length > MAX_OUTPUT_CHARS) {
    return { ...result, output: truncateOutput(out).text };
  }
  const err = result.error;
  if (typeof err === "string" && err.length > MAX_OUTPUT_CHARS) {
    return { ...result, error: truncateOutput(err).text };
  }
  return result;
}

/** Match target for a tool call (opencode parity per tool). */
export function matchTarget(name, args) {
  const a = args || {};
  switch (name) {
    case "read":
    case "edit":
    case "write":
      return { target: String(a.filePath || ""), isPath: true };
    case "glob":
      return { target: String(a.pattern || ""), isPath: true };
    case "grep":
      return { target: String(a.pattern || ""), isPath: false };
    case "bash":
      return { target: String(a.command || ""), isPath: false };
    case "git":
      return { target: String(a.verb || ""), isPath: false };
    case "todo":
      return { target: "*", isPath: false };
    default:
      return { target: "*", isPath: false };
  }
}

/** Permission key actually evaluated (write shares edit's key). */
export function permissionKey(name) {
  if (name === "write") return "edit";
  return name;
}
