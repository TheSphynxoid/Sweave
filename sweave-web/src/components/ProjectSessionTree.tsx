/**
 * ProjectSessionTree (R4.4 — unified project/session tree).
 *
 * A single collapsible tree like other agent UIs: each project is a parent
 * row (click to activate + expand) whose children are its sessions. The
 * active session is highlighted; sessions can be created inline or deleted
 * on hover. A "+" in the header opens the create-project dialog.
 *
 * Replaces the old separate ProjectSwitcher + SessionTree.
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronRight,
  Folder,
  FolderOpen,
  MessageSquare,
  Plus,
  Trash2,
  Check,
  Loader2,
  FolderPlus,
} from "lucide-react";
import { api } from "@/api/client";
import { useApp } from "@/context/AppProvider";
import { useUIStore } from "@/store/ui";
import { cn } from "@/utils/cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";

export function ProjectSessionTree() {
  const { activeProject, activeSession, setActiveProject, setActiveSession, pushNotification } =
    useApp();
  const setCreateOpen = useUIStore((s) => s.setCreateProjectOpen);
  const qc = useQueryClient();
  const navigate = useNavigate();

  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [creatingFor, setCreatingFor] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [deletingSession, setDeletingSession] = useState<string | null>(null);
  const [deletingProject, setDeletingProject] = useState<string | null>(null);

  const { data: projects = [] } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.listProjects(),
  });

  useEffect(() => {
    if (activeProject && !expanded.has(activeProject.name)) {
      setExpanded((prev) => new Set(prev).add(activeProject.name));
    }
  }, [activeProject, expanded]);

  const toggle = (name: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(name) ? next.delete(name) : next.add(name);
      return next;
    });

  const activateProject = async (name: string) => {
    try {
      await setActiveProject(name);
      setExpanded((prev) => new Set(prev).add(name));
    } catch (err) {
      pushNotification("error", `Failed to switch project: ${(err as Error).message}`);
    }
  };

  const createSession = useMutation({
    mutationFn: (projectName: string) =>
      api.createSession({ name: newName.trim() || "New session", project_name: projectName }),
    onSuccess: async (res, projectName) => {
      await qc.invalidateQueries({ queryKey: ["sessions", projectName] });
      await setActiveSession(res.session.id);
      setNewName("");
      setCreatingFor(null);
      navigate("/chat");
    },
    onError: (err) => pushNotification("error", `Failed to create session: ${(err as Error).message}`),
  });

  const deleteSession = useMutation({
    mutationFn: (id: string) => api.deleteSession(id),
    onSuccess: async () => {
      if (activeProject) await qc.invalidateQueries({ queryKey: ["sessions", activeProject.name] });
      pushNotification("success", "Session deleted.");
    },
    onError: (err) => pushNotification("error", `Failed to delete session: ${(err as Error).message}`),
    onSettled: () => setDeletingSession(null),
  });

  const deleteProject = useMutation({
    mutationFn: (name: string) => api.deleteProject(name),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["projects"] });
      pushNotification("success", "Project deleted.");
    },
    onError: (err) => pushNotification("error", `Failed to delete project: ${(err as Error).message}`),
    onSettled: () => setDeletingProject(null),
  });

  return (
    <div data-testid="project-session-tree" className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between px-2 pb-1 pt-0">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          Projects
        </span>
        <button
          type="button"
          aria-label="Create project"
          onClick={() => setCreateOpen(true)}
          className="grid h-5 w-5 place-items-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <FolderPlus size={13} />
        </button>
      </div>

      {/* Scrolls within ALL leftover sidebar height (the wrapper in the
          Sidebar is the bounded flex parent; the old max-h-[40vh] cap
          clipped long project lists and pushed the nav out of view). */}
      <ScrollArea className="min-h-0 flex-1">
        <ul className="space-y-0.5 pr-1 pb-1">
          {projects.length === 0 && (
            <li className="px-2 py-1 text-xs text-muted-foreground">
              No projects yet.
            </li>
          )}
          {projects.map((p) => {
            const isActiveProject = activeProject?.name === p.name;
            const isOpen = expanded.has(p.name);
            return (
              <li key={p.name}>
                <div
                  className={cn(
                    "group relative flex items-center gap-1.5 rounded-md pr-1 py-1.5 pl-1 text-sm transition-colors",
                    isActiveProject
                      ? "bg-primary/10 text-primary"
                      : "text-foreground hover:bg-muted",
                  )}
                >
                  <button
                    type="button"
                    aria-label={isOpen ? "Collapse" : "Expand"}
                    onClick={() => toggle(p.name)}
                    className="grid h-5 w-5 shrink-0 place-items-center rounded text-muted-foreground hover:text-foreground"
                  >
                    <ChevronRight
                      size={14}
                      className={cn("transition-transform", isOpen && "rotate-90")}
                    />
                  </button>
                  <button
                    type="button"
                    onClick={() => activateProject(p.name)}
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                  >
                    {isOpen ? (
                      <FolderOpen size={14} className="shrink-0" />
                    ) : (
                      <Folder size={14} className="shrink-0" />
                    )}
                    <span className="truncate">{p.name}</span>
                  </button>
                  {!isActiveProject && (
                    <button
                      type="button"
                      aria-label={`Delete ${p.name}`}
                      onClick={() => {
                        if (deletingProject) return;
                        setDeletingProject(p.name);
                        deleteProject.mutate(p.name);
                      }}
                      className="grid h-5 w-5 shrink-0 place-items-center rounded text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
                    >
                      {deletingProject === p.name ? (
                        <Loader2 size={13} className="animate-spin" />
                      ) : (
                        <Trash2 size={13} />
                      )}
                    </button>
                  )}
                </div>

                {isOpen && (
                  <ProjectSessions
                    projectName={p.name}
                    activeSessionId={activeSession?.id}
                    creating={creatingFor === p.name}
                    newName={newName}
                    onNewName={setNewName}
                    onStartCreate={() => {
                      setCreatingFor(p.name);
                      setNewName("");
                    }}
                    onCreate={() => createSession.mutate(p.name)}
                    creatingPending={createSession.isPending}
                    deletingSession={deletingSession}
                    onDeleteSession={(id) => {
                      if (deletingSession) return;
                      setDeletingSession(id);
                      deleteSession.mutate(id);
                    }}
                    onPickSession={async (id) => {
                      if (id === activeSession?.id) return;
                      try {
                        await setActiveSession(id);
                        navigate("/chat");
                      } catch (err) {
                        pushNotification("error", `Failed to switch session: ${(err as Error).message}`);
                      }
                    }}
                  />
                )}
              </li>
            );
          })}
        </ul>
      </ScrollArea>
    </div>
  );
}

function ProjectSessions({
  projectName,
  activeSessionId,
  creating,
  newName,
  onNewName,
  onStartCreate,
  onCreate,
  creatingPending,
  deletingSession,
  onDeleteSession,
  onPickSession,
}: {
  projectName: string;
  activeSessionId?: string;
  creating: boolean;
  newName: string;
  onNewName: (v: string) => void;
  onStartCreate: () => void;
  onCreate: () => void;
  creatingPending: boolean;
  deletingSession: string | null;
  onDeleteSession: (id: string) => void;
  onPickSession: (id: string) => void;
}) {
  const { data: sessions = [], isLoading } = useQuery({
    queryKey: ["sessions", projectName],
    queryFn: () => api.listSessions(projectName),
    enabled: true,
  });

  // Keep the active session in view inside the sidebar's scroll area —
  // activating a session deep in the tree must not leave its row (and
  // with it the check / delete affordances) below the fold.
  const activeRowRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    activeRowRef.current?.scrollIntoView({ block: "nearest" });
  }, [activeSessionId, sessions.length]);

  return (
    <div className="mt-0.5 space-y-0.5 pb-1">
      {isLoading && (
        <p className="py-1 pl-9 text-xs text-muted-foreground">Loading…</p>
      )}
      {!isLoading && sessions.length === 0 && (
        <p className="py-1 pl-9 text-xs text-muted-foreground">No sessions yet</p>
      )}
      {sessions.map((s) => {
        const isActive = activeSessionId === s.id;
        return (
          <div key={s.id} className="group relative">
            <button
              ref={isActive ? activeRowRef : undefined}
              type="button"
              onClick={() => onPickSession(s.id)}
              data-testid={`session-tree-item-${s.id}`}
              className={cn(
                "flex w-full items-center gap-2 rounded-md py-1.5 pl-9 pr-8 text-sm text-left transition-colors",
                isActive
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <MessageSquare size={13} className="shrink-0" />
              <span className="truncate flex-1">{s.name}</span>
              {isActive && (
                <Check
                  size={13}
                  className="shrink-0 transition-opacity group-hover:opacity-0"
                />
              )}
            </button>
            <button
              type="button"
              aria-label={`Delete ${s.name}`}
              onClick={() => onDeleteSession(s.id)}
              className="absolute right-1.5 top-1/2 grid h-5 w-5 -translate-y-1/2 place-items-center rounded text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
            >
              {deletingSession === s.id ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Trash2 size={13} />
              )}
            </button>
          </div>
        );
      })}

      {creating ? (
        <div className="space-y-1 pl-9 pr-2 pt-1">
          <Input
            autoFocus
            value={newName}
            onChange={(e) => onNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !creatingPending) onCreate();
              if (e.key === "Escape") onStartCreate();
            }}
            placeholder="Session name"
            className="h-8 text-xs"
            disabled={creatingPending}
          />
          <Button
            onClick={onCreate}
            disabled={creatingPending}
            className="h-8 w-full gap-1 text-xs"
          >
            {creatingPending ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
            Create
          </Button>
        </div>
      ) : (
        <button
          type="button"
          onClick={onStartCreate}
          className="flex w-full items-center gap-2 rounded-md py-1.5 pl-9 pr-2 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Plus size={13} className="shrink-0" />
          New session
        </button>
      )}
    </div>
  );
}
