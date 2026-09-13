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
 * links, inline code, fenced code blocks (language header + copy
 * button, monospace, code-token background) + a copy button.
 *
 * Chat polish (2026-09-13): code blocks carry a header bar with the
 * fenced language label + copy affordance; tables get a header tint
 * + rounded wrapper; headings/quotes/links follow the thread's
 * visual language.
 *
 * Pinned deps: react-markdown 10.1.0, remark-gfm 4.0.1.
 */
import { useState, type AnchorHTMLAttributes, type BlockquoteHTMLAttributes, type HTMLAttributes, type LiHTMLAttributes, type TableHTMLAttributes, type TdHTMLAttributes, type ThHTMLAttributes } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy, FileCode2 } from "lucide-react";
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
  return <div className={cn("markdown text-sm leading-relaxed text-foreground", className)}>{tree}</div>;
}

// ---------------------------------------------------------------------------
// Component overrides
// ---------------------------------------------------------------------------
// Each override adds the project's Tailwind classes so the markdown
// blends with the thread's visual language. Code blocks get a header
// (language label + copy button); links open in a new tab;
// lists/tables get GFM spacing.

function mdComponents() {
  return {
    p: (props: HTMLAttributes<HTMLParagraphElement>) => (
      <p {...props} className="my-2 leading-relaxed first:mt-0 last:mb-0" />
    ),
    h1: (props: HTMLAttributes<HTMLHeadingElement>) => (
      <h1 {...props} className="mb-2 mt-4 text-lg font-semibold tracking-tight first:mt-0" />
    ),
    h2: (props: HTMLAttributes<HTMLHeadingElement>) => (
      <h2 {...props} className="mb-2 mt-4 text-base font-semibold tracking-tight first:mt-0" />
    ),
    h3: (props: HTMLAttributes<HTMLHeadingElement>) => (
      <h3 {...props} className="mb-1 mt-3 text-sm font-semibold tracking-tight first:mt-0" />
    ),
    h4: (props: HTMLAttributes<HTMLHeadingElement>) => (
      <h4 {...props} className="mb-1 mt-3 text-[13px] font-semibold tracking-tight first:mt-0" />
    ),
    ul: (props: HTMLAttributes<HTMLUListElement>) => (
      <ul {...props} className="my-2 ml-5 list-disc space-y-1 marker:text-primary/60" />
    ),
    ol: (props: HTMLAttributes<HTMLOListElement>) => (
      <ol {...props} className="my-2 ml-5 list-decimal space-y-1 marker:font-medium marker:text-primary/70" />
    ),
    li: (props: LiHTMLAttributes<HTMLLIElement>) => (
      <li {...props} className="leading-relaxed" />
    ),
    a: (props: AnchorHTMLAttributes<HTMLAnchorElement>) => (
      <a
        {...props}
        target="_blank"
        rel="noreferrer noopener"
        className="font-medium text-link underline decoration-link/40 underline-offset-2 transition-colors hover:decoration-link"
      />
    ),
    blockquote: (props: BlockquoteHTMLAttributes<HTMLQuoteElement>) => (
      <blockquote
        {...props}
        className="my-2.5 rounded-r-lg border-l-[3px] border-primary/50 bg-primary/[0.05] py-1 pl-3 pr-2 italic text-muted-foreground"
      />
    ),
    table: (props: TableHTMLAttributes<HTMLTableElement>) => (
      <div className="my-3 overflow-x-auto rounded-lg border border-border/70">
        <table {...props} className="w-full border-collapse text-xs" />
      </div>
    ),
    thead: (props: HTMLAttributes<HTMLTableSectionElement>) => (
      <thead {...props} className="bg-muted/60" />
    ),
    th: (props: ThHTMLAttributes<HTMLTableCellElement>) => (
      <th {...props} className="border-b border-border px-2.5 py-1.5 text-left font-semibold" />
    ),
    td: (props: TdHTMLAttributes<HTMLTableCellElement>) => (
      <td {...props} className="border-b border-border/50 px-2.5 py-1.5 align-top last:border-b-0" />
    ),
    pre: (props: HTMLAttributes<HTMLPreElement>) => (
      <CodeBlock>{props.children}</CodeBlock>
    ),
    code: (props: HTMLAttributes<HTMLElement>) => (
      <code
        {...props}
        className="rounded-md border border-border/50 bg-muted/60 px-1.5 py-0.5 font-mono text-[0.82em] font-medium text-foreground before:content-none after:content-none"
      />
    ),
  } satisfies Parameters<typeof ReactMarkdown>[0]["components"];
}

function languageOf(children: React.ReactNode): string | null {
  const child = Array.isArray(children) ? children[0] : children;
  if (child && typeof child === "object" && "props" in (child as object)) {
    const className = (child as { props?: { className?: string } }).props?.className ?? "";
    const match = /language-([\w+-]+)/.exec(className);
    if (match) return match[1];
  }
  return null;
}

function CodeBlock({ children }: { children: React.ReactNode }) {
  const language = languageOf(children);
  return (
    <div className="group/code my-3 overflow-hidden rounded-xl border border-border/70 bg-code shadow-sm">
      <div className="flex items-center gap-1.5 border-b border-white/10 bg-white/[0.03] px-3 py-1.5">
        <FileCode2 size={12} className="text-code-foreground/60" />
        <span
          data-testid="markdown-code-lang"
          className="font-mono text-[10px] font-semibold uppercase tracking-wider text-code-foreground/70"
        >
          {language ?? "code"}
        </span>
        <span className="flex-1" />
        <CopyCodeButton />
      </div>
      <pre
        data-testid="markdown-pre"
        className="overflow-x-auto p-3 font-mono text-xs leading-relaxed text-code-foreground"
      >
        {children}
      </pre>
    </div>
  );
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
      className="flex items-center gap-1 rounded-md px-1.5 py-1 font-mono text-[10px] font-medium text-code-foreground/70 transition-all hover:bg-white/10 hover:text-code-foreground"
    >
      {copied ? <Check size={12} /> : <Copy size={12} />}
      {copied ? "copied" : "copy"}
    </button>
  );
}
