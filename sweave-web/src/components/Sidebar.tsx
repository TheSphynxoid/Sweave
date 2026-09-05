/**
 * Sidebar (M1.9 Step 1).
 *
 * Wave-1 nav: Chat (the input funnel) + Children (the output
 * funnel). Wave-2 nav (Memory / Agents workbench / Settings)
 * lands in step 5. The statusline vision from the plan (topbar
 * with project + path + session + conn state) is delivered in
 * this step; the topbar is a sub-component so the layout is
 * straightforward to read.
 */
import { NavLink, useLocation } from "react-router-dom";
import { MessageSquare, Network, ChevronLeft, ChevronRight } from "lucide-react";
import { useState } from "react";
import { cn } from "@/utils/cn";
import { useApp } from "@/context/AppProvider";
import { useWS } from "@/context/WSProvider";

const NAV = [
  { to: "/chat", label: "Chat", icon: MessageSquare },
  { to: "/children", label: "Children", icon: Network },
] as const;

export function Sidebar() {
  const [open, setOpen] = useState(true);
  const { activeProject, activeSession } = useApp();
  const { state: wsState } = useWS();
  const location = useLocation();

  const connLabel =
    wsState === "open"
      ? "Connected"
      : wsState === "connecting"
        ? "Connecting"
        : wsState === "reconnecting"
          ? "Reconnecting"
          : "Disconnected";
  const connClass =
    wsState === "open"
      ? "bg-emerald-500"
      : wsState === "reconnecting" || wsState === "connecting"
        ? "bg-amber-500"
        : "bg-rose-500";

  return (
    <aside
      data-testid="sidebar"
      className={cn(
        "flex flex-col border-r border-border bg-card transition-[width] duration-200",
        open ? "w-64" : "w-16",
      )}
    >
      <div className="flex items-center justify-between h-14 px-4 border-b border-border">
        {open && (
          <span className="text-base font-bold tracking-tight">Sweave</span>
        )}
        <button
          type="button"
          aria-label={open ? "Collapse sidebar" : "Expand sidebar"}
          onClick={() => setOpen(!open)}
          className="p-1.5 rounded hover:bg-muted"
        >
          {open ? <ChevronLeft size={18} /> : <ChevronRight size={18} />}
        </button>
      </div>
      {open && (
        <>
          <nav className="flex-1 overflow-y-auto p-3 space-y-1" aria-label="Primary">
            {NAV.map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                data-testid={`nav-${to.slice(1)}`}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-2 px-3 py-2 rounded text-sm transition-colors",
                    isActive || location.pathname.startsWith(to)
                      ? "bg-primary/10 text-primary"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )
                }
              >
                <Icon size={16} />
                <span>{label}</span>
              </NavLink>
            ))}
          </nav>
          <div className="border-t border-border p-3 space-y-1 text-xs text-muted-foreground">
            <div className="flex items-center gap-2">
              <span className={cn("w-2 h-2 rounded-full", connClass)} />
              <span>{connLabel}</span>
            </div>
            {activeProject && (
              <div className="truncate" title={activeProject.path}>
                {activeProject.name}
              </div>
            )}
            {activeSession && (
              <div className="truncate" title={activeSession.id}>
                {activeSession.name}
              </div>
            )}
          </div>
        </>
      )}
    </aside>
  );
}
