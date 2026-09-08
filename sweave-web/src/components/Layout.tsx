/**
 * Layout shell (M1.9 Step 1).
 *
 * Renders the sidebar + topbar + main content. The layout is
 * sticky-pinned to the viewport (CSS grid; full-viewport).
 * Outlet (react-router) hosts the current page.
 */
import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { NotificationContainer } from "./NotificationContainer";
import { CreateProjectDialog } from "./CreateProjectDialog";
import { CommandPalette } from "./CommandPalette";

export function Layout() {
  return (
    <div className="h-screen w-screen flex bg-background text-foreground">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <Topbar />
        <main className="flex-1 overflow-auto" data-testid="main">
          <Outlet />
        </main>
      </div>
      <NotificationContainer />
      <CreateProjectDialog />
      <CommandPalette />
    </div>
  );
}
