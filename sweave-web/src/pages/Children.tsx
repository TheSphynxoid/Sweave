/**
 * Children page (M1.9 Step 3).
 *
 * The output funnel. Renders the live delegation tree (WS
 * pulses; promote + answer inline) and the detail modal
 * (composed prompt + tool timeline + tokens + status timeline).
 *
 * Click a row -> open the detail modal for that delegation.
 * Row-level state changes (promote, answer) invalidate the
 * delegations query; the tree rebuilds; the M1.8 no-rerender
 * invariant keeps the change local (one row's status pill
 * changes; the tree isn't re-rendered top to bottom).
 */
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useApp } from "@/context/AppProvider";
import { useWS } from "@/context/WSProvider";
import { LiveTree } from "./children/LiveTree";
import { DetailView } from "./children/DetailView";
import { ArchivedGroup } from "./children/ArchivedGroup";
import type { Delegation } from "@/types";

export function ChildrenPage() {
  const { activeProject } = useApp();
  const { subscribe } = useWS();
  const qc = useQueryClient();
  const [openId, setOpenId] = useState<string | null>(null);

  // Fetch the delegations. Filters: project + parent_task_id
  // (children of the current session's chat delegation are
  // future work -- for now we show every delegation the
  // orchestrator's chat loop has created for this session).
  const { data: delegations = [] } = useQuery<Delegation[]>({
    queryKey: ["delegations"],
    queryFn: () => api.listDelegations(),
  });

  // M1.13 step 5 (ruling 2026-09-11): compact Archived group. One
  // fetch carries both the live rows (archived=false, backend
  // default hides them) and the per-project archive aggregates
  // (include_archived=true); the live query above stays untouched
  // so the M1.8 no-rerender pattern keeps functioning for
  // row-level WS updates.
  const { data: archive } = useQuery({
    queryKey: ["archivedProjects"],
    queryFn: () => api.listDelegationsWithArchive(),
  });

  // WS subscription: status_changed pulses. We invalidate
  // the delegations query (the data shape is small; the
  // re-fetch is cheap; the row-level patch is the M1.8
  // invariant's React translation: the row's status pill
  // updates because the data changes; the rest of the tree
  // doesn't re-render because the parent reconciliation
  // only diffs the changed children).
  useEffect(() => {
    const off = subscribe("delegation.status_changed", () => {
      void qc.invalidateQueries({ queryKey: ["delegations"] });
    });
    const offEsc = subscribe("specialist.escalated", () => {
      void qc.invalidateQueries({ queryKey: ["delegations"] });
    });
    const offRes = subscribe("specialist.escalation_resolved", () => {
      void qc.invalidateQueries({ queryKey: ["delegations"] });
    });
    return () => {
      off();
      offEsc();
      offRes();
    };
  }, [subscribe, qc]);

  if (!activeProject) {
    return (
      <div className="p-6" data-testid="children-page">
        <p className="text-sm text-muted-foreground">
          Activate a project to see its delegations.
        </p>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-4" data-testid="children-page">
      <header>
        <h1 className="text-xl font-semibold">Children</h1>
        <p className="text-sm text-muted-foreground">
          {/* M1.13 step 1 (ruling 2026-09-10): the tab shows ALL
              projects' delegations grouped per project -- no per-session
              claim (it wasn't true; the list isn't session-filtered). */}
          All projects
          {activeProject ? (
            <>
              {" · "}
              <span className="text-foreground">
                active: {activeProject.name}
              </span>
            </>
          ) : null}
        </p>
      </header>
      <LiveTree
        delegations={delegations}
        activeProjectName={activeProject?.name ?? null}
        onOpen={(id) => setOpenId(id)}
      />
      {/* M1.13 step 5: at the very bottom — stats preserved, no
          clutter (collapsed by default; aggregates only, never
          individual archived rows). */}
      <ArchivedGroup archivedProjects={archive?.archived_projects ?? []} />
      {openId && (
        <DetailView
          delegationId={openId}
          onClose={() => setOpenId(null)}
        />
      )}
    </div>
  );
}
