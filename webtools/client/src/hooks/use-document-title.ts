/**
 * hooks/use-document-title.ts — single exit for the app title (browser tab)
 *
 * Background (2026-10-01 Feng feedback):
 *   the tab used to show "Refine" — because Refine's <DocumentTitleHandler /> was mounted,
 *   and when none of this app's Routes had meta.title / handle.title configured,
 *   it always fell back to the framework default "Refine", overriding index.html's <title>.
 *
 * This hook replaces it: sets document.title to "<page name> · AgentGeeX" per current route,
 * unmatched pages fall back to the app name "AgentGeeX — Agent management console".
 *
 * Single source of truth: page names come from lib/nav-agentgeex.ts (nav config) and lib/cases.ts (cases),
 * both already maintain a "route → Chinese label" mapping; this only queries, does not redefine.
 */
import * as React from "react";
import { useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { findNavLeaf } from "@/lib/nav-agentgeex";
import i18n from "@/i18n";

/** App name (fallback title when no page matches) */
/** App name (fallback title when no page matches). ★ Runtime lookup: hardcoded module-level text does not change with language. */
export const APP_TITLE = () => i18n.t("miscShell.appTitle");
/** Title suffix */
export const APP_SUFFIX = "AgentGeeX";

/**
 * Static supplemental table route → page name (pages outside nav/cases).
 *  key is a path prefix; a hit is used (longest-first order guaranteed by the caller).
 *  ★ The value is an i18n key, not text (otherwise the browser tab stays Chinese in the English UI).
 */
const EXTRA_TITLES: Record<string, string> = {
  "/login": "page.login",
  "/blank": "page.blank",
  "/personal/center": "page.personalCenter",
};

/**
 * Resolve the page name of the current path. Returns "" when not found.
 *  Match priority: exact > prefix (incl. dynamic segments, e.g. /agents/:id).
 */
export function resolvePageTitle(pathname: string, t: (k: string) => string = i18n.t): string {
  // 1) exact nav leaf match (business console pages)
  const leaf = findNavLeaf(pathname);
  if (leaf) return t(leaf.labelKey);

  // 2) static supplemental table (exact)
  if (EXTRA_TITLES[pathname]) return t(EXTRA_TITLES[pathname]);

  // 3) prefix match (covers dynamic routes, e.g. /agents/aifr_pm → agents)
  const prefixHit = Object.keys(EXTRA_TITLES).find((p) => pathname.startsWith(p + "/"));
  if (prefixHit) return t(EXTRA_TITLES[prefixHit]);

  // 4) business console dynamic sub-routes: /agents/:id, /list-page/:id etc.
  const dyn = pathname.match(/^\/agents\/[^/]+$/);
  if (dyn) return t("nav.tab.detail.agent");

  return "";
}

/**
 * Update document.title as the route changes after mount.
 *  Usage: call once at the top level of AppShell.
 */
export function useDocumentTitle(): void {
  const { pathname } = useLocation();
  const { t, i18n: i18nInst } = useTranslation();
  const lang = i18nInst.language;

  React.useEffect(() => {
    const page = resolvePageTitle(pathname, t);
    document.title = page ? `${page} · ${APP_SUFFIX}` : APP_TITLE();
    /* Language is also a dependency: after switching language the browser tab title must change too,
          otherwise the user sees "page in English, tab in Chinese". */
  }, [pathname, lang, t]);
}

export default useDocumentTitle;
