/**
 * Typed app context (M1.9 Step 1).
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
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { api } from "@/api/client";
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
