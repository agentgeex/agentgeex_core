/**
 * lib/breadcrumb.tsx — top-bar breadcrumb
 * ────────────────────────────────────────────────────────────────
 * [2026-10-29 Feng] The top bar's left side used to be a 'universal search' input wired to no logic;
 * it was removed (see the shell.tsx comment) → leaving an ugly hole.
 * Now it is a breadcrumb showing the hierarchy 'left nav → menu page → detail entity'.
 *
 * Where the levels come from (two parts):
 *   (1) Group / menu item — derived back from the route pathname; the single source of truth is lib/nav-agentgeex.ts
 *   (2) Detail entity name — reported by the page itself (only the detail page knows what the entity is called)
 *      · The 10 entity detail pages go through DetailShell and report there uniformly
 *      · The agent detail page reports separately
 *
 * Why not derive from 'open tabs':
 *   Tabs are 'which pages I have open at once' (many, any order, closable);
 *   the breadcrumb is 'which object on which level I am standing on now' — different semantics, not derivable from each other.
 */
import * as React from "react";
import { Link, useLocation } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { NAV_LEAVES, type NavLeaf } from "@/lib/nav-agentgeex";

/* ------------------------------ Context ------------------------------ */

interface DetailCrumb {
  /** The pathname at report time — used to decide whether this detail name still belongs to the current page */
  path: string;
  label: string;
}

interface CrumbsCtx {
  detail: DetailCrumb | null;
  setDetail: (v: DetailCrumb | null) => void;
}

const CrumbsContext = React.createContext<CrumbsCtx | null>(null);

export function BreadcrumbProvider({ children }: { children: React.ReactNode }) {
  const [detail, setDetailState] = React.useState<DetailCrumb | null>(null);
  const setDetail = React.useCallback((v: DetailCrumb | null) => setDetailState(v), []);
  const value = React.useMemo(() => ({ detail, setDetail }), [detail, setDetail]);
  return <CrumbsContext.Provider value={value}>{children}</CrumbsContext.Provider>;
}

/* --------------------------- Detail page report --------------------------- */

/**
 * Called once by a detail page to hand the entity name to the breadcrumb.
 * ★ Get the setter through a ref: the effect depends only on [pathname, label],
 *   otherwise 'setDetail → context changes → effect reruns' loops on itself.
 */
export function useDetailCrumb(label?: string | null) {
  const ctx = React.useContext(CrumbsContext);
  const { pathname } = useLocation();
  const setRef = React.useRef(ctx?.setDetail);
  setRef.current = ctx?.setDetail;

  React.useEffect(() => {
    const set = setRef.current;
    const clean = (label ?? "").trim();
    if (!set || !clean) return;
    set({ path: pathname, label: clean });
    return () => set(null);
  }, [pathname, label]);
}

/* --------------------------- Route derivation --------------------------- */

/** Find the owning menu item from the pathname: exact hit, or '/path/' prefix hit (detail pages, flow charts, etc.) */
export function resolveNavTrail(pathname: string): { groupKey: string; leaf: NavLeaf } | null {
  let best: NavLeaf | null = null;
  for (const leaf of NAV_LEAVES) {
    const hit = pathname === leaf.path || pathname.startsWith(`${leaf.path}/`);
    // Longest path first: /project-groups is not stolen by /projects
    if (hit && (!best || leaf.path.length > best.path.length)) best = leaf;
  }
  return best ? { groupKey: best.groupKey, leaf: best } : null;
}

export interface CrumbSegment {
  label: string;
  /** Clickable only when to exists; the current page (last segment) has no to */
  to?: string;
}

export function useBreadcrumbTrail(): CrumbSegment[] {
  const { pathname } = useLocation();
  const { t } = useTranslation();
  const ctx = React.useContext(CrumbsContext);
  const detail = ctx?.detail ?? null;

  return React.useMemo(() => {
    const hit = resolveNavTrail(pathname);
    if (!hit) return [];

    const groupName = t(hit.groupKey);
    const leafName = t(hit.leaf.labelKey);
    const segs: CrumbSegment[] = [];
    /* When the group name equals the menu item name (e.g. 'Plans / Plans'), drop the group level, otherwise it reads like a stutter */
    if (groupName !== leafName) segs.push({ label: groupName });
    segs.push({ label: leafName, to: hit.leaf.path });

    /* The detail name is appended only when 'it really was reported by the current path' and 'this is not the list page itself' */
    if (detail && detail.path === pathname && pathname !== hit.leaf.path) {
      segs.push({ label: detail.label });
    }
    return segs;
  }, [pathname, detail, t]);
}

/* ------------------------------ Component ------------------------------ */

export function Breadcrumb() {
  const { t } = useTranslation();
  const trail = useBreadcrumbTrail();
  if (trail.length === 0) return null;

  return (
    <nav
      aria-label={t("miscShell.breadcrumb.label")}
      data-testid="breadcrumb"
      className="hidden min-w-0 items-center gap-0.5 text-sm md:flex"
    >
      {trail.map((seg, i) => {
        const last = i === trail.length - 1;
        return (
          <React.Fragment key={`${seg.label}-${i}`}>
            {i > 0 && (
              <ChevronRight
                className="h-3.5 w-3.5 shrink-0 text-muted-foreground/50"
                aria-hidden
              />
            )}
            {seg.to && !last ? (
              <Link
                to={seg.to}
                className="max-w-[12ch] truncate rounded px-1 py-0.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                data-testid={`breadcrumb-link-${i}`}
              >
                {seg.label}
              </Link>
            ) : (
              <span
                className={cn(
                  "max-w-[22ch] truncate px-1 py-0.5",
                  last ? "font-medium text-foreground" : "text-muted-foreground",
                )}
                title={seg.label}
                data-testid={last ? "breadcrumb-current" : undefined}
              >
                {seg.label}
              </span>
            )}
          </React.Fragment>
        );
      })}
    </nav>
  );
}
