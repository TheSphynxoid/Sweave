"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Users,
  Plus,
  Trash2,
  Pencil,
  Bot,
  Cpu,
  Layers,
  Sparkles,
  ChevronDown,
} from "lucide-react";
import { api } from "@/api/client";
import { useApp } from "@/context/AppProvider";
import type { HarnessInfo, ModelsConfig, SpecialistSummary } from "@/types";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { ModelWithEffort } from "@/components/EffortSelect";
import { EffectiveDefaultNote } from "@/components/EffectiveDefaultNote";
import { CreateSpecialistDialog } from "@/components/CreateSpecialistDialog";
import { EditSpecialistDialog } from "@/components/EditSpecialistDialog";

const SCOPE_META: Record<
  string,
  { label: string; icon: typeof Bot; tint: string }
> = {
  orchestrator: { label: "Orchestrator", icon: Sparkles, tint: "text-primary" },
  seed: { label: "Seed", icon: Layers, tint: "text-amber-500" },
  project: { label: "Project", icon: Bot, tint: "text-sky-500" },
  global: { label: "Global", icon: Cpu, tint: "text-violet-500" },
};

export function AgentsPage() {
  const { pushNotification } = useApp();
  const qc = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<SpecialistSummary | null>(null);

  const { data: specialists = [], isLoading } = useQuery<SpecialistSummary[]>({
    queryKey: ["specialists"],
    queryFn: () => api.listSpecialists(),
  });
  const { data: harnesses = [] } = useQuery<HarnessInfo[]>({
    queryKey: ["harnesses"],
    queryFn: () => api.listHarnesses(),
  });
  // The single global registry (models.yaml) is the source of truth
  // every specialist picks from; the harness list (actually-configured
  // models) is merged in as a fallback so the picker never goes empty.
  const { data: models } = useQuery<ModelsConfig>({
    queryKey: ["models"],
    queryFn: () => api.getModels(),
  });

  const modelOptions = useMemo(() => {
    const set = new Set<string>();
    (models?.all_models ?? []).forEach((m) => set.add(m));
    harnesses.forEach((h) => h.models.forEach((m) => set.add(m)));
    return Array.from(set).sort();
  }, [harnesses, models]);

  const groups = useMemo(() => {
    const order = ["orchestrator", "project", "global", "seed"];
    const map: Record<string, SpecialistSummary[]> = {
      orchestrator: [],
      project: [],
      global: [],
      seed: [],
    };
    specialists.forEach((s) => {
      const key = s.is_orchestrator ? "orchestrator" : s.scope;
      (map[key] ??= []).push(s);
    });
    return order
      .map((k) => ({ key: k, items: map[k] ?? [] }))
      .filter((g) => g.items.length > 0);
  }, [specialists]);

  const setModel = async (s: SpecialistSummary, model: string) => {
    try {
      await api.setSpecialistModel(s.name, model);
      await qc.invalidateQueries({ queryKey: ["specialists"] });
    } catch (err) {
      pushNotification("error", `Failed to set model: ${(err as Error).message}`);
    }
  };

  const remove = async (s: SpecialistSummary) => {
    try {
      await api.deleteSpecialist(s.name, s.scope === "global" ? "global" : "project");
      await qc.invalidateQueries({ queryKey: ["specialists"] });
      pushNotification("success", `Specialist "${s.name}" deleted.`);
    } catch (err) {
      pushNotification("error", `Failed to delete specialist: ${(err as Error).message}`);
    }
  };

  return (
    <div className="p-6 space-y-6" data-testid="page-agents">
      <PageHeader
        title="Agents"
        description="Specialists the orchestrator delegates to, grouped by scope."
        icon={<Users size={20} />}
        actions={
          <Button onClick={() => setCreateOpen(true)}>
            <Plus size={16} /> New specialist
          </Button>
        }
      />

      {isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" data-testid="agents-loading">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-3">
              <div className="flex items-center gap-3">
                <Skeleton className="h-9 w-9 rounded-lg" />
                <div className="space-y-1.5 flex-1">
                  <Skeleton className="h-3.5 w-24" />
                  <Skeleton className="h-2.5 w-16" />
                </div>
              </div>
              <Skeleton className="h-2.5 w-full" />
              <Skeleton className="h-2.5 w-3/4" />
              <Skeleton className="h-8 w-full rounded-lg" />
            </div>
          ))}
        </div>
      ) : specialists.length === 0 ? (
        <EmptyState
          icon={<Users size={20} />}
          title="No specialists yet"
          description="Create a specialist to extend what the orchestrator can delegate."
          action={<Button onClick={() => setCreateOpen(true)}><Plus size={16} /> New specialist</Button>}
        />
      ) : (
        <div className="space-y-6">
          {groups.map((group) => {
            const meta = SCOPE_META[group.key];
            const Icon = meta.icon;
            return (
              <section key={group.key} className="space-y-2">
                <div className="flex items-center gap-2 px-1">
                  <Icon size={15} className={meta.tint} />
                  <h2 className="text-sm font-semibold">{meta.label}</h2>
                  <Badge variant="muted">{group.items.length}</Badge>
                </div>
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {group.items.map((s) => (
                    <SpecialistCard
                      key={`${s.scope}-${s.name}`}
                      specialist={s}
                      modelOptions={modelOptions}
                      variantsMap={models?.variants ?? {}}
                      defaultModel={models?.default ?? null}
                      onModel={(m) => setModel(s, m)}
                      onDelete={() => remove(s)}
                      onEdit={() => setEditTarget(s)}
                    />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}

      <CreateSpecialistDialog open={createOpen} onOpenChange={setCreateOpen} />
      <EditSpecialistDialog
        specialist={editTarget}
        open={editTarget !== null}
        onOpenChange={(open) => {
          if (!open) setEditTarget(null);
        }}
      />
    </div>
  );
}

function SpecialistCard({
  specialist,
  modelOptions,
  variantsMap,
  defaultModel,
  onModel,
  onDelete,
  onEdit,
}: {
  specialist: SpecialistSummary;
  modelOptions: string[];
  variantsMap: Record<string, string[]>;
  /** Global default (GET /api/models `default`); effective when no current_model. */
  defaultModel?: string | null;
  onModel: (model: string) => void;
  onDelete: () => void;
  onEdit: () => void;
}) {
  const [open, setOpen] = useState(false);
  // Seeds keep a read-only prompt/description (config.yaml is the
  // source of truth) but their model picker IS editable: it persists a
  // minimal per-seed override via PUT /api/specialists/{name}/model.
  const locked = specialist.is_orchestrator;
  const editable = !locked && specialist.scope !== "seed";

  return (
    <Card className="overflow-hidden hover:shadow-md transition-shadow">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="font-medium truncate">{specialist.name}</p>
            <p className="text-xs text-muted-foreground truncate">
              {specialist.harness}
              {specialist.role_ref ? ` · ${specialist.role_ref}` : ""}
            </p>
          </div>
          <Badge variant={specialist.is_orchestrator ? "default" : "outline"} className="shrink-0">
            {specialist.scope}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-muted-foreground line-clamp-2 min-h-[2.5rem]">
          {specialist.description || "No description."}
        </p>

        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground w-12 shrink-0">Model</span>
          <ModelWithEffort
            value={specialist.current_model ?? ""}
            onValueChange={onModel}
            options={modelOptions}
            variantsMap={variantsMap}
            disabled={locked}
            className="h-8 text-xs flex-1"
            testId={`model-picker-${specialist.scope}-${specialist.name}`}
          />
          <EffectiveDefaultNote
            currentModel={specialist.current_model}
            defaultModel={defaultModel}
            testId={`effective-default-${specialist.name}`}
          />
        </div>

        <div className="flex items-center justify-between pt-1">
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ChevronDown
              size={13}
              className={open ? "rotate-180 transition-transform" : "transition-transform"}
            />
            Details
          </button>
          {editable && (
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="sm"
                className="h-7"
                onClick={onEdit}
              >
                <Pencil size={13} /> Edit
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-destructive hover:text-destructive"
                onClick={onDelete}
              >
                <Trash2 size={13} /> Delete
              </Button>
            </div>
          )}
        </div>

        {open && (
          <div className="rounded-md border border-border bg-muted/40 p-3 text-xs space-y-1.5">
            <Row label="Session" value={specialist.session_id ?? "—"} />
            <Row label="Role ref" value={specialist.role_ref ?? "—"} />
            <div>
              <p className="text-muted-foreground mb-1">System prompt</p>
              <pre className="whitespace-pre-wrap font-mono text-[11px] text-foreground/80 max-h-40 overflow-auto">
                {specialist.system_prompt || "—"}
              </pre>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono truncate max-w-[60%] text-right">{value}</span>
    </div>
  );
}
