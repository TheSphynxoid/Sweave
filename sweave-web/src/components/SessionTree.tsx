/**
 * Session tree (R4.1 step 2).
 *
 * The always-visible session list for the active project. The
 * active session is highlighted; clicking another session
 * switches to it via the AppProvider. The inline create-session
 * form sits at the bottom of the tree (the M1.9 funnel-leak
 * "you have to leave the chat to make a session" is closed by
 * surfacing the form right where the sessions live).
 *
 * The data is React-Query driven so the step-1b WS events
 * (``session.created`` / ``session.deleted`` /
 * ``active_session.changed``) refresh the tree without polling.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Plus, MessageSquare } from "lucide-react";
import { api } from "@/api/client";
import { useApp } from "@/context/AppProvider";
import { cn } from "@/utils/cn";
import type { SessionSummary } from "@/types";

export function SessionTree() {
  const { activeProject, activeSession, setActiveSession, pushNotification } =
    useApp();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);

  const { data: sessions = [], isLoading } = useQuery<SessionSummary[]>({
    queryKey: ["sessions", activeProject?.name ?? null],
    queryFn: () =>
      activeProject ? api.listSessions(activeProject.name) : Promise.resolve([]),
    enabled: !!activeProject,
  });

  const create = useMutation({
    mutationFn: (sessionName: string) => {
      if (!activeProject) throw new Error("No active project");
      return api.createSession({
        name: sessionName,
        project_name: activeProject.name,
      });
    },
    onSuccess: async (res) => {
      // The step-1b ``session.created`` WS event also invalidates
      // this key; the explicit invalidation is the deterministic
      // path (in case the WS event arrives after the navigation).
      await qc.invalidateQueries({
        queryKey: ["sessions", activeProject?.name],
      });
      await setActiveSession(res.session.id);
      setName("");
    },
    onError: (err) => {
      pushNotification(
        "error",
        `Failed to create session: ${(err as Error).message}`,
      );
    },
    onSettled: () => {
      setCreating(false);
    },
  });

  const handleCreate = () => {
    const trimmed = name.trim() || "New session";
    setCreating(true);
    create.mutate(trimmed);
  };

  if (!activeProject) {
    return (
      <div
        data-testid="session-tree-empty"
        className="text-xs text-muted-foreground px-2 py-3"
      >
        Select a project to see its sessions.
      </div>
    );
  }

  return (
    <div data-testid="session-tree" className="space-y-1">
      <div className="flex items-center justify-between px-2 py-1">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
          Sessions
        </span>
        <span className="text-[10px] text-muted-foreground">{sessions.length}</span>
      </div>
      <ul className="space-y-0.5 max-h-64 overflow-y-auto">
        {isLoading && (
          <li className="px-2 py-1 text-xs text-muted-foreground">
            Loading…
          </li>
        )}
        {!isLoading && sessions.length === 0 && (
          <li className="px-2 py-1 text-xs text-muted-foreground">
            No sessions yet
          </li>
        )}
        {sessions.map((s) => (
          <li key={s.id}>
            <button
              type="button"
              onClick={async () => {
                if (s.id === activeSession?.id) return;
                try {
                  await setActiveSession(s.id);
                } catch (err) {
                  pushNotification(
                    "error",
                    `Failed to switch session: ${(err as Error).message}`,
                  );
                }
              }}
              data-testid={`session-tree-item-${s.id}`}
              className={cn(
                "w-full flex items-center gap-2 px-2 py-1.5 rounded text-xs text-left",
                activeSession?.id === s.id
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <MessageSquare size={12} className="shrink-0" />
              <span className="truncate flex-1">{s.name}</span>
              {activeSession?.id === s.id && <Check size={12} />}
            </button>
          </li>
        ))}
      </ul>
      <div
        data-testid="session-tree-create"
        className="border-t border-border pt-2 mt-1 px-1 space-y-1"
      >
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !creating) handleCreate();
          }}
          placeholder="New session name"
          data-testid="session-tree-name-input"
          className="w-full px-2 py-1 text-xs border border-border rounded bg-input"
        />
        <button
          type="button"
          onClick={handleCreate}
          disabled={creating}
          data-testid="session-tree-create-btn"
          className="w-full flex items-center justify-center gap-1 px-2 py-1 text-xs bg-primary text-primary-foreground rounded disabled:opacity-50"
        >
          <Plus size={12} />
          New session
        </button>
      </div>
    </div>
  );
}
