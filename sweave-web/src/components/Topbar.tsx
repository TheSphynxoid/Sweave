/**
 * Topbar (M1.9 Step 1).
 *
 * Statusline vision from the plan: project + path + session +
 * WS connection. The right side carries the theme switcher
 * (drop-down, persists to localStorage) -- the customization
 * ruling from the plan.
 */
import { useMemo } from "react";
import { useApp } from "@/context/AppProvider";
import { useWS } from "@/context/WSProvider";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";
import { cn } from "@/utils/cn";

export function Topbar() {
  const { activeProject, activeSession } = useApp();
  const { state: wsState } = useWS();

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
    <header className="h-14 flex items-center justify-between border-b border-border bg-card px-6">
      <div className="flex items-center gap-4 min-w-0 text-sm">
        <div className="flex items-center gap-2 text-muted-foreground">
          <span className="font-medium text-foreground">
            {activeProject?.name ?? "No project"}
          </span>
          {activeProject && (
            <span className="truncate max-w-[40ch]" title={activeProject.path}>
              {activeProject.path}
            </span>
          )}
        </div>
        {activeSession && (
          <>
            <span className="text-border">/</span>
            <div className="flex items-center gap-2 min-w-0">
              <span className="font-medium text-foreground truncate max-w-[24ch]" title={activeSession.id}>
                {activeSession.name}
              </span>
              {activeSession.orchestrator_session_id && (
                <span
                  className="font-mono text-[10px] text-muted-foreground truncate max-w-[24ch]"
                  title={activeSession.orchestrator_session_id}
                  data-testid="orchestrator-session-id"
                >
                  orch: {activeSession.orchestrator_session_id.slice(0, 12)}…
                </span>
              )}
            </div>
          </>
        )}
      </div>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span
            data-testid="ws-state"
            className={cn(
              "w-2 h-2 rounded-full",
              wsState === "open" && "bg-emerald-500",
              wsState === "reconnecting" && "bg-amber-500",
              wsState === "connecting" && "bg-amber-500",
              wsState === "closed" && "bg-rose-500",
            )}
          />
          <span>{connLabel}</span>
        </div>
        <ThemeSwitcher />
      </div>
    </header>
  );
}
