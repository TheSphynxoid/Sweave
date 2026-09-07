/**
 * Markdown renderer (R4.2 step 2a).
 *
 * Agent-elements-derived, shadcn-style — we own this code. Streaming-
 * safe: wraps `react-markdown` in a try/catch so a partial / unclosed
 * markdown fence (typical mid-stream) renders the raw text rather
 * than throwing and tearing the thread tree down (M1.8 invariant: no
 * mid-stream re-render crash).
 *
 * Renders: headings, paragraphs, lists, GFM tables, blockquote,
 * links, inline code, fenced code blocks (plain, monospace, dark
 * background) + a copy button. The fenced code block's copy button
 * finds the nearest ``<code>`` inside the wrapper.
 *
 * **Step 2b will add syntax highlighting** via a deferred-highlight
 * pattern (async Shiki inside a ``components.code`` override + per-
 * block state to re-render the highlighted HTML once the promise
 * resolves). The async + sync-React-render mismatch means a
 * plain ``@shikijs/rehype`` integration throws ``runSync finished
 * async`` -- the deferred pattern sidesteps that and keeps the
 * first render deterministic. For now (2a), code blocks are
 * unhighlighted.
 *
 * Pinned deps: react-markdown 10.1.0, remark-gfm 4.0.1.
 */
import { useState, type AnchorHTMLAttributes, type BlockquoteHTMLAttributes, type HTMLAttributes, type LiHTMLAttributes, type TableHTMLAttributes, type TdHTMLAttributes, type ThHTMLAttributes } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy } from "lucide-react";
import { cn } from "@/utils/cn";

export interface MarkdownProps {
  source: string;
  className?: string;
}

export function Markdown({ source, className }: MarkdownProps) {
  // Streaming safety: a parse failure must NOT tear the tree down.
  // Fall back to a plain <pre> so the bubble stays visible.
  let tree: React.ReactNode;
  try {
    tree = (
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={mdComponents()}
      >
        {source}
      </ReactMarkdown>
    );
  } catch {
    tree = (
      <pre className="whitespace-pre-wrap text-sm text-foreground">
        {source}
      </pre>
    );
  }
  return <div className={cn("markdown", className)}>{tree}</div>;
}

// ---------------------------------------------------------------------------
// Component overrides
// ---------------------------------------------------------------------------
// Each override adds the project's Tailwind classes so the markdown
// blends with the thread's visual language. Code blocks get a copy
// button; links open in a new tab; lists/tables get GFM spacing.

function mdComponents() {
  return {
    p: (props: HTMLAttributes<HTMLParagraphElement>) => (
      <p {...props} className="my-2 leading-relaxed" />
    ),
    h1: (props: HTMLAttributes<HTMLHeadingElement>) => (
      <h1 {...props} className="text-lg font-semibold mt-4 mb-2" />
    ),
    h2: (props: HTMLAttributes<HTMLHeadingElement>) => (
      <h2 {...props} className="text-base font-semibold mt-3 mb-2" />
    ),
    h3: (props: HTMLAttributes<HTMLHeadingElement>) => (
      <h3 {...props} className="text-sm font-semibold mt-3 mb-1" />
    ),
    ul: (props: HTMLAttributes<HTMLUListElement>) => (
      <ul {...props} className="my-2 ml-5 list-disc space-y-1" />
    ),
    ol: (props: HTMLAttributes<HTMLOListElement>) => (
      <ol {...props} className="my-2 ml-5 list-decimal space-y-1" />
    ),
    li: (props: LiHTMLAttributes<HTMLLIElement>) => (
      <li {...props} className="leading-relaxed" />
    ),
    a: (props: AnchorHTMLAttributes<HTMLAnchorElement>) => (
      <a
        {...props}
        target="_blank"
        rel="noreferrer noopener"
        className="text-primary underline underline-offset-2"
      />
    ),
    blockquote: (props: BlockquoteHTMLAttributes<HTMLQuoteElement>) => (
      <blockquote
        {...props}
        className="my-2 border-l-2 border-border pl-3 text-muted-foreground italic"
      />
    ),
    table: (props: TableHTMLAttributes<HTMLTableElement>) => (
      <table {...props} className="my-2 w-full text-xs border-collapse" />
    ),
    th: (props: ThHTMLAttributes<HTMLTableCellElement>) => (
      <th {...props} className="border border-border px-2 py-1 text-left font-medium" />
    ),
    td: (props: TdHTMLAttributes<HTMLTableCellElement>) => (
      <td {...props} className="border border-border px-2 py-1 align-top" />
    ),
    pre: (props: HTMLAttributes<HTMLPreElement>) => (
      <div className="my-3 relative group/code">
        <CopyCodeButton />
        <pre
          {...props}
          data-testid="markdown-pre"
          className="overflow-x-auto rounded-md bg-zinc-950 text-zinc-100 text-xs p-3 leading-relaxed"
        />
      </div>
    ),
    code: (props: HTMLAttributes<HTMLElement>) => (
      <code
        {...props}
        className="font-mono text-[0.85em] bg-muted/60 px-1 py-0.5 rounded before:content-none after:content-none"
      />
    ),
  } satisfies Parameters<typeof ReactMarkdown>[0]["components"];
}

function CopyCodeButton() {
  const [copied, setCopied] = useState(false);
  const onClick = (e: React.MouseEvent<HTMLButtonElement>) => {
    const wrapper = (e.currentTarget as HTMLElement).closest(".group\\/code");
    const code = wrapper?.querySelector("code");
    if (!code) return;
    void navigator.clipboard
      .writeText(code.textContent ?? "")
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1200);
      })
      .catch(() => {
        // clipboard may be unavailable (insecure context); silent.
      });
  };
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={copied ? "Copied" : "Copy code"}
      data-testid="markdown-copy-code"
      className="absolute top-2 right-2 p-1 rounded bg-zinc-800 text-zinc-100 opacity-0 group-hover/code:opacity-100 focus:opacity-100 transition-opacity"
    >
      {copied ? <Check size={12} /> : <Copy size={12} />}
    </button>
  );
}