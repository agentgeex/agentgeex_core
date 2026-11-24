/**
 * pages/agentgeex/execution-edit.tsx — edit an execution (page, not a dialog)
 * ────────────────────────────────────────────────────────────────
 * [2026-11-01 Feng] "You can build an edit for an execution, some of this stuff may need the operation."
 *
 * The line between what may and may not be changed has to be drawn clearly:
 *
 *   editable: title / timeout / instruction / alert note — these are "how it runs" and "how it is described"
 *   not editable: run_status — the authoritative field of the result; changing it forges a success or a failure
 *   not editable: reply_read — acknowledgement has its own entry point (agx_ack_receipt); bypassing it breaks the receipt loop metrics
 *
 * The last two are not "not implemented yet", they are deliberately kept off this page: this page is called "edit",
 * not "rewrite the facts".
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, SearchX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EditPageHeader } from "@/components/agentgeex/edit-page-header";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/toast";
import { useDetailCrumb } from "@/lib/breadcrumb";
import { listExecutions, refreshPageData, updateExecution } from "@/providers/agentgeex";
import type { ExecutionRow } from "@/mock/agentgeex";
import type { ExecutionUpdateInput } from "@/providers/api";

const EDITOR_AGENT_ID = "agx_core";

interface FormState {
  exec_name: string;
  timeout_seconds: string;
  exec_instruction: string;
  alert_note: string;
}

/** Lower bound of the timeout the kernel allows (see _check_execution_field_policy: range [60, max]) */
const MIN_TIMEOUT = 60;

export default function ExecutionEditPage() {
  const { id = "" } = useParams<{ id: string }>();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { toast } = useToast();

  const row: ExecutionRow | undefined = React.useMemo(
    () => listExecutions().find((r) => r.run_uuid === id),
    [id],
  );
  useDetailCrumb(row?.execName);

  const [form, setForm] = React.useState<FormState | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");

  const baseline: FormState | null = React.useMemo(
    () =>
      row
        ? {
            exec_name: row.execName ?? "",
            timeout_seconds: String(row.timeoutSeconds ?? ""),
            exec_instruction: row.instruction ?? "",
            // The alert note is not part of the list snapshot — empty means "no change", never overwrite the stored value with an empty one
            alert_note: "",
          }
        : null,
    [row],
  );

  React.useEffect(() => {
    setForm(baseline);
    setError("");
  }, [baseline]);

  const goBack = () => navigate("/executions");

  if (!row || !form || !baseline) {
    return (
      <div className="flex flex-col" data-testid="execution-edit-missing">
        <div className="flex flex-col items-center gap-3 rounded-xl border border-strong bg-card p-12 text-center shadow-sm">
          <SearchX className="h-8 w-8 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            {t("executionEdit.notFound", { id: id || t("editCommon.emptyId") })}
          </p>
          <Button variant="back" size="sm" onClick={goBack}>
            <ArrowLeft className="mr-1 h-4 w-4" />{t("editCommon.backToList")}
          </Button>
        </div>
      </div>
    );
  }

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((f) => (f ? { ...f, [k]: v } : f));

  const diff: ExecutionUpdateInput = (() => {
    const out: ExecutionUpdateInput = {};
    if (form.exec_name !== baseline.exec_name) out.exec_name = form.exec_name.trim();
    if (form.timeout_seconds !== baseline.timeout_seconds) out.timeout_seconds = Number(form.timeout_seconds);
    if (form.exec_instruction !== baseline.exec_instruction) out.exec_instruction = form.exec_instruction;
    // Submit only when something was really typed: empty string plus empty baseline = untouched
    if (form.alert_note.trim()) out.alert_note = form.alert_note.trim();
    return out;
  })();

  const changed = Object.keys(diff).length;

  const timeoutError = (() => {
    if (!("timeout_seconds" in diff)) return "";
    const v = diff.timeout_seconds as number;
    if (!Number.isFinite(v) || !Number.isInteger(v)) return t("executionEdit.errTimeoutInteger");
    if (v < MIN_TIMEOUT) return t("executionEdit.errTimeoutTooSmall", { min: MIN_TIMEOUT });
    return "";
  })();

  /**
   * Shortening the timeout immediately fires the 0.7/0.8/0.9 wake-up tiers — that is actively pushing this task toward "about to time out".
   * The kernel only allows administrators / global roles to do that; warn here so nobody finds out after pressing save.
   */
  const shrinks = typeof diff.timeout_seconds === "number" && diff.timeout_seconds < (row.timeoutSeconds ?? 0);

  const submit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (timeoutError) return setError(timeoutError);
    if (!("exec_name" in diff) && !diff.alert_note && !changed) return setError(t("editCommon.noChanges"));
    if ("exec_name" in diff && !String(diff.exec_name).trim()) return setError(t("executionEdit.errTitleEmpty"));
    if (!changed) return setError(t("editCommon.noChanges"));
    setBusy(true);
    setError("");
    try {
      const res = await updateExecution(row.run_uuid, diff);
      toast({
        title: t("executionEdit.updated"),
        description: `${row.run_uuid} · ${(res.fields || []).join(" / ")}`,
        variant: "success",
      });
      void refreshPageData();
      navigate("/executions");
    } catch (err) {
      setError((err as { message?: string })?.message || String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col" data-testid="page-execution-edit">
      <form
        onSubmit={submit}
        className="flex flex-col gap-4 rounded-xl border border-strong bg-card p-4 shadow-sm"
        data-testid="content-frame"
      >
        <EditPageHeader
          title={t("executionEdit.title")}
          subtitle={row.run_uuid}
          subtitleClassName="min-w-0 break-all font-mono text-xs text-muted-foreground"
          onBack={goBack}
          backTestId="execution-edit-back"
          saveTestId="execution-edit-save"
          busy={busy}
          saveDisabled={!changed || !!timeoutError}
        />

        <div className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
          <span>{t("executionEdit.kernelHint")}</span>
          <span>·</span>
          <span>{t("editCommon.submitIdentity")} <span className="font-mono">{EDITOR_AGENT_ID}</span></span>
        </div>

        {/* Read-only facts: while editing you can see "what this is" instead of editing blind */}
        <div className="grid grid-cols-1 gap-3 rounded-md border bg-muted/20 px-3 py-2 text-xs md:grid-cols-3">
          <ReadOnly label={t("executionEdit.fTask")} value={row.taskName || row.taskId} />
          <ReadOnly label={t("executionEdit.fAgent")} value={row.agentName || row.agentId} />
          <ReadOnly label={t("executionEdit.fStatus")} value={row.status} />
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <FieldRow label={t("executionEdit.fExecName")} hint={t("executionEdit.fExecNameHint")}>
            <Input
              value={form.exec_name}
              onChange={(e) => set("exec_name", e.target.value)}
              disabled={busy}
              data-testid="execution-edit-name"
            />
          </FieldRow>
          <FieldRow
            label={t("executionEdit.fTimeout")}
            hint={shrinks
              ? t("executionEdit.timeoutShrinkWarn")
              : t("executionEdit.timeoutMinHint", { min: MIN_TIMEOUT })}
          >
            <Input
              value={form.timeout_seconds}
              onChange={(e) => set("timeout_seconds", e.target.value)}
              disabled={busy}
              inputMode="numeric"
              className="font-mono text-xs"
              data-testid="execution-edit-timeout"
            />
          </FieldRow>
        </div>

        <div className="flex min-w-0 flex-col gap-1">
          <Label htmlFor="exec-instr" className="text-sm font-medium">
            {t("executionEdit.instrLabel")}
          </Label>
          <Textarea
            id="exec-instr"
            value={form.exec_instruction}
            onChange={(e) => set("exec_instruction", e.target.value)}
            disabled={busy}
            rows={14}
            className="font-mono text-xs leading-relaxed"
            data-testid="execution-edit-instruction"
          />
          <span className="text-[11px] text-muted-foreground">
            {t("executionEdit.instrHint")}
          </span>
        </div>

        <FieldRow label={t("executionEdit.fAlertNote")} hint={t("executionEdit.fAlertNoteHint")}>
          <Input
            value={form.alert_note}
            onChange={(e) => set("alert_note", e.target.value)}
            disabled={busy}
            placeholder={t("executionEdit.alertNotePlaceholder")}
            data-testid="execution-edit-alertnote"
          />
        </FieldRow>

        {error && (
          <div
            className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
            data-testid="execution-edit-error"
          >
            {error}
          </div>
        )}

        <div className="flex items-center justify-between gap-2 border-t border-border pt-3 text-xs text-muted-foreground">
          <span data-testid="execution-edit-diff">
            {changed
              ? t("editCommon.diffSummary", { count: changed, fields: Object.keys(diff).join(" / ") })
              : t("editCommon.noDiff")}
          </span>
          <span>{t("executionEdit.footerHint")}</span>
        </div>
      </form>
    </div>
  );
}

function FieldRow({
  label, hint, children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  const cid = React.useId();
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <Label htmlFor={cid} className="text-sm font-medium">{label}</Label>
      {children}
      {hint && <span className="text-[11px] leading-snug text-muted-foreground">{hint}</span>}
    </div>
  );
}

function ReadOnly({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 items-baseline gap-2">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 break-all font-medium">{value || "—"}</span>
    </div>
  );
}
