/**
 * Sidebar (M1.9 Step 1, R4.1 Step 2, R4.4 polish).
 *
 * Modern agent-shell nav: brand header + collapse toggle, the project
 * switcher, the always-visible session tree, the primary funnels
 * (Chat / Children) and the pane shells (Memory / Agents / Settings).
 * Active items get a left accent bar (expanded) or a filled chip (collapsed);
 * section labels + counts keep it scannable. A ⌘K hint at the bottom opens
 * the command palette.
 *
 * Nav items are deliberately SEPARATE rounded buttons with a small gap
 * (space-y-1) rather than a margin-less connected toolbar — that is the
 * shadcn / agent-UI convention and reads cleaner at 36px height.
 */
import { NavLink, useLocation } from "react-router-dom";
import { type LucideIcon } from "lucide-react";
import {
  MessageSquare,
  Network,
  Brain,
  Users,
  Settings,
  ChevronLeft,
  ChevronRight,
  Command,
} from "lucide-react";
import { useState } from "react";
import { cn } from "@/utils/cn";
import { useApp } from "@/context/AppProvider";
import { useWS } from "@/context/WSProvider";
import { useUIStore } from "@/store/ui";
import { ProjectSessionTree } from "./ProjectSessionTree";
import { Separator } from "@/components/ui/separator";
import { Kbd } from "@/components/ui/kbd";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const FUNNELS = [
  { to: "/chat", label: "Chat", icon: MessageSquare },
  { to: "/children", label: "Children", icon: Network },
] as const;

const SCAFFOLDS = [
  { to: "/memory", label: "Memory", icon: Brain },
  { to: "/agents", label: "Agents", icon: Users },
  { to: "/settings", label: "Settings", icon: Settings },
] as const;

type NavItemDef = { to: string; label: string; icon: LucideIcon };

function NavItem({ item, collapsed }: { item: NavItemDef; collapsed?: boolean }) {
  const location = useLocation();
  const active = location.pathname === item.to || location.pathname.startsWith(item.to);
  const Icon = item.icon;

  if (collapsed) {
    return (
      <NavLink
        to={item.to}
        title={item.label}
        aria-label={item.label}
        data-testid={`nav-${item.to.slice(1)}`}
        className={cn(
          "grid h-10 w-10 place-items-center rounded-lg transition-colors mx-auto",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
          active
            ? "bg-primary/10 text-primary"
            : "text-muted-foreground hover:bg-muted hover:text-foreground",
        )}
      >
        <Icon size={18} className="shrink-0" />
      </NavLink>
    );
  }

  return (
    <NavLink
      to={item.to}
      data-testid={`nav-${item.to.slice(1)}`}
      className={cn(
        "group relative flex h-9 items-center gap-3 rounded-lg pl-4 pr-3 text-sm font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
        active
          ? "bg-primary/10 text-primary"
          : "text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      {active && (
        <span className="absolute left-0.5 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-full bg-primary" />
      )}
      <Icon size={17} className="shrink-0" />
      <span className="flex-1 truncate">{item.label}</span>
    </NavLink>
  );
}

export function Sidebar() {
  const [open, setOpen] = useState(true);
  const { activeSession } = useApp();
  const { state: wsState } = useWS();
  const setCommandOpen = useUIStore((s) => s.setCommandOpen);

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
        "flex min-h-0 flex-col overflow-hidden border-r border-border bg-sidebar text-sidebar-foreground transition-[width] duration-200 ease-in-out",
        open ? "w-64" : "w-[4.5rem]",
      )}
    >
      <div className="flex items-center justify-between h-14 px-3 border-b border-border shrink-0">
        {open ? (
          <div className="flex items-center gap-2 overflow-hidden">
            <div className="grid h-7 w-7 place-items-center rounded-md bg-primary text-primary-foreground font-bold text-sm shrink-0">
              S
            </div>
            <span className="text-base font-semibold tracking-tight truncate">Sweave</span>
          </div>
        ) : (
          <div className="mx-auto grid h-7 w-7 place-items-center rounded-md bg-primary text-primary-foreground font-bold text-sm">
            S
          </div>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 shrink-0"
          aria-label={open ? "Collapse sidebar" : "Expand sidebar"}
          onClick={() => setOpen(!open)}
        >
          {open ? <ChevronLeft size={16} /> : <ChevronRight size={16} />}
        </Button>
      </div>

      {/* The project/session tree owns ALL leftover height (min-h-0 so it
          can shrink) and scrolls internally — the nav + status rows below
          stay pinned instead of being pushed out of the viewport (the old
          max-h-[40vh] tree + non-shrinkable wrapper overflowed them). */}
      {open && (
        <div className="flex min-h-0 flex-1 flex-col border-b border-border p-3 pb-2">
          <ProjectSessionTree />
        </div>
      )}

      <nav aria-label="Primary" className="shrink-0 space-y-1 px-3 py-3">
        {open && (
          <p className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Funnels
          </p>
        )}
        {FUNNELS.map((item) => (
          <NavItem key={item.to} item={item} collapsed={!open} />
        ))}

        {open && (
          <p className="px-2 pt-4 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Panes
          </p>
        )}
        {SCAFFOLDS.map((item) => (
          <NavItem key={item.to} item={item} collapsed={!open} />
        ))}
      </nav>

      <Separator />
      <div className={cn("p-3 space-y-2", !open && "flex flex-col items-center")}>
        <Button
          variant="outline"
          onClick={() => setCommandOpen(true)}
          data-testid="command-palette-trigger"
          className={cn(
            "w-full justify-start gap-2 text-sm text-muted-foreground",
            !open && "w-10 justify-center px-0",
          )}
          title="Command palette"
        >
          <Command size={15} />
          {open && (
            <>
              <span className="flex-1 text-left">Command</span>
              <Kbd>⌘K</Kbd>
            </>
          )}
        </Button>
        <div
          className={cn(
            "flex items-center gap-2 px-1 text-xs text-muted-foreground",
            !open && "flex-col gap-1 px-0",
          )}
        >
          <span className={cn("w-2 h-2 rounded-full shrink-0", connClass)} />
          {open ? (
            <span className="capitalize">
              {wsState === "open" ? "Connected" : wsState}
            </span>
          ) : (
            <span className="sr-only">
              {wsState === "open" ? "Connected" : wsState}
            </span>
          )}
          {open && activeSession && (
            <Badge variant="muted" className="ml-auto truncate max-w-[8rem]">
              {activeSession.name}
            </Badge>
          )}
        </div>
      </div>
    </aside>
  );
}
