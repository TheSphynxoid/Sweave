/**
 * Chat reducer (M1.9 Step 2).
 *
 * The reducer is the canonical "what does the chat list look
 * like after N events" -- a single pure function. React's
 * ``useReducer`` wraps it; the test pins the merge semantics
 * without rendering. The shape mirrors the M1.8 streaming
 * invariant (a single streaming bubble per delegation id;
 * ``message.added`` replaces the bubble; ``chat.delta`` appends
 * in place) so a future render tree can plug in without
 * changing the merge logic.
 *
 * Streaming + React Query interplay (per the plan's risks):
 * ``chat.delta`` does NOT touch the query cache. Only
 * ``message.added`` (and the explicit /api/sessions/{id} invalidation
 * that the surface does on sendMessage success) refreshes the
 * persisted state. The two paths are clearly separated.
 */
import type { SessionMessage } from "@/types";

export type ChatEvent =
  | {
      kind: "delta";
      sessionId: string;
      delegationId: string;
      text: string;
    }
  | {
      kind: "messageAdded";
      sessionId: string;
      message: SessionMessage;
    }
  | {
      kind: "turnBoundary";
      sessionId: string;
    };

export interface ChatState {
  /** Persisted messages, in arrival order. */
  messages: SessionMessage[];
  /**
   * Live streaming text keyed by ``delegation_id``. A value of
   * ``undefined`` means "no active stream for that delegation".
   * Multiple delegations can stream concurrently (rare; the
   * chat loop serializes turns per session, but the reducer
   * doesn't enforce that -- it's the loop's job).
   */
  streamingByDelegation: Record<string, string>;
}

export function initialChatState(): ChatState {
  return { messages: [], streamingByDelegation: {} };
}

/** Pure reducer; the same function the React tree uses. */
export function applyChatEvent(state: ChatState, event: ChatEvent): ChatState {
  switch (event.kind) {
    case "delta": {
      const prev = state.streamingByDelegation[event.delegationId] ?? "";
      return {
        ...state,
        streamingByDelegation: {
          ...state.streamingByDelegation,
          [event.delegationId]: prev + event.text,
        },
      };
    }
    case "messageAdded": {
      const messages = [...state.messages, event.message];
      const meta = event.message.metadata ?? {};
      const delegationId = typeof meta.delegation_id === "string"
        ? (meta.delegation_id as string)
        : null;
      if (
        delegationId &&
        Object.prototype.hasOwnProperty.call(
          state.streamingByDelegation,
          delegationId,
        )
      ) {
        const { [delegationId]: _drop, ...rest } = state.streamingByDelegation;
        return { ...state, messages, streamingByDelegation: rest };
      }
      return { ...state, messages };
    }
    case "turnBoundary": {
      return { ...state, streamingByDelegation: {} };
    }
  }
}
