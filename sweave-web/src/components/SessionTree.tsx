/**
 * Session tree (R4.1 step 2, R4.4 polish).
 *
 * Always-visible list of the active project's sessions with the active
 * one highlighted, inline create form, and a per-row delete
 * affordance. React-Query driven so WS ``session.created`` /
 * ``session.deleted`` events refresh it without polling.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Plus, MessageSquare, Trash2, Loader2 } from "lucide-react";
import { api } from "@/api/client";
import { useApp } from "@/context/AppProvider";
import { cn } from "@/utils/cn";
import type { SessionSummary } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ConfirmDeleteDialog } from "@/components/ConfirmDeleteDialog";

export function SessionTree() {
  const { activeProject, activeSession, setActiveSession, pushNotification } = useApp();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<{ id: string; name: string } | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

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
      await qc.invalidateQueries({ queryKey: ["sessions", activeProject?.name] });
      await setActiveSession(res.session.id);
      setName("");
    },
    onError: (err) => {
      pushNotification("error", `Failed to create session: ${(err as Error).message}`);
    },
    onSettled: () => setCreating(false),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteSession(id),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["sessions", activeProject?.name] });
      pushNotification("success", "Session deleted.");
    },
    onError: (err) => {
      pushNotification("error", `Failed to delete session: ${(err as Error).message}`);
    },
    onSettled: () => {
      setDeleting(null);
      setPendingDelete(null);
    },
  });

  const handleCreate = () => {
    const trimmed = name.trim() || "New session";
    setCreating(true);
    create.mutate(trimmed);
  };

  if (!activeProject) {
    return (
      <div data-testid="session-tree-empty" className="text-xs text-muted-foreground px-2 py-3">
        Select a project to see its sessions.
      </div>
    );
  }

  return (
    <div data-testid="session-tree" className="space-y-1">
      <div className="flex items-center justify-between px-2 py-1">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          Sessions
        </span>
        <span className="text-[10px] text-muted-foreground">{sessions.length}</span>
      </div>
      <ScrollArea className="max-h-56">
        <ul className="space-y-0.5 pr-2">
          {isLoading && (
            <li className="px-2 py-1 text-xs text-muted-foreground">Loading…</li>
          )}
          {!isLoading && sessions.length === 0 && (
            <li className="px-2 py-1 text-xs text-muted-foreground">No sessions yet</li>
          )}
          {sessions.map((s) => {
            const isActive = activeSession?.id === s.id;
            return (
              <li key={s.id} className="group relative">
                <button
                  type="button"
                  title={s.id}
                  onClick={async () => {
                    if (s.id === activeSession?.id) return;
                    try {
                      await setActiveSession(s.id);
                    } catch (err) {
                      pushNotification("error", `Failed to switch session: ${(err as Error).message}`);
                    }
                  }}
                  data-testid={`session-tree-item-${s.id}`}
                  className={cn(
                    "w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-sm text-left transition-colors",
                    isActive ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                >
                  <MessageSquare size={13} className="shrink-0" />
                  <span className="truncate flex-1">{s.name}</span>
                  {isActive && <Check size={13} className="shrink-0" />}
                </button>
                <button
                  type="button"
                  aria-label={`Delete ${s.name}`}
                  onClick={() => {
                    if (deleting) return;
                    // Confirmation first — deletion happens only in the
                    // dialog's confirm handler.
                    setPendingDelete({ id: s.id, name: s.name });
                  }}
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-destructive"
                >
                  {deleting === s.id ? (
                    <Loader2 size={13} className="animate-spin" />
                  ) : (
                    <Trash2 size={13} />
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      </ScrollArea>
      <Separator className="my-2" />
      <div data-testid="session-tree-create" className="px-1 space-y-1">
        <Input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !creating) handleCreate();
          }}
          placeholder="New session name"
          data-testid="session-tree-name-input"
          className="w-full text-xs h-8"
          disabled={creating}
        />
        <Button
          type="button"
          onClick={handleCreate}
          disabled={creating}
          data-testid="session-tree-create-btn"
          className="w-full gap-1 text-xs h-8"
        >
          {creating ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
          New session
        </Button>
      </div>
      <ConfirmDeleteDialog
        open={pendingDelete !== null}
        title="Delete session"
        body={
          pendingDelete
            ? `Delete "${pendingDelete.name}"? This cannot be undone.`
            : ""
        }
        pending={remove.isPending}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
        onConfirm={() => {
          if (!pendingDelete || remove.isPending) return;
          setDeleting(pendingDelete.id);
          remove.mutate(pendingDelete.id);
        }}
        testId="confirm-delete-session-dialog"
      />
    </div>
  );
}
