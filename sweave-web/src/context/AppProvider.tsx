/**
 * Typed app context (M1.9 Step 1, R4.1 Step 2).
 *
 * The previous AppProvider was v1-era (``any`` types; in-memory
 * state mutated imperatively). This rewrite uses the v2 surface
 * and is wired into React Query for the heavy read paths
 * (delegations, sessions, specialists). The context only carries
 * the cheap cross-cutting state: the active project / session
 * pointers + a tiny notification list.
 *
 * Active project / session are cached from the server's
 * ``/projects/active`` + ``/sessions/active`` endpoints. The
 * context methods set the active project/session via the API
 * (the server is the source of truth) and update the local cache.
 *
 * R4.1 step 2: the provider now subscribes to the step-1b WS
 * events (``project.created`` / ``project.deleted`` /
 * ``session.created`` / ``session.deleted`` /
 * ``active_session.changed``) so the QueryClient's project +
 * session caches refresh without polling. Invalidations are
 * scoped: a session event only invalidates the project the
 * session belongs to; a project event invalidates the project
 * list + that project's session list.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useWS } from "./WSProvider";
import { invalidationsForEvent } from "./wsInvalidations";
import type { ProjectSummary, SessionSummary } from "@/types";

export type NotificationKind = "info" | "success" | "warning" | "error";

export interface Notification {
  id: number;
  kind: NotificationKind;
  message: string;
}

interface AppContextValue {
  // Cross-cutting state
  activeProject: ProjectSummary | null;
  activeSession: SessionSummary | null;
  notifications: Notification[];

  // Actions
  setActiveProject: (name: string) => Promise<void>;
  setActiveSession: (sessionId: string) => Promise<void>;
  pushNotification: (kind: NotificationKind, message: string) => void;
  dismissNotification: (id: number) => void;
}

const AppContext = createContext<AppContextValue | null>(null);

const NOTIFICATION_TTL_MS = 5_000;

export function AppProvider({ children }: { children: ReactNode }) {
  const [activeProject, setActiveProject] = useState<ProjectSummary | null>(null);
  const [activeSession, setActiveSession] = useState<SessionSummary | null>(null);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const qc = useQueryClient();
  const { subscribe } = useWS();

  // Load active project + session on mount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [project, session] = await Promise.all([
          api.getActiveProject(),
          api.getActiveSession(),
        ]);
        if (cancelled) return;
        setActiveProject(project);
        setActiveSession(session);
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error("AppProvider: initial load failed:", err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // R4.1 step 2: WS-driven cache invalidation. Each event
  // invalidates the smallest set of query keys that need to
  // refresh. The active session change refreshes the
  // /sessions/active fetch (the AppProvider's own state).
  //
  // 2026-09-17: NO local setActiveSession on the broadcast.
  // The broadcast is server-global (one pointer for all tabs);
  // the viewed session is URL-local (/chat/:sessionId), so
  // following it would yank every tab to the newest selection.
  // Tabs pick the broadcast up lazily (stale active pill
  // refreshes on next navigation/action).
  useEffect(() => {
    const unsubs: Array<() => void> = [];

    // Subscribe to the five known events. The invalidation
    // mapping lives in ``wsInvalidations.ts`` so it's a pure,
    // testable function; the AppProvider is just the wiring.
    for (const eventName of [
      "project.created",
      "project.deleted",
      "session.created",
      "session.deleted",
      "active_session.changed",
    ] as const) {
      unsubs.push(
        subscribe(eventName, (env) => {
          for (const key of invalidationsForEvent(env)) {
            qc.invalidateQueries({ queryKey: key });
          }
          // The broadcast is observed, never followed: refresh
          // the query cache so the pill reads current on next
          // navigation, but leave this tab's local viewed session
          // alone (see above).
          if (eventName === "active_session.changed") {
            void qc.invalidateQueries({ queryKey: ["active-session"] });
          }
          // R4.4: if the active project was deleted, drop both the
          // active project + session pointers so the UI falls back to
          // the empty "create a project" state instead of showing a
          // stale, now-nonexistent project.
          if (eventName === "project.deleted") {
            const deletedName = (env.data as { name?: string }).name;
            if (deletedName && activeProject?.name === deletedName) {
              setActiveProject(null);
              setActiveSession(null);
            }
          }
        }),
      );
    }

    return () => {
      for (const u of unsubs) u();
    };
  }, [qc, subscribe]);

  const pushNotification = useCallback(
    (kind: NotificationKind, message: string) => {
      const id = Date.now() + Math.floor(Math.random() * 1000);
      setNotifications((prev) => [...prev, { id, kind, message }]);
      window.setTimeout(() => {
        setNotifications((prev) => prev.filter((n) => n.id !== id));
      }, NOTIFICATION_TTL_MS);
    },
    [],
  );

  const dismissNotification = useCallback((id: number) => {
    setNotifications((prev) => prev.filter((n) => n.id !== id));
  }, []);

  const handleSetActiveProject = useCallback(
    async (name: string) => {
      try {
        await api.setActiveProject(name);
        const refreshed = await api.getActiveProject();
        setActiveProject(refreshed);
        // Switching projects invalidates the active session.
        setActiveSession(null);
        // The new project's session list is fetched lazily by
        // the SessionTree's query (keyed on activeProject.name).
        // No explicit invalidation needed; the query refetches
        // on key change.
      } catch (err) {
        pushNotification("error", `Failed to activate project: ${name}`);
        throw err;
      }
    },
    [pushNotification],
  );

  const handleSetActiveSession = useCallback(
    async (sessionId: string) => {
      try {
        await api.setActiveSession(sessionId);
        const refreshed = await api.getActiveSession();
        setActiveSession(refreshed);
      } catch (err) {
        pushNotification("error", `Failed to activate session`);
        throw err;
      }
    },
    [pushNotification],
  );

  const value: AppContextValue = {
    activeProject,
    activeSession,
    notifications,
    setActiveProject: handleSetActiveProject,
    setActiveSession: handleSetActiveSession,
    pushNotification,
    dismissNotification,
  };

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) {
    throw new Error("useApp must be used within an AppProvider");
  }
  return ctx;
}
