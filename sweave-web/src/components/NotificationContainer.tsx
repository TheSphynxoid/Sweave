/**
 * Notification container (M1.9 Step 1, R4.4 polish).
 *
 * Renders the in-memory notification list as a fixed bottom-right
 * toast stack with kind-aware icon + color and a slide-in animation.
 * The 5s TTL is handled by the AppProvider.
 */
import { useApp } from "@/context/AppProvider";
import { cn } from "@/utils/cn";
import { X, CheckCircle2, AlertTriangle, XCircle, Info } from "lucide-react";

const KIND_STYLE = {
  info: {
    icon: Info,
    className: "border-sky-500/30 bg-sky-500/10 text-sky-200",
    iconClass: "text-sky-400",
  },
  success: {
    icon: CheckCircle2,
    className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-200",
    iconClass: "text-emerald-400",
  },
  warning: {
    icon: AlertTriangle,
    className: "border-amber-500/30 bg-amber-500/10 text-amber-200",
    iconClass: "text-amber-400",
  },
  error: {
    icon: XCircle,
    className: "border-rose-500/30 bg-rose-500/10 text-rose-200",
    iconClass: "text-rose-400",
  },
} as const;

export function NotificationContainer() {
  const { notifications, dismissNotification } = useApp();
  return (
    <div
      data-testid="notification-container"
      className="fixed bottom-4 right-4 z-[60] flex flex-col gap-2 w-80 max-w-[calc(100vw-2rem)]"
    >
      {notifications.map((n) => {
        const style = KIND_STYLE[n.kind];
        const Icon = style.icon;
        return (
          <div
            key={n.id}
            role="status"
            data-testid={`notification-${n.kind}`}
            className={cn(
              "flex items-start gap-2.5 rounded-lg border px-3.5 py-2.5 shadow-lg backdrop-blur animate-in slide-in-from-bottom-2 fade-in-0",
              style.className,
            )}
          >
            <Icon size={16} className={cn("mt-0.5 shrink-0", style.iconClass)} />
            <span className="flex-1 text-sm leading-snug">{n.message}</span>
            <button
              type="button"
              onClick={() => dismissNotification(n.id)}
              aria-label="Dismiss"
              className="opacity-60 hover:opacity-100 transition-opacity shrink-0"
            >
              <X size={14} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
