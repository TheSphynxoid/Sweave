# Reviewer permission asks — plan of record (2026-09-15)

Status: ruled, awaiting implementation (deferred: live session working,
fix needs a server restart — Python map render + sidecar gate).

## Problem (observed 2026-09-15, trace `00d05484c6b9`)
Worktree-isolated reviewer asked `external_directory` for every
in-project read (8 asks in 7 min: project root, a doc, `.worktrees`,
`.git/refs/heads/sweave/*`), and "always allow" never generalized.

## Root causes (both confirmed code + trace)
1. Project root is not an allow-root. Engine map carries only
   `external_directory` (`sweave/runtime/specialist_runtime.py:896`);
   `read` defaults allow but outside-cwd paths hit the gate
   (`sweave-engine/src/tools.js:89-101`). `_root_globs`
   (`sweave/runtime/mcp_config.py:201-230`) allows only `~/.sweave` +
   `{project}/.worktrees/*` — never the project dir itself (bare
   `.worktrees` doesn't even match its own `base\*` rule).
2. "Always allow" stores the exact path (`loop.js:330-333`) while
   `approvalMatches` demands exact `===` (`loop.js:277-281`), so a
   path-keyed `external_directory` always-allow can never cover the
   next file. Session persistence itself is fine (durable session).

## Ruling (user-locked 2026-09-15): A + narrowed B
- **A**: project subtree joins the silent allow-roots (an agent
  reading its own project's files never asks).
- **Narrowed B**: "always allow" on a path-keyed ask generalizes to
  the permission *within the project subtree* (not exact-path, not
  global `*`). Genuinely outside-project paths still ask every time.

## Sketch (for the execution round)
- `mcp_config.py::_root_globs`: append resolved `project_dir`
  (renders `base\*` + `base\**` allow like the other roots).
- `loop.js::resolveAsk`: on "always", store the enclosing scope —
  project-subtree wildcard when the target sits under the turn cwd's
  project, exact path otherwise; `approvalMatches` gains prefix-rule
  matching for such scoped patterns (exact-match behavior unchanged
  for everything else).
- Mirror for the opencode path if it shares the exact-path storage
  (permission bridge posts pinned replies; check before touching).
- Tests: scoped-render unit (project root silent, outside still ask),
  always-generalizes unit (second distinct in-project path silent,
  outside-project path still asks), live sidecar scene optional.
- Gate: touched suites + `run.py --check`; needs server restart
  (render is Python per-turn, gate is sidecar-resident).

## Non-goals
- No permission-model redesign; no blanket `"*": "allow"`.
- No reviewer-prompt changes (the `../../` worktree-escape habit is
  noted, not fixed here — with A it stops hurting).
