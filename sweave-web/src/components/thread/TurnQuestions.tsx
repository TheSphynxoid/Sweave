/**
 * Inline blocking-question card for a chat turn (M1.11, extended by
 * TOOL_CARDS Step 3 — ask_human batch).
 *
 * When the orchestrator calls `ask_human` during its turn, the MCP
 * call returns immediately but the ChatLoop holds the turn open
 * (no assistant persisted) until the escalation resolves. This
 * card renders the pending question *where the user is* — under
 * the streaming/final assistant bubble — instead of forcing a
 * trip to the Children audit log.
 *
 * Two rendering paths share one card shell:
 *
 * 1. Legacy / single-question (and ALL permission asks — never
 *    batched): the record carries a top-level `question` (+ optional
 *    `options`). The card is the original single section; answering
 *    posts `{response}` via `answerEscalation(id, text)` — byte-
 *    identical to the pre-batch wire contract (legacy callers
 *    untouched). Permission rendering/logic is preserved verbatim.
 *
 * 2. Batched (TOOL_CARDS Step 3): a record with `questions[]` (≤5)
 *    renders stacked per-question sections (question text, options
 *    as buttons, per-question input) plus a single Send-all. Every
 *    answer posts the FULL `answers[]` array via `answerEscalation
 *    Batch(id, answers)`; the server persists partials without
 *    resolving the record until every slot is filled (all-at-once
 *    flip). Per-question option clicks + per-question answer buttons
 *    are both supported and post the full array (the targeted index
 *    set, already-answered slots carried forward).
 *
 * Shared behavior:
 * - Fetches `GET /api/delegations/{turnId}/escalation`; renders
 *   only for `kind === "question" | "permission"` + `status === "pending"`.
 * - Skip runs the system-issued `window.confirm` first (the anti-
 *   fat-finger guard — a system dialog, never LLM text) then posts
 *   to `/skip`. For a batch, skip = the whole batch (best judgment).
 * - WS-driven: `specialist.escalated` + `specialist.escalation_resolved`
 *   refresh the card; a resolved question unmounts (returns null).
 * - Fetch failures render nothing (the turn text stands alone).
 */

import { useCallback, useEffect, useState } from "react";
import { HelpCircle, Send, ShieldCheck, SkipForward } from "lucide-react";
import { api } from "@/api/client";
import { useWS } from "@/context/WSProvider";
import type { EscalationRecord } from "@/types";
import { cn } from "@/utils/cn";

const SKIP_CONFIRM_TEXT =
  "Skip this question? The agent will proceed with its best judgment. This cannot be undone.";

export function TurnQuestions({ delegationId }: { delegationId: string }) {
  const [esc, setEsc] = useState<EscalationRecord | null>(null);
  const [drafts, setDrafts] = useState<string[]>([]);
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
    setDrafts([]);
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

  if (!esc || esc.status !== "pending") return null;
  const isPermission = esc.kind === "permission";
  if (!isPermission && esc.kind !== "question") return null;

  // Batch path: a question record carrying `questions[]` (never
  // permission — those are never batched). Absent → legacy single
  // card. Both paths keep the same outer shell + WS/resolve logic.
  const batch =
    !isPermission && esc.questions && esc.questions.length > 0
      ? esc.questions
      : null;

  const sendSkip = async () => {
    if (busy) return;
    // System-issued confirm (not LLM text): the fat-finger guard.
    if (!window.confirm(skipText)) return;
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

  const skipText = isPermission
    ? "Deny this permission? The tool call fails and the turn reports it explicitly. This cannot be undone."
    : SKIP_CONFIRM_TEXT;

  // ---- Legacy single-question (and all permission) path -------------------
  if (!batch) {
    const options = esc.options ?? [];

    const sendAnswer = async (response: string) => {
      const text = response.trim();
      if (!text || busy) return;
      setBusy(true);
      setError(null);
      try {
        await api.answerEscalation(delegationId, text);
        setDrafts([]);
        await load();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Answer failed");
      } finally {
        setBusy(false);
      }
    };

    return (
      <div
        data-testid="turn-question-card"
        data-delegation-id={delegationId}
        data-kind={esc.kind}
        className="animate-message-in mt-3 overflow-hidden rounded-2xl border border-amber-500/40 bg-gradient-to-b from-amber-500/[0.10] to-amber-500/[0.03] shadow-lg shadow-amber-500/5"
      >
        <div className="flex items-center gap-2 border-b border-amber-500/20 px-3.5 py-2">
          <span className="grid h-6 w-6 place-items-center rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-300">
            {isPermission ? <ShieldCheck size={13} /> : <HelpCircle size={13} />}
          </span>
          <span className="text-xs font-semibold text-amber-700 dark:text-amber-300">
            {isPermission
              ? "Permission required — the turn is waiting for your decision"
              : "Question — the turn is waiting for your answer"}
          </span>
          <span className="ml-auto flex items-center gap-1 text-[10px] font-medium text-amber-700/70 dark:text-amber-300/70">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500" />
            waiting
          </span>
        </div>
        <div className="px-3.5 py-3">
          <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground">
            {esc.question}
          </p>
          {isPermission && esc.metadata != null && (
            <p data-testid="turn-question-permission-detail" className="mt-2 rounded-lg bg-background/60 px-2.5 py-1.5 font-mono text-[11px] leading-relaxed text-muted-foreground">
              {(() => {
                const meta = esc.metadata as {
                  permission?: string;
                  patterns?: string[];
                  command?: string;
                };
                const parts = [
                  meta.permission ? `tool check: ${meta.permission}` : "",
                  meta.patterns?.length ? `patterns: ${meta.patterns.join(", ")}` : "",
                  meta.command ? `command: ${meta.command}` : "",
                ].filter(Boolean);
                // 'always allow' persists the pattern list opencode-side;
                // the card labels it so the grant is explicit (audit).
                parts.push(`'always allow' grants exactly: ${meta.patterns?.join(", ") ?? "the checked paths"}`);
                return parts.join(" · ");
              })()}
            </p>
          )}
          {options.length > 0 && (
            <div className="mt-2.5 flex flex-wrap gap-1.5">
              {options.map((opt) => (
                <button
                  key={opt}
                  type="button"
                  disabled={busy}
                  onClick={() => void sendAnswer(opt)}
                  data-testid={`turn-question-option-${opt}`}
                  className="rounded-full border border-amber-500/40 bg-background/80 px-3 py-1.5 text-xs font-semibold text-amber-800 transition-all hover:-translate-y-px hover:bg-amber-500/15 hover:shadow-sm disabled:opacity-50 dark:text-amber-200"
                >
                  {opt}
                </button>
              ))}
            </div>
          )}
          <div className="mt-2.5 flex items-center gap-1.5">
            <input
              type="text"
              value={drafts[0] ?? ""}
              onChange={(e) => setDrafts([e.target.value])}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (drafts[0] ?? "").trim())
                  void sendAnswer(drafts[0]);
              }}
              disabled={busy}
              data-testid="turn-question-input"
              placeholder={options.length > 0 ? "Or type a custom answer…" : "Type your answer…"}
              className="min-w-0 flex-1 rounded-xl border border-border/70 bg-input px-3 py-2 text-sm shadow-sm transition-all focus:border-amber-500/60 focus:outline-none focus:ring-2 focus:ring-amber-500/20"
            />
            <button
              type="button"
              onClick={() => void sendAnswer(drafts[0] ?? "")}
              disabled={busy || !(drafts[0] ?? "").trim()}
              data-testid="turn-question-send"
              aria-label="Send answer"
              className={cn(
                "grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-amber-500 to-amber-600 text-white shadow-md shadow-amber-500/25 transition-all",
                "hover:-translate-y-px hover:shadow-lg disabled:translate-y-0 disabled:opacity-40 disabled:shadow-none",
              )}
            >
              <Send size={14} />
            </button>
            <button
              type="button"
              onClick={() => void sendSkip()}
              disabled={busy}
              data-testid="turn-question-skip"
              title={isPermission ? "Deny — the tool call fails loud" : "Skip — the agent proceeds with best judgment"}
              className="inline-flex h-9 shrink-0 items-center gap-1 rounded-xl border border-border/70 bg-background/60 px-2.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
            >
              <SkipForward size={13} />
              Skip
            </button>
          </div>
          {error && (
            <p data-testid="turn-question-error" className="mt-2 rounded-lg bg-rose-500/10 px-2.5 py-1.5 text-xs text-rose-600 dark:text-rose-300">
              {error}
            </p>
          )}
          <p className="mt-2 text-[11px] text-muted-foreground/80">
            No deadline — the turn holds until you answer or skip.
          </p>
        </div>
      </div>
    );
  }

  // ---- Batched (TOOL_CARDS Step 3) path -----------------------------------
  const len = batch.length;

  // Build the full `answers[]` (length === questions.length) for the
  // server: carried-forward record answers win, then any current
  // draft; unfilled slots go "" (the server persists partials and
  // flips status only when every slot is filled — all-at-once).
  const buildAnswers = (setIndex?: number, value?: string): string[] => {
    const out: string[] = [];
    for (let i = 0; i < len; i++) {
      if (setIndex === i && value !== undefined) {
        out.push(value.trim());
        continue;
      }
      const existing = esc.answers?.[i];
      if (existing) {
        out.push(existing);
        continue;
      }
      const d = (drafts[i] ?? "").trim();
      out.push(d);
    }
    return out;
  };

  const submitBatch = async (setIndex?: number, value?: string) => {
    if (busy) return;
    const answers = buildAnswers(setIndex, value);
    // Nothing to send (every slot empty) — ignore.
    if (answers.every((a) => a === "")) return;
    setBusy(true);
    setError(null);
    try {
      await api.answerEscalationBatch(delegationId, answers);
      if (setIndex !== undefined) {
        setDrafts((prev) => {
          const next = [...prev];
          next[setIndex] = "";
          return next;
        });
      } else {
        setDrafts([]);
      }
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Answer failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      data-testid="turn-question-card"
      data-delegation-id={delegationId}
      data-kind={esc.kind}
      data-batch={len}
      className="animate-message-in mt-3 overflow-hidden rounded-2xl border border-amber-500/40 bg-gradient-to-b from-amber-500/[0.10] to-amber-500/[0.03] shadow-lg shadow-amber-500/5"
    >
      <div className="flex items-center gap-2 border-b border-amber-500/20 px-3.5 py-2">
        <span className="grid h-6 w-6 place-items-center rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-300">
          <HelpCircle size={13} />
        </span>
        <span className="text-xs font-semibold text-amber-700 dark:text-amber-300">
          {`Questions (${len}) — the turn is waiting for your answers`}
        </span>
        <span className="ml-auto flex items-center gap-1 text-[10px] font-medium text-amber-700/70 dark:text-amber-300/70">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500" />
          waiting
        </span>
      </div>
      <div className="px-3.5 py-3">
        <div className="flex flex-col gap-3">
          {batch.map((q, i) => {
            const options = q.options ?? [];
            const answered = Boolean(esc.answers?.[i]);
            return (
              <div
                key={i}
                data-testid={`turn-question-section-${i}`}
                className="rounded-xl border border-amber-500/20 bg-background/50 p-3"
              >
                <p
                  data-testid={`turn-question-text-${i}`}
                  className="whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground"
                >
                  <span className="mr-1.5 font-semibold text-amber-700/80 dark:text-amber-300/80">
                    {i + 1}.
                  </span>
                  {q.question}
                </p>
                {options.length > 0 && (
                  <div className="mt-2.5 flex flex-wrap gap-1.5">
                    {options.map((opt) => (
                      <button
                        key={opt}
                        type="button"
                        disabled={busy}
                        onClick={() => void submitBatch(i, opt)}
                        data-testid={`turn-question-option-${i}-${opt}`}
                        className="rounded-full border border-amber-500/40 bg-background/80 px-3 py-1.5 text-xs font-semibold text-amber-800 transition-all hover:-translate-y-px hover:bg-amber-500/15 hover:shadow-sm disabled:opacity-50 dark:text-amber-200"
                      >
                        {opt}
                      </button>
                    ))}
                  </div>
                )}
                <div className="mt-2.5 flex items-center gap-1.5">
                  <input
                    type="text"
                    value={drafts[i] ?? ""}
                    onChange={(e) =>
                      setDrafts((prev) => {
                        const next = [...prev];
                        next[i] = e.target.value;
                        return next;
                      })
                    }
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && (drafts[i] ?? "").trim())
                        void submitBatch(i, drafts[i]);
                    }}
                    disabled={busy}
                    data-testid={`turn-question-input-${i}`}
                    placeholder={
                      options.length > 0
                        ? `Or type a custom answer for #${i + 1}…`
                        : `Type your answer for #${i + 1}…`
                    }
                    className="min-w-0 flex-1 rounded-xl border border-border/70 bg-input px-3 py-2 text-sm shadow-sm transition-all focus:border-amber-500/60 focus:outline-none focus:ring-2 focus:ring-amber-500/20"
                  />
                  <button
                    type="button"
                    onClick={() => void submitBatch(i, drafts[i])}
                    disabled={busy || !(drafts[i] ?? "").trim()}
                    data-testid={`turn-question-answer-${i}`}
                    aria-label={`Answer question ${i + 1}`}
                    className="inline-flex h-9 shrink-0 items-center gap-1 rounded-xl border border-amber-500/40 bg-background/80 px-2.5 text-xs font-semibold text-amber-800 transition-colors hover:-translate-y-px hover:bg-amber-500/15 disabled:opacity-40 dark:text-amber-200"
                  >
                    <Send size={13} />
                    Answer
                  </button>
                  {answered && (
                    <span
                      data-testid={`turn-question-answered-${i}`}
                      className="shrink-0 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-700 dark:text-emerald-300"
                    >
                      answered
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        <div className="mt-3 flex items-center gap-1.5 border-t border-amber-500/20 pt-3">
          <button
            type="button"
            onClick={() => void submitBatch()}
            disabled={busy}
            data-testid="turn-question-send"
            className={cn(
              "inline-flex h-9 shrink-0 items-center gap-1.5 rounded-xl bg-gradient-to-br from-amber-500 to-amber-600 px-3 text-xs font-semibold text-white shadow-md shadow-amber-500/25 transition-all",
              "hover:-translate-y-px hover:shadow-lg disabled:translate-y-0 disabled:opacity-40 disabled:shadow-none",
            )}
          >
            <Send size={14} />
            Send all
          </button>
          <button
            type="button"
            onClick={() => void sendSkip()}
            disabled={busy}
            data-testid="turn-question-skip"
            title="Skip — the agent proceeds with best judgment (whole batch)"
            className="inline-flex h-9 shrink-0 items-center gap-1 rounded-xl border border-border/70 bg-background/60 px-2.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
          >
            <SkipForward size={13} />
            Skip
          </button>
          <span className="ml-auto text-[10px] font-medium text-amber-700/70 dark:text-amber-300/70">
            all-at-once
          </span>
        </div>
        {error && (
          <p data-testid="turn-question-error" className="mt-2 rounded-lg bg-rose-500/10 px-2.5 py-1.5 text-xs text-rose-600 dark:text-rose-300">
            {error}
          </p>
        )}
        <p className="mt-2 text-[11px] text-muted-foreground/80">
          No deadline — the turn holds until every question is answered or you skip.
        </p>
      </div>
    </div>
  );
}
