/**
 * Manual intervention confirmation dialog (urge / acknowledge reminder)
 * ────────────────────────────────────────────────────────────────────────────
 * [2026-10-31 Feng] Why a second confirmation:
 *   this is not "just a refresh" -- pressing it sends a message to **another agent**, which will really stop
 *   and read it and really reply. One misclick disturbs a person out of nowhere (more precisely, an
 *   execution body that burns compute).
 *   So the confirmation box must spell out "who it goes to, what it says, what the consequence is", not just
 *   ask "are you sure".
 *
 * ★ Direction must be stated here (the most easily botched point):
 *     urge                 -> the executor (the one doing the work)
 *     acknowledge reminder -> the initiator (the one who dispatched)
 *   Getting it backwards equals urging someone who already handed in their homework to keep working.
 */
import { useTranslation } from "react-i18next";
import { BellRing, Stamp } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export type NudgeConfirmKind = "urge" | "ack_remind";

export interface NudgeConfirmTarget {
  kind: NudgeConfirmKind;
  /** run_uuid, used to locate which row in the dialog */
  runUuid: string;
  /** Task name (people recognize this, not the uuid) */
  taskName?: string;
  /** The agent that will be nudged */
  toAgent: string;
  /** Number of nudges already sent (shows "already nudged N times", letting people judge whether to change tactics) */
  nudgeCount?: number;
}

/* Keys only (module-level constants cannot reach the hook). */
const COPY_KEY: Record<NudgeConfirmKind, { title: string; who: string; what: string; verb: string }> = {
  urge: {
    title: "nudgeDialog.nudgeTitle",
    who: "nudgeDialog.nudgeWho",
    what: "nudgeDialog.nudgeWhat",
    verb: "nudgeDialog.nudgeVerb",
  },
  ack_remind: {
    title: "nudgeDialog.ackTitle",
    who: "nudgeDialog.ackWho",
    what: "nudgeDialog.ackWhat",
    verb: "nudgeDialog.ackVerb",
  },
};

export function NudgeConfirmDialog({
  target,
  busy,
  onOpenChange,
  onConfirm,
}: {
  target: NudgeConfirmTarget | null;
  busy?: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  const ck = COPY_KEY[target ? target.kind : "urge"];
  const copy = { title: t(ck.title), who: t(ck.who), what: t(ck.what), verb: t(ck.verb) };
  const Icon = target?.kind === "ack_remind" ? Stamp : BellRing;

  return (
    <Dialog open={!!target} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md" data-testid="nudge-confirm-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Icon className="h-5 w-5 text-action-run" aria-hidden />
            {copy.title}
          </DialogTitle>
          <DialogDescription>
            {t("nudgeDialog.warning")}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-2 rounded-md border bg-muted/20 px-3 py-2 text-sm">
          <div className="flex items-baseline gap-2">
            <span className="w-14 shrink-0 text-xs text-muted-foreground">{t("nudgeDialog.fTarget")}</span>
            <span className="font-medium" data-testid="nudge-confirm-to">
              {target?.toAgent || "—"}
            </span>
            <span className="text-xs text-muted-foreground">（{copy.who}）</span>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="w-14 shrink-0 text-xs text-muted-foreground">{t("nudgeDialog.fContent")}</span>
            <span>{copy.what}</span>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="w-14 shrink-0 text-xs text-muted-foreground">{t("nudgeDialog.fTask")}</span>
            <span className="min-w-0 break-all" data-testid="nudge-confirm-run">
              <span className="mr-1.5">{target?.taskName || "—"}</span>
              <span className="font-mono text-xs text-muted-foreground">{target?.runUuid}</span>
            </span>
          </div>
        </div>

        {/* Already nudged and still not moving — exactly the signal that it is time to change tactics, must be visible before clicking */}
        {(target?.nudgeCount ?? 0) > 0 ? (
          <p className="text-xs text-warning" data-testid="nudge-confirm-repeat">
            {t("nudgeDialog.repeated", { count: target?.nudgeCount ?? 0 })}
          </p>
        ) : null}

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            data-testid="nudge-confirm-cancel"
          >
            {t("nudgeDialog.cancel")}
          </Button>
          <Button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            data-testid="nudge-confirm-submit"
          >
            {busy ? t("nudgeDialog.sending") : t("nudgeDialog.confirm", { verb: copy.verb })}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
