/**
 * App root (M1.9 Step 1).
 *
 * Provider stack:
 *   QueryClientProvider (React Query; default 5 min stale time)
 *     -> WSProvider (single WebSocket; reconnects; topic dispatch)
 *       -> AppProvider (active project / session + notifications)
 *         -> BrowserRouter (Routes; Layout shell wraps every page)
 *
 * Pages imported here are placeholders for Step 1; Step 2 ships
 * the chat surface, Step 3 ships the children live tree + detail
 * view, Step 4 retires the v1 vanilla UI.
 */
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryProvider } from "@/context/QueryProvider";
import { AppProvider } from "@/context/AppProvider";
import { WSProvider } from "@/context/WSProvider";
import { Layout } from "@/components/Layout";
import { ChatPage } from "@/pages/Chat";
import { ChildrenPage } from "@/pages/Children";
import { NotFoundPage } from "@/pages/NotFound";
import { ThemeApplier } from "@/components/ThemeApplier";
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
                <Route path="*" element={<NotFoundPage />} />
              </Route>
            </Routes>
          </BrowserRouter>
        </AppProvider>
      </WSProvider>
    </QueryProvider>
  );
}
