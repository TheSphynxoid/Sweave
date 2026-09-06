/**
 * Scaffold page shell (R4.1 step 3).
 *
 * Every R4 surface that doesn't have its feature work yet
 * ships as a "designed scaffold" -- a real layout, a real
 * empty state, and an honest "Pending R4.X" badge so the
 * user knows the feature is on the roadmap. This is the
 * scaffold-first ruling from the R4 hub.
 *
 * The shell is intentionally small and shared (per the
 * scaffold-first definition: "layout + nav + empty states
 * only; zero data wiring"). Surfaces that need data wiring
 * upgrade to a real page in their respective milestone.
 */
import type { ReactNode } from "react";
import { Construction, ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";
import { cn } from "@/utils/cn";

export interface ScaffoldPageProps {
  /** Short noun describing the surface (e.g. "Memory", "Agents", "Settings", "Delegation"). */
  surface: string;
  /** One-line description of what this surface will become. */
  description: string;
  /** The R-milestone that will fill this scaffold (e.g. "R4.4", "R4.3"). */
  pendingMilestone: string;
  /** Optional body content (the designed empty state). */
  children?: ReactNode;
  /** Optional back link target (defaults to /chat -- the input funnel). */
  backTo?: string;
  /** Optional data-testid; defaults to the surface slug. */
  testId?: string;
}

export function ScaffoldPage({
  surface,
  description,
  pendingMilestone,
  children,
  backTo = "/chat",
  testId,
}: ScaffoldPageProps) {
  return (
    <div
      data-testid={testId ?? `scaffold-${slugify(surface)}`}
      className="h-full flex flex-col items-center justify-center p-8 text-center"
    >
      <div className="max-w-md space-y-4">
        <div className="flex items-center justify-center">
          <div className="w-12 h-12 rounded-full bg-muted flex items-center justify-center">
            <Construction size={20} className="text-muted-foreground" />
          </div>
        </div>
        <div className="space-y-1">
          <div className="flex items-center justify-center gap-2">
            <h1
              data-testid={`scaffold-${slugify(surface)}-title`}
              className="text-xl font-semibold tracking-tight"
            >
              {surface}
            </h1>
            <span
              data-testid={`scaffold-${slugify(surface)}-badge`}
              className={cn(
                "px-2 py-0.5 text-[10px] font-medium rounded-full",
                "bg-amber-500/10 text-amber-700 border border-amber-500/30",
              )}
            >
              Pending {pendingMilestone}
            </span>
          </div>
          <p
            data-testid={`scaffold-${slugify(surface)}-description`}
            className="text-sm text-muted-foreground"
          >
            {description}
          </p>
        </div>
        {children && <div className="pt-2">{children}</div>}
        <div className="pt-2">
          <Link
            to={backTo}
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft size={12} />
            Back to Chat
          </Link>
        </div>
      </div>
    </div>
  );
}

function slugify(s: string): string {
  return s.toLowerCase().replace(/\s+/g, "-");
}
