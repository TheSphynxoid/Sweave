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
import { ModelWithEffort } from "@/components/EffortSelect";import type { HarnessInfo } from "@/types";

export function CreateSpecialistDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const qc = useQueryClient();
  const { pushNotification } = useApp();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [harness, setHarness] = useState("opencode");
  const [scope, setScope] = useState<"project" | "global">("project");
  const [model, setModel] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const { data: harnesses = [] } = useQuery<HarnessInfo[]>({
    queryKey: ["harnesses"],
    queryFn: () => api.listHarnesses(),
  });
  const { data: models } = useQuery({
    queryKey: ["models"],
    queryFn: () => api.getModels(),
  });
  const modelOptions = (models?.all_models ?? []).length
    ? (models?.all_models ?? [])
    : harnesses.flatMap((h) => h.models);

  useEffect(() => {
    if (harnesses.length && !harnesses.find((h) => h.name === harness)) {
      setHarness(harnesses[0].name);
    }
  }, [harnesses, harness]);

  const reset = () => {
    setName("");
    setDescription("");
    setSystemPrompt("");
    setModel("");
  };

  const submit = async () => {
    // Names are lowercase alphanumeric + _- (the API 400s otherwise).
    const cleanName = name.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
    if (!cleanName) {
      pushNotification("warning", "Specialist name is required.");
      return;
    }
    if (cleanName !== name.trim()) {
      setName(cleanName);
    }
    setSubmitting(true);
    try {
      await api.createSpecialist(
        {
          name: cleanName,
          description: description.trim(),
          system_prompt: systemPrompt.trim(),
          harness,
          ...(model ? { current_model: model } : {}),
        },
        scope,
      );
      await qc.invalidateQueries({ queryKey: ["specialists"] });
      pushNotification("success", `Specialist "${cleanName}" created.`);
      onOpenChange(false);
      reset();
    } catch (err) {
      pushNotification("error", `Failed to create specialist: ${(err as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>New specialist</DialogTitle>
          <DialogDescription>
            A specialist is a scoped agent the orchestrator can delegate to.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-1">
          <div className="space-y-1.5">
            <Label htmlFor="spec-name">Name</Label>
            <Input
              id="spec-name"
              value={name}
              autoFocus
              onChange={(e) => setName(e.target.value)}
              placeholder="code-reviewer"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="spec-desc">Description</Label>
            <Input
              id="spec-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Reviews diffs for correctness and style"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>Harness</Label>
              <Select value={harness} onValueChange={setHarness}>
                <SelectTrigger className="h-9">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {harnesses.map((h) => (
                    <SelectItem key={h.name} value={h.name}>
                      {h.display_name ?? h.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Scope</Label>
              <Select
                value={scope}
                onValueChange={(v) => setScope(v as "project" | "global")}
              >
                <SelectTrigger className="h-9">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="project">Project</SelectItem>
                  <SelectItem value="global">Global</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>Model (optional — global default applies when empty)</Label>
            <ModelWithEffort
              value={model}
              onValueChange={setModel}
              options={modelOptions}
              variantsMap={models?.variants ?? {}}
              placeholder="Global default"
              className="h-9"
              testId="model-picker-new-specialist"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="spec-prompt">System prompt</Label>
            <Textarea
              id="spec-prompt"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="You are a meticulous code reviewer…"
              className="min-h-[80px]"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={submitting}>
            {submitting && <Loader2 size={14} className="animate-spin" />}
            Create specialist
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
