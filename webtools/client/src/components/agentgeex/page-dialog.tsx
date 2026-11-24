/**
 * components/agentgeex/page-dialog.tsx — the unified shell that "puts a list page inside a dialog"
 * ────────────────────────────────────────────────────────────────
 * [2026-11-06 00:03 Feng]
 *   "This agent one works, and the system status page too, do the same for all the others...
 *     This feature is very powerful and very useful."
 *   "Add a check: if it is 0, skip it, clicking into a 0 is useless."
 *
 * The settled approach (validated by the agent page): **page body + query-condition input**, not another list.
 *   Page side: `XBoard({ initialFilters, embedded })` = the body; `XPage()` = thin shell (reads URL params)
 *   Dialog side: `<PageDialog title=...><XBoard initialFilters=... embedded /></PageDialog>`
 *
 * This file provides only the **shell** (size/title/scroll area) and contains no business logic;
 * every page's dialog lives in `page-dialogs.tsx` and uses the same shell.
 *
 * Three hard rules (all learned the hard way):
 *   ① the only close entry is the X at the top right (`DialogContent` provides it, shared site-wide)
 *   ② the body must be `min-h-0 flex-1 overflow-y-auto`, otherwise flex children do not shrink and cannot scroll
 *   ③ filters inside the dialog are "temporary viewing" and **are not persisted locally** (handled by each Board's embedded)
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Loader2, AlertTriangle, RefreshCw } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { refreshStoreOnce } from "@/lib/fresh-data";

/* ── pull fresh data once when the dialog opens (2026-11-06 22:08 Feng)────────────────
 *
 * [Problem] Feng's steps: on the agent page send a task that "needs human confirmation" → go to "system status" →
 *   "waiting for human confirmation" on "process executions" shows 1 → click in and **the dialog does not have it** →
 *   go back to the executions page, refresh, and there it is.
 *
 * [Why] The number on the system-status card comes from `/v1/dashboard/stats` (**a live aggregation SQL**,
 *   the page polls it), while the list inside the dialog comes from the frontend store (the snapshot hydrated
 *   when the page opened). One is live, the other is stale → the number says 1, the list has 0.
 *   This is not a forgotten refresh somewhere, it is **two data sources that are inherently out of sync**.
 *
 * [Approach] On open, re-pull the snapshot once, then remount children (key=tick) so the store is read again.
 *
 * [Why not refreshPageData()] It calls `notifyPageRefreshed()` → the App's `RefreshableRoutes` key changes →
 *   **the whole page remounts** → dialog state is lost and the dialog closes itself.
 *   An "refresh on open" action that refreshes away its own window is worse than not refreshing.
 *   So `hydrateFromApi()` is used (swaps the store only, no global notification) and this component remounts
 *   children itself.
 *
 * [Cost] snapshot 48MB / about 0.6s (on this host). Hence inflight de-duplication: opening several dialogs sends
 *   one request; a failed refresh does not hang in loading but says clearly "you are looking at the cache".
 */

export function PageDialog({
  open,
  onOpenChange,
  icon,
  title,
  subtitle,
  testId,
  children,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  icon?: React.ReactNode;
  title: React.ReactNode;
  /** small text after the title (e.g. "76 total" / "host card says 76") */
  subtitle?: React.ReactNode;
  /** lets the acceptance script identify this dialog */
  testId: string;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  const [refreshing, setRefreshing] = React.useState(false);
  const [stale, setStale] = React.useState<string>("");
  const [tick, setTick] = React.useState(0);
  const aliveRef = React.useRef(true);

  const runRefresh = React.useCallback(async (force: boolean) => {
    setRefreshing(true);
    setStale("");
    const r = await refreshStoreOnce(force);
    if (!aliveRef.current) return;
    if (!r.ok) setStale(r.message || t("miscShell.pageDialog.refreshFailedCached"));
    setRefreshing(false);
    /* the store is a plain object (not reactive): without remounting children it always reads a stale value */
    setTick((t) => t + 1);
  }, []);

  React.useEffect(() => {
    aliveRef.current = true;
    if (!open) return;
    void runRefresh(false);
    return () => { aliveRef.current = false; };
  }, [open, runRefresh]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="flex h-[92vh] w-[min(97vw,1480px)] max-w-none flex-col gap-0 overflow-hidden p-0"
        data-testid={testId}
        aria-describedby={undefined}
      >
        {/* pr-20: the top right now has two buttons, "refresh" and "close"; not enough room squeezes the title */}
        {/* [2026-11-06 22:15 Feng] "Add a refresh button next to the X... so you refresh it manually,
            I want a safety net". Placed right beside the site-wide X (right-4), one slot to the left = right-11.
            ★ Why: auto refresh happens only **at the moment of opening**; a dialog left open grows stale.
              Automatic is "best effort", manual is "guaranteed" — when the user wants the latest, there must be
              something they can definitely press.
            ★ z-20 on par with the X: sticky elements in the title bar / content must not cover it. */}
        <button
          type="button"
          data-testid={`${testId}-refresh`}
          aria-label={t("miscShell.pageDialog.refreshAria")}
          title={t("miscShell.pageDialog.refreshTitle")}
          disabled={refreshing}
          onClick={() => void runRefresh(true)}
          className="absolute right-11 top-3.5 z-20 rounded-sm p-0.5 opacity-70 transition-opacity hover:opacity-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40"
        >
          <RefreshCw className={refreshing ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
        </button>
        <DialogHeader className="shrink-0 border-b px-4 py-2.5 pr-20">
          <DialogTitle className="flex flex-wrap items-center gap-2 text-sm">
            {icon}
            {title}
            {subtitle ? (
              <span className="text-[11px] font-normal text-muted-foreground">{subtitle}</span>
            ) : null}
            {/* the refreshing state must be said out loud: a 48MB snapshot is not instant; without a hint people think
   "this dialog is stuck" */}
            {refreshing ? (
              <span className="inline-flex items-center gap-1 text-[11px] font-normal text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" />
                {t("miscShell.pageDialog.loadingLatest")}
              </span>
            ) : null}
            {!refreshing && stale ? (
              <span
                className="inline-flex items-center gap-1 text-[11px] font-normal text-destructive"
                title={stale}
              >
                <AlertTriangle className="h-3 w-3" />
                {t("miscShell.pageDialog.refreshFailedCached")}
              </span>
            ) : null}
          </DialogTitle>
        </DialogHeader>
        {/* key=tick: remount children after refresh — the store is a plain object (not reactive), without remounting
            it is not re-read and the UI stays old. */}
        <div key={tick} className="min-h-0 flex-1 overflow-y-auto" data-testid={`${testId}-body`}>
          {children}
        </div>
      </DialogContent>
    </Dialog>
  );
}
