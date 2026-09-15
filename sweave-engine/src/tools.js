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
  if (count === 0) return fail("edit: oldString not found in file");
  if (count > 1 && !replaceAll) {
    return fail(`edit: oldString matches ${count} times; use replaceAll or add context`);
  }
  const next = replaceAll ? raw.split(oldString).join(newString) : raw.replace(oldString, newString);
  await fsp.writeFile(abs, next, "utf8");
  return ok(`edited ${filePath} (${count} replacement${count === 1 ? "" : "s"})`);
}

async function writePath(cwd, filePath, content) {
  const abs = resolve(cwd, filePath);
  await fsp.mkdir(join(abs, ".."), { recursive: true });
  await fsp.writeFile(abs, content === undefined ? "" : String(content), "utf8");
  return ok(`wrote ${filePath}`);
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
      { cwd, timeout, maxBuffer: 4 * 1024 * 1024, windowsHide: true },
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
    description:
      "Run a shell command in the turn cwd. Oversized output keeps the tail (most recent); redirect to a file only for logs you will grep, and prefer the OS temp dir.",
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
