/**
 * Dev-only REAL-Thread visual test (R4.2 step 2-pre; ruling 4).
 *
 * The old lab rendered a component gallery (bubble shells + markdown)
 * outside any runtime — it structurally COULD NOT surface thread-level
 * polish gaps, which is how step 2a shipped a thread the user rejected.
 * This lab renders the actual `Thread` component against a fixture
 * `useExternalStoreRuntime`, with controls to toggle the states that
 * matter for review:
 *
 *   - Seed    — the resolved 4-message thread (markdown + GFM + code
 *               block with the copy affordance; the step-2a markdown
 *               demo survives here, rendered inside the real Thread).
 *   - Empty   — the welcome screen with suggested prompts.
 *   - Stream  — a user turn followed by a chunked assistant reply
 *               (streaming cursor + partial-markdown fallback +
 *               composer send->stop swap; the stop affordance is
 *               disabled-with-tooltip per the 2026-09-07 ruling).
 *
 * The route is gated by ``import.meta.env.DEV`` at registration time
 * (App.tsx) so the lab is tree-shaken from the production bundle.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { Eraser, ListRestart, Play } from "lucide-react";
import { Thread } from "@/components/thread/Thread";

const MARKDOWN_SAMPLE = [
  "## Markdown inside the real Thread",
  "",
  "Assistant text renders as **markdown** (GFM tables, lists, links,",
  "blockquotes) with Shiki-highlighted code blocks:",
  "",
  "```ts",
  "import { useSweaveChatRuntime } from '@/lib/chat/useSweaveChatRuntime';",
  "",
  "export function ChatPage() {",
  "  const runtime = useSweaveChatRuntime(activeSession?.id ?? null);",
  "  return <AssistantRuntimeProvider runtime={runtime}><Thread /></AssistantRuntimeProvider>;",
  "}",
  "```",
  "",
  "| state     | meaning                      |",
  "| --------- | ---------------------------- |",
  "| `idle`    | no turn in flight            |",
  "| `queued`  | submit fired, server pending |",
  "| `running` | chat.delta streaming         |",
  "",
  "- runtime view-projection of REST + WS",
  "- [assistant-ui docs](https://assistant-ui.com) for primitives",
  "",
  "> Streaming is safe: partial markdown falls back to plain text.",
].join("\n");

const STREAM_REPLY = [
  "Streaming answer: the cursor pulses while the run is in flight,",
  "the composer swaps send for the stop affordance,",
  "and a partial code block must not crash the renderer:",
  "",
  "```ts",
  "const x: number = 1;",
  "// still streaming ...",
  "```",
  "",
  "Done.",
].join(" ");

const minsAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

const SEED: ThreadMessageLike[] = [
  {
    id: "lab-u1",
    role: "user",
    content: [{ type: "text", text: "Show me what the chat surface renders.", status: { type: "complete" } }],
    metadata: { custom: { timestamp: minsAgo(4) } },
  },
  {
    id: "lab-a1",
    role: "assistant",
    status: { type: "complete", reason: "stop" },
    content: [{ type: "text", text: MARKDOWN_SAMPLE, status: { type: "complete" } }],
    metadata: { custom: { timestamp: minsAgo(4), delegationId: "chat-lab-1" } },
  },
  {
    id: "lab-u2",
    role: "user",
    content: [{ type: "text", text: "Nice. And a code block with a copy affordance?", status: { type: "complete" } }],
    metadata: { custom: { timestamp: minsAgo(2) } },
  },
  {
    id: "lab-a2",
    role: "assistant",
    status: { type: "complete", reason: "stop" },
    content: [
      {
        type: "text",
        text: "```python\ndef hello():\n    return 'world'\n```\nHover the block — the copy button is on its header.",
        status: { type: "complete" },
      },
    ],
    metadata: { custom: { timestamp: minsAgo(2), delegationId: "chat-lab-2" } },
  },
];

const CHUNK = 14;
const CHUNK_MS = 90;

export function ChatLab() {
  const [messages, setMessages] = useState<ThreadMessageLike[]>(SEED);
  const [running, setRunning] = useState(false);
  const timersRef = useRef<number[]>([]);

  const clearTimers = useCallback(() => {
    for (const t of timersRef.current) window.clearTimeout(t);
    timersRef.current = [];
  }, []);

  useEffect(() => clearTimers, [clearTimers]);

  const streamReply = useCallback(() => {
    const streamId = `lab-stream-${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      {
        id: streamId,
        role: "assistant",
        content: [{ type: "text", text: "", status: { type: "running" } }],
        status: { type: "running" },
        metadata: { custom: { timestamp: new Date().toISOString() } },
      } as ThreadMessageLike,
    ]);
    setRunning(true);

    let i = 0;
    const tick = () => {
      i += CHUNK;
      const partial = STREAM_REPLY.slice(0, i);
      const done = i >= STREAM_REPLY.length;
      setMessages((prev) =>
        prev.map((m) =>
          m.id === streamId
            ? {
                ...m,
                content: [{ type: "text", text: partial, status: done ? { type: "complete" } : { type: "running" } }],
                ...(done ? { status: { type: "complete", reason: "stop" } } : {}),
              }
            : m,
        ),
      );
      if (done) {
        setRunning(false);
      } else {
        timersRef.current.push(window.setTimeout(tick, CHUNK_MS));
      }
    };
    timersRef.current.push(window.setTimeout(tick, CHUNK_MS));
  }, []);

  const runtime = useExternalStoreRuntime({
    messages,
    isRunning: running,
    isSendDisabled: running,
    convertMessage: (m) => m,
    onNew: async (message) => {
      const text = message.content
        .filter((p): p is { type: "text"; text: string } => p.type === "text")
        .map((p) => p.text)
        .join("");
      if (!text.trim() || running) return;
      setMessages((prev) => [
        ...prev,
        {
          id: `lab-user-${Date.now()}`,
          role: "user",
          content: [{ type: "text", text, status: { type: "complete" } }],
          metadata: { custom: { timestamp: new Date().toISOString() } },
        },
      ]);
      streamReply();
    },
  });

  const seed = () => {
    clearTimers();
    setRunning(false);
    setMessages(SEED);
  };
  const empty = () => {
    clearTimers();
    setRunning(false);
    setMessages([]);
  };
  const runStream = () => {
    if (running) return;
    setMessages((prev) => [
      ...prev,
      {
        id: `lab-user-${Date.now()}`,
        role: "user",
        content: [{ type: "text", text: "Stream a reply for the visual check.", status: { type: "complete" } }],
        metadata: { custom: { timestamp: new Date().toISOString() } },
      },
    ]);
    streamReply();
  };

  return (
    <div data-testid="chat-lab" className="flex h-full flex-col">
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-border px-4 py-2">
        <div className="min-w-0">
          <h1 className="text-sm font-semibold tracking-tight">R4.2 real-Thread lab</h1>
          <p className="text-xs text-muted-foreground">
            The actual Thread + runtime with fixture data. Toggle states and watch the
            surface: welcome, resolved thread, streaming, composer states.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <LabButton testid="lab-btn-seed" onClick={seed} icon={<ListRestart size={13} />}>
            Seed thread
          </LabButton>
          <LabButton testid="lab-btn-empty" onClick={empty} icon={<Eraser size={13} />}>
            Empty
          </LabButton>
          <LabButton
            testid="lab-btn-stream"
            onClick={runStream}
            icon={<Play size={13} />}
            disabled={running}
          >
            Stream reply
          </LabButton>
        </div>
      </header>

      <div className="min-h-0 flex-1">
        <AssistantRuntimeProvider runtime={runtime}>
          <Thread />
        </AssistantRuntimeProvider>
      </div>
    </div>
  );
}

function LabButton({
  onClick,
  icon,
  children,
  testid,
  disabled,
}: {
  onClick: () => void;
  icon: React.ReactNode;
  children: React.ReactNode;
  testid: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      data-testid={testid}
      onClick={onClick}
      disabled={disabled}
      className="flex h-7 items-center gap-1.5 rounded-md border border-border bg-card px-2.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
    >
      {icon}
      {children}
    </button>
  );
}
