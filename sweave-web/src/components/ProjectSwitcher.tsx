/**
 * Project switcher (R4.1 step 2, R4.4).
 *
 * A dropdown of every project with the active one highlighted, plus a
 * "Create project" entry (the R4.4 unblock) and an inline delete
 * affordance. Project list is React-Query driven (``["projects"]``) so
 * the WS ``project.created`` / ``project.deleted`` events keep it fresh.
 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, Folder, FolderPlus, Trash2, Loader2 } from "lucide-react";
import { api } from "@/api/client";
import { useApp } from "@/context/AppProvider";
import { useUIStore } from "@/store/ui";
import { cn } from "@/utils/cn";
import type { ProjectSummary } from "@/types";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";

export function ProjectSwitcher() {
  const { activeProject, setActiveProject, pushNotification } = useApp();
  const setCreateOpen = useUIStore((s) => s.setCreateProjectOpen);
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);

  const { data: projects = [] } = useQuery<ProjectSummary[]>({
    queryKey: ["projects"],
    queryFn: () => api.listProjects(),
  });

  const handleSelect = async (name: string) => {
    if (name === activeProject?.name) {
      setOpen(false);
      return;
    }
    try {
      await setActiveProject(name);
      setOpen(false);
    } catch (err) {
      pushNotification("error", `Failed to switch project: ${(err as Error).message}`);
    }
  };

  const handleDelete = async (name: string) => {
    if (deleting) return;
    setDeleting(name);
    try {
      await api.deleteProject(name);
      await qc.invalidateQueries({ queryKey: ["projects"] });
      pushNotification("success", `Project "${name}" deleted.`);
    } catch (err) {
      pushNotification("error", `Failed to delete project: ${(err as Error).message}`);
    } finally {
      setDeleting(null);
    }
  };

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          data-testid="project-switcher-toggle"
          className="w-full flex items-center gap-2 px-3 py-2 border border-border rounded-md text-sm bg-background hover:bg-muted transition-colors"
        >
          <Folder size={14} className="text-muted-foreground shrink-0" />
          <span className="truncate flex-1 text-left">
            {activeProject?.name ?? "Select project"}
          </span>
          <ChevronDown size={14} className="text-muted-foreground shrink-0" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-full min-w-[15rem] max-h-72 overflow-y-auto">
        <DropdownMenuLabel className="flex items-center justify-between">
          <span>Projects</span>
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              setCreateOpen(true);
            }}
            className="flex items-center gap-1 text-[11px] text-primary hover:underline"
          >
            <FolderPlus size={12} /> New
          </button>
        </DropdownMenuLabel>
        {projects.length === 0 && (
          <DropdownMenuItem disabled className="px-3 py-2 text-xs text-muted-foreground">
            No projects yet
          </DropdownMenuItem>
        )}
        {projects.map((p) => {
          const isActive = activeProject?.name === p.name;
          return (
            <DropdownMenuItem
              key={p.name}
              onSelect={(e) => {
                e.preventDefault();
                void handleSelect(p.name);
              }}
              className={cn(
                "group flex items-center gap-2 px-3 py-2 text-sm cursor-pointer",
                isActive ? "bg-primary/10 text-primary" : "",
              )}
              title={p.path}
            >
              <span className="truncate flex-1">{p.name}</span>
              {isActive && <Check size={14} className="shrink-0" />}
              <button
                type="button"
                aria-label={`Delete ${p.name}`}
                onClick={(e) => {
                  e.stopPropagation();
                  void handleDelete(p.name);
                }}
                className="opacity-0 group-hover:opacity-100 hover:text-destructive data-[open]:opacity-60 transition-opacity"
                disabled={!!deleting}
              >
                {deleting === p.name ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <Trash2 size={13} />
                )}
              </button>
            </DropdownMenuItem>
          );
        })}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={(e) => {
            e.preventDefault();
            setOpen(false);
            setCreateOpen(true);
          }}
          className="flex items-center gap-2 px-3 py-2 text-sm text-primary cursor-pointer"
        >
          <FolderPlus size={14} />
          Create new project…
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
