/**
 * Memory page (R4.1 step 3 scaffold).
 *
 * R4.4 will fill this with the M1.2-era recall/reflect/retain
 * surface (and a chat-owned memory + git-diff + synthesis
 * path -- the M1.7 transcript already does memory work in the
 * thread; the pane is the read-only inspector fallback per the
 * R4.4 plan). Until then, this is a designed scaffold.
 */
import { ScaffoldPage } from "@/components/ScaffoldPage";
import { Brain } from "lucide-react";

export function MemoryPage() {
  return (
    <ScaffoldPage
      surface="Memory"
      description="Recall, reflect, and retain across the project's three memory banks. Read-only inspector of the orchestrator's recall/reflect/retain loop."
      pendingMilestone="R4.4"
      testId="page-memory"
    >
      <div className="flex items-center justify-center gap-1 text-xs text-muted-foreground">
        <Brain size={12} />
        <span>Three banks: global / project-{`{name}`} / session-{`{id}`}</span>
      </div>
    </ScaffoldPage>
  );
}
