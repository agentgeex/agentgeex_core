/**
 * App.tsx — AgentGeeX SPA
 *
 * Shape (after Feng's second correction, 2026-09-16): **a collection of finished page cases**.
 *  - Left nav organized by page type (dashboard / list / form / detail / auth / error / components / sections)
 *  - Each nav item = one complete runnable page case; opening it shows the whole page
 *  - **Components have been moved down into each case page**; no separate "base components" nav layer
 *  - **No displays aimed at human programmers** ("page parts" / "part reverse index" removed) —
 *    this demo targets Agents: an Agent reads the case page source to learn the usage
 *
 * Route layering (2026-09-28 Feng acceptance feedback: auth page standalone):
 *  - **Standalone auth page `/login`**: outside AppShell, a full-screen self-contained login page,
 *    without sidebar / workspace tab bar (in a real app it appears before the admin shell).
 *  - **All other routes**: inside AppShell (with sidebar + multi-window tab bar).
 *
 * Stack: Refine (Headless core) + React Router + Tailwind + shadcn base.
 */
import { Refine } from "@refinedev/core";
import routerBindings, {
  UnsavedChangesNotifier,
} from "@refinedev/react-router-v6";
import * as React from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { usePageRefreshVersion } from "@/lib/page-refresh";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AppShell } from "@/components/shell";
import { TabsProvider } from "@/components/tabs/TabsContext";
import { LockScreenProvider } from "@/components/lock-screen";
import { ToastProvider } from "@/components/ui/toast";

// Standalone auth page (outside AppShell)
import AuthLoginPage from "@/pages/auth-login";
import { DataGate } from "@/components/agentgeex/data-gate";
import { useBodyUnlock } from "@/hooks/use-body-unlock";

// ===== AgentGeeX admin business pages (Feng 2026-09-30 design notes) =====
import SystemDashboardPage from "@/pages/agentgeex/system";
import QuickAccessPage from "@/pages/agentgeex/quick-access";
import AgentsPage from "@/pages/agentgeex/agents";
import { AgentProfilePage } from "@/pages/agentgeex/agent-profile";
import AgentDetailPage from "@/pages/agentgeex/agent-detail";
import SessionsPage from "@/pages/agentgeex/sessions";
import TasksPage from "@/pages/agentgeex/tasks";
import ExecutionsPage from "@/pages/agentgeex/executions";
import QuickCmdsPage from "@/pages/agentgeex/quick-cmds";
import SkillsPage from "@/pages/agentgeex/skills";
import HostsPage from "@/pages/agentgeex/hosts";
import HostEditPage from "@/pages/agentgeex/host-edit";
import TaskEditPage from "@/pages/agentgeex/task-edit";
import ExecutionEditPage from "@/pages/agentgeex/execution-edit";
import SkillEditPage from "@/pages/agentgeex/skill-edit";
import NoticesPage from "@/pages/agentgeex/notices";
import ProjectsPage from "@/pages/agentgeex/projects";
import ProjectGroupsPage from "@/pages/agentgeex/project-groups";
import PlansPage from "@/pages/agentgeex/plans";
import EntityDetailPage from "@/pages/agentgeex/entity-detail";
import PlanFlowRoute from "@/pages/agentgeex/plan-flow-route";
import PersonalCenterPage from "@/pages/personal-center";
// ★ Data exit (2026-10-01 read-only wiring to the real backend):
//   - dataProvider: Refine native data interface → @refinedev/simple-rest + VITE_API_URL
//   - authProvider：/auth/*（JWT）
import { dataProvider } from "@/providers/dataProvider";
import { authProvider } from "@/providers/authProvider";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});


/** Wrap the routes: remount the current page when the refresh version changes (key change = remount = re-read data) */
function RefreshableRoutes({ children }: { children: React.ReactNode }) {
  const v = usePageRefreshVersion();
  return <React.Fragment key={v}>{children}</React.Fragment>;
}

export function App() {
  // [2026-10-20] Mask-leak self-healing: prevent an abnormal Radix overlay unmount from permanently locking body (whole window unclickable)
  useBodyUnlock();
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ToastProvider>
        <Refine
          dataProvider={dataProvider}
          authProvider={authProvider}
          routerProvider={routerBindings}
          options={{ syncWithLocation: false, warnWhenUnsavedChanges: false }}
        >
          <Routes>
            {/* ★ Standalone auth page: outside AppShell (no sidebar / no tab bar) */}
            <Route path="/login" element={<AuthLoginPage />} />

            {/* ★ All other routes: wrapped inside AppShell (including TabsProvider) */}
            <Route
              path="*"
              element={
                <TabsProvider>
                  <LockScreenProvider>
                  {/* ★ Data gate (2026-10-20): do not render business pages before real data arrives —
                      credentials invalid → go to /login; backend unavailable → error page. Never fake it with seed mock. */}
                  <DataGate>
                  <AppShell>
                    {/* [2026-10-28 Feng] The nav refresh button → version number +1 → this remounts the current route page,
                        the page re-reads the store, data is fresh. (navigating to the same route does not remount, hence "clicked, nothing happened") */}
                    <RefreshableRoutes>
                    <Routes>
                      {/* Entry: lands on "system status" by default */}
                      <Route path="/" element={<Navigate to="/system" replace />} />

                      {/* Personal center (target of top bar "user settings → personal center", aligned with vue-admin-tpl) */}
                      <Route path="/personal/center" element={<PersonalCenterPage />} />

                      {/* ===== AgentGeeX admin business pages ===== */}
                      <Route path="/system" element={<SystemDashboardPage />} />
                      <Route path="/quick-access" element={<QuickAccessPage />} />
                      <Route path="/agents" element={<AgentsPage />} />
                      <Route path="/agents/:id" element={<AgentDetailPage />} />
                      {/* 2026-10-31 Feng: agent profile (identity / environment / persona files / instructions) —
                          separate from the console, reached via "details" on the card. Must be written after :id;
                          more specific paths taking precedence in this route table is explicit, not match-order dependent. */}
                      <Route path="/agents/:agentId/profile" element={<AgentProfilePage />} />
                      <Route path="/sessions" element={<SessionsPage />} />
                      <Route path="/tasks" element={<TasksPage />} />
                      <Route path="/executions" element={<ExecutionsPage />} />
                      <Route path="/quick-cmds" element={<QuickCmdsPage />} />
                      <Route path="/skills" element={<SkillsPage />} />
                      <Route path="/hosts" element={<HostsPage />} />
                      {/* [2026-10-30] Host edit = page window (no dialog);
                          the static /edit segment must come before /hosts/:id */}
                      <Route path="/hosts/:id/edit" element={<HostEditPage />} />
                      <Route path="/notices" element={<NoticesPage />} />
                      <Route path="/projects" element={<ProjectsPage />} />
                      {/* Detail pages (2026-10-26 Feng: every entity must have a detail view) */}
                      <Route path="/projects/:id" element={<EntityDetailPage kind="project" />} />
                      {/* [2026-11-01] Edit = page window (same rule as host edit); the /edit segment must come before /:id */}
                      <Route path="/tasks/:id/edit" element={<TaskEditPage />} />
                      <Route path="/executions/:id/edit" element={<ExecutionEditPage />} />
                      <Route path="/skills/:id/edit" element={<SkillEditPage />} />
                      <Route path="/tasks/:id" element={<EntityDetailPage kind="task" />} />
                      <Route path="/sessions/:id" element={<EntityDetailPage kind="session" />} />
                      <Route path="/executions/:id" element={<EntityDetailPage kind="execution" />} />
                      <Route path="/skills/:id" element={<EntityDetailPage kind="skill" />} />
                      <Route path="/notices/:id" element={<EntityDetailPage kind="notice" />} />
                      <Route path="/hosts/:id" element={<EntityDetailPage kind="host" />} />
                      <Route path="/plans/:id" element={<EntityDetailPage kind="plan" />} />
                      <Route path="/project-groups/:id" element={<EntityDetailPage kind="project-group" />} />
                      <Route path="/quick-cmds/:id" element={<EntityDetailPage kind="quick-cmd" />} />
                      <Route path="/project-groups" element={<ProjectGroupsPage />} />
                      <Route path="/plans" element={<PlansPage />} />
                      <Route path="/plans/:planId/flow" element={<PlanFlowRoute />} />

                      {/* Fallback: the example pages (/list, /form, /gallery…) were deleted on 2026-11-18,
                          so old bookmarks/tabs land here and always go back to system status.
                          Old tabs themselves are cleaned up by TabsContext's REMOVED_PATHS. */}
                      <Route path="*" element={<Navigate to="/system" replace />} />
                    </Routes>
                    </RefreshableRoutes>
                  </AppShell>
                  </DataGate>
                  </LockScreenProvider>
                </TabsProvider>
              }
            />
          </Routes>
          <UnsavedChangesNotifier />
          {/* ★ Tab title: self-managed (replaces Refine's DocumentTitleHandler,
              which falls back to the framework default "Refine" when no route title exists).
              Implementation: the useDocumentTitle call in components/shell.tsx. */}
        </Refine>
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
