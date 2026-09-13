"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Settings as SettingsIcon, Palette, Folder, Cpu, SlidersHorizontal } from "lucide-react";
import { api } from "@/api/client";
import { useApp } from "@/context/AppProvider";
import {
  presetsByMode,
  type ThemeMode,
  useTheme,
  SYSTEM_PRESET_NAME,
} from "@/lib/theme";
import { useFontScale, FONT_SCALE_OPTIONS } from "@/lib/theme/fontScale";
import { PageHeader } from "@/components/PageHeader";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { CustomColorEditor } from "@/components/CustomColorEditor";
import { ModelWithEffort } from "@/components/EffortSelect";
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

  // Appearance (theme + font size) is global — persisted in localStorage,
  // not per-project — so it renders even with no active project. The
  // Project/Models/System tabs need a project or a registry and keep
  // their own empty states.
  if (!activeProject) {
    return (
      <div className="p-6 space-y-6" data-testid="page-settings">
        <PageHeader
          title="Settings"
          description="Appearance lives here. Activate a project for project, model, and system settings."
          icon={<SettingsIcon size={20} />}
        />
        <AppearanceSettings />
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
  const { theme, setPreset, setCustom, resetCustom } = useTheme();
  const [customOpen, setCustomOpen] = useState(false);
  const customCount = Object.keys(theme.custom).length;

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Theme preset</CardTitle>
          <CardDescription>Pick a base palette. Custom overrides apply on top.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              System
            </p>
            <button
              type="button"
              onClick={() => setPreset(SYSTEM_PRESET_NAME)}
              data-testid="settings-preset-system"
              title="Follow the operating system"
              className={cn(
                "flex w-full items-center gap-2 rounded-lg border p-2.5 text-left transition-colors",
                theme.preset === SYSTEM_PRESET_NAME
                  ? "border-primary ring-1 ring-primary"
                  : "hover:bg-muted",
              )}
            >
              <span
                className="grid h-7 w-7 shrink-0 rounded-md"
                style={{
                  background: "linear-gradient(135deg, #0f172a 0 50%, #f8fafc 50% 100%)",
                }}
                aria-hidden
              />
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium">System</span>
                <span className="block truncate text-[10px] text-muted-foreground">
                  Follow the operating system
                </span>
              </span>
            </button>
          </div>

          {(["light", "dark"] as const).map((mode: ThemeMode) => (
            <div key={mode} className="space-y-2">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                {mode === "light" ? "Light" : "Dark"} · {presetsByMode(mode).length}
              </p>
              <div className="grid grid-cols-2 gap-2 lg:grid-cols-3">
                {presetsByMode(mode).map((preset) => {
                  const primary = `rgb(${preset.tokens.primary})`;
                  const bg = `rgb(${preset.tokens.background})`;
                  const accent = `rgb(${preset.tokens.accent})`;
                  const success = `rgb(${preset.tokens.success})`;
                  const isActive = theme.preset === preset.name;
                  return (
                    <button
                      key={preset.name}
                      type="button"
                      onClick={() => setPreset(preset.name)}
                      data-testid={`settings-preset-${preset.name}`}
                      title={`${preset.label} — ${preset.description}`}
                      className={cn(
                        "flex items-center gap-2 rounded-lg border p-2.5 text-left transition-colors",
                        isActive ? "border-primary ring-1 ring-primary" : "hover:bg-muted",
                      )}
                    >
                      <span
                        className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-[11px] font-semibold"
                        style={{ background: primary, color: `rgb(${preset.tokens["primary-foreground"]})` }}
                      >
                        A
                      </span>
                      <span className="min-w-0">
                        <span className="block truncate text-[13px] font-medium">{preset.label}</span>
                        <span
                          className="mt-1 flex gap-1"
                          aria-hidden
                        >
                          <span className="h-2.5 w-2.5 rounded-full" style={{ background: bg, border: "1px solid rgba(127,127,127,0.3)" }} />
                          <span className="h-2.5 w-2.5 rounded-full" style={{ background: primary }} />
                          <span className="h-2.5 w-2.5 rounded-full" style={{ background: accent, border: "1px solid rgba(127,127,127,0.3)" }} />
                          <span className="h-2.5 w-2.5 rounded-full" style={{ background: success }} />
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Customize</CardTitle>
          <CardDescription>Fine-tune individual surface colors.</CardDescription>
        </CardHeader>
        <CardContent>
          <button
            type="button"
            onClick={() => setCustomOpen((o) => !o)}
            aria-expanded={customOpen}
            data-testid="settings-customize-toggle"
            className="flex w-full items-center justify-between rounded-lg border border-border px-3 py-2 text-sm transition-colors hover:bg-muted"
          >
            <span>
              {customCount > 0 ? `${customCount} override${customCount === 1 ? "" : "s"} active` : "Per-token color overrides"}
            </span>
            <span className="text-xs text-muted-foreground">{customOpen ? "Hide" : "Show"}</span>
          </button>
          {customOpen && (
            <div className="pt-2">
              <CustomColorEditor
                theme={theme}
                onChange={(next) => setCustom(next.custom)}
                onReset={resetCustom}
              />
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="md:col-span-2">
        <CardHeader>
          <CardTitle className="text-sm">Font size</CardTitle>
          <CardDescription>
            Scales the whole interface (chrome, chat, and messages) relative to your
            browser default. Respects OS/browser zoom too.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <FontScaleControl />
        </CardContent>
      </Card>
    </div>
  );
}

function FontScaleControl() {
  const [scaleId, setScaleId] = useFontScale();
  return (
    <div className="space-y-3">
      <div
        className="inline-flex rounded-lg border p-1"
        role="group"
        aria-label="Font size"
        data-testid="settings-font-scale"
      >
        {FONT_SCALE_OPTIONS.map((opt) => {
          const isActive = scaleId === opt.id;
          return (
            <button
              key={opt.id}
              type="button"
              data-testid={`settings-font-scale-${opt.id}`}
              aria-pressed={isActive}
              title={`${opt.label} · ~${opt.basePx}px base`}
              onClick={() => setScaleId(opt.id)}
              className={cn(
                "rounded-md px-4 py-1.5 text-sm font-medium transition-colors",
                isActive ? "bg-primary text-primary-foreground" : "hover:bg-muted",
              )}
            >
              {opt.label}
            </button>
          );
        })}
      </div>
      <div className="space-y-1 rounded-lg border border-border bg-muted/40 px-3 py-2.5" aria-hidden>
        <p className="text-sm">The quick brown fox jumps over the lazy dog 0123456789</p>
        <p className="font-mono text-xs">const answer = await orchestrator.ask("ship it");</p>
      </div>
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
            <span className="max-w-[60%] truncate text-right font-mono text-xs" title={v}>{v}</span>
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

export function ModelsSettings({ models }: { models?: ModelsConfig }) {
  const qc = useQueryClient();
  const { pushNotification } = useApp();
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);

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

  const syncModels = async () => {
    setSyncing(true);
    try {
      const report = await api.regenerateModels();
      await qc.invalidateQueries({ queryKey: ["models"] });
      pushNotification(
        "success",
        `Models synced: +${report.added}/−${report.removed} across ${report.providers} providers (${report.source}).`,
      );
    } catch (err) {
      pushNotification("error", `Model sync failed: ${(err as Error).message}`);
    } finally {
      setSyncing(false);
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
            <div className="flex items-center gap-2">
              {models.default && <Badge variant="secondary" className="font-mono text-[10px]">{models.default}</Badge>}
              <button
                type="button"
                onClick={() => void syncModels()}
                disabled={syncing || saving}
                data-testid="models-sync"
                title="Regenerate the model registry from models.dev + the live serve overlay. Takes a minute; your default is preserved."
                className="rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted disabled:opacity-50"
              >
                {syncing ? "Syncing…" : "Sync models"}
              </button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <ModelWithEffort
            value={models.default ?? ""}
            onValueChange={(m) => void setDefault(m)}
            options={allModels}
            variantsMap={models.variants ?? {}}
            placeholder="Select default model"
            disabled={saving}
            className="h-9 font-mono text-xs"
            effortClassName="h-9 text-xs"
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
          <CardContent className="text-xs">
            <details>
              <summary
                data-testid={`models-provider-${provider}`}
                className="cursor-pointer text-muted-foreground hover:text-foreground"
              >
                Show {modelList.length} models
              </summary>
              <div className="flex flex-wrap gap-1 pt-2">
                {modelList.map((model) => (
                  <Badge key={model} variant="outline" className="font-mono text-[10px]">
                    {provider}/{model}
                  </Badge>
                ))}
              </div>
            </details>
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
