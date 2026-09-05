/**
 * Message composer (M1.9 Step 2).
 *
 * A controlled textarea that submits a user message to the
 * chat loop. The submission flow:
 *   1. POST /api/sessions/{id}/messages (role=user)
 *   2. The chat loop runs the orchestrator turn + persists the
 *      assistant reply; the response includes the assistant
 *      message.
 *   3. We push the user + assistant messages into the local
 *      chat state (so the bubble appears immediately), then
 *      WS events ``chat.delta`` + ``message.added`` keep it in
 *      sync as the orchestrator streams + finalizes.
 *
 * Serial-turn indicator: a submit during an in-flight turn
 * queues the text in the composer but doesn't send (the loop
 * is serial per session per the M1.7 ruling). The disabled
 * state on the button reflects this.
 */
import { useEffect, useRef, useState } from "react";
import { Send, Loader2 } from "lucide-react";
import { api } from "@/api/client";
import { useApp } from "@/context/AppProvider";
import { cn } from "@/utils/cn";

export interface ComposerProps {
  sessionId: string;
  onUserMessage: (content: string) => void;
  onAssistantMessage: (content: string, delegationId: string) => void;
  disabled?: boolean;
}

export function Composer({
  sessionId,
  onUserMessage,
  onAssistantMessage,
  disabled,
}: ComposerProps) {
  const { pushNotification } = useApp();
  const [value, setValue] = useState("");
  const [sending, setSending] = useState(false);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  // Auto-grow the textarea up to a max.
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
  }, [value]);

  const send = async () => {
    const text = value.trim();
    if (!text || sending) return;
    setSending(true);
    // Optimistically push the user message into the chat list
    // before the POST completes. The local store updates the
    // persisted message on the next /api/sessions/{id} refresh
    // (or on the WS message.added event).
    onUserMessage(text);
    setValue("");
    try {
      const res = await api.sendMessage(sessionId, {
        role: "user",
        content: text,
      });
      if (res.assistant) {
        const meta = (res.assistant.metadata ?? {}) as Record<string, unknown>;
        const delegationId =
          typeof meta.delegation_id === "string"
            ? (meta.delegation_id as string)
            : "";
        onAssistantMessage(res.assistant.content, delegationId);
      }
    } catch (err) {
      pushNotification("error", `Chat failed: ${(err as Error).message}`);
    } finally {
      setSending(false);
    }
  };

  return (
    <div
      data-testid="composer"
      className="border-t border-border bg-card p-3"
    >
      <div className="flex items-end gap-2">
        <textarea
          ref={taRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          disabled={disabled || sending}
          placeholder="Ask the orchestrator..."
          rows={1}
          data-testid="composer-textarea"
          className={cn(
            "flex-1 resize-none px-3 py-2 text-sm border border-border rounded bg-input",
            "focus:outline-none focus:ring-2 focus:ring-ring",
            "disabled:opacity-50",
          )}
        />
        <button
          type="button"
          onClick={send}
          disabled={disabled || sending || !value.trim()}
          data-testid="composer-send"
          className="px-3 py-2 bg-primary text-primary-foreground rounded text-sm flex items-center gap-1 disabled:opacity-50"
          aria-label="Send"
        >
          {sending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
          <span>Send</span>
        </button>
      </div>
      {sending && (
        <div
          data-testid="composer-busy"
          className="mt-1 text-xs text-muted-foreground"
        >
          Orchestrator is thinking...
        </div>
      )}
    </div>
  );
}
