/**
 * WebSocket provider (M1.9 Step 1).
 *
 * The v1 vanilla app.js opened a single WebSocket and dispatched
 * events via ``data.event`` keys (an inconsistent vocabulary; the
 * data envelope was sometimes ``{event, data, ts}``, sometimes just
 * a flat payload). The v2 WS envelope is locked (see
 * ``sweave/web/events.py`` docstring): every event is
 * ``{event, data, timestamp}``.
 *
 * The provider:
 *   1. Opens a single WS to ``<host>/ws`` on mount.
 *   2. Auto-reconnects with exponential backoff (1s -> 30s cap).
 *   3. Dispatches every envelope to a topic map (event name -> Set
 *      of handlers). React Query doesn't subscribe directly here;
 *      the provider exposes a ``subscribe(event, handler)`` API
 *      that the chat / children / detail surfaces use to patch
 *      their local state on incoming events.
 *
 * React StrictMode double-mounts in dev: the provider's connect
 * path is idempotent (a single ref holds the WebSocket; a re-mount
 * in StrictMode is a no-op on the existing connection). Disconnect
 * only happens on the provider unmount, which StrictMode does
 * call -- the close is safe because reconnect re-opens it.
 *
 * Streaming + cache: chat deltas bypass the cache entirely (the
 * chat surface patches a single streaming bubble in place -- the
 * M1.8 no-rerender invariant). ``message.added`` invalidates the
 * session messages query so the persisted message is reflected
 * when the user reloads the view.
 */
import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { WSEnvelope, WSHandler } from "@/types";

export type ConnectionState = "connecting" | "open" | "reconnecting" | "closed";

interface WSContextValue {
  state: ConnectionState;
  subscribe: (event: string, handler: WSHandler) => () => void;
  send: (data: unknown) => void;
}

const WSContext = createContext<WSContextValue | null>(null);

function wsUrl(): string {
  if (typeof window === "undefined") return "ws://localhost:8100/ws";
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  // Same host as the page; port 8100 is the canonical sweave
  // server. The vite proxy in dev forwards both /api and /ws.
  return `${proto}//${window.location.host}/ws`;
}

const RECONNECT_BASE_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;

export function WSProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ConnectionState>("connecting");
  // The handlers map is held in a ref so a re-render doesn't
  // tear down + re-create the WebSocket (the M1.8 invariant:
  // keep the connection stable across re-renders).
  const handlersRef = useRef<Map<string, Set<WSHandler>>>(new Map());
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptRef = useRef<number>(0);
  const reconnectTimerRef = useRef<number | null>(null);
  const isUnmountedRef = useRef<boolean>(false);

  useEffect(() => {
    isUnmountedRef.current = false;
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      if (
        wsRef.current &&
        (wsRef.current.readyState === WebSocket.OPEN ||
          wsRef.current.readyState === WebSocket.CONNECTING)
      ) {
        return; // already open / connecting
      }
      setState(reconnectAttemptRef.current === 0 ? "connecting" : "reconnecting");
      let ws: WebSocket;
      try {
        ws = new WebSocket(wsUrl());
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error("WSProvider: failed to construct WebSocket:", err);
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectAttemptRef.current = 0;
        setState("open");
      };

      ws.onmessage = (ev: MessageEvent<string>) => {
        let parsed: WSEnvelope | null = null;
        try {
          parsed = JSON.parse(ev.data) as WSEnvelope;
        } catch {
          return; // malformed envelope; ignore
        }
        if (!parsed || typeof parsed !== "object" || !("event" in parsed)) {
          return;
        }
        const set = handlersRef.current.get(parsed.event);
        if (set) {
          for (const handler of set) {
            try {
              handler(parsed);
            } catch (err) {
              // A misbehaving handler must not poison the bus.
              // eslint-disable-next-line no-console
              console.error("WSProvider: handler error:", err);
            }
          }
        }
      };

      ws.onclose = () => {
        if (cancelled) return;
        scheduleReconnect();
      };

      ws.onerror = () => {
        // onclose will fire after onerror; let the close handler
        // own the reconnect. Just log here.
        // eslint-disable-next-line no-console
        console.warn("WSProvider: WebSocket error");
      };
    };

    const scheduleReconnect = () => {
      if (cancelled) return;
      const attempt = reconnectAttemptRef.current + 1;
      reconnectAttemptRef.current = attempt;
      setState("reconnecting");
      const delay = Math.min(
        RECONNECT_BASE_MS * 2 ** (attempt - 1),
        RECONNECT_MAX_MS,
      );
      reconnectTimerRef.current = window.setTimeout(() => {
        reconnectTimerRef.current = null;
        connect();
      }, delay);
    };

    connect();

    return () => {
      cancelled = true;
      isUnmountedRef.current = true;
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (wsRef.current) {
        try {
          wsRef.current.close();
        } catch {
          // ignore
        }
        wsRef.current = null;
      }
      setState("closed");
    };
  }, []);

  const subscribe = (event: string, handler: WSHandler): (() => void) => {
    let set = handlersRef.current.get(event);
    if (!set) {
      set = new Set();
      handlersRef.current.set(event, set);
    }
    set.add(handler);
    return () => {
      const s = handlersRef.current.get(event);
      if (s) {
        s.delete(handler);
        if (s.size === 0) handlersRef.current.delete(event);
      }
    };
  };

  const send = (data: unknown) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify(data));
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error("WSProvider: send failed:", err);
      }
    }
  };

  return (
    <WSContext.Provider value={{ state, subscribe, send }}>
      {children}
    </WSContext.Provider>
  );
}

export function useWS(): WSContextValue {
  const ctx = useContext(WSContext);
  if (!ctx) {
    throw new Error("useWS must be used within a WSProvider");
  }
  return ctx;
}
