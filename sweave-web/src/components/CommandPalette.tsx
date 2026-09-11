"use client";

import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  MessageSquare,
  Network,
  ListTodo,
  Brain,
  Users,
  Settings,
  FolderPlus,
  Plus,
  Folder,
  Palette,
} from "lucide-react";
import { api } from "@/api/client";
import { useApp } from "@/context/AppProvider";
import { useUIStore } from "@/store/ui";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "@/components/ui/command";
import type { ProjectSummary } from "@/types";
import { PRESETS, type PresetName } from "@/lib/theme";
import { applyThemeToDocument, saveActiveTheme, loadActiveTheme } from "@/lib/theme";

export function CommandPalette() {
  const open = useUIStore((s) => s.commandOpen);
  const setOpen = useUIStore((s) => s.setCommandOpen);
  const setCreateOpen = useUIStore((s) => s.setCreateProjectOpen);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { activeProject, setActiveProject, setActiveSession, pushNotification } = useApp();

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen(!open);
      }
    };
    document.addEventListener("keydown", down);
    return () => document.removeEventListener("keydown", down);
  }, [open, setOpen]);

  const { data: projects = [] } = useQuery<ProjectSummary[]>({
    queryKey: ["projects"],
    queryFn: () => api.listProjects(),
  });

  const run = (fn: () => void) => {
    setOpen(false);
    // Defer so the dialog close animation doesn't fight navigation.
    setTimeout(fn, 0);
  };

  const switchTheme = (preset: PresetName) => {
    const theme = loadActiveTheme();
    const next = { ...theme, preset };
    saveActiveTheme(next);
    applyThemeToDocument(next);
  };

  return (
    <CommandDialog open={open} onOpenChange={setOpen}>
      <CommandInput placeholder="Type a command or search…" />
      <CommandList>
        <CommandEmpty>No results found.</CommandEmpty>

        <CommandGroup heading="Navigate">
          <CommandItem onSelect={() => run(() => navigate("/chat"))}>
            <MessageSquare />
            <span>Go to Chat</span>
            <CommandShortcut>G C</CommandShortcut>
          </CommandItem>
          <CommandItem onSelect={() => run(() => navigate("/children"))}>
            <Network />
            <span>Go to Children</span>
          </CommandItem>
          <CommandItem onSelect={() => run(() => navigate("/plan"))}>
            <ListTodo />
            <span>Go to Plan</span>
          </CommandItem>
          <CommandItem onSelect={() => run(() => navigate("/memory"))}>
            <Brain />
            <span>Go to Memory</span>
          </CommandItem>
          <CommandItem onSelect={() => run(() => navigate("/agents"))}>
            <Users />
            <span>Go to Agents</span>
          </CommandItem>
          <CommandItem onSelect={() => run(() => navigate("/settings"))}>
            <Settings />
            <span>Go to Settings</span>
          </CommandItem>
        </CommandGroup>

        <CommandSeparator />

        <CommandGroup heading="Create">
          <CommandItem onSelect={() => run(() => setCreateOpen(true))}>
            <FolderPlus />
            <span>Create project…</span>
          </CommandItem>
          {activeProject && (
            <CommandItem
              onSelect={() =>
                run(async () => {
                  try {
                    const res = await api.createSession({
                      name: "New session",
                      project_name: activeProject.name,
                    });
                    await qc.invalidateQueries({ queryKey: ["sessions", activeProject.name] });
                    await setActiveSession(res.session.id);
                    navigate("/chat");
                  } catch (err) {
                    pushNotification("error", `Failed to create session: ${(err as Error).message}`);
                  }
                })
              }
            >
              <Plus />
              <span>New session in {activeProject.name}</span>
            </CommandItem>
          )}
        </CommandGroup>

        {projects.length > 0 && (
          <>
            <CommandSeparator />
            <CommandGroup heading="Switch project">
              {projects.map((p) => (
                <CommandItem
                  key={p.name}
                  onSelect={() =>
                    run(async () => {
                      try {
                        await setActiveProject(p.name);
                      } catch (err) {
                        pushNotification("error", `Failed to switch project: ${(err as Error).message}`);
                      }
                    })
                  }
                >
                  <Folder />
                  <span>{p.name}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        )}

        <CommandSeparator />
        <CommandGroup heading="Theme — Light">
          {PRESETS.filter((p) => p.mode === "light").map((preset) => (
            <CommandItem
              key={preset.name}
              onSelect={() => run(() => switchTheme(preset.name))}
            >
              <Palette />
              <span>{preset.label}</span>
            </CommandItem>
          ))}
        </CommandGroup>
        <CommandGroup heading="Theme — Dark">
          {PRESETS.filter((p) => p.mode === "dark").map((preset) => (
            <CommandItem
              key={preset.name}
              onSelect={() => run(() => switchTheme(preset.name))}
            >
              <Palette />
              <span>{preset.label}</span>
            </CommandItem>
          ))}
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
