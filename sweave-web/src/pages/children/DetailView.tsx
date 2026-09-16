/**
 * Delegation detail view (rescoped Step 4a — tabbed).
 *
 * Renders the data from ``GET /api/delegations/{id}/detail`` across
 * five tabs:
 *   Overview | Transcript | Tools | Prompt | Tokens/Status
 *
 * The tab *content* components are container-agnostic (see
 * `./detail/sections.tsx`): they take props + own local state only
 * and assume no modal (no portal, no document.body, no Escape hook)
 * so they can graduate into a docked pane later (ruling Q6). This
 * component owns the modal chrome — the portal to document.body, the
 * overlay, the close affordance, the Esc handler, and the live poll
 * wiring (4c-frontend).
 *
 * Live (4c-frontend): the detail query refetches on a ~4s cadence
 * while the delegation is running/queued (derived from the latest
 * status_timeline entry), and stops once settled. The three standing
 * WS events (delegation.status_changed + specialist.escalated/
 * resolved) still invalidate, so a status transition snaps the pane
 * to settled immediately. Settled = no poll, no live affordance.
 */
import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "@/api/client";
import { useWS } from "@/context/WSProvider";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  OverviewTab,
  PromptTab,
  ToolsTab,
  TokensStatusTab,
  TranscriptTab,
} from "./detail/sections";
import type { DelegationStatus, DelegationDetail } from "@/types";

export type DetailTab = "overview" | "transcript" | "tools" | "prompt" | "tokens";

export interface DetailViewProps {
  delegationId: string;
  onClose: () => void;
  /** Where to open the tabbed surface (4b: cards open at Transcript). */
  initialTab?: DetailTab;
}

/** Poll cadence while a record is live (ruling Q3: 3–5s). */
const LIVE_POLL_MS = 4000;

/** Derive liveness from the status timeline (the latest causal entry). */
function isLive(detail: DelegationDetail | undefined): boolean {
  if (!detail || !detail.status_timeline || detail.status_timeline.length === 0) {
    return false;
  }
  const latest = detail.status_timeline[detail.status_timeline.length - 1];
  return latest.status === "running" || latest.status === "queued";
}

export function DetailView({ delegationId, onClose, initialTab = "overview" }: DetailViewProps) {
  const [tab, setTab] = useState<DetailTab>(initialTab);

  const { data, isLoading, error, refetch } = useQuery<DelegationDetail>({
    queryKey: ["delegation-detail", delegationId],
    queryFn: () => api.getDelegationDetail(delegationId),
    // 4c-frontend: live poll while running/queued, idle once settled.
    refetchInterval: (query) => (isLive(query.state.data) ? LIVE_POLL_MS : false),
  });

  // Escalation fetched once here (single source for the Overview tab;
  // the old inline section is replaced by OverviewTab's render of it).
  const { data: escalation } = useQuery({
    queryKey: ["escalation", delegationId],
    queryFn: () => api.getEscalation(delegationId),
  });

  // Esc closes the modal; the chrome lives here, not in the tabs.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // 4c-frontend: WS pulse — a status transition (or escalation
  // resolution) snaps the pane; the existing 3-event contract is
  // preserved. We refetch immediately so a transition to settled
  // stops the interval without waiting out the 4s tick. The dev lab
  // renders without a WSProvider; default to unsubscribed there.
  let subscribe: ((event: string, handler: () => void) => () => void) | null = null;
  try {
    subscribe = useWS().subscribe;
  } catch {
    subscribe = null;
  }
  useEffect(() => {
    if (!subscribe) return;
    const offs = [
      subscribe("delegation.status_changed", () => void refetch()),
      subscribe("specialist.escalated", () => void refetch()),
      subscribe("specialist.escalation_resolved", () => void refetch()),
    ];
    return () => offs.forEach((off) => off());
  }, [subscribe, refetch]);

  // Portal to document.body: the modal mounts inside the thread
  // tree / Children tab, where ancestors with backdrop-blur,
  // animations, or sticky positioning trap `position: fixed` in
  // their stacking context (the overlay then paints UNDER the
  // composer + status bar). At body level z-50 wins unconditionally.
  return createPortal(
    <div
      data-testid="detail-modal"
      className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bg-card border border-border rounded shadow-xl w-full max-w-3xl max-h-[90vh] flex flex-col">
        <header className="flex items-center justify-between px-4 py-3 border-b border-border">
          <h2 className="text-sm font-semibold">Delegation {delegationId.slice(0, 8)}…</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            data-testid="detail-close"
            className="p-1 rounded hover:bg-muted"
          >
            <X size={16} />
          </button>
        </header>
        <div className="overflow-y-auto p-4">
          {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
          {error && (
            <p className="text-sm text-rose-500">
              Failed to load detail: {(error as Error).message}
            </p>
          )}
          {data && (
            <Tabs value={tab} onValueChange={(v) => setTab(v as DetailTab)} activationMode="manual">
              <TabsList className="flex-wrap">
                <TabsTrigger data-testid="tab-overview" value="overview">
                  Overview
                </TabsTrigger>
                <TabsTrigger data-testid="tab-transcript" value="transcript">
                  Transcript
                </TabsTrigger>
                <TabsTrigger data-testid="tab-tools" value="tools">
                  Tools
                </TabsTrigger>
                <TabsTrigger data-testid="tab-prompt" value="prompt">
                  Prompt
                </TabsTrigger>
                <TabsTrigger data-testid="tab-tokens" value="tokens">
                  Tokens/Status
                </TabsTrigger>
              </TabsList>

              <TabsContent value="overview" data-testid="tabcontent-overview">
                <OverviewTab
                  delegationId={delegationId}
                  escalation={escalation ?? null}
                  detail={data}
                />
              </TabsContent>
              <TabsContent value="transcript" data-testid="tabcontent-transcript">
                <TranscriptTab transcript={data.transcript} />
              </TabsContent>
              <TabsContent value="tools" data-testid="tabcontent-tools">
                <ToolsTab tools={data.tool_timeline} />
              </TabsContent>
              <TabsContent value="prompt" data-testid="tabcontent-prompt">
                <PromptTab composed={data.composed_prompt} />
              </TabsContent>
              <TabsContent value="tokens" data-testid="tabcontent-tokens">
                <TokensStatusTab tokens={data.tokens} timeline={data.status_timeline} />
              </TabsContent>
            </Tabs>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** Re-export so callers/tests can name the live predicate if needed. */
export const __isLive = isLive;
export type { DelegationStatus };
