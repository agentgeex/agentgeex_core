/**
 * components/agentgeex/detail-popup.tsx — the global 'entity detail dialog'
 * ────────────────────────────────────────────────────────────────
 * [2026-10-29 Feng] 'many windows have no linkage ... wherever there is a relation, one click should pop up its detail'.
 *
 * Why a dialog instead of a page jump:
 *   While reading executions you want to confirm 'who sent this / to whom / in which session',
 *   and jumping to a full page loses the context; you have to click back and find your way again.
 *   A dialog = **look in place, close and you are back where you were** (Feng: put the detail page inside a dialog, back becomes close).
 *
 * Structure (the two parts do not eat each other):
 *   · DetailPopupProvider — provides `open(kind, id)`, mounted once in the shell; keeps a **stack** internally,
 *     supporting chained drill-down such as 'execution detail → click agent → click a task of its own' (stack up, close layer by layer).
 *   · DetailEmbedContext — true only inside dialog content; DetailShell uses it to turn 'back' into
 *     'close', and stops rewriting the top-bar breadcrumb (a dialog is not navigation).
 *
 * Where the content comes from: the shell injects `render`, pointing at `EntityDetailBody` (10 entity kinds + agents);
 * this file does not import page components — avoiding a 'dialog ↔ page' circular dependency.
 *
 * [2026-11-05 23:54 Feng 'let us unify this'] The close entry **exists only once site-wide**:
 * the × at the dialog's top right (built into DialogContent, one shared implementation site-wide).
 * Before that it was hideClose + a '✕ close' text button in the content (a temporary tradeoff from 2026-11-03);
 * now it is the reverse: the shell provides the X, the content does not duplicate it — DetailShell no longer renders a close button in dialog mode either.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { ArrowUpRight } from "lucide-react";
import { RefreshCw } from "lucide-react";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { refreshStoreOnce } from "@/lib/fresh-data";
import { cn } from "@/lib/utils";

/* ------------------------------ Entity kinds ------------------------------ */

export type DetailKind =
  | "project" | "task" | "session" | "execution" | "agent"
  | "skill" | "notice" | "host" | "plan" | "project-group" | "quick-cmd";

/** kind → i18n key (a module-level constant cannot reach the hook, so only the key is stored). */
export const DETAIL_KIND_KEY: Record<DetailKind, string> = {
  project: "entityKind.project",
  task: "entityKind.task",
  session: "entityKind.session",
  execution: "entityKind.execution",
  agent: "entityKind.agent",
  skill: "entityKind.skill",
  notice: "entityKind.notice",
  host: "entityKind.host",
  plan: "entityKind.plan",
  "project-group": "entityKind.projectGroup",
  "quick-cmd": "entityKind.quickCmd",
};

interface Target {
  kind: DetailKind;
  id: string;
  /** [2026-11-16 00:04 Feng] 'add a param — this param says where this window came from' —
   *  an origin marker. Detail content narrows itself by it (e.g. an agent opened from 'message send'
   *  must not show another 'message send' button, otherwise a second send window stacks on itself).
   *  ★ Passed down one layer only, not inherited by further drill-downs inside the dialog — a drill-down is 'looking at a relation', not 'the same origin'. */
  from?: string;
}

interface PopupApi {
  open: (t: Target) => void;
  closeTop: () => void;
  depth: number;
}

const PopupCtx = React.createContext<PopupApi | null>(null);
const EmbedCtx = React.createContext<{ onClose: () => void } | null>(null);

/** Open the entity detail dialog (returns null without a Provider; the caller degrades to plain text) */
export function useDetailPopup(): PopupApi | null {
  return React.useContext(PopupCtx);
}

/** Whether currently rendering inside a dialog (used by DetailShell: back → close) */
export function useDetailEmbed(): { onClose: () => void } | null {
  return React.useContext(EmbedCtx);
}

/* ------------------------------ Provider ------------------------------ */

export function DetailPopupProvider({
  render,
  children,
}: {
  /** Injected by the shell: renders detail content for a given kind/id (+ origin marker) */
  render: (kind: DetailKind, id: string, from?: string) => React.ReactNode;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  const [stack, setStack] = React.useState<Target[]>([]);

  /* [2026-11-06 22:15 Feng] 'add a refresh button next to the X ... a manual refresh, as an extra safety net'.
     The entity detail dialog has the same disease as the board dialog: content reads the store, so a dialog left open goes stale.
     A tick change remounts each layer of content (the store is not reactive; without a remount it does not re-read). */
  const [tick, setTick] = React.useState(0);
  const [refreshing, setRefreshing] = React.useState(false);
  const [staleMsg, setStaleMsg] = React.useState("");
  const doRefresh = React.useCallback(async () => {
    setRefreshing(true);
    setStaleMsg("");
    const r = await refreshStoreOnce(true);
    if (!r.ok) setStaleMsg(r.message || t("detailPopup.refreshFailed"));
    setRefreshing(false);
    setTick((n) => n + 1);
  }, []);

  /* ★ The param must not be named t — in i18n-ified files t is the translate function (5th time this round) */
  const open = React.useCallback((target: Target) => {
    if (!target?.id) return;
    setStack((s) => {
      const top = s[s.length - 1];
      // Same entry already on top of the stack → do not stack again (double-clicking does not open two identical layers)
      if (top && top.kind === target.kind && top.id === target.id) return s;
      return [...s, target];
    });
  }, []);

  const closeTop = React.useCallback(() => setStack((s) => s.slice(0, -1)), []);

  const api = React.useMemo<PopupApi>(
    () => ({ open, closeTop, depth: stack.length }),
    [open, closeTop, stack.length],
  );

  return (
    <PopupCtx.Provider value={api}>
      {children}
      {stack.map((item, i) => {
        const isTop = i === stack.length - 1;
        return (
          <Dialog
            key={`${item.kind}:${item.id}:${i}`}
            open
            onOpenChange={(o) => {
              // Only the top layer responds to close (lower layers are covered and should not be closed together with Esc)
              if (!o && isTop) closeTop();
            }}
          >
            <DialogContent
              /* [2026-11-16 00:04 Feng] 'make this window a bit bigger, that is fine'
                 1240 → 1440 wide, 92 → 94vh tall: the agent detail's five cards + task grid are cramped at 1240. */
              className="max-h-[94vh] w-[min(97vw,1440px)] max-w-none overflow-y-auto border-strong bg-card p-0"
              aria-describedby={undefined}
              data-testid="entity-detail-dialog"
              data-kind={item.kind}
              data-id={item.id}
            >
              {/* [2026-11-06 10:44 Feng] 'the agent detail window itself has two buttons ... at this point
                  you have an X colliding with the message-send button ... other dialogs have no button at the top right,
                  so the X sits there; now that your top right has buttons ... you can add a bar and put an X on it.'

                  Root cause: the X is `DialogContent` absolutely positioned at right-4/top-4 (the only one site-wide);
                  the **content's own page header** (DetailShell / agent-detail's detail-head) also has
                  action buttons at the top right (profile / message send). The two fight over the same pixel.

                  Approach: give the dialog **a separate window title bar** that 'frames' the X inside it —
                    1. the X sits on the bar and never enters the content area (no matter how many top-right buttons content gains later)
                    2. the bar's right end has `pr-12` reserving a fixed slot for the X, so content buttons can never collide
                    3. the bar is `sticky top-0` — when content is long and scrolls, bar and X both stay put
                  Distinguishing it from the content (the 'distinction' Feng asked for):
                    bar = 11px gray text + light background + border-b (**window chrome**: which entity is this)
                    content header = avatar + 18px name + badges (**entity identity**: who this entity is)
                  The two differ in font size/background/information level, so nobody reads them as 'two titles'. */}
              <div
                /* [2026-11-16 00:04 Feng] 'your X and refresh button are not centered, they sit too low'
                   Root cause: this X comes from the site-wide `DialogContent`, absolutely positioned `right-4 top-4`,
                   16px icon → its vertical center is **24px from the top**. The title bar used to be `py-2` + 11px text
                   ≈ 33px tall, center ≈16px — so both X and refresh looked like they were sinking.
                   Fix: make the bar a fixed 48px (center exactly 24px), aligned with the X's center;
                   the refresh button previously used `top-1/2`, which was wrong: it was relative to the whole DialogContent
                   (a fixed-position block, 900+ tall), not the title bar — measured, it landed at the dialog's vertical middle (Y≈550).
                   Changed to the same geometry as the X: `top-4` + 16px icon = center 24px, so both necessarily share one midline. */
                className="sticky top-0 z-10 flex h-12 shrink-0 items-center gap-2 border-b border-border bg-muted px-4 pr-12"
                data-testid="detail-dialog-bar"
              >
                <span className="text-[11px] font-medium text-muted-foreground">
                  {/* 'Project detail' and the like attach as a suffix in Chinese but a prefix in English → hand the whole phrase to the language pack */}
                  {t("detailPopup.barLabel", { kind: t(DETAIL_KIND_KEY[item.kind]) })}
                </span>
                <span className="min-w-0 truncate font-mono text-[11px] text-muted-foreground/60">
                  {item.id}
                </span>
                {staleMsg && isTop ? (
                  <span className="ml-auto shrink-0 text-[10px] text-destructive" title={staleMsg}>
                    {t("detailPopup.refreshFailedCached")}
                  </span>
                ) : null}
                {stack.length > 1 && (
                  <span className="ml-auto shrink-0 rounded border border-border px-1.5 py-px text-[10px] text-muted-foreground">
                    {t("detailPopup.layer", { n: i + 1, total: stack.length })}
                  </span>
                )}
              </div>
              {/* Refresh: one slot left of the site-wide X (right-4) */}
              <button
                type="button"
                data-testid={`detail-dialog-refresh-${i}`}
                aria-label={t("detailPopup.refreshAria")}
                title={t("detailPopup.refreshTitle")}
                disabled={refreshing}
                onClick={() => void doRefresh()}
                className="absolute right-11 top-4 z-20 flex h-4 w-4 items-center justify-center rounded-sm opacity-70 transition-opacity hover:opacity-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40"
              >
                <RefreshCw className={refreshing ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
              </button>
              <EmbedCtx.Provider value={{ onClose: closeTop }}>
                <div key={tick} className="p-4">{render(item.kind, item.id, item.from)}</div>
              </EmbedCtx.Provider>
            </DialogContent>
          </Dialog>
        );
      })}
    </PopupCtx.Provider>
  );
}

/* --------------------------- Related entity links --------------------------- */

/**
 * An entity reference that 'opens the detail on click' (inline link style).
 * Without a Provider / without an id it degrades to plain text, so no dead links appear.
 */
export function EntityRef({
  kind,
  id,
  label,
  className,
  title,
  mono = true,
  showIcon = true,
}: {
  kind: DetailKind;
  id?: string | null;
  /** Display text (defaults to the id) */
  label?: React.ReactNode;
  className?: string;
  title?: string;
  /** Monospace for id-like fields (default); pass false for name-like ones */
  mono?: boolean;
  showIcon?: boolean;
}) {
  const { t } = useTranslation();
  const api = useDetailPopup();
  const text = label ?? id ?? "";
  const empty = !id || (typeof text === "string" && !text.trim());

  if (empty) return <span className="text-muted-foreground">—</span>;
  if (!id || !api) {
    return <span className={cn(mono && "font-mono text-xs", className)}>{text}</span>;
  }

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        api.open({ kind, id });
      }}
      title={title ?? t("detailPopup.openTitle", { kind: t(DETAIL_KIND_KEY[kind]), id })}
      className={cn(
        "group inline-flex max-w-full items-center gap-1 text-left text-primary underline-offset-2 hover:underline",
        mono && "font-mono text-xs",
        className,
      )}
      data-testid={`entity-ref-${kind}`}
      data-entity-id={id}
    >
      <span className="min-w-0 truncate">{text}</span>
      {showIcon && <ArrowUpRight className="h-3 w-3 shrink-0 opacity-60 group-hover:opacity-100" />}
    </button>
  );
}

/** 'View detail' button style (for the card's bottom right / a block's action area) */
export function DetailPopupButton({
  kind,
  id,
  label,
  size = "sm",
  className,
}: {
  kind: DetailKind;
  id?: string | null;
  label?: string;
  size?: "sm" | "default";
  className?: string;
}) {
  const { t } = useTranslation();
  const api = useDetailPopup();
  if (!id || !api) return null;
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        api.open({ kind, id });
      }}
      className={cn(
        "inline-flex items-center gap-1 rounded-md border border-border bg-card px-2 text-xs font-medium text-foreground transition-colors hover:border-primary/50 hover:text-primary",
        size === "sm" ? "h-6" : "h-8",
        className,
      )}
      data-testid={`entity-open-${kind}`}
    >
      {label ?? t("detailPopup.fallbackLabel")}
      <ArrowUpRight className="h-3 w-3" />
    </button>
  );
}
