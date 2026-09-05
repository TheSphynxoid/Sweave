/**
 * Message list (M1.9 Step 2).
 *
 * Renders the persisted messages + a single streaming bubble
 * per active delegation id. The streaming bubble is the M1.8
 * no-rerender invariant: a single ref + textContent patch keeps
 * the bubble patched in place; React doesn't re-render the
 * bubble on every ``chat.delta``.
 *
 * User / assistant / tool messages render as plain cards. The
 * "thinking" indicator on a streaming bubble is the
 * ``streaming`` dot at the bubble's right edge.
 */
import { useEffect, useRef } from "react";
import { Bot, User, Wrench } from "lucide-react";
import { cn } from "@/utils/cn";
import type { SessionMessage } from "@/types";

export interface MessageListProps {
  messages: SessionMessage[];
  streaming: Record<string, string>;
}

export function MessageList({ messages, streaming }: MessageListProps) {
  return (
    <div
      data-testid="message-list"
      className="flex-1 overflow-y-auto p-4 space-y-3"
    >
      {messages.length === 0 && Object.keys(streaming).length === 0 && (
        <div className="text-center text-sm text-muted-foreground py-8">
          Send a message to start the conversation.
        </div>
      )}
      {messages.map((m) => (
        <MessageBubble key={m.id} message={m} />
      ))}
      {Object.entries(streaming).map(([delegationId, text]) => (
        <StreamingBubble key={delegationId} delegationId={delegationId} text={text} />
      ))}
    </div>
  );
}

function MessageBubble({ message }: { message: SessionMessage }) {
  const isUser = message.role === "user";
  const isTool = message.role === "tool";
  return (
    <div
      data-testid={`message-${message.role}-${message.id}`}
      className={cn(
        "flex gap-2",
        isUser ? "justify-end" : "justify-start",
      )}
    >
      {!isUser && (
        <div className="w-7 h-7 rounded bg-primary/10 text-primary flex items-center justify-center flex-shrink-0">
          {isTool ? <Wrench size={14} /> : <Bot size={14} />}
        </div>
      )}
      <div
        className={cn(
          "max-w-[70ch] px-3 py-2 rounded text-sm",
          isUser
            ? "bg-primary text-primary-foreground"
            : isTool
              ? "bg-muted text-muted-foreground border border-border"
              : "bg-card border border-border",
        )}
      >
        <p className="whitespace-pre-wrap break-words">{message.content}</p>
        {message.metadata && message.metadata.tool_name ? (
          <p className="text-[10px] mt-1 opacity-70">
            tool: {String(message.metadata.tool_name)}
          </p>
        ) : null}
      </div>
      {isUser && (
        <div className="w-7 h-7 rounded bg-muted text-muted-foreground flex items-center justify-center flex-shrink-0">
          <User size={14} />
        </div>
      )}
    </div>
  );
}

/**
 * The streaming bubble. Patches in place via ref + textContent
 * to avoid re-rendering React on every chat.delta (the M1.8
 * no-rerender invariant; carrying it through to React was
 * one of the Step 2 risks called out in the plan).
 */
function StreamingBubble({ delegationId, text }: { delegationId: string; text: string }) {
  const ref = useRef<HTMLParagraphElement | null>(null);
  useEffect(() => {
    if (ref.current) {
      ref.current.textContent = text;
    }
  }, [text]);
  return (
    <div
      data-testid={`streaming-${delegationId}`}
      data-streaming="true"
      className="flex gap-2 justify-start"
    >
      <div className="w-7 h-7 rounded bg-primary/10 text-primary flex items-center justify-center flex-shrink-0">
        <Bot size={14} />
      </div>
      <div className="max-w-[70ch] px-3 py-2 rounded text-sm bg-card border border-border">
        <p ref={ref} className="whitespace-pre-wrap break-words" data-testid="streaming-text">
          {text}
        </p>
        <span className="inline-block w-2 h-3 ml-1 align-middle bg-primary animate-pulse" />
      </div>
    </div>
  );
}
