/**
 * Children page placeholder (M1.9 Step 1).
 *
 * Step 1 ships the shell; Step 3 fills the live tree (WS-driven
 * status pulses), the escalation lane, the per-row promote /
 * answer buttons, and the detail-view modal.
 */
import { useApp } from "@/context/AppProvider";

export function ChildrenPage() {
  const { activeProject, activeSession } = useApp();
  return (
    <div className="p-6 space-y-4" data-testid="children-page">
      <header>
        <h1 className="text-xl font-semibold">Children</h1>
        <p className="text-sm text-muted-foreground">
          {activeProject
            ? activeSession
              ? `Live delegation tree for ${activeSession.name}.`
              : "Select a session to see its delegation tree."
            : "Activate a project to see its delegations."}
        </p>
      </header>
      <div
        data-testid="children-placeholder"
        className="border border-dashed border-border rounded p-8 text-center text-sm text-muted-foreground"
      >
        Live tree + detail view land in Step 3.
      </div>
    </div>
  );
}
