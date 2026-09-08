/**
 * Topbar (M1.9 Step 1, R4.4 polish).
 *
 * Statusline: project + session breadcrumbs on the left, a command-palette
 * launcher + connection pill + theme switcher on the right.
 */
import { useMemo } from "react";
import { ChevronRight, Search } from "lucide-react";
import { useApp } from "@/context/AppProvider";
import { useWS } from "@/context/WSProvider";
import { useUIStore } from "@/store/ui";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";
import { Kbd } from "@/components/ui/kbd";
import { cn } from "@/utils/cn";

export function Topbar() {
  const { activeProject, activeSession } = useApp();
  const { state: wsState } = useWS();
  const setCommandOpen = useUIStore((s) => s.setCommandOpen);

  const connLabel = useMemo(() => {
    switch (wsState) {
      case "open":
        return "Connected";
      case "connecting":
        return "Connecting";
      case "reconnecting":
        return "Reconnecting";
      case "closed":
        return "Disconnected";
    }
  }, [wsState]);

  return (
    <header className="h-14 flex items-center justify-between gap-4 border-b border-border bg-card/60 backdrop-blur px-4 shrink-0">
      <div className="flex items-center gap-1.5 min-w-0 text-sm">
        {activeProject ? (
          <>
            <span className="font-medium text-foreground truncate max-w-[28ch]">
              {activeProject.name}
            </span>
            {activeSession && (
              <>
                <ChevronRight size={14} className="text-muted-foreground shrink-0" />
                <span className="font-medium text-foreground truncate max-w-[24ch]">
                  {activeSession.name}
                </span>
              </>
            )}
          </>
        ) : (
          <span className="text-muted-foreground">No project selected</span>
        )}
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setCommandOpen(true)}
          data-testid="topbar-command"
          className="flex h-9 items-center gap-2 rounded-md border border-border bg-background px-3 text-xs text-muted-foreground transition-colors hover:bg-muted"
        >
          <Search size={14} />
          <span>Search or jump to…</span>
          <Kbd>⌘K</Kbd>
        </button>

        <div
          data-testid="ws-state"
          className="flex h-9 items-center gap-1.5 rounded-md border border-border bg-background px-2.5 text-xs text-muted-foreground"
        >
          <span
            className={cn(
              "w-2 h-2 rounded-full",
              wsState === "open" && "bg-emerald-500",
              wsState === "reconnecting" && "bg-amber-500",
              wsState === "connecting" && "bg-amber-500",
              wsState === "closed" && "bg-rose-500",
            )}
          />
          <span className="hidden sm:inline">{connLabel}</span>
        </div>

        <ThemeSwitcher />
      </div>
    </header>
  );
}
