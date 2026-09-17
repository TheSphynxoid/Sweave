/**
 * ProjectSessionTree (R4.4 — unified project/session tree; consolidated 2026-09-13).
 *
 * The SOLE session-switching surface: each project is a parent row (click
 * to activate + expand) whose children are its sessions. Every row shows
 * a session count and a marker when the project holds the active
 * session, so a collapsed tree still orients. Session AND project
 * deletes both go through a confirm dialog (projects used to delete on
 * a single click — a misclick could destroy a project).
 *
 * Session lists are fetched per project at the row level (same
 * ["sessions", name] cache key the expanded view uses), so counts are
 * free and expanding never refetches.
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
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
import { ConfirmDeleteDialog } from "@/components/ConfirmDeleteDialog";
import type { ProjectSummary, SessionSummary } from "@/types";

export function ProjectSessionTree() {
  const { activeProject, activeSession, setActiveProject, setActiveSession, pushNotification } =
    useApp();
  // Highlight follows the VIEWED session (URL on chat routes),
  // not the server-global pointer (2026-09-17, cross-tab follow).
  const { sessionId: urlSessionId } = useParams<{ sessionId?: string }>();
  const viewedSessionId = urlSessionId ?? activeSession?.id ?? undefined;
  const setCreateOpen = useUIStore((s) => s.setCreateProjectOpen);
  const qc = useQueryClient();
  const navigate = useNavigate();

  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [creatingFor, setCreatingFor] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [deletingSession, setDeletingSession] = useState<string | null>(null);
  const [deletingProject, setDeletingProject] = useState<string | null>(null);
  const [pendingDeleteSession, setPendingDeleteSession] = useState<{
    id: string;
    name: string;
  } | null>(null);
  const [pendingDeleteProject, setPendingDeleteProject] = useState<string | null>(null);

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
      navigate(`/chat/${encodeURIComponent(res.session.id)}`);
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
    onSettled: () => {
      setDeletingSession(null);
      setPendingDeleteSession(null);
    },
  });

  const deleteProject = useMutation({
    mutationFn: (name: string) => api.deleteProject(name),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["projects"] });
      pushNotification("success", "Project deleted.");
    },
    onError: (err) => pushNotification("error", `Failed to delete project: ${(err as Error).message}`),
    onSettled: () => {
      setDeletingProject(null);
      setPendingDeleteProject(null);
    },
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
          {projects.map((p) => (
            <ProjectRow
              key={p.name}
              project={p}
              isActiveProject={activeProject?.name === p.name}
              holdsActiveSession={activeSession?.project_name === p.name}
              isOpen={expanded.has(p.name)}
              activeSessionId={viewedSessionId}
              creating={creatingFor === p.name}
              newName={newName}
              creatingPending={createSession.isPending}
              deletingSession={deletingSession}
              deletingProject={deletingProject === p.name}
              onToggle={() => toggle(p.name)}
              onActivate={() => activateProject(p.name)}
              onStartCreate={() => {
                setCreatingFor(p.name);
                setNewName("");
              }}
              onNewName={setNewName}
              onCreate={() => createSession.mutate(p.name)}
              onDeleteProject={() => {
                if (deletingProject) return;
                // Confirmation first — deletion happens only in the
                // dialog's confirm handler (previously a single click
                // deleted the project immediately).
                setPendingDeleteProject(p.name);
              }}
              onDeleteSession={(id, name) => {
                if (deletingSession) return;
                // Confirmation first — deletion happens only in the
                // dialog's confirm handler.
                setPendingDeleteSession({ id, name });
              }}
              onPickSession={async (id) => {
                if (id === viewedSessionId) return;
                try {
                  await setActiveSession(id);
                  navigate(`/chat/${encodeURIComponent(id)}`);
                } catch (err) {
                  pushNotification("error", `Failed to switch session: ${(err as Error).message}`);
                }
              }}
            />
          ))}
        </ul>
      </ScrollArea>
      <ConfirmDeleteDialog
        open={pendingDeleteSession !== null}
        title="Delete session"
        body={
          pendingDeleteSession
            ? `Delete "${pendingDeleteSession.name}"? This cannot be undone.`
            : ""
        }
        pending={deleteSession.isPending}
        onOpenChange={(open) => {
          if (!open) setPendingDeleteSession(null);
        }}
        onConfirm={() => {
          if (!pendingDeleteSession || deleteSession.isPending) return;
          setDeletingSession(pendingDeleteSession.id);
          deleteSession.mutate(pendingDeleteSession.id);
        }}
        testId="confirm-delete-session-dialog"
      />
      <ConfirmDeleteDialog
        open={pendingDeleteProject !== null}
        title="Delete project"
        body={
          pendingDeleteProject
            ? `Delete "${pendingDeleteProject}" and all its sessions? This cannot be undone.`
            : ""
        }
        pending={deleteProject.isPending}
        onOpenChange={(open) => {
          if (!open) setPendingDeleteProject(null);
        }}
        onConfirm={() => {
          if (!pendingDeleteProject || deleteProject.isPending) return;
          setDeletingProject(pendingDeleteProject);
          deleteProject.mutate(pendingDeleteProject);
        }}
        testId="confirm-delete-project-dialog"
      />
    </div>
  );
}

function ProjectRow({
  project,
  isActiveProject,
  holdsActiveSession,
  isOpen,
  activeSessionId,
  creating,
  newName,
  creatingPending,
  deletingSession,
  deletingProject,
  onToggle,
  onActivate,
  onStartCreate,
  onNewName,
  onCreate,
  onDeleteProject,
  onDeleteSession,
  onPickSession,
}: {
  project: ProjectSummary;
  isActiveProject: boolean;
  holdsActiveSession: boolean;
  isOpen: boolean;
  activeSessionId?: string;
  creating: boolean;
  newName: string;
  creatingPending: boolean;
  deletingSession: string | null;
  deletingProject: boolean;
  onToggle: () => void;
  onActivate: () => void;
  onStartCreate: () => void;
  onNewName: (v: string) => void;
  onCreate: () => void;
  onDeleteProject: () => void;
  onDeleteSession: (id: string, name: string) => void;
  onPickSession: (id: string) => void;
}) {
  // Fetched for every row (not just expanded ones) so the count badge
  // and the expanded view share one ["sessions", name] cache entry —
  // expanding never refetches.
  const { data: sessions = [], isLoading } = useQuery({
    queryKey: ["sessions", project.name],
    queryFn: () => api.listSessions(project.name),
  });

  return (
    <li>
      <div
        data-testid={`project-row-${project.name}`}
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
          onClick={onToggle}
          className="grid h-5 w-5 shrink-0 place-items-center rounded text-muted-foreground hover:text-foreground"
        >
          <ChevronRight
            size={14}
            className={cn("transition-transform", isOpen && "rotate-90")}
          />
        </button>
        <button
          type="button"
          onClick={onActivate}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          {isOpen ? (
            <FolderOpen size={14} className="shrink-0" />
          ) : (
            <Folder size={14} className="shrink-0" />
          )}
          <span className="truncate">{project.name}</span>
        </button>
        {holdsActiveSession && (
          <span
            data-testid={`project-active-dot-${project.name}`}
            title="Contains the active session"
            className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500"
          />
        )}
        {!isLoading && (
          <span
            data-testid={`project-session-count-${project.name}`}
            title={`${sessions.length} session${sessions.length === 1 ? "" : "s"}`}
            className="shrink-0 rounded-full bg-muted px-1.5 py-px text-[10px] tabular-nums text-muted-foreground"
          >
            {sessions.length}
          </span>
        )}
        {!isActiveProject && (
          <button
            type="button"
            aria-label={`Delete ${project.name}`}
            onClick={onDeleteProject}
            className="grid h-5 w-5 shrink-0 place-items-center rounded text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
          >
            {deletingProject ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <Trash2 size={13} />
            )}
          </button>
        )}
      </div>

      {isOpen && (
        <ProjectSessions
          sessions={sessions}
          isLoading={isLoading}
          activeSessionId={activeSessionId}
          creating={creating}
          newName={newName}
          onNewName={onNewName}
          onStartCreate={onStartCreate}
          onCreate={onCreate}
          creatingPending={creatingPending}
          deletingSession={deletingSession}
          onDeleteSession={onDeleteSession}
          onPickSession={onPickSession}
        />
      )}
    </li>
  );
}

function ProjectSessions({
  sessions,
  isLoading,
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
  sessions: SessionSummary[];
  isLoading: boolean;
  activeSessionId?: string;
  creating: boolean;
  newName: string;
  onNewName: (v: string) => void;
  onStartCreate: () => void;
  onCreate: () => void;
  creatingPending: boolean;
  deletingSession: string | null;
  onDeleteSession: (id: string, name: string) => void;
  onPickSession: (id: string) => void;
}) {
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
              onClick={() => onDeleteSession(s.id, s.name)}
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
