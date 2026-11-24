/**
 * components/tabs/tab-meta.ts — route → tab meta resolution (AgentGeeX IA adaptation)
 *
 * Purpose: resolve 'one access path' into { title, iconKey } needed by a workspace tab.
 *
 * Adaptation points (versus 5300's hardcoding):
 *   - AgentGeeX's 'default tab' is `/blank` (blank fallback page), not 5300's `/dashboard`.
 *     Closing all tabs falls back to it; it is a fixed, non-closable default tab.
 *   - The 'case index' `/` is demoted to an ordinary process tab (openable / closable from the left nav).
 *   - [2026-11-18] Example pages removed (open-source release prep); only one nav set remains, AGX_NAV_GROUPS.
 *     Resolution order: default page → business list leaf → edit/detail page (attached to the list tab) → fallback;
 *     unknown paths fall back to pathname, and TabBar renders a neutral dot as the icon.
 */
import { NAV_LEAVES, findNavLeaf } from "@/lib/nav-agentgeex";
import i18n from "@/i18n";

/** Translate function signature (compatible with i18next's t) */
export type TFn = (key: string) => string;

/** Default tab key (= the blank fallback route); kept in sync with TabsContext.HOME_TAB */
export const HOME_PATH = "/system";

export interface TabMeta {
  title: string;
  iconKey?: string;
  /**
   * Tab ownership key (optional): which existing tab the current route should attach to instead of opening a new one.
   * Typical case: entity detail/edit pages (/hosts/:id, /tasks/:id/edit, ...) attach to their respective list tabs,
   * so each visit does not open another tab.
   * Returning nothing means this route forms its own tab.
   */
  tabKey?: string;
}

/** Path → icon key (takes the first segment, consistent with the TabBar ICONS key contract); HOME_PATH maps specially to ggx */
function iconKeyFromPath(path: string): string {
  const seg = path.replace(/^\/+/, "").split("/")[0];
  return seg || "ggx";
}

/**
 * Path → { title, icon key }
 *
 * ★ The t / lang params are kept only for backward compatibility (TabBar still passes its own t, re-rendering on locale switch).
 *   When omitted they fall back to the i18n instance — this file is called from non-React code (TabsContext persistence, tests),
 *   where no hook is available, but i18n.t works synchronously and yields the same result.
 */
export function resolveTabMeta(rawPath: string, _lang = "zh-CN", t?: TFn): TabMeta {
  // Strip query/hash, match on the pure path
  const path = rawPath.split(/[?#]/)[0] || HOME_PATH;
  const L = (key: string) => (t ? t(key) : i18n.t(key));

  if (path === HOME_PATH) return { title: L("nav.tab.system"), iconKey: "system" };
  if (path === "/") return { title: L("nav.tab.index"), iconKey: "index" };

  // (1).7 AgentGeeX admin backend nav leaves
  //     iconKey is derived from 'the path's first segment' (consistent with the TabBar ICONS key contract),
  //     otherwise everything is registered as the same "ggx" → all tab icons look identical (the same root defect).
  const navLeaf = findNavLeaf(path);
  if (navLeaf) return { title: L(navLeaf.labelKey), iconKey: iconKeyFromPath(path) };
  // (1).8 Entity detail / plan flow — [2026-10-28 Feng] always attach to their respective list tabs,
  //      no new tabs.
  //      Previously only /agents/:id had this rule; the rest (/hosts/:id, /sessions/:id ...) fell into the fallback branch
  //      → the tab title became the path, the icon a gray dot, plus a gratuitous extra tab, inconsistent with how
  //      'entity detail' is used. Now unified: detail = one screen inside the list tab (the detail page has its own 'back').
  // (1).75 Entity edit page ([2026-10-30 Feng] editing does not use a dialog, it opens as a page window)
  //      shares the tab with the list (tabKey = list path); the title says 'Edit host' rather than 'Host detail'.
  //      ★ Must come before the ENTITY_TAB regex: otherwise /hosts/:id/edit would match 'detail' first.
  const EDIT_TAB: Record<string, { title: string; key: string }> = {
    hosts: { title: L("nav.tab.edit.host"), key: "/hosts" },
    // [2026-11-01] Editing tasks / executions / skills is likewise 'one screen inside the list tab',
    // no new tab — otherwise the tab bar grows by one per edit, inconsistent with the host-edit experience.
    tasks: { title: L("nav.tab.edit.task"), key: "/tasks" },
    executions: { title: L("nav.tab.edit.execution"), key: "/executions" },
    skills: { title: L("nav.tab.edit.skill"), key: "/skills" },
  };
  {
    const m = path.match(/^\/([a-z-]+)\/[^/]+\/edit$/);
    const hit = m ? EDIT_TAB[m[1]] : undefined;
    if (hit) return { title: hit.title, iconKey: iconKeyFromPath(hit.key), tabKey: hit.key };
  }

  const ENTITY_TAB: Record<string, { title: string; key: string }> = {
    agents: { title: L("nav.tab.detail.agent"), key: "/agents" },
    sessions: { title: L("nav.tab.detail.session"), key: "/sessions" },
    executions: { title: L("nav.tab.detail.execution"), key: "/executions" },
    tasks: { title: L("nav.tab.detail.task"), key: "/tasks" },
    projects: { title: L("nav.tab.detail.project"), key: "/projects" },
    "project-groups": { title: L("nav.tab.detail.projectGroup"), key: "/project-groups" },
    skills: { title: L("nav.tab.detail.skill"), key: "/skills" },
    notices: { title: L("nav.tab.detail.notice"), key: "/notices" },
    hosts: { title: L("nav.tab.detail.host"), key: "/hosts" },
    plans: { title: L("nav.tab.detail.plan"), key: "/plans" },
    "quick-cmds": { title: L("nav.tab.detail.quickCmd"), key: "/quick-cmds" },
  };
  {
    const m = path.match(/^\/([a-z-]+)\/[^/]+/);
    const hit = m ? ENTITY_TAB[m[1]] : undefined;
    if (hit) return { title: hit.title, iconKey: iconKeyFromPath(hit.key), tabKey: hit.key };
  }
  // (4) Fallback: use the path as the title, neutral dot as the icon
  return { title: path };
}

/** For tests/docs: the title table of all known tab-opening routes */
export function knownTabTitles(): Record<string, string> {
  const out: Record<string, string> = { [HOME_PATH]: i18n.t("nav.tab.system") };
  for (const l of NAV_LEAVES) out[l.path] = i18n.t(l.labelKey);
  return out;
}
