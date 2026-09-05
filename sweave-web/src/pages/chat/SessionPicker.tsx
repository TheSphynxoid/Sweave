/**
 * Session picker (M1.9 Step 2).
 *
 * The picker is a small popover listing the active project's
 * sessions, with a "new session" affordance. Closing the
 * funnel-leak the M1.9 self-hosting scene flagged (creating a
 * session required an API call outside the chat thread).
 *
 * Active session is taken from the AppProvider (the source of
 * truth). Selecting a session calls AppProvider.setActiveSession
 * (the server is updated, then the local cache is refreshed).
 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, ChevronDown, Check } from "lucide-react";
import { api } from "@/api/client";
import { useApp } from "@/context/AppProvider";
import { cn } from "@/utils/cn";
import type { SessionSummary } from "@/types";

export function SessionPicker() {
  const { activeProject, activeSession, setActiveSession, pushNotification } = useApp();
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const qc = useQueryClient();

  const { data: sessions = [] } = useQuery<SessionSummary[]>({
    queryKey: ["sessions", activeProject?.name ?? null],
    queryFn: () =>
      activeProject ? api.listSessions(activeProject.name) : Promise.resolve([]),
    enabled: !!activeProject,
  });

  const handleCreate = async () => {
    if (!activeProject) return;
    setCreating(true);
    try {
      const res = await api.createSession({
        name: name.trim() || "New session",
        project_name: activeProject.name,
      });
      await qc.invalidateQueries({
        queryKey: ["sessions", activeProject.name],
      });
      await setActiveSession(res.session.id);
      setName("");
      setOpen(false);
    } catch (err) {
      pushNotification("error", `Failed to create session: ${(err as Error).message}`);
    } finally {
      setCreating(false);
    }
  };

  if (!activeProject) {
    return (
      <div
        data-testid="session-picker-empty"
        className="text-xs text-muted-foreground px-2 py-1"
      >
        No active project
      </div>
    );
  }

  return (
    <div className="relative">
      <button
        type="button"
        data-testid="session-picker-toggle"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 px-3 py-1.5 border border-border rounded text-sm hover:bg-muted"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span className="truncate max-w-[24ch]">
          {activeSession?.name ?? "Select session"}
        </span>
        <ChevronDown size={14} />
      </button>
      {open && (
        <div
          data-testid="session-picker-menu"
          role="menu"
          className="absolute left-0 mt-1 w-72 border border-border bg-card rounded shadow-lg z-40"
        >
          <div className="p-2 border-b border-border flex gap-2">
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="New session name"
              data-testid="session-name-input"
              className="flex-1 px-2 py-1 text-sm border border-border rounded bg-input"
              onKeyDown={(e) => {
                if (e.key === "Enter") void handleCreate();
              }}
            />
            <button
              type="button"
              onClick={handleCreate}
              disabled={creating}
              data-testid="session-create-btn"
              className="px-2 py-1 text-xs bg-primary text-primary-foreground rounded disabled:opacity-50 flex items-center gap-1"
            >
              <Plus size={12} /> New
            </button>
          </div>
          <ul className="max-h-64 overflow-y-auto py-1">
            {sessions.length === 0 && (
              <li className="px-3 py-2 text-xs text-muted-foreground">
                No sessions yet.
              </li>
            )}
            {sessions.map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  role="menuitemradio"
                  aria-checked={activeSession?.id === s.id}
                  onClick={async () => {
                    await setActiveSession(s.id);
                    setOpen(false);
                  }}
                  data-testid={`session-option-${s.id}`}
                  className={cn(
                    "w-full flex items-center justify-between gap-2 px-3 py-2 text-sm text-left",
                    activeSession?.id === s.id
                      ? "bg-primary/10 text-primary"
                      : "hover:bg-muted",
                  )}
                >
                  <span className="truncate">{s.name}</span>
                  {activeSession?.id === s.id && <Check size={14} />}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
