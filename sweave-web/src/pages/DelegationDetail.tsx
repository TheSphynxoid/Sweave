/**
 * Delegation detail page (R4.1 step 3 scaffold; status taxonomy
 * slice 2026-09-10).
 *
 * The route reads the delegation id from the URL
 * (``/delegations/:id``), fetches the delegation record via the
 * existing client, and renders the honest-failure taxonomy:
 *
 *  * A `turn_timeout_exceeded_*` error is NOT a failure — a calm
 *    amber "Timed out (after N min)" pill plus explanatory copy
 *    and a "state at timeout" hint (partial output if persisted,
 *    else an explicit none-persisted note).
 *  * Non-timeout failures keep the red treatment.
 *  * Completed delegations surface elapsed runtime when the
 *    timestamps exist.
 *
 * The full detail surface (composed prompt, tool timeline, tokens
 * + cost, parts-model output) remains pending R4.3 via the shared
 * DetailView modal.
 */
import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ScaffoldPage } from "@/components/ScaffoldPage";
import { StatusPill } from "@/components/delegation/StatusPill";
import { Clock, Network } from "lucide-react";
import { api } from "@/api/client";
import {
  isTimeoutDelegation,
  parseTurnTimeout,
  formatRuntime,
} from "@/lib/delegation/taxonomy";
import type { Delegation } from "@/types";

export function DelegationDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data, isLoading } = useQuery<Delegation>({
    queryKey: ["delegation", id],
    queryFn: () => api.getDelegation(id!),
    enabled: !!id,
  });

  return (
    <ScaffoldPage
      surface="Delegation"
      description="Composed prompt, tool timeline, tokens + cost, and the output text — full surface pending R4.3; the status taxonomy lives here now."
      pendingMilestone="R4.3"
      testId="page-delegation-detail"
    >
      <div className="space-y-2">
        <div className="text-xs text-muted-foreground flex items-center justify-center gap-1">
          <Network size={12} />
          <span>
            id:{" "}
            <code
              data-testid="page-delegation-detail-id"
              className="font-mono text-foreground"
            >
              {id ?? "(missing)"}
            </code>
          </span>
        </div>
        <div className="text-xs">
          <Link
            to="/children"
            className="text-primary hover:underline"
            data-testid="page-delegation-detail-back-to-children"
          >
            See this delegation in the Children tree →
          </Link>
        </div>
        {isLoading && (
          <p className="text-xs text-muted-foreground" data-testid="page-delegation-detail-loading">
            Loading delegation…
          </p>
        )}
        {data && <DelegationStatusBlock delegation={data} />}
      </div>
    </ScaffoldPage>
  );
}

function DelegationStatusBlock({ delegation }: { delegation: Delegation }) {
  const timedOut = isTimeoutDelegation(delegation);
  const timeout = timedOut ? parseTurnTimeout(delegation.error) : null;
  const hasOutput = !!delegation.output.trim();
  const runtime =
    delegation.status === "done"
      ? formatRuntime(delegation.started_at, delegation.completed_at)
      : null;
  return (
    <div className="space-y-2 text-left">
      <div className="flex items-center justify-center gap-2">
        <span className="font-medium text-foreground text-xs">{delegation.agent}</span>
        <span data-testid="delegation-status-pill-slot" className="inline-flex">
          <StatusPill
            status={delegation.status}
            error={delegation.error}
            startedAt={delegation.started_at}
            completedAt={delegation.completed_at}
            pillTestId="delegation-status-pill"
          />
        </span>
      </div>

      {timedOut && (
        <div
          data-testid="delegation-timeout"
          className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs leading-relaxed text-amber-700 dark:text-amber-300"
        >
          <p>
            The specialist ran out of its turn budget
            {timeout?.seconds ? ` (${Math.round(timeout.seconds / 60)} min limit)` : "" }; partial
            output may still be present.
          </p>
          {delegation.error && (
            <code
              data-testid="delegation-timeout-raw"
              className="mt-1 block font-mono text-[10px] text-muted-foreground"
            >
              {delegation.error}
            </code>
          )}
        </div>
      )}

      <div data-testid="delegation-state-at-timeout">
        {hasOutput ? (
          <pre className="whitespace-pre-wrap break-words font-sans text-xs leading-relaxed text-muted-foreground rounded border border-border p-2 max-h-48 overflow-auto">
            {delegation.output}
          </pre>
        ) : timedOut ? (
          <p className="text-xs italic text-muted-foreground/70">
            No output was persisted before the timeout.
          </p>
        ) : null}
      </div>

      {!timedOut && delegation.status === "failed" && delegation.error && (
        <p
          data-testid="delegation-error"
          className="rounded border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-xs leading-relaxed text-rose-700 dark:text-rose-300 break-words"
        >
          {delegation.error}
        </p>
      )}

      {runtime && (
        <p
          data-testid="delegation-status-runtime"
          className="flex items-center justify-center gap-1 text-[11px] text-muted-foreground"
        >
          <Clock size={11} />
          Completed in {runtime}.
        </p>
      )}
    </div>
  );
}
