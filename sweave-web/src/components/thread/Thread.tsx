/**
 * Assistant-ui Thread, Message and Composer (R4.2 step 1) — built
 * from assistant-ui primitives, shadcn-style (we own this code).
 *
 * Step 1 renders plain text bubbles (whitespace-pre-wrap). Markdown +
 * code highlighting + tool cards land in step 2 (agent-elements-derived
 * copied components). Session management stays in the R4.1 tree; the
 * thread header (create/switch/rename in-thread) lands in step 3.
 *
 * API note (assistant-ui 0.15.18): the deprecated `components={...}`
 * form of `ThreadPrimitive.Messages` can't coexist with `children`
 * (used for the empty state), so we use the render-function children
 * form and render the empty placeholder at the Viewport level via
 * ``useThreadIsEmpty``.
 */
import {
  ThreadPrimitive,
  MessagePrimitive,
  ComposerPrimitive,
  useAuiState,
} from "@assistant-ui/react";
import { ArrowUp } from "lucide-react";
import { cn } from "@/utils/cn";
import { AssistantTextPart } from "./markdown/AssistantTextPart";

// ---------------------------------------------------------------------------
// Thread
// ---------------------------------------------------------------------------

export function Thread() {
  const isEmpty = useAuiState((s) => s.thread.isEmpty);
  return (
    <ThreadPrimitive.Root className="flex flex-col h-full min-h-0">
      <ThreadPrimitive.Viewport
        className="flex-1 overflow-y-auto scrollbar-thin"
        data-testid="thread-viewport"
      >
        {isEmpty && (
          <div
            data-testid="thread-empty"
            className="flex items-center justify-center h-full text-sm text-muted-foreground py-8"
          >
            Send a message to start the conversation.
          </div>
        )}
        <ThreadPrimitive.Messages>
          {() => <Message />}
        </ThreadPrimitive.Messages>
      </ThreadPrimitive.Viewport>
      <Composer />
    </ThreadPrimitive.Root>
  );
}

// ---------------------------------------------------------------------------
// Message
// ---------------------------------------------------------------------------

function Message() {
  return (
    <MessagePrimitive.Root className="group/message relative mx-auto w-full max-w-3xl px-4 py-1">
      <MessagePrimitive.If user>
        <div className="flex justify-end">
          <div className="max-w-[80%] rounded-lg px-3 py-2 bg-primary/10 text-foreground text-sm whitespace-pre-wrap">
            <MessagePrimitive.Parts />
          </div>
        </div>
      </MessagePrimitive.If>
      <MessagePrimitive.If assistant>
        <div className="flex justify-start">
          <div className="max-w-[92%] rounded-lg px-3 py-2 bg-card border border-border text-foreground text-sm">
            <MessagePrimitive.Parts
              components={{ Text: AssistantTextPart }}
            />
          </div>
        </div>
      </MessagePrimitive.If>
    </MessagePrimitive.Root>
  );
}

// ---------------------------------------------------------------------------
// Composer
// ---------------------------------------------------------------------------

function Composer() {
  return (
    <ComposerPrimitive.Root className="border-t border-border p-3 flex items-end gap-2">
      <ComposerPrimitive.Input
        placeholder="Write a message…"
        autoFocus
        data-testid="chat-composer-input"
        className={cn(
          "flex-1 resize-none bg-input rounded-lg px-3 py-2 text-sm",
          "border border-border focus:outline-none focus:ring-2 focus:ring-ring",
          "min-h-[40px] max-h-[200px]",
        )}
      />
      <ComposerPrimitive.Send asChild>
        <button
          type="button"
          data-testid="chat-composer-send"
          className="shrink-0 p-2 rounded-lg bg-primary text-primary-foreground hover:opacity-80 disabled:opacity-50"
        >
          <ArrowUp size={16} />
        </button>
      </ComposerPrimitive.Send>
    </ComposerPrimitive.Root>
  );
}