/**
 * Shared enriched-detail renderer (TOOL_CARDS plan step 2, 2026-09-16).
 *
 * One presentational component renders the additive `detail` blob for
 * BOTH surfaces — the chat thread (`Thread.tsx` ToolRow expander) and
 * the delegation detail view (`sections.tsx` ToolTimelineRow + Tools
 * tab) — so the two renderers can never diverge (the backend's
 * `build_tool_detail` is the single source of shape; this is the single
 * source of render). It shows window/counts/stats/command, NEVER match
 * content (grep doctrine F6). The caller decides WHEN to mount it (chat
 * = behind a collapsed expander; detail = always, full output already
 * shown for bash).
 */
import type { ToolRowDetail } from "@/types";

function isGlob(tool: string): boolean {
  return (tool || "").toLowerCase() === "glob";
}

export function ToolDetailMeta({
  tool,
  detail,
  className,
}: {
  tool: string;
  detail: ToolRowDetail | null | undefined;
  className?: string;
}) {
  if (!detail) return null;
  const d = detail;
  const pad = "mt-1.5 space-y-1.5 text-[11px] leading-relaxed text-muted-foreground";
  return (
    <div className={className ?? pad}>
      {/* Read: explicit window row (F3) — never parse the footer. */}
      {d.window ? (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono">
          <span className="font-medium text-foreground/80">Window</span>
          <span>
            lines {d.window.shownFrom ?? "?"}–{d.window.shownTo ?? "?"}
            {d.window.total != null ? ` of ${d.window.total}` : ""}
          </span>
          {d.window.nextOffset != null ? (
            <span className="text-muted-foreground/70">· next offset {d.window.nextOffset}</span>
          ) : null}
          {d.window.from_footer ? (
            <span className="text-muted-foreground/70">· derived from legacy footer</span>
          ) : null}
        </div>
      ) : null}

      {/* Write: mode + stats (F4). */}
      {d.mode ? (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono">
          <span className="font-medium text-foreground/80">
            {d.mode === "overwrite" ? "Overwrite" : "Create"}
          </span>
          {d.linesAdded != null ? (
            <span className="text-emerald-600 dark:text-emerald-400">+{d.linesAdded}</span>
          ) : null}
          {d.linesRemoved != null ? (
            <span className="text-rose-600 dark:text-rose-400">−{d.linesRemoved}</span>
          ) : null}
        </div>
      ) : null}
      {d.preview ? (
        <pre className="max-h-40 overflow-auto rounded bg-background/60 p-1.5 font-mono text-[11px] whitespace-pre-wrap break-words">
          {d.preview}
          {d.preview.length >= 200 ? "…" : ""}
        </pre>
      ) : null}

      {/* Bash: command (always) + exit/truncated (F2). The full output
          is rendered by the BashTool card in the detail surface; the
          chat surface shows the 2K excerpt inline. */}
      {d.command != null ? (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono">
          <span className="font-medium text-foreground/80">$ {d.command}</span>
          {d.exit != null ? (
            <span
              className={
                d.exit === 0
                  ? "text-emerald-600 dark:text-emerald-400"
                  : "text-rose-600 dark:text-rose-400"
              }
            >
              exit {d.exit}
            </span>
          ) : null}
          {d.truncated ? <span className="text-muted-foreground/70">· truncated</span> : null}
        </div>
      ) : null}

      {/* Grep: pattern + path/include + matchCount (never content). */}
      {d.pattern != null && !isGlob(tool) ? (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono">
          <span className="font-medium text-foreground/80">grep “{d.pattern}”</span>
          {d.path ? <span>in {d.path}</span> : null}
          {d.include ? <span>(incl {d.include})</span> : null}
          {d.matchCount != null ? (
            <span className="text-muted-foreground/70">
              · {d.matchCount} match{d.matchCount === 1 ? "" : "es"}
            </span>
          ) : null}
        </div>
      ) : null}

      {/* Glob: pattern + count. */}
      {d.pattern != null && isGlob(tool) ? (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono">
          <span className="font-medium text-foreground/80">glob {d.pattern}</span>
          {d.count != null ? (
            <span className="text-muted-foreground/70">
              · {d.count} path{d.count === 1 ? "" : "s"}
            </span>
          ) : null}
        </div>
      ) : null}

      {/* Git: verb + args. */}
      {d.verb != null ? (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono">
          <span className="font-medium text-foreground/80">git {d.verb}</span>
          {Array.isArray(d.args) ? <span>{d.args.join(" ")}</span> : d.args ? <span>{String(d.args)}</span> : null}
        </div>
      ) : null}

      {/* Todo: titles list. */}
      {d.titles && d.titles.length ? (
        <ul className="list-inside list-disc space-y-0.5 font-mono">
          {d.titles.map((t, i) => (
            <li key={i}>{t}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
