/**
 * Notification container (M1.9 Step 1).
 *
 * Renders the in-memory notification list as a fixed bottom-right
 * toast stack. The notifications are short-lived (5s TTL by
 * default; the AppProvider handles auto-dismiss).
 */
import { useApp } from "@/context/AppProvider";
import { cn } from "@/utils/cn";
import { X } from "lucide-react";

const KIND_CLASS = {
  info: "border-blue-500/40 bg-blue-500/10 text-blue-700 dark:text-blue-300",
  success:
    "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  warning:
    "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  error: "border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-300",
} as const;

export function NotificationContainer() {
  const { notifications, dismissNotification } = useApp();
  return (
    <div
      data-testid="notification-container"
      className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm"
    >
      {notifications.map((n) => (
        <div
          key={n.id}
          role="status"
          data-testid={`notification-${n.kind}`}
          className={cn(
            "border rounded shadow px-3 py-2 text-sm flex items-start gap-2",
            KIND_CLASS[n.kind],
          )}
        >
          <span className="flex-1">{n.message}</span>
          <button
            type="button"
            onClick={() => dismissNotification(n.id)}
            aria-label="Dismiss"
            className="opacity-70 hover:opacity-100"
          >
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
