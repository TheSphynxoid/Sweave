"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { FolderPlus, Loader2 } from "lucide-react";
import { api } from "@/api/client";
import { useApp } from "@/context/AppProvider";
import { useUIStore } from "@/store/ui";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { PathPicker } from "@/components/PathPicker";

export function CreateProjectDialog() {
  const open = useUIStore((s) => s.createProjectOpen);
  const setOpen = useUIStore((s) => s.setCreateProjectOpen);
  const qc = useQueryClient();
  const { setActiveProject, pushNotification } = useApp();

  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const reset = () => {
    setName("");
    setPath("");
    setDescription("");
  };

  const close = () => {
    setOpen(false);
    reset();
  };

  const handleSubmit = async () => {
    if (!name.trim() || !path.trim()) {
      pushNotification("warning", "Project name and path are required.");
      return;
    }
    setSubmitting(true);
    try {
      const res = await api.createProject({
        name: name.trim(),
        path: path.trim(),
        description: description.trim() || undefined,
      });
      await qc.invalidateQueries({ queryKey: ["projects"] });
      await setActiveProject(res.project.name);
      pushNotification("success", `Project "${res.project.name}" created.`);
      close();
    } catch (err) {
      pushNotification("error", `Failed to create project: ${(err as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && close()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FolderPlus size={18} className="text-primary" />
            Create project
          </DialogTitle>
          <DialogDescription>
            A project points Sweave at a codebase folder so sessions, delegations,
            and specialists have a home.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-1">
          <div className="space-y-1.5">
            <Label htmlFor="project-name">Name</Label>
            <Input
              id="project-name"
              value={name}
              autoFocus
              onChange={(e) => setName(e.target.value)}
              placeholder="my-agent-project"
              onKeyDown={(e) => {
                if (e.key === "Enter" && path) void handleSubmit();
              }}
            />
          </div>

          <div className="space-y-1.5">
            <Label>Folder</Label>
            <PathPicker value={path} onChange={setPath} />
            <p className="text-[11px] text-muted-foreground">
              Absolute path on the server's filesystem. Use "Browse" to pick or
              create a folder.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="project-desc">Description (optional)</Label>
            <Textarea
              id="project-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What is this project for?"
              className="min-h-[60px]"
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={close} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={() => void handleSubmit()} disabled={submitting}>
            {submitting && <Loader2 size={14} className="animate-spin" />}
            Create project
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
