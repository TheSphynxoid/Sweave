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
  Search,
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
import { ConfirmDeleteDialog } from "@/components/ConfirmDeleteDialog";

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
  const [pendingDelete, setPendingDelete] = useState<SpecialistSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [query, setQuery] = useState("");

  const { data: specialists = [], isLoading } = useQuery<SpecialistSummary[]>({
    queryKey: ["specialists"],
    queryFn: () => api.listSpecialists(),
  });
  // The orchestrator singleton lives outside the routing pool, so the
  // list above never contains it — fetch it on its own branch (404 =
  // not seeded yet for this project, no card). Read-only: locked.
  const { data: orchestrator = null } = useQuery<SpecialistSummary | null>({
    queryKey: ["specialist", "orchestrator"],
    queryFn: () => api.getSpecialist("orchestrator").catch(() => null),
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
    const q = query.trim().toLowerCase();
    const matches = (s: SpecialistSummary) =>
      q.length === 0 ||
      s.name.toLowerCase().includes(q) ||
      (s.description ?? "").toLowerCase().includes(q) ||
      (s.harness ?? "").toLowerCase().includes(q);
    // The singleton never comes through the list — merge it into its
    // group (guarded: tests + future lists may include it already).
    const all = [...specialists];
    if (orchestrator && !all.some((s) => s.is_orchestrator)) all.push(orchestrator);
    all.forEach((s) => {
      if (!matches(s)) return;
      const key = s.is_orchestrator ? "orchestrator" : s.scope;
      (map[key] ??= []).push(s);
    });
    return order
      .map((k) => ({ key: k, items: map[k] ?? [] }))
      .filter((g) => g.items.length > 0);
  }, [specialists, orchestrator, query]);

  const setModel = async (s: SpecialistSummary, model: string) => {
    try {
      await api.setSpecialistModel(s.name, model);
      await qc.invalidateQueries({ queryKey: ["specialists"] });
    } catch (err) {
      pushNotification("error", `Failed to set model: ${(err as Error).message}`);
    }
  };

  const remove = async (s: SpecialistSummary) => {
    if (deleting) return;
    setDeleting(true);
    try {
      await api.deleteSpecialist(s.name, s.scope === "global" ? "global" : "project");
      await qc.invalidateQueries({ queryKey: ["specialists"] });
      pushNotification("success", `Specialist "${s.name}" deleted.`);
    } catch (err) {
      pushNotification("error", `Failed to delete specialist: ${(err as Error).message}`);
    } finally {
      setDeleting(false);
      setPendingDelete(null);
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
          <div className="relative max-w-sm">
            <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter by name, description, or harness…"
              aria-label="Filter specialists"
              data-testid="agents-search"
              className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            />
          </div>
          {groups.length === 0 && (
            <p className="px-1 text-sm text-muted-foreground">
              No specialists match “{query.trim()}”.
            </p>
          )}
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
                      onDelete={() => setPendingDelete(s)}
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
      <ConfirmDeleteDialog
        open={pendingDelete !== null}
        title="Delete specialist"
        body={
          pendingDelete
            ? `Delete "${pendingDelete.name}"? This cannot be undone.`
            : ""
        }
        pending={deleting}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
        onConfirm={() => {
          if (!pendingDelete || deleting) return;
          void remove(pendingDelete);
        }}
        testId="confirm-delete-specialist-dialog"
      />
    </div>
  );
}

function HarnessBadge({ harness, name }: { harness: string; name: string }) {
  const native = harness === "sweave-engine";
  return (
    <Badge
      variant={native ? "success" : "muted"}
      className="mr-1 align-middle"
      data-testid={`harness-badge-${name}`}
      title={native ? "Runs on the native Sweave engine" : "Runs on opencode"}
    >
      {harness || "sweave-engine"}
    </Badge>
  );
}

/**
 * Worktree-policy badge (per-specialist isolation toggle). Shown only
 * for non-default policies — `isolated` is the norm and needs no ink.
 * `inherit` runs in the parent delegation's tree (reviewers), else the
 * project root; `none` always runs in the project root.
 */
function PolicyBadge({ policy, name }: { policy?: string; name: string }) {
  if (!policy || policy === "isolated") return null;
  return (
    <Badge
      variant="muted"
      className="mr-1 align-middle"
      data-testid={`policy-badge-${name}`}
      title={
        policy === "inherit"
          ? "Runs in the parent delegation's worktree (project root when the parent has no tree)"
          : "Runs in the project root with no worktree"
      }
    >
      {policy === "inherit" ? "inherits tree" : "no worktree"}
    </Badge>
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
  // source of truth) but their model picker IS editable (per-seed
  // override) — and since 2026-09-14 their harness too (seed
  // override; the dialog locks everything else in seed mode).
  const locked = specialist.is_orchestrator;
  const isSeed = specialist.scope === "seed";
  const editable = !locked;
  const deletable = !locked && !isSeed;

  return (
    <Card className="overflow-hidden hover:shadow-md transition-shadow">
      <CardHeader className="pb-2">
        <div className="min-w-0">
          <p className="font-medium truncate">{specialist.name}</p>
          <div className="text-xs text-muted-foreground truncate">
            <HarnessBadge harness={specialist.harness} name={specialist.name} />
            <PolicyBadge policy={specialist.worktree_policy} name={specialist.name} />
            {specialist.role_ref ? ` · ${specialist.role_ref}` : ""}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-muted-foreground line-clamp-2 min-h-[2.5rem]">
          {specialist.description || "No description."}
        </p>

        <div className="flex items-start gap-2">
          <span className="w-12 shrink-0 pt-2 text-xs text-muted-foreground">Model</span>
          <ModelWithEffort
            value={specialist.current_model ?? ""}
            onValueChange={onModel}
            options={modelOptions}
            variantsMap={variantsMap}
            disabled={locked}
            className="h-8 text-xs flex-1"
            effortClassName="h-8 text-xs"
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
              {deletable && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 text-destructive hover:text-destructive"
                  onClick={onDelete}
                >
                  <Trash2 size={13} /> Delete
                </Button>
              )}
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
