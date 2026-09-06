/**
 * Sidebar (M1.9 Step 1, R4.1 Step 2).
 *
 * R4.1 Step 2 nav backbone: project switcher (dropdown of all
 * projects) + session tree (always-visible list of the active
 * project's sessions, with the active session highlighted) +
 * inline create-session form (at the bottom of the tree) +
 * primary nav (Chat + Children, the input + output funnels).
 *
 * The session tree is the foundation of the chat/children
 * navigation: every surface scopes to the active session. The
 * inline create-session form closes the M1.9 funnel leak
 * (creating a session previously required leaving the chat).
 *
 * Memory / Agents / Settings are R4.4; the project-create entry
 * is R4.4 too (the R4.1 amendment: foundation nav only).
 */
import { NavLink, useLocation } from "react-router-dom";
import {
  MessageSquare,
  Network,
  ChevronLeft,
  ChevronRight,
  Brain,
  Users,
  Settings,
} from "lucide-react";
import { useState } from "react";
import { cn } from "@/utils/cn";
import { useApp } from "@/context/AppProvider";
import { useWS } from "@/context/WSProvider";
import { ProjectSwitcher } from "./ProjectSwitcher";
import { SessionTree } from "./SessionTree";

const FUNNELS = [
  { to: "/chat", label: "Chat", icon: MessageSquare },
  { to: "/children", label: "Children", icon: Network },
] as const;

const SCAFFOLDS = [
  { to: "/memory", label: "Memory", icon: Brain, milestone: "R4.4" },
  { to: "/agents", label: "Agents", icon: Users, milestone: "R4.4" },
  { to: "/settings", label: "Settings", icon: Settings, milestone: "R4.4" },
] as const;

export function Sidebar() {
  const [open, setOpen] = useState(true);
  const { activeSession } = useApp();
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
        open ? "w-72" : "w-16",
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
          <div className="p-3 space-y-3 border-b border-border">
            <ProjectSwitcher />
            <SessionTree />
          </div>
          <nav
            className="flex-1 overflow-y-auto p-3 space-y-1"
            aria-label="Primary"
          >
            <div className="px-2 py-1 text-[10px] uppercase tracking-wide text-muted-foreground">
              Funnels
            </div>
            {FUNNELS.map(({ to, label, icon: Icon }) => (
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
            <div className="px-2 py-1 mt-2 text-[10px] uppercase tracking-wide text-muted-foreground">
              Pane shells
            </div>
            {SCAFFOLDS.map(({ to, label, icon: Icon, milestone }) => (
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
                <span className="flex-1">{label}</span>
                <span className="text-[9px] px-1 py-0.5 rounded bg-amber-500/10 text-amber-700 border border-amber-500/30">
                  {milestone}
                </span>
              </NavLink>
            ))}
          </nav>
          <div className="border-t border-border p-3 space-y-1 text-xs text-muted-foreground">
            <div className="flex items-center gap-2">
              <span className={cn("w-2 h-2 rounded-full", connClass)} />
              <span>{connLabel}</span>
            </div>
            {activeSession && (
              <div
                className="truncate font-mono text-[10px]"
                title={activeSession.orchestrator_session_id ?? activeSession.id}
              >
                {activeSession.orchestrator_session_id
                  ? `orch: ${activeSession.orchestrator_session_id.slice(0, 12)}…`
                  : activeSession.id}
              </div>
            )}
          </div>
        </>
      )}
    </aside>
  );
}
