/**
 * components/tabs/TabsContext.tsx — multi-window (multi-tab) workspace state management.
 *
 * Migrated from 5300 (refine-shadcn-admin) TabsContext, adapted for AgentGeeX:
 *   - home tab = `/` (case index), no longer 5300's hardcoded `/dashboard`
 *   - tab titles/icons resolved via `resolveTabMeta(pathname)`, compatible with AgentGeeX's
 *     two nav sets (case nav + multi-level menu); unknown paths fall back to pathname
 *   - separate localStorage key: `agentgeex.worktabs.v1`
 *
 * Design points:
 *   - pure frontend state (localStorage persistence), tab path linked to location.pathname
 *   - one tab = one visited path (title, icon key, pinned, is-home)
 *   - provides open / close / closeOthers / closeRight / closeAll / togglePin / switch
 *   - the default tab (/blank blank page) cannot be closed; when all tabs are closed it falls back to it
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";
import { HOME_PATH, resolveTabMeta } from "./tab-meta";

export interface WorkTab {
  /** Unique key = route path (incl. query) */
  key: string;
  /** Title */
  title: string;
  /** Icon key (used by TabBar to render the icon) */
  iconKey?: string;
  /** Pinned (cannot be cleared by "close others", always sorted first) */
  pinned?: boolean;
  /** Home (cannot be closed) */
  home?: boolean;
}

interface TabsCtx {
  tabs: WorkTab[];
  activeKey: string;
  open: (tab: WorkTab) => void;
  close: (key: string) => void;
  closeOthers: (key: string) => void;
  closeRight: (key: string) => void;
  closeAll: () => void;
  togglePin: (key: string) => void;
}

const Ctx = React.createContext<TabsCtx | null>(null);
const STORAGE_KEY = "agentgeex.worktabs.v1";

/**
 * Pinned tabs always come first (stable sort: the pinned group keeps its relative order).
 * This is the semantics declared in the 5300 comment ("pinned... and always sorted first"); the original
 * implementation did not deliver it, added here so the boundaries of "close right / close others" are predictable.
 */
/**
 * Pinned tabs always come first (stable sort: the pinned group keeps its relative order).
 * This is the semantics declared in the 5300 comment ("pinned... and always sorted first"); the original
 * implementation did not deliver it, added here so the boundaries of "close right / close others" are predictable.
 */
function normalize(list: WorkTab[]): WorkTab[] {
  return [...list.filter((t) => t.pinned), ...list.filter((t) => !t.pinned)];
}

/**
 * The tab key the current route should attach to.
 *  - normal route: the pathname (+search) → its own / matched tab
 *  - route with a tabKey alias (e.g. /list-page/new): attaches to the parent tab (/list-page),
 *    so clicking "page-style create" does not open a new tab but shares the list page's tab.
 */
function resolveActiveKey(rawPath: string): string {
  const meta = resolveTabMeta(rawPath);
  return meta.tabKey ?? rawPath;
}

/**
 * Default tab = system status (/system).
 * Semantics: after all tabs are closed (closeAll / closing down to the last), fall back to it; it is pinned and
 * cannot be closed.
 */
const HOME_TAB: WorkTab = {
  key: HOME_PATH,
  /* Module-level constants cannot get the hook → store the key, translate at the read site (see homeTab()) */
  title: "miscShell.tabs.systemStatus",
  iconKey: "system",
  pinned: true,
  home: true,
};

/**
 * Deleted example page paths (removed during open-source prep, 2026-11-18).
 *
 * ★ Why recognize them here: the tab bar is persisted. Before the upgrade users may have had /list, /form
 *   example pages open — after the routes were deleted, clicking those tabs only hits the `*` fallback redirecting to
 *   system status, leaving a tab that can never open. Dropping them on restore is better than making people close
 *   them one by one.
 * ★ Only list **confirmed deleted** path prefixes; do not use it as a "route allowlist" (that would block new pages).
 */
const REMOVED_PATHS = [
  "/blank", "/dashboard", "/list", "/list-page", "/form", "/table", "/search",
  "/detail", "/auth", "/error", "/components", "/waterfall", "/blocks", "/menu", "/gallery",
];

function isRemovedPath(key: string): boolean {
  const p = key.split(/[?#]/)[0] || "";
  return REMOVED_PATHS.some((r) => p === r || p.startsWith(r + "/"));
}

function load(): WorkTab[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [HOME_TAB];
    const parsed = JSON.parse(raw) as WorkTab[];
    if (!Array.isArray(parsed) || parsed.length === 0) return [HOME_TAB];
    // Drop tabs pointing to deleted example pages (see REMOVED_PATHS)
    const alive = parsed.filter((t) => t && !isRemovedPath(String(t.key || "")));
    // Ensure the home tab exists and is pinned
    const hasHome = alive.some((t) => t.key === HOME_TAB.key);
    const list = alive.map((t) => (t.key === HOME_TAB.key ? { ...t, pinned: true, home: true } : t));
    return normalize(hasHome ? list : [HOME_TAB, ...list]);
  } catch {
    return [HOME_TAB];
  }
}

export function TabsProvider({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [tabs, setTabs] = React.useState<WorkTab[]>(() => load());
  // ★ Active tab = the key the route attaches to (hidden child routes merge into the parent, see resolveActiveKey)
  const activeKey = resolveActiveKey(location.pathname + location.search);

  React.useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(tabs));
    } catch {
      /* ignore */
    }
  }, [tabs]);

  const open = React.useCallback(
    (tab: WorkTab) => {
      setTabs((prev) => (prev.some((t) => t.key === tab.key) ? prev : [...prev, tab]));
      navigate(tab.key);
    },
    [navigate]
  );

  const close = React.useCallback(
    (key: string) => {
      setTabs((prev) => {
        const target = prev.find((t) => t.key === key);
        if (!target || target.home) return prev;
        const next = prev.filter((t) => t.key !== key);
        if (key === activeKey) {
          // Close current: switch to the adjacent tab
          const idx = prev.findIndex((t) => t.key === key);
          const fallback = next[Math.max(0, idx - 1)] ?? next[0] ?? HOME_TAB;
          navigate(fallback.key);
        }
        return next;
      });
    },
    [activeKey, navigate]
  );

  const closeOthers = React.useCallback(
    (key: string) => {
      setTabs((prev) => {
        const next = prev.filter((t) => t.pinned || t.key === key);
        if (!next.some((t) => t.key === activeKey)) navigate(next[next.length - 1]?.key ?? HOME_TAB.key);
        return next;
      });
    },
    [activeKey, navigate]
  );

  const closeRight = React.useCallback(
    (key: string) => {
      setTabs((prev) => {
        const idx = prev.findIndex((t) => t.key === key);
        const next = prev.filter((t, i) => i <= idx || t.pinned);
        if (!next.some((t) => t.key === activeKey)) navigate(next[next.length - 1]?.key ?? HOME_TAB.key);
        return next;
      });
    },
    [activeKey, navigate]
  );

  const closeAll = React.useCallback(() => {
    setTabs((prev) => prev.filter((t) => t.home || t.pinned));
    navigate(HOME_TAB.key);
  }, [navigate]);

  const togglePin = React.useCallback((key: string) => {
    setTabs((prev) => normalize(prev.map((t) => (t.key === key ? { ...t, pinned: !t.pinned } : t))));
  }, []);

  // Route change → auto-register the tab (title/icon resolved via AgentGeeX information architecture)
  // ★ Routes with a tabKey alias (/list-page/new etc.) do not open a new tab, only reuse the parent tab (/list-page)
  React.useEffect(() => {
    const key = resolveActiveKey(location.pathname + location.search);
    setTabs((prev) => {
      if (prev.some((t) => t.key === key)) return prev;
      const meta = resolveTabMeta(key);
      return [...prev, { key, title: meta.title, iconKey: meta.iconKey }];
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname, location.search]);

  const value: TabsCtx = { tabs, activeKey, open, close, closeOthers, closeRight, closeAll, togglePin };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTabs() {
  const ctx = React.useContext(Ctx);
  if (!ctx) throw new Error("useTabs must be used within TabsProvider");
  return ctx;
}
