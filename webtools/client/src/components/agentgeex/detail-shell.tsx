/**
 * components/agentgeex/detail-shell.tsx — shared skeleton for detail pages
 * ────────────────────────────────────────────────────────────────
 * [2026-10-26 Feng] "You cannot see the details of anything; you need a page that can show its details"
 *
 * Why a shared skeleton instead of one per page:
 *   - 10 entities (project/task/session/execution/skill/notice/host/plan/agent group/quick command) have
 *     detail pages with exactly the same structure: back + title area + several info sections + raw data.
 *   - One per page → font sizes/spacing/label widths inevitably drift (historically the section title drifted twice).
 *
 * Design tokens only; the label column has a fixed width and the value column is `min-w-0` and wraps
 * (a long x_desc / path must not break the layout).
 */
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowLeft } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { JsonView } from "@/components/ui/json-view";
import { useDetailCrumb } from "@/lib/breadcrumb";
import { useDetailEmbed } from "@/components/agentgeex/detail-popup";

/* --------------------------- page skeleton --------------------------- */

export function DetailShell({
  backTo,
  backLabel,
  title,
  subtitle,
  leading,
  badges,
  actions,
  children,
  testId,
  onClose,
  backText,
}: {
  backTo: string;
  /** accessible name / hover hint (states "returns to where"), default copy in backText */
  backLabel?: string;
  /** the two characters on the button. Defaults to common.back — it used to be hard-coded Chinese "back",
   *  so all 10 detail pages showed a Chinese button in the English UI. */
  backText?: string;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  /** left of the title (avatar / logo / icon) */
  leading?: React.ReactNode;
  /** tag row right of the title */
  badges?: React.ReactNode;
  /** top-right action area */
  actions?: React.ReactNode;
  children: React.ReactNode;
  testId?: string;
  /** [2026-11-03] details inside its own dialog (e.g. a flow graph node): when passed, treat as "inside a dialog" —
   *  no breadcrumb (a dialog is not navigation), "back" becomes "close". */
  onClose?: () => void;
}) {
  const navigate = useNavigate();
  const { t } = useTranslation();
  /* [2026-10-29] rendered inside a dialog: no breadcrumb (a dialog is not navigation), "back" becomes "close". */
  const embed = useDetailEmbed();
  const closeFn = onClose ?? embed?.onClose;
  const inDialog = !!closeFn;
  /* [2026-10-29] hand the entity name to the top bar breadcrumb (all 10 detail pages share this, one place takes effect) */
  useDetailCrumb(inDialog ? null : (typeof title === "string" ? title : null));

  return (
    /* [2026-10-29 11:39 Feng] The page header (entity name/icon/badges/actions/back) used to hang **outside** the
        white frame, clashing with the whole-page "everything lives in the white frame" system.
        Now: the white frame is the first layer, the header is its first row, a divider (border-b) under the header,
        and the info sections start below the line — same style as the line between "session info | session
        executions" in session details. */
    <div className="flex flex-col" data-testid={testId}>
      <div
        className={cn(
          "rounded-xl border border-strong bg-card shadow-sm",
          /* inside a dialog: the outer frame is the dialog itself, another frame would be a "frame in a frame" (visual noise) */
          inDialog ? "border-0 p-0 shadow-none" : "p-5",
        )}
        data-testid="content-frame"
      >
        {/* page header (first row inside the frame, horizontal: left = icon + title + subtitle + badges, right = actions + back) */}
        <div
          className="mb-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-border pb-4"
          data-testid="detail-head"
        >
          <div className="flex min-w-0 items-center gap-3">
            {leading}
            <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
              <h1 className="min-w-0 truncate text-xl font-semibold tracking-tight">
                {title}
              </h1>
              {subtitle && (
                <span className="min-w-0 truncate font-mono text-xs text-muted-foreground">
                  {subtitle}
                </span>
              )}
              {badges && (
                <div className="flex flex-wrap items-center gap-1.5" data-testid="detail-badges">
                  {badges}
                </div>
              )}
            </div>
          </div>

          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {actions}
            {/* [2026-10-28 15:05 Feng] back = purple (back variant), colored apart from buttons for other uses. */}
            {/* [2026-11-05 23:54 Feng "unify it"]
                Inside a dialog **no longer render a "✕ close" text button** — the close entry is uniformly the X
                at the dialog's top right (`DialogContent` provides it, one shared copy site-wide). Before it was
                hideClose plus a text button here, i.e. "every dialog builds its own close"; now the reverse: one X
                in the shell, content does not duplicate it.
                Page mode (not in a dialog) still keeps "back", because a detail page needs to return to the list. */}
            {inDialog ? null : (
              <Button
                variant="back"
                size="sm"
                className="gap-1"
                onClick={() => navigate(backTo)}
                aria-label={backLabel ?? t("miscShell.detailShell.backLabel")}
                title={backLabel ?? t("miscShell.detailShell.backLabel")}
                data-testid="detail-back"
              >
                <ArrowLeft className="h-4 w-4" />
                {backText ?? t("common.back")}
              </Button>
            )}
          </div>
        </div>

        {children}
      </div>
    </div>
  );
}

/* --------------------------- sections --------------------------- */

export function DetailSection({
  title,
  hint,
  actions,
  children,
  className,
  testId,
}: {
  title: string;
  hint?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <section
      className={cn(
        "flex min-w-0 flex-col gap-3 border-b border-border pb-5 last:border-b-0 last:pb-0",
        className,
      )}
      data-testid={testId}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h2 className="text-base font-semibold">{title}</h2>
          {hint && <p className="text-[11px] leading-snug text-muted-foreground">{hint}</p>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

/* --------------------------- field grid --------------------------- */

export interface FieldItem {
  label: string;
  value: React.ReactNode;
  /** monospace (ID / path / timestamp) */
  mono?: boolean;
  /** spans two columns (long text, Markdown) */
  span?: 1 | 2;
  /** empty-value copy (default "—") */
  empty?: string;
}

/** single/double-column adaptive key-value table: label fixed at 7em, value `min-w-0` and wraps */
export function FieldGrid({
  fields,
  columns = 2,
  className,
}: {
  fields: FieldItem[];
  columns?: 1 | 2;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "grid gap-x-6 gap-y-2",
        columns === 2 ? "grid-cols-1 md:grid-cols-2" : "grid-cols-1",
        className,
      )}
    >
      {fields.map((f) => {
        const empty = f.value === null || f.value === undefined || f.value === "";
        return (
          <div
            key={f.label}
            className={cn(
              "flex min-w-0 items-start gap-2",
              f.span === 2 && "md:col-span-2",
            )}
          >
            {/* [2026-10-29 19:27 Feng] The label used to be muted light grey with no distinction from the value →
                font size unchanged (text-sm), changed to bold sans + body color, same weight as section titles. */}
            <span className="w-[7em] shrink-0 text-right text-sm font-semibold text-foreground">
              {f.label}
            </span>
            <span
              className={cn(
                "min-w-0 flex-1 break-all text-sm",
                f.mono && "font-mono text-xs",
                empty && "text-muted-foreground",
              )}
            >
              {empty ? (f.empty ?? "—") : f.value}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/* --------------------------- long text block --------------------------- */

/** Markdown / long text / JSON: one shared frame, so pages do not each write their own <pre> */
export function TextBlock({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0 rounded-md border bg-muted/30 p-3", className)}>{children}</div>
  );
}

/** raw data (collapsible) — the detail page fallback: whatever the fields do not cover can still be seen here */
export function RawBlock({ data, label }: { data: unknown; label?: string }) {
  const { t } = useTranslation();
  return (
    <details className="min-w-0 rounded-md border bg-muted/20 px-3 py-2" data-testid="detail-raw">
      <summary className="cursor-pointer text-xs text-muted-foreground">
        {label ?? t("miscShell.detailShell.rawData")}
      </summary>
      <div className="mt-2">
        <JsonView value={data} showCopy />
      </div>
    </details>
  );
}

/* --------------------------- empty state --------------------------- */

export function DetailNotFound({ what, backTo }: { what: string; backTo: string }) {
  const navigate = useNavigate();
  const { t } = useTranslation();
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed py-16">
      <p className="text-sm text-muted-foreground">{t("common.recordMissing", { what })}</p>
      <Button variant="outline" size="sm" onClick={() => navigate(backTo)}>
        {t("common.backToList")}
      </Button>
    </div>
  );
}
