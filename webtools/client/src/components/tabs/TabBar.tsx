/**
 * components/tabs/TabBar.tsx — tab bar of the multi-window workspace.
 *
 * Migrated from TabBar in 5300 (refine-shadcn-admin), adapted for AgentGeeX:
 *   - icon set replaced with AgentGeeX case/menu icons (unknown keys fall back to a neutral dot)
 *   - brand decoupled (no Win3 wording anywhere)
 *
 * Abilities:
 *   - switch / close / pin
 *   - context menu: refresh current, pin/unpin, close, close others, close to the right, close all
 *   - horizontal scrolling, gradient glow on the active tab
 */
import * as React from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import {
  Star,
  X, Pin, PinOff, RotateCw, ChevronsRight, ChevronsLeft,
  Boxes, LayoutDashboard, LayoutList, ClipboardList, Component, FileText,
  Lock, AlertTriangle, Blocks, FolderTree, Rows3, SquareDashed,
  Activity, Bot, MessageSquare, ListChecks, ReceiptText,
  FolderKanban, UsersRound, CalendarClock, Sparkles, Server, Newspaper, Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";
import { useToast } from "@/components/ui/toast";
import { refreshPageData } from "@/providers/agentgeex";
import { useTabs, type WorkTab } from "./TabsContext";
import { resolveTabMeta } from "./tab-meta";

const ICONS: Record<string, React.ReactNode> = {
  index: <Boxes className="h-3.5 w-3.5" />,
  dashboard: <LayoutDashboard className="h-3.5 w-3.5" />,
  list: <LayoutList className="h-3.5 w-3.5" />,
  "list-page": <Rows3 className="h-3.5 w-3.5" />,
  form: <ClipboardList className="h-3.5 w-3.5" />,
  components: <Component className="h-3.5 w-3.5" />,
  detail: <FileText className="h-3.5 w-3.5" />,
  auth: <Lock className="h-3.5 w-3.5" />,
  error: <AlertTriangle className="h-3.5 w-3.5" />,
  blocks: <Blocks className="h-3.5 w-3.5" />,
  blank: <SquareDashed className="h-3.5 w-3.5" />,
  menu: <FolderTree className="h-3.5 w-3.5" />,
  // AgentGeeX main menu (key = first path segment, matching `to` in nav-agentgeex.ts)
  system: <Activity className="h-3.5 w-3.5" />,
  "quick-access": <Star className="h-3.5 w-3.5" />,
  agents: <Bot className="h-3.5 w-3.5" />,
  sessions: <MessageSquare className="h-3.5 w-3.5" />,
  tasks: <ListChecks className="h-3.5 w-3.5" />,
  executions: <ReceiptText className="h-3.5 w-3.5" />,
  "quick-cmds": <Zap className="h-3.5 w-3.5" />,
  projects: <FolderKanban className="h-3.5 w-3.5" />,
  "project-groups": <UsersRound className="h-3.5 w-3.5" />,
  plans: <CalendarClock className="h-3.5 w-3.5" />,
  skills: <Sparkles className="h-3.5 w-3.5" />,
  hosts: <Server className="h-3.5 w-3.5" />,
  notices: <Newspaper className="h-3.5 w-3.5" />,
  // legacy / fallback key
  ggx: <Boxes className="h-3.5 w-3.5" />,
};

/**
 * Menu/tab key normalization: accepts a bare name (dashboard), a route (/dashboard), multi-segment paths
 * (/agents/xxx). When key contracts disagree (bare names on the definition side vs routes at runtime) they
 * converge here, so each `ICONS[key]` lookup does not miss wholesale.
 */
function iconFor(key?: string | null): React.ReactNode {
  if (!key) return null; // empty: hand back to the caller's fallback (e.g. TabBar's circle placeholder)
  const name = key.replace(/^\/+/, "").split("/")[0];
  return ICONS[name] ?? null;
}

/** refresh button state (one at a time: disabled while in flight, so double clicks cannot fire several snapshots) */
type RefreshState = "idle" | "running" | "ok" | "fail";

interface MenuState {
  x: number;
  y: number;
  tab: WorkTab;
}

export function TabBar() {
  const { tabs, activeKey, close, closeOthers, closeRight, closeAll, togglePin } = useTabs();
  const { t, i18n } = useTranslation();
  // tab title resolved live in the current language (switching language re-renders instantly, no stored state change)
  const tabTitle = (tab: WorkTab) => resolveTabMeta(tab.key, i18n.language, t).title;
  const navigate = useNavigate();
  const { toast } = useToast();
  const [menu, setMenu] = React.useState<MenuState | null>(null);
  /* [2026-10-28 Feng] refresh button pinned in the navbar: one click refreshes current page data */
  const [refreshing, setRefreshing] = React.useState<RefreshState>("idle");

  const doRefresh = React.useCallback(async () => {
    if (refreshing === "running") return; // in-flight de-duplication
    setRefreshing("running");
    try {
      const r = await refreshPageData();
      if (r.ok) {
        setRefreshing("ok");
        toast({
          title: t("miscShell.tabBar.refreshed"),
          /* time format follows the language: zh-CN for Chinese, en-GB for English (24-hour, no AM/PM) */
          description: t("miscShell.tabBar.updatedAt", {
            time: new Date().toLocaleTimeString(i18n.language.startsWith("zh") ? "zh-CN" : "en-GB", {
              hour12: false,
            }),
          }),
          variant: "success",
        });
      } else {
        setRefreshing("fail");
        toast({
          title: t("miscShell.tabBar.refreshFailed"),
          description: r.authRequired
            ? t("miscUi.agentState.sessionExpired")
            : (r.code || t("miscShell.tabBar.backendDown")),
          variant: "destructive",
        });
      }
    } finally {
      // keep the status light for 1.2s as user feedback, then back to idle
      window.setTimeout(() => setRefreshing("idle"), 1200);
    }
  }, [refreshing, toast]);
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const menuRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    const closeMenu = () => setMenu(null);
    window.addEventListener("click", closeMenu);
    window.addEventListener("scroll", closeMenu, true);
    return () => {
      window.removeEventListener("click", closeMenu);
      window.removeEventListener("scroll", closeMenu, true);
    };
  }, []);

  /**
   * Viewport-bound clamping: measure the menu after render (layout phase, before paint).
   * Prefer "flip" (flip left/up when near the right/bottom edge), then "clamp" into the viewport if still overflowing.
   * Before the fix the menu was offset wholesale because of a backdrop-filter containing-block issue; this also
   * guarantees it never overflows the screen in any case.
   */
  React.useLayoutEffect(() => {
    const el = menuRef.current;
    if (!menu || !el) return;
    const MARGIN = 8;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let left = menu.x;
    let top = menu.y;
    // horizontal: overflow at the right edge → flip and expand leftwards
    if (left + w > vw - MARGIN) left = menu.x - w;
    // still overflowing (or flipped past the left edge) → clamp
    if (left + w > vw - MARGIN) left = vw - MARGIN - w;
    if (left < MARGIN) left = MARGIN;
    // vertical: overflow at the bottom edge → flip and expand upwards
    if (top + h > vh - MARGIN) top = menu.y - h;
    if (top + h > vh - MARGIN) top = vh - MARGIN - h;
    if (top < MARGIN) top = MARGIN;
    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
  }, [menu]);

  const scrollBy = (dx: number) => scrollRef.current?.scrollBy({ left: dx, behavior: "smooth" });

  const onContextMenu = (e: React.MouseEvent, tab: WorkTab) => {
    e.preventDefault();
    setMenu({ x: e.clientX, y: e.clientY, tab });
  };

  return (
    <div
      data-testid="tabbar"
      className="relative flex h-11 shrink-0 items-center gap-1 border-b border-strong bg-card"
    >
      <button
        onClick={() => scrollBy(-180)}
        data-testid="tabbar-scroll-left"
        className="flex h-full w-7 shrink-0 items-center justify-center text-muted-foreground hover:bg-accent hover:text-accent-foreground"
        title={t("tabMenu.scrollLeft")}
      >
        <ChevronsLeft className="h-4 w-4" />
      </button>

      <div
        ref={scrollRef}
        className="scrollbar-thin flex h-full flex-1 items-center gap-1 overflow-x-auto scroll-smooth px-1"
      >
        {tabs.map((tab) => {
          const active = tab.key === activeKey;
          return (
            <div
              key={tab.key}
              data-testid={`tab-${tab.key}`}
              data-active={active ? "true" : "false"}
              data-pinned={tab.pinned ? "true" : "false"}
              onClick={() => navigate(tab.key)}
              onContextMenu={(e) => onContextMenu(e, tab)}
              className={cn(
                "group relative flex h-8 shrink-0 cursor-pointer items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-all",
                active
                  ? "border-primary/40 bg-primary/10 text-foreground shadow-[0_0_0_1px_hsl(var(--primary)/0.15),0_0_18px_-6px_hsl(var(--primary)/0.55)]"
                  : "border-border bg-muted/40 text-muted-foreground hover:border-border hover:bg-accent hover:text-accent-foreground"
              )}
              title={tabTitle(tab)}
            >
              {active && (
                <span className="pointer-events-none absolute inset-x-1 -bottom-px h-px bg-gradient-to-r from-transparent via-primary to-transparent" />
              )}
              <span className={cn(active ? "text-primary" : "text-muted-foreground")}>
                {iconFor(tab.iconKey) ?? (
                  <span className="block h-3.5 w-3.5 rounded-full border border-current opacity-40" />
                )}
              </span>
              <span className="max-w-[140px] truncate">{tabTitle(tab)}</span>
              {tab.pinned ? (
                <Pin className="h-3 w-3 shrink-0 text-primary/70" />
              ) : (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    close(tab.key);
                  }}
                  className="ml-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded opacity-0 transition-opacity hover:bg-destructive/15 hover:text-destructive group-hover:opacity-100"
                  title={t("common.close")}
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </div>
          );
        })}
      </div>

      <button
        onClick={() => scrollBy(180)}
        data-testid="tabbar-scroll-right"
        className="flex h-full w-7 shrink-0 items-center justify-center text-muted-foreground hover:bg-accent hover:text-accent-foreground"
        title={t("tabMenu.scrollRight")}
      >
        <ChevronsRight className="h-4 w-4" />
      </button>

      {/* ★ pinned refresh button (2026-10-28 Feng): always at the far right, one click refreshes the current page.
          It used to take two actions (right-click tab → refresh), now one. Spinner while refreshing, toast on
          success/failure. */}
      <button
        onClick={() => void doRefresh()}
        disabled={refreshing === "running"}
        data-testid="tabbar-refresh"
        data-state={refreshing}
        aria-label={t("tabMenu.refresh")}
        title={t("miscShell.tabBar.refreshHint", { label: t("tabMenu.refresh") })}
        className={cn(
          "flex h-full w-9 shrink-0 items-center justify-center border-l border-border transition-colors",
          "hover:bg-accent hover:text-accent-foreground disabled:cursor-progress",
          refreshing === "running" && "text-primary",
          refreshing === "ok" && "text-success",
          refreshing === "fail" && "text-destructive",
          refreshing === "idle" && "text-muted-foreground"
        )}
      >
        <RotateCw
          className={cn("h-4 w-4 transition-transform", refreshing === "running" && "animate-spin")}
        />
      </button>

      {/* context menu: createPortal to document.body, out of TabBar's backdrop-filter containing block */}
      {menu &&
        createPortal(
        <div
          ref={menuRef}
          data-testid="tab-context-menu"
          className="fixed z-50 min-w-[168px] overflow-hidden rounded-md border bg-popover p-1 text-popover-foreground shadow-lg"
          style={{ top: menu.y, left: menu.x }}
          onClick={(e) => e.stopPropagation()}
        >
          <MenuItem
            testid="menu-refresh"
            icon={<RotateCw className="h-3.5 w-3.5" />}
            onClick={() => {
              // activate the target tab first (it may be another tab), then do the real refresh
              if (menu.tab.key !== activeKey) navigate(menu.tab.key);
              setMenu(null);
              void doRefresh();
            }}
          >
            {t("tabMenu.refresh")}
          </MenuItem>
          <MenuItem
            testid="menu-pin"
            icon={menu.tab.pinned ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}
            onClick={() => {
              togglePin(menu.tab.key);
              setMenu(null);
            }}
          >
            {menu.tab.pinned ? t("tabMenu.unpin") : t("tabMenu.pin")}
          </MenuItem>
          <div className="my-1 h-px bg-border" />
          <MenuItem
            testid="menu-close"
            icon={<X className="h-3.5 w-3.5" />}
            disabled={menu.tab.home}
            onClick={() => {
              close(menu.tab.key);
              setMenu(null);
            }}
          >
            {t("tabMenu.close")}
          </MenuItem>
          <MenuItem
            testid="menu-close-others"
            icon={<Component className="h-3.5 w-3.5" />}
            onClick={() => {
              closeOthers(menu.tab.key);
              setMenu(null);
            }}
          >
            {t("tabMenu.closeOthers")}
          </MenuItem>
          <MenuItem
            testid="menu-close-right"
            icon={<ChevronsRight className="h-3.5 w-3.5" />}
            onClick={() => {
              closeRight(menu.tab.key);
              setMenu(null);
            }}
          >
            {t("tabMenu.closeRight")}
          </MenuItem>
          <MenuItem
            testid="menu-close-all"
            icon={<X className="h-3.5 w-3.5" />}
            onClick={() => {
              closeAll();
              setMenu(null);
            }}
          >
            {t("tabMenu.closeAll")}
          </MenuItem>
        </div>,
        document.body
      )}
    </div>
  );
}

function MenuItem({
  children,
  icon,
  onClick,
  disabled,
  testid,
}: {
  children: React.ReactNode;
  icon?: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  testid?: string;
}) {
  return (
    <button
      data-testid={testid}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-xs transition-colors",
        disabled ? "cursor-not-allowed opacity-40" : "hover:bg-accent hover:text-accent-foreground"
      )}
    >
      {icon}
      {children}
    </button>
  );
}
