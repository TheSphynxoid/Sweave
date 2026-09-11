/**
 * App root (M1.9 Step 1, R4.1 Step 3).
 *
 * Provider stack:
 *   QueryClientProvider (React Query; default 5 min stale time)
 *     -> WSProvider (single WebSocket; reconnects; topic dispatch)
 *       -> AppProvider (active project / session + notifications)
 *         -> BrowserRouter (Routes; Layout shell wraps every page)
 *
 * Pages:
 *   - /chat (M1.9 step 2)
 *   - /children (M1.9 step 3)
 *   - /plan (TRACKING_PLAN Phase A: read-only board over delegations)
 *   - /delegations/:id (R4.1 step 3 scaffold; R4.3 fills in)
 *   - /memory, /agents, /settings (R4.1 step 3 scaffolds; R4.4 fills in)
 */
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryProvider } from "@/context/QueryProvider";
import { AppProvider } from "@/context/AppProvider";
import { WSProvider } from "@/context/WSProvider";
import { Layout } from "@/components/Layout";
import { ChatPage } from "@/pages/Chat";
import { ChildrenPage } from "@/pages/Children";
import { PlanPage } from "@/pages/Plan";
import { MemoryPage } from "@/pages/Memory";
import { AgentsPage } from "@/pages/Agents";
import { SettingsPage } from "@/pages/Settings";
import { DelegationDetailPage } from "@/pages/DelegationDetail";
import { NotFoundPage } from "@/pages/NotFound";
import { ThemeApplier } from "@/components/ThemeApplier";
import { ChatLab } from "@/dev/chat-lab/ChatLab";
import "@/styles/globals.css";

export default function App() {
  return (
    <QueryProvider>
      <WSProvider>
        <AppProvider>
          <ThemeApplier />
          <BrowserRouter>
            <Routes>
              <Route path="/" element={<Layout />}>
                {/* Wave-1 entries: chat is the input funnel;
                    children is the output funnel. */}
                <Route index element={<Navigate to="/chat" replace />} />
                <Route path="chat" element={<ChatPage />} />
                <Route path="children" element={<ChildrenPage />} />
                {/* TRACKING_PLAN Phase A: read-only plan board
                    (Kanban + table + bugs lane over delegations). */}
                <Route path="plan" element={<PlanPage />} />
                {/* R4.1 step 3: designed scaffolds for the
                    surfaces whose feature work ships in
                    R4.3 (delegation detail) and R4.4 (memory
                    + agents + settings). The scaffolds are
                    honest: real layout, real empty state, a
                    "Pending R4.X" badge. Memory/Agents/Settings
                    content lands in R4.4; delegation detail in
                    R4.3. */}
                <Route path="delegations/:id" element={<DelegationDetailPage />} />
                <Route path="memory" element={<MemoryPage />} />
                <Route path="agents" element={<AgentsPage />} />
                <Route path="settings" element={<SettingsPage />} />
                {/* R4.2 step 2a: dev-only chat-lab visual test.
                    Gated by import.meta.env.DEV so the lab is
                    tree-shaken from the production bundle. */}
                {import.meta.env.DEV && (
                  <Route path="dev/chat-lab" element={<ChatLab />} />
                )}
                <Route path="*" element={<NotFoundPage />} />
              </Route>
            </Routes>
          </BrowserRouter>
        </AppProvider>
      </WSProvider>
    </QueryProvider>
  );
}
