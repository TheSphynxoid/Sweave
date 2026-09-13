/**
 * Topbar (M1.9 Step 1, R4.4 polish, consolidated 2026-09-13).
 *
 * Slim statusline: project breadcrumb on the left, theme switcher on
 * the right. Session switching lives ONLY in the sidebar tree, the
 * command palette launcher + connection state live in the sidebar
 * status row — nothing here duplicates them.
 */
import { useApp } from "@/context/AppProvider";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";

export function Topbar() {
  const { activeProject } = useApp();

  return (
    <header className="relative z-50 h-14 flex items-center justify-between gap-4 border-b border-border bg-topbar/60 text-topbar-foreground backdrop-blur px-4 shadow-sm shadow-black/5 shrink-0">
      <div className="flex items-center gap-2 min-w-0 text-sm">
        {activeProject ? (
          <span className="font-medium text-foreground truncate max-w-[40ch]">
            {activeProject.name}
          </span>
        ) : (
          <span className="text-muted-foreground">No project selected</span>
        )}
      </div>

      <div className="flex items-center gap-2">
        <ThemeSwitcher />
      </div>
    </header>
  );
}
