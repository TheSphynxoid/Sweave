"use client";

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { api } from "@/api/client";
import { useApp } from "@/context/AppProvider";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { SpecialistSummary } from "@/types";

export function EditSpecialistDialog({
  specialist,
  open,
  onOpenChange,
}: {
  specialist: SpecialistSummary | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const qc = useQueryClient();
  const { pushNotification } = useApp();
  const [description, setDescription] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [roleRef, setRoleRef] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Prefill from the record being edited (the dialog is reused
  // across cards, so sync on every specialist change).
  useEffect(() => {
    setDescription(specialist?.description ?? "");
    setSystemPrompt(specialist?.system_prompt ?? "");
    setRoleRef(specialist?.role_ref ?? "");
  }, [specialist]);

  const submit = async () => {
    if (!specialist) return;
    setSubmitting(true);
    try {
      await api.updateSpecialist(
        specialist.name,
        {
          description: description.trim(),
          system_prompt: systemPrompt,
          role_ref: roleRef.trim() || null,
        },
        specialist.scope === "global" ? "global" : "project",
      );
      await qc.invalidateQueries({ queryKey: ["specialists"] });
      pushNotification("success", `Specialist "${specialist.name}" updated.`);
      onOpenChange(false);
    } catch (err) {
      pushNotification("error", `Failed to update specialist: ${(err as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            Edit specialist
            {specialist ? (
              <span className="font-mono text-muted-foreground"> {specialist.name}</span>
            ) : null}
          </DialogTitle>
          <DialogDescription>
            Name and scope are immutable — clone via create to rename or move.
            Supports{" "}
            <span className="font-mono">{"{{var}}"}</span> template variables
            (task, worktree_path, today, branch, git_status, …), rendered fresh
            on every delegation.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-1">
          <div className="space-y-1.5">
            <Label htmlFor="spec-edit-desc">Description</Label>
            <Input
              id="spec-edit-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What should the orchestrator defer here?"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="spec-edit-role">Role ref (optional model-tier hint)</Label>
            <Input
              id="spec-edit-role"
              value={roleRef}
              onChange={(e) => setRoleRef(e.target.value)}
              placeholder="backend"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="spec-edit-prompt">System prompt</Label>
            <Textarea
              id="spec-edit-prompt"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="You are a …"
              className="min-h-[160px] font-mono text-xs"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={submitting || !specialist}>
            {submitting && <Loader2 size={14} className="animate-spin" />}
            Save changes
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
