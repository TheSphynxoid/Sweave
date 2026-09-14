"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { HarnessInfo, SpecialistSummary } from "@/types";

/**
 * PUT body for the edit dialog. Seeds own nothing but their
 * per-seed overrides: harness + worktree policy (model rides the
 * card picker). Anything else on a seed 400s server-side — never
 * send it.
 */
export const WORKTREE_POLICIES = [
  { value: "isolated", label: "Isolated", hint: "Fresh worktree per task" },
  { value: "inherit", label: "Inherit", hint: "Parent delegation's tree, else project root" },
  { value: "none", label: "No worktree", hint: "Run in the project root" },
] as const;

export function specialistEditBody(
  spec: SpecialistSummary,
  fields: {
    description: string;
    systemPrompt: string;
    roleRef: string | null;
    harness: string;
    worktreePolicy: string;
  },
): {
  description?: string;
  system_prompt?: string;
  role_ref?: string | null;
  harness?: string;
  worktree_policy?: string;
} {
  if (spec.scope === "seed") {
    return {
      ...(fields.harness ? { harness: fields.harness } : {}),
      ...(fields.worktreePolicy ? { worktree_policy: fields.worktreePolicy } : {}),
    };
  }
  return {
    description: fields.description,
    system_prompt: fields.systemPrompt,
    role_ref: fields.roleRef,
    ...(fields.harness ? { harness: fields.harness } : {}),
    ...(fields.worktreePolicy ? { worktree_policy: fields.worktreePolicy } : {}),
  };
}

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
  const [harness, setHarness] = useState("");
  const [worktreePolicy, setWorktreePolicy] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const { data: harnesses = [] } = useQuery<HarnessInfo[]>({
    queryKey: ["harnesses"],
    queryFn: () => api.listHarnesses(),
  });

  // Seed mode: prompt/description/role live in config.yaml (the PUT
  // refuses them) — only the harness + worktree overrides are editable
  // here (model rides the card picker). Saving fully-unchanged seeds
  // would 400 ("nothing to do"), so the button locks instead.
  const isSeed = specialist?.scope === "seed";
  const seedPolicy = specialist?.worktree_policy ?? "isolated";
  const seedUnchanged =
    !!isSeed &&
    (!harness || harness === (specialist?.harness ?? "")) &&
    (!worktreePolicy || worktreePolicy === seedPolicy);

  // Prefill from the record being edited (the dialog is reused
  // across cards, so sync on every specialist change).
  useEffect(() => {
    setDescription(specialist?.description ?? "");
    setSystemPrompt(specialist?.system_prompt ?? "");
    setRoleRef(specialist?.role_ref ?? "");
    setHarness(specialist?.harness ?? "");
    setWorktreePolicy(specialist?.worktree_policy ?? "isolated");
  }, [specialist]);

  const submit = async () => {
    if (!specialist) return;
    setSubmitting(true);
    try {
      await api.updateSpecialist(
        specialist.name,
        specialistEditBody(specialist, {
          description: description.trim(),
          systemPrompt,
          roleRef: roleRef.trim() || null,
          harness,
          worktreePolicy,
        }),
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
          {isSeed && (
            <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
              Seed-owned prompt — description, role ref, and system prompt live in
              sweave/agents/*/config.yaml. Only the harness + worktree overrides
              are editable here.
            </p>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="spec-edit-desc">Description</Label>
            <Input
              id="spec-edit-desc"
              value={description}
              disabled={isSeed}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What should the orchestrator defer here?"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="spec-edit-role">Role ref (optional model-tier hint)</Label>
            <Input
              id="spec-edit-role"
              value={roleRef}
              disabled={isSeed}
              onChange={(e) => setRoleRef(e.target.value)}
              placeholder="backend"
            />
          </div>
          <div className="space-y-1.5">
            <Label>Harness</Label>
            <Select value={harness} onValueChange={setHarness}>
              <SelectTrigger className="h-9" data-testid="spec-edit-harness">
                <SelectValue placeholder="Select harness" />
              </SelectTrigger>
              <SelectContent>
                {harness && !harnesses.find((h) => h.name === harness) && (
                  <SelectItem key={harness} value={harness}>
                    {harness} (unlisted)
                  </SelectItem>
                )}
                {harnesses.map((h) => (
                  <SelectItem key={h.name} value={h.name}>
                    {h.display_name ?? h.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Applies to the next delegation — never mid-task.
            </p>
          </div>
          <div className="space-y-1.5">
            <Label>Worktree policy</Label>
            <Select value={worktreePolicy} onValueChange={setWorktreePolicy}>
              <SelectTrigger className="h-9" data-testid="spec-edit-worktree-policy">
                <SelectValue placeholder="Select worktree policy" />
              </SelectTrigger>
              <SelectContent>
                {WORKTREE_POLICIES.map((p) => (
                  <SelectItem key={p.value} value={p.value}>
                    {p.label} — {p.hint}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Isolated = fresh tree per task. Inherit = parent delegation&apos;s
              tree (reviewers), else project root. No worktree = project root.
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="spec-edit-prompt">System prompt</Label>
            <Textarea
              id="spec-edit-prompt"
              value={systemPrompt}
              disabled={isSeed}
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
          <Button onClick={() => void submit()} disabled={submitting || !specialist || seedUnchanged}>
            {submitting && <Loader2 size={14} className="animate-spin" />}
            Save changes
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
