/**
 * Delegation detail page (R4.1 step 3 scaffold).
 *
 * R4.3 will fill this with the full detail surface:
 * composed prompt, tool timeline, tokens + cost, and the
 * output text (M1.9's parts-model traces via
 * ``GET /api/delegations/{id}/detail``). The children page
 * currently renders a DetailView modal; the route is a
 * navigable deep-link (the M1.9 funnel-leak #4 closes
 * through this). Until then, this is a designed scaffold.
 *
 * The route reads the delegation id from the URL
 * (``/delegations/:id``); a real implementation would
 * fetch via the existing client.ts endpoint and render
 * the same fields the modal shows. The scaffold shows
 * the id so the user can verify the deep-link works.
 */
import { useParams, Link } from "react-router-dom";
import { ScaffoldPage } from "@/components/ScaffoldPage";
import { Network } from "lucide-react";

export function DelegationDetailPage() {
  const { id } = useParams<{ id: string }>();
  return (
    <ScaffoldPage
      surface="Delegation"
      description="Composed prompt, tool timeline, tokens + cost, and the output text (M1.9 parts-model traces via /api/delegations/{id}/detail)."
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
      </div>
    </ScaffoldPage>
  );
}
