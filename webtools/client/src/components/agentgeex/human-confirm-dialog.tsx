/**
 * Human approve / reject (needs_confirm) second-step confirmation dialog
 * ────────────────────────────────────────────────────────────────────────────
 * [2026-11-06 Feng] For a task with needs_confirm=1, the executing agent does **not write succeeded** when done;
 * it lands in `awaiting_confirm` and only counts as finished after a human reviews the result and clicks "Approve".
 *
 * Why a second confirmation is unavoidable: these two buttons are **mutually exclusive and irreversible** state transitions —
 *   approve = move to succeeded, **downstream chained tasks are activated at once** (they really start working);
 *   reject = move to failed, and force needs_reply=1 (the initiator receives a failure receipt).
 * One misclick is not "the display looks wrong", it is **driving later tasks in the wrong direction**.
 *
 * So the dialog must spell out "what happens after approve" and keep the result summary
 * the executing agent handed back within reach — a human has to see the result before deciding. Asking only "are you sure" is not a confirmation.
 */
import { useTranslation } from "react-i18next";
import { CheckCircle2, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { ConfirmAction } from "@/providers/api";

export interface HumanConfirmTarget {
  runUuid: string;
  /** Task name (humans recognize this, not the uuid) */
  taskName?: string;
  /** Who executed it (who did the work) */
  agentName?: string;
  /** Result summary handed back by the executing agent — must be visible before approving */
  summary?: string;
}

export function HumanConfirmDialog({
  target,
  busy,
  onOpenChange,
  onConfirm,
}: {
  target: HumanConfirmTarget | null;
  busy?: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (action: ConfirmAction) => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog open={!!target} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg" data-testid="human-confirm-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <CheckCircle2 className="h-5 w-5 text-success" aria-hidden />
            {t("miscShell.humanConfirm.waiting")}
          </DialogTitle>
          <DialogDescription>
            {t("miscShell.humanConfirm.body")}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-2 rounded-md border bg-muted/20 px-3 py-2 text-sm">
          <div className="flex items-baseline gap-2">
            <span className="w-14 shrink-0 text-xs text-muted-foreground">{t("miscShell.humanConfirm.fTask")}</span>
            <span className="min-w-0 break-all" data-testid="hc-task">
              <span className="mr-1.5">{target?.taskName || "—"}</span>
              <span className="font-mono text-xs text-muted-foreground">{target?.runUuid}</span>
            </span>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="w-14 shrink-0 text-xs text-muted-foreground">{t("miscShell.humanConfirm.fAgent")}</span>
            <span data-testid="hc-agent">{target?.agentName || "—"}</span>
          </div>
        </div>

        {/* Result summary: the only thing worth reading before approving. If there is none, say "no summary"
            explicitly instead of showing a blank area that looks like a load failure. */}
        <div className="flex flex-col gap-1">
          <span className="text-xs text-muted-foreground">{t("miscShell.humanConfirm.resultTitle")}</span>
          <div
            className="max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-background px-3 py-2 text-sm"
            data-testid="hc-summary"
          >
            {target?.summary?.trim() ? target.summary : t("miscShell.humanConfirm.noSummary")}
          </div>
        </div>

        <p className="text-xs text-muted-foreground">
          {t("miscShell.humanConfirm.consequence")}
        </p>

        <DialogFooter className="gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            data-testid="hc-cancel"
          >
            {t("miscShell.humanConfirm.later")}
          </Button>
          <Button
            type="button"
            variant="destructive"
            onClick={() => onConfirm("reject")}
            disabled={busy}
            data-testid="hc-reject"
          >
            <XCircle className="mr-1.5 h-4 w-4" />
            {t("miscShell.humanConfirm.reject")}
          </Button>
          <Button
            type="button"
            onClick={() => onConfirm("approve")}
            disabled={busy}
            data-testid="hc-approve"
          >
            <CheckCircle2 className="mr-1.5 h-4 w-4" />
            {t("miscShell.humanConfirm.approve")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default HumanConfirmDialog;
