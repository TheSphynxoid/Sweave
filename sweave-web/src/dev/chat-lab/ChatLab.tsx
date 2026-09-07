/**
 * Dev-only visual test (R4.2 step 2a).
 *
 * The chat-lab is a **component gallery** that renders every step-2
 * surface in its final visual state with fixture data, so a
 * developer (or the user, for review) can hit
 * ``/dev/chat-lab`` in the dev server and exercise the new
 * rendering without a real running orchestrator.
 *
 * The lab is intentionally NOT wired to ``useSweaveChatRuntime``
 * (that's the integration path; the manual ``/chat`` route and the
 * ``runtime.test.ts`` vitest already cover it). The lab is a
 * surface-level visual smoke: markdown render, GFM tables,
 * Shiki-highlighted code block with copy button, etc.
 *
 * The route is gated by ``import.meta.env.DEV`` at registration
 * time so the lab is tree-shaken from the production bundle.
 */
import { ArrowUp, Code2, Eye, FileText, MessageSquare } from "lucide-react";
import { Markdown } from "../../components/thread/markdown/Markdown";
import { AssistantTextPart } from "../../components/thread/markdown/AssistantTextPart";

const MARKDOWN_SAMPLE = [
  "# R4.2 chat surface",
  "",
  "R4.2 step 2a: **markdown + Shiki**. The thread now renders assistant",
  "messages as real markdown (GFM tables, code blocks with syntax",
  "highlight, links open in a new tab).",
  "",
  "## Code block",
  "",
  "```ts",
  "import { useSweaveChatRuntime } from '@/lib/chat/useSweaveChatRuntime';",
  "",
  "export function ChatPage() {",
  "  const runtime = useSweaveChatRuntime(activeSession?.id ?? null);",
  "  // ... etc",
  "}",
  "```",
  "",
  "## GFM table",
  "",
  "| state           | meaning                     |",
  "| --------------- | --------------------------- |",
  "| `idle`          | no turn in flight           |",
  "| `queued`        | submit fired, server pending |",
  "| `running`       | chat.delta deltas streaming |",
  "",
  "## List + link",
  "",
  "- agent-elements-derived cards (shadcn-style, we own)",
  "- runtime view-projection of REST + WS",
  "- [assistant-ui docs](https://assistant-ui.com) for primitives",
  "",
  "> Streaming is safe: partial markdown falls back to plain text.",
].join("\n");

const PARTIAL_STREAM_SAMPLE = [
  "# streaming test",
  "",
  "Here is a code block that hasn't closed yet:",
  "",
  "```ts",
  "const x: number = 1;",
  "// still streaming",
].join("\n");

export function ChatLab() {
  return (
    <div
      data-testid="chat-lab"
      className="mx-auto w-full max-w-3xl px-4 py-6 space-y-8"
    >
      <header className="space-y-1">
        <h1 className="text-xl font-semibold tracking-tight">R4.2 chat lab</h1>
        <p className="text-sm text-muted-foreground">
          Dev-only visual smoke for step 2 surfaces. Renders every
          component in its final visual state with fixture data.
        </p>
      </header>

      {/* ----------------------------------------------------------------- */}
      {/* 1. Markdown (assistant message, full)                            */}
      {/* ----------------------------------------------------------------- */}
      <Section
        icon={<MessageSquare size={14} />}
        title="Assistant message — full markdown"
        note="GFM tables, Shiki code block, links, lists, blockquote."
      >
        <Bubble role="assistant">
          <AssistantTextPart
            type="text"
            text={MARKDOWN_SAMPLE}
            status={{ type: "complete" }}
          />
        </Bubble>
      </Section>

      {/* ----------------------------------------------------------------- */}
      {/* 2. Markdown (partial / mid-stream)                                 */}
      {/* ----------------------------------------------------------------- */}
      <Section
        icon={<Eye size={14} />}
        title="Assistant message — partial stream (streaming-safety)"
        note="Unclosed code block: must not crash; the raw text is the fallback."
      >
        <Bubble role="assistant">
          <AssistantTextPart
            type="text"
            text={PARTIAL_STREAM_SAMPLE}
            status={{ type: "running" }}
          />
        </Bubble>
      </Section>

      {/* ----------------------------------------------------------------- */}
      {/* 3. User message (plain text)                                      */}
      {/* ----------------------------------------------------------------- */}
      <Section
        icon={<MessageSquare size={14} />}
        title="User message — plain text"
        note="User side keeps the plain pre-wrap (no markdown, no shiki)."
      >
        <Bubble role="user">What does the chat-lab show?</Bubble>
      </Section>

      {/* ----------------------------------------------------------------- */}
      {/* 4. Bare Markdown component (no aui context)                       */}
      {/* ----------------------------------------------------------------- */}
      <Section
        icon={<FileText size={14} />}
        title="Markdown — standalone"
        note="Same component used by the assistant message-part wrapper."
      >
        <div className="rounded-md border border-border bg-card p-4 text-sm">
          <Markdown source={"Hello **world**.\n\n```ts\nconst x = 1;\n```"} />
        </div>
      </Section>

      {/* ----------------------------------------------------------------- */}
      {/* 5. Code block with copy button (hover)                            */}
      {/* ----------------------------------------------------------------- */}
      <Section
        icon={<Code2 size={14} />}
        title="Code block — copy button"
        note="Hover the block to surface the copy affordance. The lab"
        note2="renders the same component the thread uses."
      >
        <div className="rounded-md border border-border bg-card p-4 text-sm">
          <Markdown
            source={"```python\ndef hello():\n    return 'world'\n```"}
          />
        </div>
      </Section>

      {/* ----------------------------------------------------------------- */}
      {/* 6. Composer (visual only — no submit)                              */}
      {/* ----------------------------------------------------------------- */}
      <Section
        icon={<ArrowUp size={14} />}
        title="Composer (visual)"
        note="Live in /chat. The lab renders the same primitives for"
        note2="the visual reference; the submit handler is inert."
      >
        <div className="rounded-md border border-border bg-card p-3 flex items-end gap-2">
          <div className="flex-1 rounded-lg px-3 py-2 text-sm border border-border bg-input text-muted-foreground min-h-[40px]">
            Write a message…
          </div>
          <button
            type="button"
            data-testid="chat-lab-send"
            className="shrink-0 p-2 rounded-lg bg-primary text-primary-foreground"
            disabled
          >
            <ArrowUp size={16} />
          </button>
        </div>
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Local helpers
// ---------------------------------------------------------------------------

function Section({
  icon,
  title,
  note,
  note2,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  note: string;
  note2?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground">{icon}</span>
        <h2 className="text-sm font-medium tracking-tight">{title}</h2>
      </div>
      <p className="text-xs text-muted-foreground">{note}</p>
      {note2 && <p className="text-xs text-muted-foreground">{note2}</p>}
      <div className="pt-2">{children}</div>
    </section>
  );
}

function Bubble({
  role,
  children,
}: {
  role: "user" | "assistant";
  children: React.ReactNode;
}) {
  return (
    <div className="flex">
      {role === "user" ? (
        <div className="ml-auto max-w-[80%] rounded-lg px-3 py-2 bg-primary/10 text-foreground text-sm whitespace-pre-wrap">
          {children}
        </div>
      ) : (
        <div className="mr-auto max-w-[92%] rounded-lg px-3 py-2 bg-card border border-border text-foreground text-sm">
          {children}
        </div>
      )}
    </div>
  );
}