"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Settings as SettingsIcon, Palette, Folder, Cpu, SlidersHorizontal } from "lucide-react";
import { api } from "@/api/client";
import { useApp } from "@/context/AppProvider";
import {
  PRESETS,
  type PresetName,
  loadActiveTheme,
  saveActiveTheme,
  applyThemeToDocument,
  type ActiveTheme,
} from "@/lib/theme";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { CustomColorEditor } from "@/components/CustomColorEditor";
import { ModelPicker } from "@/components/ModelPicker";
import { cn } from "@/utils/cn";
import type { HarnessInfo, ModelsConfig } from "@/types";

export function SettingsPage() {
  const { activeProject } = useApp();

  const { data: models } = useQuery<ModelsConfig>({
    queryKey: ["models"],
    queryFn: () => api.getModels(),
  });
  const { data: harnesses = [] } = useQuery<HarnessInfo[]>({
    queryKey: ["harnesses"],
    queryFn: () => api.listHarnesses(),
  });

  if (!activeProject) {
    return (
      <div className="p-6" data-testid="page-settings">
        <EmptyState
          icon={<SettingsIcon size={20} />}
          title="No project active"
          description="Activate a project to see its settings."
        />
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6" data-testid="page-settings">
      <PageHeader
        title="Settings"
        description={`Configuration for ${activeProject.name}.`}
        icon={<SettingsIcon size={20} />}
      />

      <Tabs defaultValue="appearance">
        <TabsList>
          <TabsTrigger value="appearance">
            <Palette size={14} className="mr-1.5" /> Appearance
          </TabsTrigger>
          <TabsTrigger value="project">
            <Folder size={14} className="mr-1.5" /> Project
          </TabsTrigger>
          <TabsTrigger value="models">
            <SlidersHorizontal size={14} className="mr-1.5" /> Models
          </TabsTrigger>
          <TabsTrigger value="system">
            <Cpu size={14} className="mr-1.5" /> System
          </TabsTrigger>
        </TabsList>

        <TabsContent value="appearance">
          <AppearanceSettings />
        </TabsContent>

        <TabsContent value="project">
          <ProjectSettings />
        </TabsContent>

        <TabsContent value="models">
          <ModelsSettings models={models} />
        </TabsContent>

        <TabsContent value="system">
          <SystemSettings harnesses={harnesses} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function AppearanceSettings() {
  const [theme, setTheme] = useState<ActiveTheme>(() => loadActiveTheme());

  useEffect(() => {
    applyThemeToDocument(theme);
  }, [theme]);

  const choose = (preset: PresetName) => {
    const next: ActiveTheme = { ...theme, preset };
    setTheme(next);
    saveActiveTheme(next);
  };

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Theme preset</CardTitle>
          <CardDescription>Pick a base palette. Custom overrides apply on top.</CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-2">
          {PRESETS.map((preset) => {
            const primary = `rgb(${preset.tokens.primary})`;
            const bg = `rgb(${preset.tokens.background})`;
            const fg = `rgb(${preset.tokens.foreground})`;
            const isActive = theme.preset === preset.name;
            return (
              <button
                key={preset.name}
                type="button"
                onClick={() => choose(preset.name)}
                className={cn(
                  "flex items-center gap-2 rounded-lg border p-3 text-left transition-colors",
                  isActive ? "border-primary ring-1 ring-primary" : "hover:bg-muted",
                )}
              >
                <span
                  className="grid h-9 w-9 shrink-0 place-items-center rounded-md text-xs font-semibold"
                  style={{ background: primary, color: `rgb(${preset.tokens["primary-foreground"]})` }}
                >
                  A
                </span>
                <span className="min-w-0">
                  <span className="block text-sm font-medium">{preset.label}</span>
                  <span
                    className="mt-1 flex gap-1"
                    aria-hidden
                  >
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: bg, border: "1px solid rgba(127,127,127,0.3)" }} />
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: fg }} />
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: primary }} />
                  </span>
                </span>
              </button>
            );
          })}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Customize</CardTitle>
          <CardDescription>Fine-tune individual surface colors.</CardDescription>
        </CardHeader>
        <CardContent>
          <CustomColorEditor
            theme={theme}
            onChange={(next) => {
              setTheme(next);
              saveActiveTheme(next);
              applyThemeToDocument(next);
            }}
            onReset={() => {
              const next: ActiveTheme = { ...theme, custom: {} };
              setTheme(next);
              saveActiveTheme(next);
              applyThemeToDocument(next);
            }}
          />
        </CardContent>
      </Card>
    </div>
  );
}

function ProjectSettings() {
  const { activeProject } = useApp();
  if (!activeProject) return null;
  const rows: [string, string][] = [
    ["Path", activeProject.path],
    ["Description", activeProject.description || "—"],
    ["Default harness", activeProject.default_harness],
    ["Memory bank", activeProject.memory_bank],
    ["Worktree base", activeProject.worktree_base ?? "global default"],
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{activeProject.name}</CardTitle>
        <CardDescription>Active project configuration.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-start justify-between gap-4 border-b border-border pb-2 last:border-0">
            <span className="text-sm text-muted-foreground">{k}</span>
            <span className="max-w-[60%] truncate text-right font-mono text-xs">{v}</span>
          </div>
        ))}
        <div>
          <p className="text-sm text-muted-foreground mb-1">Model overrides</p>
          {Object.keys(activeProject.model_overrides).length === 0 ? (
            <p className="text-xs text-muted-foreground">None</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(activeProject.model_overrides).map(([k, v]) => (
                <Badge key={k} variant="outline" className="font-mono text-[10px]">
                  {k}: {v}
                </Badge>
              ))}
            </div>
          )}
        </div>
        <div>
          <p className="text-sm text-muted-foreground mb-1">Routing rules</p>
          {activeProject.routing_rules.length === 0 ? (
            <p className="text-xs text-muted-foreground">None</p>
          ) : (
            <div className="space-y-1">
              {activeProject.routing_rules.map((r, i) => (
                <div key={i} className="rounded-md border border-border bg-muted/40 px-2 py-1 text-xs font-mono">
                  {r.pattern} → {r.agent}
                  {r.model ? ` (${r.model})` : ""}
                </div>
              ))}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function ModelsSettings({ models }: { models?: ModelsConfig }) {
  const qc = useQueryClient();
  const { pushNotification } = useApp();
  const [saving, setSaving] = useState(false);

  const setDefault = async (model: string) => {
    setSaving(true);
    try {
      await api.setDefaultModel(model);
      await qc.invalidateQueries({ queryKey: ["models"] });
      pushNotification("success", `Default model set to ${model}.`);
    } catch (err) {
      pushNotification("error", `Failed to set default model: ${(err as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  if (!models || !models.providers) {
    return (
      <div className="grid gap-3 sm:grid-cols-2" data-testid="models-loading">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-3">
            <div className="flex items-center justify-between">
              <Skeleton className="h-3.5 w-20" />
              <Skeleton className="h-5 w-16 rounded-full" />
            </div>
            <Skeleton className="h-2.5 w-full" />
            <Skeleton className="h-2.5 w-2/3" />
          </div>
        ))}
      </div>
    );
  }
  const providers = Object.entries(models.providers);
  const allModels = (models.all_models ?? []).length
    ? (models.all_models ?? [])
    : providers.flatMap(([provider, modelList]) =>
        modelList.map((m) => (m.includes("/") ? m : `${provider}/${m}`)),
      );
  return (
    <div className="space-y-3">
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between gap-2">
            <div>
              <CardTitle className="text-sm">Default model</CardTitle>
              <CardDescription>
                Used by the orchestrator and any specialist without an explicit model.
              </CardDescription>
            </div>
            {models.default && <Badge variant="secondary" className="font-mono text-[10px]">{models.default}</Badge>}
          </div>
        </CardHeader>
        <CardContent>
          <ModelPicker
            value={models.default ?? ""}
            onValueChange={(m) => void setDefault(m)}
            options={allModels}
            placeholder="Select default model"
            disabled={saving}
            className="h-9 font-mono text-xs"
            testId="model-picker-default"
          />
        </CardContent>
      </Card>
    <div className="grid gap-3 sm:grid-cols-2">
      {providers.map(([provider, modelList]) => (
        <Card key={provider}>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm">{provider}</CardTitle>
              <Badge variant="secondary">{modelList.length} models</Badge>
            </div>
          </CardHeader>
          <CardContent className="space-y-1.5 text-xs">
            <div className="flex flex-wrap gap-1">
              {modelList.map((model) => (
                <Badge key={model} variant="outline" className="font-mono text-[10px]">
                  {provider}/{model}
                </Badge>
              ))}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
    </div>
  );
}

function SystemSettings({ harnesses }: { harnesses: HarnessInfo[] }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {harnesses.map((h) => (
        <Card key={h.name}>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm">{h.display_name ?? h.name}</CardTitle>
              <Badge variant="muted">{h.version}</Badge>
            </div>
          </CardHeader>
          <CardContent className="space-y-2 text-xs">
            <div className="flex flex-wrap gap-1">
              {h.providers.map((p) => (
                <Badge key={p} variant="outline" className="text-[10px]">
                  {p}
                </Badge>
              ))}
            </div>
            <p className="text-muted-foreground">{h.models.length} models available</p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
