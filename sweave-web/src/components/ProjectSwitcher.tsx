/**
 * Project switcher (R4.1 step 2).
 *
 * A small dropdown of every project; the active project is
 * highlighted. The list comes from React Query (the
 * ``["projects"]`` key) so the same data drives the Sessions
 * tree below -- the user can pick a project and watch the
 * sessions refresh without a page reload.
 *
 * The "create project" entry is deferred to R4.4 (per the R4.1
 * amendment: foundation nav only). The dropdown is a pure
 * affordance for switching, not creating.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronDown, Folder } from "lucide-react";
import { api } from "@/api/client";
import { useApp } from "@/context/AppProvider";
import { cn } from "@/utils/cn";
import type { ProjectSummary } from "@/types";

export function ProjectSwitcher() {
  const { activeProject, setActiveProject, pushNotification } = useApp();
  const [open, setOpen] = useState(false);

  // R4.1 step 1b: the project list is invalidated by the
  // ``project.created`` / ``project.deleted`` WS events
  // (subscribed at the AppProvider level). The same key the
  // Sessions tree uses ensures both stay in sync.
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

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        data-testid="project-switcher-toggle"
        aria-haspopup="menu"
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-2 border border-border rounded text-sm hover:bg-muted"
      >
        <Folder size={14} className="text-muted-foreground" />
        <span className="truncate flex-1 text-left">
          {activeProject?.name ?? "Select project"}
        </span>
        <ChevronDown size={14} className="text-muted-foreground" />
      </button>
      {open && (
        <div
          data-testid="project-switcher-menu"
          role="menu"
          className="absolute left-0 right-0 mt-1 border border-border bg-card rounded shadow-lg z-40 max-h-64 overflow-y-auto"
        >
          {projects.length === 0 && (
            <div className="px-3 py-2 text-xs text-muted-foreground">
              No projects yet
            </div>
          )}
          {projects.map((p) => (
            <button
              key={p.name}
              type="button"
              role="menuitemradio"
              aria-checked={activeProject?.name === p.name}
              onClick={() => handleSelect(p.name)}
              data-testid={`project-option-${p.name}`}
              className={cn(
                "w-full flex items-center justify-between gap-2 px-3 py-2 text-sm text-left",
                activeProject?.name === p.name
                  ? "bg-primary/10 text-primary"
                  : "hover:bg-muted",
              )}
              title={p.path}
            >
              <span className="truncate">{p.name}</span>
              {activeProject?.name === p.name && <Check size={14} />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
