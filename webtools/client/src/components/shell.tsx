import * as React from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Boxes, Menu, Sun, Moon, ChevronsLeft, ChevronsRight, Github,
  Maximize2, Minimize2, Languages, Check, ChevronDown, User, BookOpen, Lock, LogOut,
  SlidersHorizontal, Send,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Popover, PopoverTrigger, PopoverContent } from "@/components/ui/popover";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { useLockScreen } from "@/components/lock-screen";
/* [2026-11-13 08:30 Feng] Global "send message" button in the top bar: the dialog was lifted to the Shell layer so any page can open it */
import { MessageSendDialog } from "@/components/agentgeex/message-send-dialog";
import { AGX_NAV_GROUPS, type AgxNavItem } from "@/lib/nav-agentgeex";
import { useGetIdentity } from "@refinedev/core";
import { useTranslation } from "react-i18next";
import { SUPPORTED_LOCALES, changeLocale, type LocaleKey } from "@/i18n";
import { TabBar } from "@/components/tabs/TabBar";
import { useTabs } from "@/components/tabs/TabsContext";
import { useDocumentTitle } from "@/hooks/use-document-title";
import { Breadcrumb, BreadcrumbProvider } from "@/lib/breadcrumb";
import { ServiceSettingsDialog } from "@/components/agentgeex/service-settings-dialog";
/* [2026-10-29] Global entity detail dialog: clicking a related entity on any page → detail pops up in place, no page jump */
import { DetailPopupProvider } from "@/components/agentgeex/detail-popup";
import { EntityDetailBody } from "@/pages/agentgeex/entity-detail";


function useTheme() {
  const [dark, setDark] = React.useState<boolean>(() => {
    try {
      const t = localStorage.getItem("agentgeex.theme");
      return t ? t === "dark" : false;
    } catch {
      return false;
    }
  });
  React.useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try {
      localStorage.setItem("agentgeex.theme", dark ? "dark" : "light");
    } catch {
      /* ignore */
    }
  }, [dark]);
  return { dark, toggle: () => setDark((d) => !d) };
}

function Brand({ collapsed }: { collapsed: boolean }) {
  return (
    <Link
      to="/"
      className={cn("flex h-14 items-center gap-2 border-b border-white/10 px-4 font-semibold text-white", collapsed && "justify-center px-2")}
    >
      {/* [2026-11-10 15:03 Feng] Logo switched to SVG (vector): 28px in the sidebar, 96px on the login page,
           crisp at any size in the future. The PNG is still kept under the same name (favicon / email / third-party
           references still need it), but **the source of truth is `public/brand/logo-mark.svg`**: change the image there,
           then run `node scripts/brand_export.mjs --ico` to regenerate all bitmaps. */}
      {/* [2026-11-11 00:40 Feng] 28px → 40px.
           Reason: the logo has three letters (capital A + lowercase g + X); at 28px each letter is only 6-7 pixels,
           and the tail of the g works out to **1 pixel** — it cannot show no matter how it is drawn.
           Visual-model testing size by size: 28/32/36 read as "O/q/circle", 40 is marginal, **48 is clearly a g**.
           So it is not a shape problem, it is not enough pixels. Enlarging the icon a bit beats redrawing the glyph.
           Picked 36px instead of 40: 40px in a 56px header takes 71%, leaving only 8px above and below, visually "filled to
           the brim" (the acceptance review called it cramped). 36px takes 64%, close to the comfortable UI range (50-65%),
           while the tail grows from 1.0px to 1.3px. */}
      <img
        src="/brand/logo-mark.svg"
        alt="AgentGeeX"
        width={36}
        height={36}
        className="h-9 w-9 shrink-0 rounded-lg object-contain"
        data-testid="brand-logo"
      />
      {!collapsed && (
        <span className="truncate text-sm text-white">
          AgentGeeX
        </span>
      )}
    </Link>
  );
}

/**
 * HeaderActions - area C (right side of the top bar) action group
 *   Aligned with vue-admin-tpl "ToolHeader" right side: fullscreen / language / user info (+ theme / repo)
 *   Note: language and user settings are button placeholders; the actual features come later.
 */
const LANGUAGES = SUPPORTED_LOCALES;

function HeaderActions({ dark, toggle, onSend }: { dark: boolean; toggle: () => void; onSend: () => void }) {
  const { t, i18n } = useTranslation();
  const [full, setFull] = React.useState(false);
  const [langOpen, setLangOpen] = React.useState(false);
  const lang = i18n.language as LocaleKey;

  React.useEffect(() => {
    const on = () => setFull(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", on);
    return () => document.removeEventListener("fullscreenchange", on);
  }, []);

  const toggleFullscreen = () => {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else document.documentElement.requestFullscreen?.();
  };

  return (
    <div className="flex items-center gap-1" data-testid="header-actions">
      {/* [2026-11-13 08:30 Feng] Global "send message" —
           Original words: "Put one on the top status bar too, next to that global button, roughly the same size,
                   and change it to this blue. That way I can send a message from any page."
          ★ Before, you had to go to the system status page to click that button — sending a message required a page change, too long a path.
          ★ Size matches the neighbouring icon button (h-9), but it has text so it needs px-3;
            use the default variant = blue primary, consistent with the "primary action" palette (no new color invented). */}
      <Button
        size="sm"
        className="h-9 gap-1.5 px-3"
        onClick={onSend}
        title={t("header.sendHint")}
        data-testid="header-send"
      >
        <Send className="h-4 w-4" />
        {t("header.send")}
      </Button>
      <span className="mx-1 h-5 w-px bg-border" aria-hidden />
      {/* Fullscreen */}
      <Button
        variant="ghost"
        size="icon"
        onClick={toggleFullscreen}
        title={full ? t("header.exitFullscreen") : t("header.fullscreen")}
        aria-label={full ? t("header.exitFullscreen") : t("header.fullscreen")}
        data-testid="header-fullscreen"
      >
        {full ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
      </Button>

      {/* Language switch */}
      <Popover open={langOpen} onOpenChange={setLangOpen}>
        <PopoverTrigger asChild>
          <Button variant="ghost" size="icon" title={t("header.switchLanguage")} aria-label={t("header.switchLanguage")} data-testid="header-locale">
            <Languages className="h-4 w-4" />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-40 p-1" data-testid="header-locale-menu">
          {LANGUAGES.map((l) => (
            <button
              key={l.key}
              type="button"
              data-testid={`locale-${l.key}`}
              onClick={() => {
                changeLocale(l.key);
                setLangOpen(false);
              }}
              className={cn(
                "flex w-full items-center justify-between rounded-sm px-2 py-1.5 text-sm transition-colors hover:bg-accent hover:text-accent-foreground",
                lang === l.key ? "text-primary" : "text-muted-foreground"
              )}
            >
              {l.label}
              {lang === l.key && <Check className="h-3.5 w-3.5" />}
            </button>
          ))}
        </PopoverContent>
      </Popover>

      {/* Theme switch */}
      <Button variant="ghost" size="icon" onClick={toggle} title={dark ? t("header.switchLight") : t("header.switchDark")} data-testid="header-theme">
        {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      </Button>

      {/* Repository */}
      <a
        href="https://gitee.com/robertrfeng/agentgeex"
        target="_blank"
        rel="noreferrer"
        data-testid="header-repo"
        title={t("header.repo")}
        className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-input text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
      >
        <Github className="h-4 w-4" />
      </a>

      {/* User info settings (aligned with vue-admin-tpl UserInfo: personal center / docs / lock screen / logout) */}
      <UserMenu />
    </div>
  );
}

/**
 * UserMenu - area C right-side "user info" dropdown (ref. vue-admin-tpl UserInfo)
 *   Personal center → route to /personal/center
 *   Docs            → open in a new window
 *   Test            → pop the "MCP service self-check" window (2026-10-29 Feng: MCP updates often, you must see at a glance
 *                     which version the frontend is connected to; data comes from the BFF passing through agx_check_services)
 *   Lock screen     → pop the lock-screen password dialog (LockScreenProvider)
 *   Logout          → confirm, then go to /login
 */
function UserMenu() {
  /* ★ 2026-11-24: the header used to hard-code "feng" / "F". It must show the signed-in
     person instead -- screenshots and the shipped UI should never carry a real name. */
  const { data: identity } = useGetIdentity<{ id: string; name: string }>();
  const displayName = identity?.name || "operator";
  const initial = (displayName.trim()[0] || "A").toUpperCase();
  const { t } = useTranslation();
  const [open, setOpen] = React.useState(false);
  const [confirmLogout, setConfirmLogout] = React.useState(false);
  const [checkOpen, setCheckOpen] = React.useState(false);
  const navigate = useNavigate();
  const { requestLock } = useLockScreen();

  const go = (fn: () => void) => {
    setOpen(false);
    // Wait for the Popover to close before running, to avoid focus stealing
    setTimeout(fn, 0);
  };

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            data-testid="header-user"
            title={t("header.settings")}
            className="ml-1 flex items-center gap-2 rounded-md px-1.5 py-1 transition-colors hover:bg-accent"
          >
            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-gradient-to-br from-sky-400 via-indigo-500 to-fuchsia-500 text-xs font-semibold text-white">
              {initial}
            </span>
            <span className="hidden text-sm font-medium lg:inline">{displayName}</span>
            <ChevronDown className="hidden h-3.5 w-3.5 text-muted-foreground lg:inline" />
          </button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-44 p-1" data-testid="header-user-menu">
          <button
            type="button"
            data-testid="user-profile"
            onClick={() => go(() => navigate("/personal/center"))}
            className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
          >
            <User className="h-3.5 w-3.5" />
            {t("header.profile")}
          </button>
          <button
            type="button"
            data-testid="user-doc"
            onClick={() => go(() => window.open("https://element-plus-admin-doc.cn/", "_blank", "noopener"))}
            className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
          >
            <BookOpen className="h-3.5 w-3.5" />
            {t("header.document")}
          </button>
          {/* [2026-11-07 Feng change] The old "Test → MCP self-check" is upgraded to a unified "service settings" window.
              Feng: "There may be other services in the future, because you cannot keep hanging them in the menu."
              → The menu keeps only this one entry; each service inside is a tab (today: speech recognition / MCP connection).
              Adding a new service = adding a tab, the menu stays. Position is still in the "personal center / docs" group:
              it is a "look at / tune the environment" action, not an account operation. */}
          <button
            type="button"
            data-testid="user-mcp-check"
            onClick={() => go(() => setCheckOpen(true))}
            className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
          >
            <SlidersHorizontal className="h-3.5 w-3.5" />
            {t("header.serviceSettings")}
            <span className="ml-auto rounded border border-strong px-1 font-mono text-[10px] text-muted-foreground">
              {t("header.serviceBadge")}
            </span>
          </button>
          <div className="my-1 h-px bg-border" />
          <button
            type="button"
            data-testid="user-lock"
            onClick={() => go(requestLock)}
            className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
          >
            <Lock className="h-3.5 w-3.5" />
            {t("header.lock")}
          </button>
          <button
            type="button"
            data-testid="user-logout"
            onClick={() => go(() => setConfirmLogout(true))}
            className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm text-destructive transition-colors hover:bg-destructive/10"
          >
            <LogOut className="h-3.5 w-3.5" />
            {t("header.logout")}
          </button>
        </PopoverContent>
      </Popover>

      {/* MCP version self-check (the "Test" menu item) */}
      <ServiceSettingsDialog open={checkOpen} onOpenChange={setCheckOpen} />

      {/* Logout confirmation */}
      <Dialog open={confirmLogout} onOpenChange={setConfirmLogout}>
        <DialogContent data-testid="logout-confirm" className="max-w-[380px]">
          <DialogHeader>
            <DialogTitle>{t("header.logout")}</DialogTitle>
          </DialogHeader>
          <p className="py-2 text-sm text-muted-foreground">{t("header.logoutPrompt")}</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmLogout(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              variant="destructive"
              data-testid="logout-confirm-btn"
              onClick={() => {
                setConfirmLogout(false);
                navigate("/login");
              }}
            >
              {t("header.logoutConfirmBtn")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

export function AppShell({ children }: { children?: React.ReactNode }) {
  const { t } = useTranslation();
  // ★ Browser tab title: set to "<page name> · AgentGeeX" by the current route
  useDocumentTitle();
  const [collapsed, setCollapsed] = React.useState(false);
  /* [2026-11-13 08:30 Feng] On/off for the site-wide "send message" dialog —
     lifted to the Shell layer because it must open on **any page**; mounted on one page (the system status page before)
     would mean "to send a message you first have to jump back". The dialog itself only fetches data when open,
     so mounting it here early adds no first-screen requests. */
  const [sendOpen, setSendOpen] = React.useState(false);
  const { dark, toggle } = useTheme();
  const { pathname } = useLocation();
  const { open } = useTabs();

  /** Ctrl/⌘/middle click → open in a new workspace tab (default is navigating inside the current tab) */
  const onNavClick = (e: React.MouseEvent, it: AgxNavItem) => {
    if (e.metaKey || e.ctrlKey || e.button === 1) {
      e.preventDefault();
      /* The tab title stores a **snapshot of the language at that moment**; on display TabBar recomputes with the current
         language, so old tabs follow a language switch and it does not matter what is stored here. */
      open({ key: it.to, title: t(it.labelKey), iconKey: it.to.replace(/^\/+/, "").split("/")[0] || "index" });
    }
  };

  /** AgentGeeX nav entry rendering (same style as the case nav, flat with no child indentation) */
  const agxNavLink = (it: AgxNavItem) => {
    const active = pathname === it.to || pathname.startsWith(it.to + "/");
    return (
      <Link
        key={it.to}
        to={it.to}
        data-testid={`agx-nav-${it.to.replace(/\//g, "") || "index"}`}
        title={`${t(it.labelKey)}（${t("nav.newTabHint")}）`}
        onClick={(e) => onNavClick(e, it)}
        className={cn(
          "group relative flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
          active
            ? "bg-chrome-active font-semibold text-chrome-active-foreground shadow-raised"
            : "text-chrome-foreground hover:bg-chrome-hover hover:text-white",
          collapsed && "justify-center px-2"
        )}
      >
        <it.icon className={cn("h-4 w-4 shrink-0", active ? "text-chrome-active-foreground" : "opacity-80")} />
        {!collapsed && <span className="truncate">{t(it.labelKey)}</span>}
      </Link>
    );
  };


  return (
    /* The breadcrumb Provider must wrap both sides — "top bar + page":
       the top bar reads it, the detail page writes it, otherwise header and main would not get the same data. */
    <BreadcrumbProvider>
    <DetailPopupProvider render={(kind, id, from) => <EntityDetailBody kind={kind} id={id} from={from} />}>
    <div className="flex h-dvh w-full overflow-hidden">
      <div className="pointer-events-none fixed inset-0 -z-10 bg-background">
        <div className="absolute -left-40 -top-40 h-[420px] w-[420px] rounded-full bg-info/10 blur-[120px]" />
        <div className="absolute right-0 top-1/3 h-[380px] w-[380px] rounded-full bg-chart-4/10 blur-[120px]" />
        <div className="absolute bottom-0 left-1/3 h-[320px] w-[320px] rounded-full bg-chart-1/10 blur-[120px]" />
      </div>

      <aside
        className={cn(
          "hidden h-dvh shrink-0 flex-col border-r border-strong bg-chrome text-chrome-foreground transition-all duration-200 md:flex",
          /* [2026-10-28 16:30/16:31 Feng] Left nav width "smaller, keep making it smaller": expanded 256 → 232 → 208px.
              Collapsed stays 68px (icons only, any narrower is cramped). */
          collapsed ? "w-[68px]" : "w-[208px]"
        )}
      >
        <Brand collapsed={collapsed} />
        <nav className="flex flex-1 flex-col gap-4 overflow-y-auto p-2" data-nav-collapsed={collapsed}>
          {/* AgentGeeX admin nav: group titles + flat entries (aligned with the case nav's "Start / Table / Page cases" style) */}
          {/* ★ Group and entry testids use separate namespaces: group key "agents" and entry path /agents
              would collide into the same id (both `agx-nav-agents`), and Playwright strict mode reports "matched 2". */}
          {AGX_NAV_GROUPS.map((g) => (
            <div key={g.key} className="flex flex-col gap-1" data-testid={`agx-nav-group-${g.key}`}>
              {!collapsed && (
                <div className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wider text-chrome-foreground/75">
                  {t(g.labelKey)}
                </div>
              )}
              {g.items.map(agxNavLink)}
            </div>
          ))}

        </nav>
        {/* Bottom action area: height-aligned with the right main area's footer (border-t + px-6 py-2 + text-xs, same spec on both sides) */}
        <div className="flex shrink-0 items-center justify-center border-t border-white/10 px-6 py-2">
          <button
            onClick={() => setCollapsed((c) => !c)}
            className={cn(
              "flex w-full items-center justify-center gap-2 text-xs text-chrome-foreground hover:text-white",
              collapsed && "gap-0"
            )}
          >
            {collapsed ? <ChevronsRight className="h-4 w-4" /> : <><ChevronsLeft className="h-4 w-4" /> {t("nav.collapse")}</>}
          </button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="z-20 flex h-14 shrink-0 items-center gap-3 border-b border-strong bg-card px-4">
          <Button variant="ghost" size="icon" className="md:hidden"><Menu className="h-5 w-5" /></Button>
          <Link to="/" className="flex items-center gap-2 md:hidden">
            <img src="/brand/logo-mark.svg" alt="AgentGeeX" width={32} height={32} className="h-8 w-8 object-contain" />
            <span className="text-sm font-semibold">AgentGeeX</span>
          </Link>
          {/* [2026-10-29 Feng] Removed the top "general query" box: it was wired to nothing (dead input), useless here.
              The freed space now holds breadcrumbs — showing the hierarchy "nav group / menu page / detail entity":
                it is navigation (click a parent to go back to the list), and the left of the top bar is no longer a blank slab. */}
          <Breadcrumb />
          <div className="ml-auto flex items-center gap-2">
            <HeaderActions dark={dark} toggle={toggle} onSend={() => setSendOpen(true)} />
          </div>
        </header>

        {/* Multi-window workspace tab bar */}
        <TabBar />

        {/* Content area: the outer layer does not scroll; the inner wrapper handles default scrolling.
            ★ 2026-10-11: full-height pages (e.g. agent detail) may use h-full on children and scroll inside themselves,
              keeping the page header/Tab fixed and only the content area scrolling. */}
        <main className="flex min-h-0 flex-1 flex-col bg-background p-4 md:p-6">
          <div className="min-h-0 flex-1 overflow-y-auto">{children ?? <Outlet />}</div>
        </main>
        <footer className="shrink-0 border-t border-strong bg-card px-6 py-2 text-xs text-muted-foreground">
          {t("footer.builtWith")} · Apache-2.0 © 2026
          <span className="ml-2 font-mono text-[10px] opacity-60" data-testid="build-stamp" title={t("miscShell.shell.buildStamp")}>
            {__BUILD_STAMP__}
          </span>
        </footer>
      </div>

      {/* Site-wide send-message dialog (openable on any page):
          inside the Provider so that entities clicked in the dialog can still pop details;
          outside the <div> so that sidebar/main overflow does not clip it. */}
      <MessageSendDialog open={sendOpen} onOpenChange={setSendOpen} />
    </div>
    </DetailPopupProvider>
    </BreadcrumbProvider>
  );
}
