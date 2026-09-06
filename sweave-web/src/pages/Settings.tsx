/**
 * Settings page (R4.1 step 3 scaffold).
 *
 * R4.4 will fill this with three sub-panes:
 *   - Models: the four roles' default models (M1.2 step 3).
 *   - Routing: the rules.yaml rules (M1.2 step 3).
 *   - Catalog: a model picker (the old UI_PLAN's catalog).
 * Backed by /api/harnesses (already exposes providers); the
 * catalog is a flattened view of those + the v1 presets.
 * Until then, this is a designed scaffold.
 */
import { ScaffoldPage } from "@/components/ScaffoldPage";
import { Settings as SettingsIcon } from "lucide-react";

export function SettingsPage() {
  return (
    <ScaffoldPage
      surface="Settings"
      description="Three sub-panes: Models (per-role defaults), Routing (rules.yaml), Catalog (model picker backed by /api/harnesses)."
      pendingMilestone="R4.4"
      testId="page-settings"
    >
      <div className="flex items-center justify-center gap-1 text-xs text-muted-foreground">
        <SettingsIcon size={12} />
        <span>Models · Routing · Catalog</span>
      </div>
    </ScaffoldPage>
  );
}
