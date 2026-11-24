/**
 * components/agentgeex/dispatch-exempt-panel.tsx
 * ────────────────────────────────────────────────────────────────
 * [2026-10-31 Feng] "The agent detail page shows the exemption state (distinguishing active / expired / none),
 * and offers 30 minutes / 2 hours / 8 hours quick settings plus a close button"
 *
 * What this solves: a worker's role level is {0,1,2,3}, while agx_dispatch_task /
 * agx_schedule_task / agx_quick_cmd_send are L5 — a worker can never call those three tools.
 * The current approach is "open the door temporarily, it closes itself on expiry" (the field is a timestamp, not a 0/1 switch).
 *
 * ★ The three-state decision is made in the BFF; the frontend does not compute time itself
 *   Only until > now is "active". Judging by `until IS NOT NULL` would treat **expired** as active,
 *   which is the most dangerous error of this feature (users think they can still dispatch, but the permission layer refuses already).
 *   Conversely, showing expired as "none" makes users think it was never set and keep clicking.
 *   So the frontend only reads the two already-decided values the server exposes, active / remainingSeconds,
 *   and never compares with the browser clock again — with mismatched clocks you get "list says active, badge says expired".
 *
 * ★ Failures must show the core's own words
 *   "Permission denied" (403) and "invalid argument" (400) are two different things: the former needs someone with
 *   permission to grant it, the latter just needs the parameter fixed. Blurring them into one "operation failed" leaves
 *   users guessing.
 *
 * ★ The acting identity is fixed by the BFF to agx_core
 *   (realdata.EDITOR_AGENT_ID) — the page does not pass caller_agent_id, and should not:
 *   identity is the server's business; exposing it as a frontend parameter would allow forgery.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Clock, Loader2, ShieldOff, ShieldCheck, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { agentsApi, type DispatchExemptInput } from "@/providers/api";
import { refreshPageData } from "@/providers/agentgeex";
import type { AgentRow } from "@/mock/agentgeex";
import { cn } from "@/lib/utils";
import { apiErrorText } from "@/lib/api-error";

/** Quick durations (minutes) — only "temporary" magnitudes; for 30 days use the custom field.
 *  ★ Store keys only, translate at render time (module-level constants cannot get the hook). */
const QUICK = [
  { minutes: 30, labelKey: "dispatchExempt.quick30" },
  { minutes: 120, labelKey: "dispatchExempt.quick2h" },
  { minutes: 480, labelKey: "dispatchExempt.quick8h" },
] as const;

type TFn = (k: string, o?: Record<string, unknown>) => string;

/** Remaining seconds → "1 hour 23 min" / "12 min" / "less than 1 minute".
 *  ★ t must be passed in: this file is a pure function outside the component, it cannot get the hook; time units follow the language. */
export function formatRemaining(seconds: number, t: TFn): string {
  if (seconds <= 0) return t("dispatchExempt.remainZero");
  const totalMin = Math.floor(seconds / 60);
  if (totalMin < 1) return t("dispatchExempt.remainUnderMinute");
  const d = Math.floor(totalMin / 1440);
  const h = Math.floor((totalMin % 1440) / 60);
  const m = totalMin % 60;
  if (d > 0) return t("dispatchExempt.remainDays", { d, h });
  if (h > 0) return m > 0
    ? t("dispatchExempt.remainHoursMinutes", { h, m })
    : t("dispatchExempt.remainHours", { h });
  return t("dispatchExempt.remainMinutes", { m });
}

type Msg = { kind: "ok" | "err"; text: string } | null;

export function DispatchExemptPanel({
  agent,
  onChanged,
}: {
  agent: AgentRow;
  /** Callback after a successful submit (the page uses it to re-fetch the agent's latest state) */
  onChanged?: () => void | Promise<void>;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = React.useState<string>(""); // Identifier of the button currently executing
  const [msg, setMsg] = React.useState<Msg>(null);
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const { toast } = useToast();

  // ★ Roles that are not worker (PM / host_manager / workshop_manager / agent_manager)
  //   already have L5, an exemption changes nothing for them → **render nothing at all**.
  //   Explicitly test `=== false` (not `!agent.dispatchExemptApplicable`):
  //   in the seed mock these two fields may be undefined; when absent the panel should not be hidden.
  if (agent.dispatchExemptApplicable === false) return null;

  const until = agent.dispatchExemptUntil ?? "";
  const active = !!agent.dispatchExemptActive;
  const remain = agent.dispatchExemptRemainingSeconds ?? 0;
  /** Has an expiry time but it has passed — different from "never granted", must be shown separately */
  const expired = !!until && !active;

  const submit = React.useCallback(
    async (body: DispatchExemptInput, tag: string) => {
      setBusy(tag);
      setMsg(null);
      try {
        const r = await agentsApi.setDispatchExempt(agent.id, body);
        // [2026-11-01 Feng] After granting, proactively notify the agent itself (send to its main session),
        // otherwise it does not know it can dispatch now — granting without that is the same as not granting.
        // Notification failure is not authorization failure: the write already succeeded, the two must be reported separately.
        const notifyTail = r.notified
          ? t("dispatchExempt.notifyOk")
          : r.notifyError
            ? t("dispatchExempt.notifyFail", { error: r.notifyError })
            : "";
        const text = r.cleared
          ? t("dispatchExempt.closedMsg", { name: agent.name, tail: notifyTail })
          : r.effectiveNow
            ? t("dispatchExempt.grantedMsg", {
                name: agent.name,
                until: r.until,
                left: formatRemaining(r.remainingSeconds, t),
                tail: notifyTail,
              })
            : t("dispatchExempt.notEffectiveMsg", { until: r.until });
        // Success feedback goes through the **global toast**, not an inline hint in the panel:
        // refreshPageData() runs right after, which remounts the whole page — this component's state would be lost,
        // and an inline hint would flash for a frame (measured: after clicking you never see "applied").
        // Failure needs no remount, so it stays inline (red text right next to the action, easier to find than a toast).
        if (r.effectiveNow || r.cleared) {
          toast({
            title: r.cleared ? t("dispatchExempt.toastClosed") : t("dispatchExempt.toastGranted"),
            description: text,
            // The grant itself succeeded, only the notification did not arrive → use normal styling (not a red error),
            // but say "notification not sent" in the text so it can be re-sent.
            variant: r.notified || !r.notifyError ? "success" : "default",
            duration: r.notifyError ? 9000 : undefined,
          });
          setMsg(null);
        } else {
          setMsg({ kind: "err", text });
        }
        // ★ Re-fetch the latest state, not just local state: the exemption's source of truth is the DB; editing it locally is self-delusion
        await onChanged?.();
      } catch (e) {
        // Show the core message as-is (insufficient permission / conflicting arguments / channel unavailable are different things)
        const err = e as { status?: number; code?: string; message?: string };
        const hint = err.status === 403 ? t("dispatchExempt.permissionHint") : "";
        setMsg({ kind: "err", text: `${apiErrorText(e, t)}${hint}` });
      } finally {
        setBusy("");
      }
    },
    [agent.id, agent.name, onChanged],
  );

  return (
    <div
      className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border bg-muted/20 px-3 py-2"
      data-testid="exempt-panel"
      data-state={active ? "active" : expired ? "expired" : "none"}
    >
      <span className="flex items-center gap-1.5 text-[12px] font-semibold text-foreground">
        <Clock className="h-3.5 w-3.5 text-muted-foreground" />
        {t("dispatchExempt.title")}
      </span>

      {/* ── Three-state badge ── */}
      {active ? (
        <Badge
          variant="outline"
          className="gap-1 border-success/40 bg-success/10 text-success"
          data-testid="exempt-badge"
          data-state="active"
          title={t("dispatchExempt.badgeActiveTitle", { until })}
        >
          <ShieldCheck className="h-3 w-3" />
          {t("dispatchExempt.badgeActive", { left: formatRemaining(remain, t) })}
        </Badge>
      ) : expired ? (
        <Badge
          variant="outline"
          className="gap-1 border-warning/40 bg-warning/10 text-warning"
          data-testid="exempt-badge"
          data-state="expired"
          title={t("dispatchExempt.badgeExpiredTitle", { until })}
        >
          <AlertTriangle className="h-3 w-3" />
          {t("dispatchExempt.badgeExpired", { until })}
        </Badge>
      ) : (
        <Badge variant="outline" className="text-muted-foreground" data-testid="exempt-badge" data-state="none">
          <ShieldOff className="mr-1 h-3 w-3" />
          {t("dispatchExempt.none")}
        </Badge>
      )}

      <span className="hidden text-[11px] text-muted-foreground sm:inline">
        {t("dispatchExempt.scopeHint")}
      </span>

      {/* ── Actions ── */}
      <div className="ml-auto flex flex-wrap items-center gap-1.5">
        {QUICK.map((q) => (
          <Button
            key={q.minutes}
            size="sm"
            variant="outline"
            className="h-7 px-2 text-[12px]"
            disabled={!!busy}
            data-testid={`exempt-quick-${q.minutes}`}
            onClick={() =>
              void submit(
                { minutes: q.minutes, reason: t("dispatchExempt.quickReason", { label: t(q.labelKey) }) },
                `q${q.minutes}`,
              )
            }
          >
            {busy === `q${q.minutes}` ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
            {t(q.labelKey)}
          </Button>
        ))}
        <Button
          size="sm"
          variant="view"
          className="h-7 px-2 text-[12px]"
          disabled={!!busy}
          data-testid="exempt-custom-open"
          onClick={() => setDialogOpen(true)}
        >
          {t("dispatchExempt.customOpen")}
        </Button>
        {until ? (
          <Button
            size="sm"
            variant="destructive"
            className="h-7 px-2 text-[12px]"
            disabled={!!busy}
            data-testid="exempt-clear"
            onClick={() => void submit({}, "clear")}
          >
            {busy === "clear" ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
            {t("dispatchExempt.clear")}
          </Button>
        ) : null}
      </div>

      {/* ── Feedback: success green / failure red, failure carries the core message as-is ── */}
      {msg ? (
        <div
          className={cn(
            "basis-full text-[12px]",
            msg.kind === "ok" ? "text-success" : "text-destructive",
          )}
          data-testid="exempt-msg"
          data-kind={msg.kind}
          role={msg.kind === "err" ? "alert" : "status"}
        >
          {msg.text}
        </div>
      ) : null}

      <DispatchExemptDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        agentName={agent.name}
        initialUntil={until}
        busy={!!busy}
        onSubmit={async (body) => {
          await submit(body, "custom");
          setDialogOpen(false);
        }}
      />
    </div>
  );
}

/** Custom expiry moment + reason (the reason goes into the core audit log, so it can be traced later "who opened it and why") */
function DispatchExemptDialog({
  open,
  onOpenChange,
  agentName,
  initialUntil,
  busy,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  agentName: string;
  initialUntil: string;
  busy: boolean;
  onSubmit: (body: DispatchExemptInput) => Promise<void>;
}) {
  const { t } = useTranslation();
  // datetime-local needs "YYYY-MM-DDTHH:mm"; the DB uses "YYYY-MM-DD HH:MM:SS"
  const [when, setWhen] = React.useState("");
  const [reason, setReason] = React.useState("");

  // On open, give a sensible default: the existing expiry time (if still valid), otherwise 2 hours from now
  React.useEffect(() => {
    if (!open) return;
    const base = initialUntil ? new Date(initialUntil.replace(" ", "T")) : null;
    const fallback = new Date(Date.now() + 2 * 60 * 60 * 1000);
    const d = base && base.getTime() > Date.now() ? base : fallback;
    const pad = (n: number) => String(n).padStart(2, "0");
    setWhen(
      `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`,
    );
    setReason("");
  }, [open, initialUntil]);

  const submit = () => {
    if (!when) return;
    // datetime-local → core contract "YYYY-MM-DD HH:MM:SS"
    const until = `${when.replace("T", " ")}:00`;
    void onSubmit({ until, reason: reason.trim() || t("dispatchExempt.customReason") });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t("dispatchExempt.dialogTitle", { name: agentName })}</DialogTitle>
          <DialogDescription>
            {t("dispatchExempt.dialogDesc")}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="exempt-until">{t("dispatchExempt.fUntil")}</Label>
            <Input
              id="exempt-until"
              type="datetime-local"
              value={when}
              onChange={(e) => setWhen(e.target.value)}
              data-testid="exempt-until"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="exempt-reason">{t("dispatchExempt.fReason")}</Label>
            <Textarea
              id="exempt-reason"
              rows={2}
              value={reason}
              placeholder={t("dispatchExempt.reasonPlaceholder")}
              onChange={(e) => setReason(e.target.value)}
              data-testid="exempt-reason"
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            {t("dispatchExempt.cancel")}
          </Button>
          <Button size="sm" onClick={submit} disabled={!when || busy} data-testid="exempt-custom-submit">
            {busy ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : null}
            {t("dispatchExempt.confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
