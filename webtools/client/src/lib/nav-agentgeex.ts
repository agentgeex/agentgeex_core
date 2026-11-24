/**
 * lib/nav-agentgeex.ts — AgentGeeX admin navigation config (single source of truth)
 *
 * Basis: docs/agent_docs/agentgeex_ui.md §1 (global architecture and route navigation)
 *
 * Structure (matching the agentgeex sidebar style: **group title + flat entries**, no second-level submenus):
 *   [System]      system status /system · quick access /quick-access · hosts /hosts
 *   [Agent]       agents /agents · sessions /sessions · tasks /tasks · executions /executions …
 *   [Project]     project list /projects · project group templates /project-groups
 *   [Plan]        plans /plans
 *
 * "Agent management / Project management" are **category titles** (like agentgeex's Start / Table / Page cases),
 * not expandable parent nodes — their entries are flat.
 *
 * ★ 2026-11-18 i18n: this file stores **only i18n keys, never copy**.
 *   A nav name appears in three places (sidebar / breadcrumb / browser tab title);
 *   copy in the config = the English version stays half Chinese. Config decoupled from language, translated at render.
 */
import type { LucideIcon } from "lucide-react";
import {
  Activity, Bot, MessageSquare, ListChecks, ReceiptText,
  FolderKanban, UsersRound, CalendarClock, Sparkles, Server, Newspaper, Zap, Star,
} from "lucide-react";

export interface AgxNavItem {
  to: string;
  /** i18n key (`nav.item.*`) — render with t(labelKey) */
  labelKey: string;
  icon: LucideIcon;
}

export interface AgxNavGroup {
  key: string;
  /** i18n key（`nav.group.*`） */
  labelKey: string;
  items: AgxNavItem[];
}

export const AGX_NAV_GROUPS: AgxNavGroup[] = [
  {
    key: "system",
    labelKey: "nav.group.system",
    items: [
      { to: "/system", labelKey: "nav.item.system", icon: Activity },
      /* [2026-11-13 22:03 Feng] "Add another module above system status called quick access" ——
         placed in the system group, right next to system status: both are "my own view", not a business entity. */
      { to: "/quick-access", labelKey: "nav.item.quickAccess", icon: Star },
      { to: "/hosts", labelKey: "nav.item.hosts", icon: Server },
    ],
  },
  {
    key: "agents",
    labelKey: "nav.group.agents",
    items: [
      { to: "/agents", labelKey: "nav.item.agents", icon: Bot },
      { to: "/sessions", labelKey: "nav.item.sessions", icon: MessageSquare },
      { to: "/tasks", labelKey: "nav.item.tasks", icon: ListChecks },
      { to: "/executions", labelKey: "nav.item.executions", icon: ReceiptText },
      { to: "/quick-cmds", labelKey: "nav.item.quickCmds", icon: Zap },
      { to: "/skills", labelKey: "nav.item.skills", icon: Sparkles },
      { to: "/notices", labelKey: "nav.item.notices", icon: Newspaper },
    ],
  },
  {
    key: "projects",
    labelKey: "nav.group.projects",
    items: [
      { to: "/projects", labelKey: "nav.item.projects", icon: FolderKanban },
      { to: "/project-groups", labelKey: "nav.item.projectGroups", icon: UsersRound },
    ],
  },
  {
    key: "plans",
    labelKey: "nav.group.plans",
    items: [{ to: "/plans", labelKey: "nav.item.plans", icon: CalendarClock }],
  },
];

/* ------------------------------ leaf derivation ------------------------------ */
export interface NavLeaf {
  path: string;
  /** i18n key */
  labelKey: string;
  /** i18n key of the owning group */
  groupKey: string;
}

export const NAV_LEAVES: NavLeaf[] = AGX_NAV_GROUPS.flatMap((g) =>
  g.items.map((it) => ({ path: it.to, labelKey: it.labelKey, groupKey: g.labelKey }))
);

export function findNavLeaf(path: string): NavLeaf | undefined {
  return NAV_LEAVES.find((l) => l.path === path);
}
