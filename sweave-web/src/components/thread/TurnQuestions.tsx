/**
 * Inline blocking-question card for a chat turn (M1.11).
 *
 * When the orchestrator calls `ask_human` during its turn, the MCP
 * call returns immediately but the ChatLoop holds the turn open
 * (no assistant persisted) until the escalation resolves. This
 * card renders the pending question *where the user is* — under
 * the streaming/final assistant bubble — instead of forcing a
 * trip to the Children audit log.
 *
 * Behavior:
 * - Fetches `GET /api/delegations/{turnId}/escalation`; renders
 *   only for `kind === "question"` + `status === "pending"`.
 * - Options render as buttons (single-pick); otherwise a free-form
 *   input. Answer posts to `/answer`; Skip runs the system-issued
 *   `window.confirm` first (the anti-fat-finger guard — a system
 *   dialog, never LLM text) then posts to `/skip`.
 * - WS-driven: `specialist.escalated` + `specialist.escalation_resolved`
 *   refresh the card; a resolved question unmounts (returns null).
 * - Fetch failures render nothing (the turn text stands alone).
 */

import { useCallback, useEffect, useState } from "react";
import { HelpCircle, Send, SkipForward } from "lucide-react";
import { api } from "@/api/client";
import { useWS } from "@/context/WSProvider";
import type { EscalationRecord } from "@/types";

const SKIP_CONFIRM_TEXT =
  "Skip this question? The agent will proceed with its best judgment. This cannot be undone.";

export function TurnQuestions({ delegationId }: { delegationId: string }) {
  const [esc, setEsc] = useState<EscalationRecord | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const rec = await api.getEscalation(delegationId);
      setEsc(rec);
    } catch {
      setEsc(null);
    }
  }, [delegationId]);

  useEffect(() => {
    setEsc(null);
    setDraft("");
    setError(null);
    void load();
  }, [load]);

  let subscribe: ((event: string, handler: () => void) => () => void) | null = null;
  try {
    subscribe = useWS().subscribe;
  } catch {
    subscribe = null;
  }
  useEffect(() => {
    if (!subscribe) return;
    const off1 = subscribe("specialist.escalated", () => {
      void load();
    });
    const off2 = subscribe("specialist.escalation_resolved", () => {
      void load();
    });
    return () => {
      off1();
      off2();
    };
  }, [subscribe, load]);

  if (!esc || esc.status !== "pending" || esc.kind !== "question") return null;

  const options = esc.options ?? [];

  const sendAnswer = async (response: string) => {
    const text = response.trim();
    if (!text || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.answerEscalation(delegationId, text);
      setDraft("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Answer failed");
    } finally {
      setBusy(false);
    }
  };

  const sendSkip = async () => {
    if (busy) return;
    // System-issued confirm (not LLM text): the fat-finger guard.
    if (!window.confirm(SKIP_CONFIRM_TEXT)) return;
    setBusy(true);
    setError(null);
    try {
      await api.skipEscalation(delegationId);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Skip failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      data-testid="turn-question-card"
      data-delegation-id={delegationId}
      className="mt-2 rounded-xl border border-amber-500/40 bg-amber-500/5 p-3"
    >
      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-amber-700 dark:text-amber-300">
        <HelpCircle size={14} />
        <span>Question — the turn is waiting for your answer</span>
      </div>
      <p className="whitespace-pre-wrap break-words text-sm text-foreground">
        {esc.question}
      </p>
      {options.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {options.map((opt) => (
            <button
              key={opt}
              type="button"
              disabled={busy}
              onClick={() => void sendAnswer(opt)}
              data-testid={`turn-question-option-${opt}`}
              className="rounded-lg border border-border bg-card px-2.5 py-1 text-xs font-medium hover:border-primary hover:text-primary disabled:opacity-50"
            >
              {opt}
            </button>
          ))}
        </div>
      )}
      <div className="mt-2 flex items-center gap-1.5">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && draft.trim()) void sendAnswer(draft);
          }}
          disabled={busy}
          data-testid="turn-question-input"
          placeholder={options.length > 0 ? "Or type a custom answer…" : "Type your answer…"}
          className="min-w-0 flex-1 rounded-lg border border-border bg-input px-2.5 py-1.5 text-sm focus:outline-none"
        />
        <button
          type="button"
          onClick={() => void sendAnswer(draft)}
          disabled={busy || !draft.trim()}
          data-testid="turn-question-send"
          aria-label="Send answer"
          className="grid h-8 w-8 place-items-center rounded-lg bg-primary text-primary-foreground disabled:opacity-40"
        >
          <Send size={14} />
        </button>
        <button
          type="button"
          onClick={() => void sendSkip()}
          disabled={busy}
          data-testid="turn-question-skip"
          title="Skip — the agent proceeds with best judgment"
          className="inline-flex h-8 items-center gap-1 rounded-lg border border-border px-2 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
        >
          <SkipForward size={13} />
          Skip
        </button>
      </div>
      {error && (
        <p data-testid="turn-question-error" className="mt-1.5 text-xs text-rose-500">
          {error}
        </p>
      )}
      <p className="mt-1.5 text-[11px] text-muted-foreground">
        No deadline — the turn holds until you answer or skip.
      </p>
    </div>
  );
}
