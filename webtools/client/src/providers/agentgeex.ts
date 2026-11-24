/**
 * providers/agentgeex.ts — AgentGeeX business data access layer (semantic hooks)
 *
 * Role: pages never read mocks directly, nor assemble Refine query params directly;
 *       they fetch uniformly through the semantic hooks in this file. This way:
 *         ① page code stays clean;
 *         ② when the backend is connected only this layer + the dataProvider url mapping change; pages change zero.
 *
 * [2026-10-01 read-only hookup to the real backend]
 *   - Data source: the runtime store in `mock/agentgeex.ts` (seed as fallback).
 *   - On SPA start, `hydrateFromApi()` pulls a snapshot from the Win3 BFF (VITE_API_URL) into the store.
 *   - The semantic hook signatures pages read are **unchanged** (synchronous), so page code changes zero.
 *   - When the backend is unreachable it falls back to seed mocks (UI still renders; `dataSource()` tells the origin).
 */
import {
  store, hydrate,
  type AgentRow, type SessionRow, type TaskRow, type ExecutionRow,
  type ProjectRow, type ProjectGroupRow, type PlanRow, type SkillRow, type DashboardStats,
  type HostRow, type NoticeRow,
  type Snapshot,
  sessionsOf, tasksOf, executionsOf, projectName,
  type QuickEntryRow,
} from "@/mock/agentgeex";
import { api, agentsApi, dashboardApi, getToken, isTokenExpired, hostsApi, skillsApi, tasksApi, sessionRuntimeApi, sessionMessagesApi, quickEntryApi } from "@/providers/api";
import { notifyPageRefreshed } from "@/lib/page-refresh";
import { executionsApi, plansApi, type PlanGraph, quickCmdApi, type QuickCmd, type QuickCmdListResult, type QuickCmdRunInput, type QuickCmdRunResult, type QuickCmdByTaskResult, type QuickCmdParam, type QuickCmdMeta, type QuickCmdRoleLimit, type QuickCmdParamType, type QuickCmdValueSource } from "@/providers/api";
import i18n from "@/i18n";

export type {
  AgentRow, SessionRow, TaskRow, ExecutionRow, ProjectRow, ProjectGroupRow, PlanRow, SkillRow, DashboardStats,
  HostRow, NoticeRow, QuickEntryRow,
};
export type {
  QuickCmd, QuickCmdParam, QuickCmdListResult, QuickCmdRunInput, QuickCmdRunResult,
  QuickCmdByTaskResult,
  QuickCmdMeta, QuickCmdRoleLimit, QuickCmdParamType, QuickCmdValueSource,
};

export { sessionsOf, tasksOf, executionsOf, projectName, agentsApi };

/* ------------------------------ Lists ------------------------------ */
export const listAgents = (): AgentRow[] => store.agents;

/** Whether this Agent can be selected to do work (Feng 2026-10-17):
 *  - templates (isTemplate / deployStatus=template / kind=template) are not dispatchable entities;
 *  - offline (status=offline, i.e. active_status=0) no longer receives new work.
 *  ★ Only for [selector candidates]; list/detail pages still show everything (incl. templates and offline agents,
 *    for management and audit).
 */
export const isAgentSelectable = (a: AgentRow): boolean => !isAgentUnusable(a);

/* [2026-11-04 Feng] Every "select Agent" dropdown: **label carries a [host_id] prefix**, **sorted by host**.
 *    Words: "put the host ID in brackets in front" "sort by host ID"
 *          "so we don't need a separate host ID filter, and there's no room anyway".
 *
 * Why sorting lives here (instead of being done per page):
 *   sorting = replacing a separate host filter control with "agents of the same host grouped together";
 *   there is no room for the control, but the information cannot be lost. Sorting in scattered places = some dropdowns
 *   by host, some alphabetically, so the user has to re-find the position each time they switch dropdowns.
 */

/** Visible suffix for "can this Agent still work now".
 *
 *  [2026-11-07 Feng] after listing offline / template agents in the dropdown too, they **must be marked**:
 *  listing them all is so they can be looked up (history sessions, history executions), not so work can be dispatched to them.
 *  Without a mark, the user selects one, clicks send, and only then eats a 400 — pushing "what the browser refuses to say"
 *  to the backend. So this suffix and "can be listed" stand together; cannot do only half.
 */
/** "This Agent is only a template" = a skeleton that was **never registered in OpenClaw**, not a real instance.
 *
 *  [2026-11-11 Feng] "the 8 templates should not appear either; none of them are registered in OpenClaw".
 *  Not registered = no session, no execution, no history — pure noise in any dropdown
 *  (select it and the session list is necessarily empty; nothing can be sent anyway).
 *
 *  ★ Difference from "offline" (critical): an offline Agent **has history**, it just stops taking new work;
 *    a template **has no history at all**. So the two are treated differently in "history lookup" entries.
 *
 *  Extracted separately so that the three-line check
 *  `a.kind === "template" || a.deployStatus === "template" || a.isTemplate`
 *  is no longer written four times in four places — repetition always misses one.
 */
export function isAgentTemplate(a: AgentRow): boolean {
  return !!a.isTemplate || a.kind === "template" || a.deployStatus === "template";
}

/** "This Agent cannot be used now" = offline or template.
 *  Use: **weaken the whole row** in the dropdown (not disable — Feng needs to be able to pick it to look up history,
 *  disabling would make it unreachable).
 */
export function isAgentUnusable(a: AgentRow): boolean {
  return isAgentTemplate(a) || a.status === "offline";
}

/* Not a React module → read the i18n singleton directly (language switch reloads the page, so no subscription needed). */
export function agentStateLabel(a: AgentRow): string {
  if (isAgentTemplate(a)) return i18n.t("miscUi.agentState.template");
  if (a.status === "offline") return i18n.t("miscUi.agentState.offline");
  return "";
}

export function agentStateSuffix(a: AgentRow): string {
  const label = agentStateLabel(a);
  return label ? ` · ${label}` : "";
}

/** Dropdown label: [host_id] name (id) [· role] [· offline/template] — each part can be switched off per site */
export function agentOptionText(
  a: AgentRow,
  opts: { withId?: boolean; withRole?: boolean; withState?: boolean } = {},
): string {
  const head = a.host_id ? `[${a.host_id}] ` : "";
  const name = a.name || a.id;
  const idPart = opts.withId === false ? "" : ` (${a.id})`;
  const rolePart = opts.withRole ? ` · ${a.role}` : "";
  /* [2026-11-07] By default **no** status suffix — the dropdown renders status as a badge instead (see AgentOptionLabel).
        Reason: a plain-text suffix cannot be spotted in a column of same-colored text; the user thinks a normal Agent is
        selected and only finds out on send. Greying the whole row reads as "not clickable" (it must be clickable, to look up
        history) — so status needs its own block. */
  const statePart = opts.withState ? agentStateSuffix(a) : "";
  return `${head}${name}${idPart}${rolePart}${statePart}`;
}

/** host-first sort: same host grouped together, then name, then id (for stability).
 *  Empty host goes last — putting it first would push "no host" incomplete data in front of the user.
 */
export function sortAgentsByHost(list: AgentRow[]): AgentRow[] {
  return [...list].sort((a, b) => {
    const ha = a.host_id || "\uffff";
    const hb = b.host_id || "\uffff";
    return ha.localeCompare(hb) || a.name.localeCompare(b.name) || a.id.localeCompare(b.id);
  });
}

/** [2026-11-07 Feng] Dropdown **value source** = all Agents (incl. offline, incl. templates).
 *
 *  [2026-11-11 fix] This function only "fetch all + sort by host", it does **no narrowing** —
 *  whether to narrow is decided by the caller based on that dropdown's **semantics** (two proven opposite cases):
 *    · "send to whom" type (top of the message send dialog) → filter with `isAgentSelectable`, 100→85
 *    · "look up history" type (①③ sender/receiver) → drop templates only (`isAgentTemplate`), keep offline (92)
 *  Putting the narrowing here = forcing two dropdowns with opposite purposes onto the same allowlist.
 *
 *  Feng's words (for the history-lookup type): "even if none are online, they must still be listed in the dropdown,
 *  because it may need to look something up."
 *
 *  ★ Why not use listSelectableAgents instead: it removes 15 (100 → 85:
 *    7 with active_status=0 + 8 templates). The removed offline Agents **are exactly the ones most needing lookup** —
 *    they are offline, but their history sessions and executions are all still there.
 */
export const listAgentOptions = (): AgentRow[] => sortAgentsByHost(store.agents);

/** Agents that can be dispatched (only for "actually sending" validation, **no longer for dropdown candidates**). */
export const listSelectableAgents = (): AgentRow[] =>
  sortAgentsByHost(store.agents.filter(isAgentSelectable));
export const listSessions = (): SessionRow[] => store.sessions;
export const listTasks = (): TaskRow[] => store.tasks;

/** task_id → task_labs (task logo deduped by labs; when a task_id is passed use it to match the same image). */
export function getTaskLabs(taskId?: string): string {
  if (!taskId) return "";
  return store.tasks.find((t) => t.id === taskId)?.labs ?? "";
}
export const listExecutions = (): ExecutionRow[] => store.executions;
export const listProjects = (): ProjectRow[] => store.projects;

/* ------------------- Project pinning (is_quick / quick_order) -------------------
 * [2026-11-17 15:07 Feng] "in all project dropdowns, pinned projects go to the top, because this project is in use,
 *   put it on top, then you can see it and use it. Also see if you can add a star, if you can add it, else forget it,
 *   but it must be sorted to the top."
 *
 * ★ The data already exists: `agx_project.is_quick` + `quick_order` (added earlier for "quick projects"),
 *   the BFF now exposes it. So this is not a new concept, just **using** the existing pin marker.
 * ★ Sorting goes through these two functions uniformly, no per-page comparator — otherwise the half-working state
 *   "pinned on the project page, not on the agent page" would appear sooner or later.
 * ★ Stable sort: unpinned items keep the caller's original order (each page has its own basis, e.g. headcount/name).
 */
export function sortByQuickProject<T>(rows: T[], idOf: (row: T) => string): T[] {
  const meta = new Map(store.projects.map((p) => [p.id, p]));
  return rows
    .map((row, i) => ({ row, i }))
    .sort((a, b) => {
      const pa = meta.get(idOf(a.row));
      const pb = meta.get(idOf(b.row));
      const qa = pa?.isQuick ? 1 : 0;
      const qb = pb?.isQuick ? 1 : 0;
      if (qa !== qb) return qb - qa;
      if (qa && qb) {
        const oa = pa?.quickOrder ?? 0;
        const ob = pb?.quickOrder ?? 0;
        if (oa !== ob) return oa - ob;
      }
      return a.i - b.i;
    })
    .map((x) => x.row);
}

/** Project name in the dropdown: pinned ones get a star — so others can see why it is on top. */
export function projectNameWithPin(pid: string, name: string): string {
  const p = store.projects.find((x) => x.id === pid);
  return p?.isQuick ? `★ ${name}` : name;
}
export const listProjectGroups = (): ProjectGroupRow[] => store.projectGroups;
export const listPlans = (): PlanRow[] => store.plans;
export { plansApi, executionsApi };
export type { PlanGraph };

/** Fetch plan flow chart data (async, on demand; does not go through the store). */
export const fetchPlanGraph = (planId: string): Promise<PlanGraph> =>
  plansApi.graph(planId);
export const fetchPlanStatus = (planId: string) => plansApi.status(planId);export const listSkills = (): SkillRow[] => store.skills;
export const listHosts = (): HostRow[] => store.hosts;
export const listNotices = (): NoticeRow[] => store.notices;

/**
 * Host edit ([2026-10-30 Feng] console edits are submitted to the kernel uniformly as agx_core).
 *  Writes go only through BFF PATCH /v1/hosts/{id} → MCP `agx_update_host`;
 *  on success the caller re-fetches the snapshot (see refreshPageData in the hosts page), reflecting it immediately.
 */
export const updateHost = (
  hostId: string,
  body: import("@/providers/api").HostUpdateInput,
): Promise<import("@/providers/api").HostUpdateResult> => hostsApi.update(hostId, body);

/**
 * Edit write entry ([2026-11-01 Feng] "you need to do editing; you have not done any of the other edits").
 *  Uniform rule: the BFF only normalizes params; field permissions/cache sync are all in the kernel; this just forwards.
 *  On success the caller calls refreshPageData(), and the list reflects the new values immediately.
 */
export const updateTask = (
  taskId: string,
  body: import("@/providers/api").TaskUpdateInput,
): Promise<import("@/providers/api").WriteResult> => tasksApi.update(taskId, body);

export const updateExecution = (
  runUuid: string,
  body: import("@/providers/api").ExecutionUpdateInput,
): Promise<import("@/providers/api").WriteResult> => executionsApi.update(runUuid, body);

export const updateSkill = (
  skillId: string,
  body: import("@/providers/api").SkillUpdateInput,
): Promise<import("@/providers/api").WriteResult> => skillsApi.update(skillId, body);

/* ------------------------- Quick Command ------------------------- */
/**
 * Fetch all quick commands (incl. meta + roles of [the selected sender identity]). Async, on demand; not via store.
 *  quick_cmd is driven by table agx_quick_cmd (BFF connects MySQL directly), and userRoles are strongly tied to
 *  [the sender Agent identity] (human != Agent), so it is fetched separately instead of poured into the global snapshot.
 *  @param agentId filter by owner (used by the agent detail page)
 *  @param callerAgentId [sender Agent identity] the operator identity selected in the frontend (to compute userRoles)
 */
export const fetchQuickCommands = (
  agentId?: string,
  callerAgentId?: string,
  /** [2026-11-14 Feng] true = also return the text snippets of all Agents (text type only).
 *   Used by the "common phrases" in the compose window: phrasing does not dispatch work, the owner is just "who wrote it".
 */
  includeSharedText = false,
): Promise<QuickCmdListResult> =>
  quickCmdApi.list(agentId, callerAgentId, true, includeSharedText);

/** Submit a quick command: send only {cmd_id, params, caller_agent_id, self_session_id},
 *  the backend does the authoritative validation + string composition + dispatch. caller_agent_id = the
 *  [sender Agent identity] selected in the frontend.
 */
export const runQuickCommand = (body: QuickCmdRunInput): Promise<QuickCmdRunResult> =>
  quickCmdApi.run(body);

/** Create a quick command (table agx_quick_cmd). */
export const createQuickCommand = (body: import("@/providers/api").QuickCmdUpsert): Promise<QuickCmd> =>
  quickCmdApi.create(body);

/** Update a quick command. */
export const updateQuickCommand = (
  cmdId: string,
  body: import("@/providers/api").QuickCmdUpsert,
): Promise<QuickCmd> => quickCmdApi.update(cmdId, body);

/** Archive (soft delete) a quick command. */
export const removeQuickCommand = (cmdId: string): Promise<{ cmd_id: string; activeStatus: number }> =>
  quickCmdApi.remove(cmdId);

/**
 * [Ownership rework 2026-10-08] Look up the quick command config of a task by task identifier.
 *  Used by the agent detail page to route "task → run": with config → open the custom dialog; without → default.
 */
export const fetchQuickCmdByTask = (p: {
  agent_id?: string;
  task_labs?: string;
  task_name?: string;
}): Promise<QuickCmdByTaskResult> => quickCmdApi.byTask(p);

/* ------------------------------ Detail ------------------------------ */
export const getAgent = (id: string): AgentRow | undefined => store.agents.find((a) => a.id === id);
export const getProjectName = projectName;

/* ------------------------ Agent dimension aggregation ------------------------ */
export interface AgentBundle {
  agent: AgentRow;
  sessions: SessionRow[];
  tasks: TaskRow[];
  executions: ExecutionRow[];
  projectName: string;
}

export function getAgentBundle(id: string): AgentBundle | undefined {
  const agent = getAgent(id);
  if (!agent) return undefined;
  return {
    agent,
    sessions: sessionsOf(id),
    tasks: tasksOf(id),
    executions: executionsOf(id),
    projectName: projectName(agent.project_id),
  };
}

/** Group Agents by project */
export function agentsGroupedByProject(): { projectId: string; projectName: string; agents: AgentRow[] }[] {
  const groups = new Map<string, AgentRow[]>();
  for (const a of store.agents) {
    if (!groups.has(a.project_id)) groups.set(a.project_id, []);
    groups.get(a.project_id)!.push(a);
  }
  return [...groups.entries()].map(([projectId, list]) => ({
    projectId,
    projectName: projectName(projectId) || i18n.t("miscUi.agentState.unregisteredProject"),
    agents: list,
  }));
}

/* ------------------------------ Stats ------------------------------ */
export const getDashboardStats = (): DashboardStats => store.dashboardStats;

/**
 * Live dashboard fetch (2026-10-28 Feng: "the system status data is not dynamic").
 *
 * Difference from `hydrateFromApi()`: that one hydrates the full snapshot once at SPA start;
 * this one goes to `/v1/dashboard/stats` (server-side aggregation SQL, ~30–100ms),
 * called every 5s by the page, updating only dashboard numbers, without re-hydrating everything or touching other pages.
 * Failures are **not silently degraded**: thrown to the caller for an explicit notice (reads may degrade, but the user
 * must know it has stalled).
 */
export const fetchDashboardStats = async (): Promise<DashboardStats> => {
  const d = await dashboardApi.stats();
  if (!d || typeof d !== "object") throw new Error(i18n.t("miscUi.agentState.boardApiError"));
  store.dashboardStats = { ...store.dashboardStats, ...d };
  return store.dashboardStats;
};

/** Execution stats (total / succeeded / failed / running) */
export function executionSummary(rows: ExecutionRow[]) {
  return {
    total: rows.length,
    succeeded: rows.filter((r) => r.status === "succeeded").length,
    failed: rows.filter((r) => r.status === "failed").length,
    running: rows.filter((r) => r.status === "running").length,
  };
}

/* --------------------------- Data source --------------------------- */
export const dataSource = () => store.source;
export const hydratedAt = () => store.hydratedAt;

/* --------------------------- Backend hydration --------------------------- */
/**
 * Pull the full business snapshot from the Win3 BFF and hydrate the runtime store.
 *
 * When called: SPA start (main.tsx), awaited before render.
 *
 * [2026-10-20 refactor — Feng reported "after refresh only mock data remains"]
 * Three changes:
 *   ① return a structured result (no longer just a boolean) — let the caller distinguish "credential expired" from
 *      "backend unavailable";
 *   ② 401 does not auto re-login and does not fall back to seed mocks → DataGate goes to the login page
 *      (Feng: do not show test data);
 *   ③ failures are not silent: the caller must write DataStatus into global state.
 *
 * Still keeps the "UI can render on failure" tolerance (seed mock fallback), but the user must be clearly told this
 * is sample data.
 *
 * [2026-10-30 fix "a wasted 401 on startup"]
 *   At SPA start, App mount unconditionally fetches /snapshot, but at that moment localStorage has no token
 *   (the user is on the login page) → guaranteed 401 → a console error that looks like a fault.
 *   Now **check for a local token before sending**: if none, return authRequired (NO_TOKEN) directly,
 *   without sending a single byte. Semantics unchanged: 401 still does not auto re-login, no seed mock fallback
 *   (left to the route guard). Note it only checks "present", not "expired" — an expired/corrupted token still makes
 *   a real request decided by the server, so the existing "401 → login page" path is used rather than a silent failure.
 */
export interface HydrateResult {
  ok: boolean;
  /** Credential expired (401) — the caller should go to the login page directly, rendering no data. */
  authRequired?: boolean;
  /** Failure code: BFF_UNREACHABLE / HTTP_401 / HTTP_500 / KERNEL_ERROR … */
  code?: string;
  status?: number;
  message?: string;
}

function toResult(e: unknown): HydrateResult {
  const err = e as { status?: number; code?: string; message?: string };
  const authRequired = err?.status === 401 || err?.code === "UNAUTHENTICATED";
  return {
    ok: false,
    authRequired,
    code: err?.code || "HYDRATE_FAILED",
    status: err?.status,
    message: err?.message || String(e),
  };
}

/**
 * [2026-10-28 Feng] The only entry of the nav bar "refresh" button.
 *
 * Semantics = re-fetch the full BFF snapshot → broadcast a refresh version → the current page remounts and re-reads
 * the store. Difference from `navigate(current route)`: navigating to the same route neither remounts nor refetches,
 * so it looks like "clicked and nothing happened"; this really refetches.
 *
 * Failures are not silent: HydrateResult is returned as-is, and the button side gives an explicit notice based on
 * authRequired / code.
 */
export async function refreshPageData(): Promise<HydrateResult> {
  const r = await hydrateFromApi();
  if (r.ok) notifyPageRefreshed();
  return r;
}

export async function hydrateFromApi(): Promise<HydrateResult> {
  // No credential = definitely cannot log in; don't waste a request that is bound to 401 (check presence, not expiry).
  if (!getToken()) {
    return {
      ok: false,
      authRequired: true,
      code: "NO_TOKEN",
      message: i18n.t("miscUi.agentState.notLoggedIn"),
    };
  }

  /* ② JWT that can be locally judged "already expired" — also not sent.
 *
 * The server will surely reject it (exp passed); the only output of this request is a 401 in the console;
 * and DataGate, on receiving authRequired, clears the token and goes to the login page anyway. Since the outcome is
 * certain, there is no point sending the 24MB snapshot request first.
 *
 * Only intercept at this tier, **do not widen to "looks suspicious, so block"**:
 *   · non-JWT (placeholder token) / no exp / parse failure → isTokenExpired always returns false
 *     → send anyway, let the server decide (local code must not conclude on the server's behalf).
 *   · Known cost: the judgement uses the **browser clock**. If the machine clock is ahead of the server's,
 *     it kicks the user out slightly early (60s ahead). Is that harder to accept than "one more 401"? No —
 *     only a clock 12h+ ahead would cause a "re-login then immediately kicked" loop, and such a machine cannot
 *     even get through TLS.
 */
  if (isTokenExpired()) {
    return {
      ok: false,
      authRequired: true,
      status: 401,
      code: "TOKEN_EXPIRED",
      message: i18n.t("miscUi.agentState.sessionExpired"),
    };
  }

  try {
    const snap = (await api.get<Snapshot>("/snapshot")) as Snapshot;
    hydrate(snap);
    return { ok: true };
  } catch (e) {
    const status = (e as { status?: number })?.status;

    // 401 = credential expired.
    // [2026-10-20 Feng] no auto re-login, no seed mock fallback → hand it to the route guard to go to the login page.
    // (auto re-login used to shove the user back into a business page "invisibly", impossible to tell what happened)
    if (status === 401) {
      console.warn("[agentgeex] hydrate 401 → sign-in required");
      return toResult(e);
    }

    console.warn("[agentgeex] hydrateFromApi failed:", (e as Error)?.message);
    return toResult(e);
  }
}

/* [2026-11-13 21:39] Session live status query: the page layer only knows this exit,
   never fetches directly, nor imports from api.ts directly (keeps the boundary "pages can switch data source with zero change"). */
export { sessionRuntimeApi, sessionMessagesApi };
export type { SessionMessageItem, SessionMessagesPage } from "@/providers/api";

/* -- Quick access (2026-11-13 22:03 Feng) -----------------------------
   ★ Reads and writes both go here; pages do not touch api / localStorage directly.
   ★ After a successful favorite, update the copy in the store (the snapshot was fetched at start; without a refresh the new favorite is invisible). */
export type { QuickEntryItem } from "@/providers/api";

/** My quick access entries (synchronously read the store, for page rendering) */
export function listQuickEntries(): QuickEntryRow[] {
  return store.quickEntries;
}

/** Whether already favorited (for the star state on list pages) */
export function isQuickEntry(entryType: QuickEntryRow["entryType"], entryKey: string): boolean {
  return store.quickEntries.some((e) => e.entryType === entryType && e.entryKey === entryKey);
}

/** Re-fetch from the backend and write back to the store (called after favorite/unfavorite, to keep pages in sync) */
export async function refreshQuickEntries(): Promise<QuickEntryRow[]> {
  const r = await quickEntryApi.list();
  store.quickEntries = (r.entries || []) as QuickEntryRow[];
  return store.quickEntries;
}

export async function addQuickEntry(
  entryType: QuickEntryRow["entryType"],
  entryKey: string,
  label = "",
): Promise<QuickEntryRow[]> {
  await quickEntryApi.add(entryType, entryKey, label);
  return refreshQuickEntries();
}

export async function removeQuickEntry(
  entryType: QuickEntryRow["entryType"],
  entryKey: string,
): Promise<QuickEntryRow[]> {
  await quickEntryApi.remove(entryType, entryKey);
  return refreshQuickEntries();
}
