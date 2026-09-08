/**
 * MarkdownTextPrimitive (assistant-ui shadcn-registry copy).
 *
 * Streaming-safe markdown renderer. Uses react-markdown + remark-gfm
 * with try/catch fallback (M1.8 invariant: no mid-stream crash).
 *
 * Provides: MarkdownText component for use as Text slot in MessagePrimitive.Parts.
 */

"use client";

import { type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Copy, Check } from "lucide-react";
import { useState, type HTMLAttributes, type AnchorHTMLAttributes, type BlockquoteHTMLAttributes, type LiHTMLAttributes, type TableHTMLAttributes, type TdHTMLAttributes, type ThHTMLAttributes } from "react";
import { cn } from "@/utils/cn";

interface MarkdownTextProps {
  text: string;
  status?: { type: "running" | "complete" | "error" };
  className?: string;
}

export function MarkdownText({ text, status, className }: MarkdownTextProps) {
  const isRunning = status?.type === "running";

  // Streaming cursor for running state
  const displayText = isRunning ? text + "\u200b" : text;

  // Streaming safety: try/catch so partial markdown doesn't crash
  let tree: ReactNode;
  try {
    tree = (
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={mdComponents()}
      >
        {displayText}
      </ReactMarkdown>
    );
  } catch {
    tree = (
      <pre className="whitespace-pre-wrap text-sm text-foreground">
        {displayText}
      </pre>
    );
  }

  return (
    <div
      className={cn("markdown prose prose-sm dark:prose-invert max-w-none", className)}
      data-status={status?.type}
    >
      {tree}
      {isRunning && <StreamingCursor />}
    </div>
  );
}

// Streaming cursor (CSS-only animation, respects prefers-reduced-motion)
function StreamingCursor() {
  return (
    <span
      className="inline-block w-1 h-4 bg-primary animate-pulse ml-0.5 align-bottom"
      style={{ animation: "pulse 1s ease-in-out infinite" }}
      aria-hidden="true"
    />
  );
}

// ---------------------------------------------------------------------------
// Component overrides (same as Markdown.tsx but extracted)
// ---------------------------------------------------------------------------

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

export const MarkdownTextPrimitive = {
  MarkdownText,
};