/**
 * Agents page (R4.1 step 3 scaffold).
 *
 * R4.4 will fill this with the M1.2-era workbench:
 * specialists grouped by scope (project / global / seed) with
 * a detail view (model + system prompt + tools + session_id),
 * idle/running status pill, inline model switch, and a "Run
 * task" affordance. The chat surface already covers the daily
 * use case via the orchestrator's defer tool; the workbench is
 * a backstop. Until then, this is a designed scaffold.
 */
import { ScaffoldPage } from "@/components/ScaffoldPage";
import { Users } from "lucide-react";

export function AgentsPage() {
  return (
    <ScaffoldPage
      surface="Agents"
      description="Specialists grouped by scope (project / global / seed) with model + system prompt + session inspection, inline model switch, and a 'Run task' affordance."
      pendingMilestone="R4.4"
      testId="page-agents"
    >
      <div className="flex items-center justify-center gap-1 text-xs text-muted-foreground">
        <Users size={12} />
        <span>Per-scope specialist list with idle/running status</span>
      </div>
    </ScaffoldPage>
  );
}
