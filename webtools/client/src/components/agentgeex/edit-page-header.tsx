/**
 * components/agentgeex/edit-page-header.tsx — edit page header (the only implementation)
 * ────────────────────────────────────────────────────────────────
 * [2026-11-04 Feng] "The back button must be at the very edge… all edit dialogs are like this"
 *   The four edit pages — compute hosts / tasks / task executions / skill sharing — each wrote its own header,
 *   and all put "Back" left of "Save" — treating "exit" as a neighbour of the primary action.
 *   Unified to: **[extra action] → [Save] → [Back]**, back/close = the rightmost item of the whole window.
 *
 * Why extract a component instead of fixing four places:
 *   hand-fixing four aligns today, not tomorrow — the next edit page will get it wrong again.
 *   One implementation for one visual rule, and the position can no longer drift.
 *
 * ★ Same rule as `DetailShell`: the detail page's "back/close" already comes after actions (rightmost),
 *   so the edit page matches it instead of establishing a second convention.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, Loader2, Save } from "lucide-react";
import { Button } from "@/components/ui/button";

export function EditPageHeader({
  title,
  subtitle,
  subtitleClassName,
  onBack,
  backTestId,
  saveTestId,
  saveLabel,
  saveDisabled,
  busy = false,
  actions,
}: {
  title: string;
  /** Small text on the right of the title (id / name etc.), monospace */
  subtitle?: React.ReactNode;
  /** On a few pages the subtitle is not an id (needs a line break), so the default style can be overridden */
  subtitleClassName?: string;
  onBack: () => void;
  /** data-testid of the back button (pages keep their original values, so acceptance scripts need no change) */
  backTestId?: string;
  saveTestId?: string;
  saveLabel?: string;
  saveDisabled?: boolean;
  busy?: boolean;
  /** Extra action left of Save (e.g. "Reset"); Back is always last */
  actions?: React.ReactNode;
}) {
  /* Default text must not be written in the parameter list: defaults are evaluated at call time, when the hook has not run. */
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-border pb-4">
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle ? (
          <span
            className={
              subtitleClassName ??
              "font-mono text-xs text-muted-foreground"
            }
          >
            {subtitle}
          </span>
        ) : null}
      </div>

      {/* ★ Order is fixed: [extra action] [Save] [Back]. Back last in the group = visually rightmost. */}
      <div className="flex shrink-0 items-center gap-2" data-testid="edit-header-actions">
        {actions}
        <Button
          type="submit"
          size="sm"
          disabled={busy || saveDisabled}
          data-testid={saveTestId}
        >
          {busy ? (
            <Loader2 className="mr-1 h-4 w-4 animate-spin" />
          ) : (
            <Save className="mr-1 h-4 w-4" />
          )}
          {saveLabel ?? t("common.save")}
        </Button>
        <Button
          type="button"
          variant="back"
          size="sm"
          onClick={onBack}
          disabled={busy}
          data-testid={backTestId}
        >
          <ArrowLeft className="mr-1 h-4 w-4" />{t("common.back")}
        </Button>
      </div>
    </div>
  );
}
