/**
 * Archived group (M1.13 step 5, ruling 2026-09-11: orphan stats
 * preserved but no clutter).
 *
 * ONE compact collapsed row at the bottom of the Children tab:
 * "Archived · N projects · M delegations". Expanding it lists
 * per-project AGGREGATE summaries — counts, by-status, by-kind,
 * last activity — sourced from the backend's
 * ``archived_projects`` rows (``?include_archived=true``; live
 * store rows unioned with the persisted ``~/.sweave/archived``
 * index rows). Records keep their stats forever; the archived
 * group never renders individual delegation rows.
 *
 * Token note: the backend aggregate shape (sweave/runtime/
 * delegation_archive.py ``archive_group_entry``) does NOT expose a
 * token sum, so none is rendered — nothing invented here.
 */
import { useState } from "react";
import { ChevronDown, ChevronRight, Archive } from "lucide-react";
import type { ArchivedProjectSummary } from "@/types";

export function ArchivedGroup({
  archivedProjects,
}: {
  archivedProjects: ArchivedProjectSummary[];
}) {
  const [expanded, setExpanded] = useState(false);
  if (archivedProjects.length === 0) return null;
  const total = archivedProjects.reduce((s, p) => s + (p.total || 0), 0);

  return (
    <section
      data-testid="archived-group"
      className="rounded border border-border bg-muted/20 overflow-hidden"
    >
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        data-testid="archived-group-toggle"
        aria-label={expanded ? "Collapse archived group" : "Expand archived group"}
        className="flex items-center gap-2 px-3 py-2 text-sm text-muted-foreground hover:bg-muted/40 w-full text-left"
      >
        {expanded ? (
          <ChevronDown size={14} />
        ) : (
          <ChevronRight size={14} />
        )}
        <Archive size={14} />
        <span data-testid="archived-group-row">
          {expanded ? (
            <>Archived · {archivedProjects.length} projects · {total} delegations</>
          ) : (
            <span data-testid="archived-group-summary">
              Archived · {archivedProjects.length} projects · {total} delegations
            </span>
          )}
        </span>
      </button>
      {expanded && (
        <ul className="p-2 space-y-1" data-testid="archived-project-list">
          {archivedProjects.map((p) => (
            <li
              key={p.project_name}
              data-testid={`archived-project-${p.project_name}`}
              className="flex items-center gap-3 px-2 py-1.5 rounded text-xs hover:bg-muted/40"
            >
              <span className="font-medium text-foreground truncate">
                {p.project_name}
              </span>
              <span className="text-muted-foreground">
                {p.total} delegation{p.total === 1 ? "" : "s"}
              </span>
              <span className="flex items-center gap-1.5">
                {Object.entries(p.by_status || {}).map(([status, n]) => (
                  <span key={status} data-testid={`archived-status-${status}`}>
                    {status} {n}
                  </span>
                ))}
              </span>
              <span className="flex items-center gap-1.5 text-muted-foreground/80">
                {Object.entries(p.by_kind || {}).map(([kind, n]) => (
                  <span key={kind} data-testid={`archived-kind-${kind}`}>
                    {kind} {n}
                  </span>
                ))}
              </span>
              {p.last_created_at && (
                <span
                  className="ml-auto text-[10px] text-muted-foreground whitespace-nowrap"
                  title={`Archived ${p.archived_at ?? "?"}`}
                >
                  last activity {p.last_created_at.slice(0, 10)}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
