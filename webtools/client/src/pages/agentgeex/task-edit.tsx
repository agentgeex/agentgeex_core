/**
 * pages/agentgeex/task-edit.tsx — edit a task template (page, not a dialog)
 * ────────────────────────────────────────────────────────────────
 * [2026-11-01 Feng] "I told you the detail is not needed, because clicking this is the detail,
 *   if not it should be edit, there is an edit… you have not built the edit, all of these are detail.
 *   Tasks need an edit, drop the detail button, on the task page."
 *
 * Two decisions, with the reasoning:
 *
 * ① No dialog, make it a /tasks/:id/edit page window —
 *    [2026-10-30 Feng] already settled this for host editing: "no dialogs, open the window
 *    under the current navigation item". In one console, half the edits in dialogs and half on pages is messier than before.
 *
 * ② The spec (x_desc) is the main dish here and takes the full width —
 *    the real content of a task template is that spec (it is what the agent reads when executing),
 *    priority / private / enabled are three lines of metadata and should not compete for space.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Loader2, SearchX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EditPageHeader } from "@/components/agentgeex/edit-page-header";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/toast";
import { useDetailCrumb } from "@/lib/breadcrumb";
import { refreshPageData, updateTask } from "@/providers/agentgeex";
import { tasksApi, type TaskDetail, type TaskUpdateInput } from "@/providers/api";

const EDITOR_AGENT_ID = "agx_core";

interface FormState {
  x_desc: string;
  priority: string;
  target_object: string;
  payload: string;
  is_private: 0 | 1;
  active_status: 0 | 1;
}

/** payload may be an object, array or string: normalize into editable JSON text */
function payloadToText(v: unknown): string {
  if (v === null || v === undefined || v === "") return "";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

export default function TaskEditPage() {
  const { t } = useTranslation();
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { toast } = useToast();

  const [detail, setDetail] = React.useState<TaskDetail | null>(null);
  const [loadError, setLoadError] = React.useState("");
  const [loading, setLoading] = React.useState(true);
  const [form, setForm] = React.useState<FormState | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");

  useDetailCrumb(detail?.name);

  // The list row snapshot has no x_desc / payload, so editing must fetch the detail separately (the BFF splits the endpoints accordingly, see the task_detail comment)
  React.useEffect(() => {
    let alive = true;
    setLoading(true);
    setLoadError("");
    tasksApi
      .detail(id)
      .then((d) => {
        if (!alive) return;
        setDetail(d);
        setForm({
          x_desc: d.xDesc ?? "",
          priority: String(d.priority ?? ""),
          target_object: d.targetObject ?? "",
          payload: payloadToText(d.payload),
          is_private: (d.isPrivate === 1 ? 1 : 0) as 0 | 1,
          active_status: (d.activeStatus === 0 ? 0 : 1) as 0 | 1,
        });
      })
      .catch((e) => {
        if (alive) setLoadError((e as { message?: string })?.message || String(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [id]);

  const goBack = () => navigate("/tasks");

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((f) => (f ? { ...f, [k]: v } : f));

  const diff: TaskUpdateInput = (() => {
    if (!form || !detail) return {};
    const out: TaskUpdateInput = {};
    if (form.x_desc !== (detail.xDesc ?? "")) out.x_desc = form.x_desc;
    if (form.target_object !== (detail.targetObject ?? "")) out.target_object = form.target_object.trim();
    if (form.payload !== payloadToText(detail.payload)) out.payload = form.payload;
    if (form.priority !== String(detail.priority ?? "")) out.priority = Number(form.priority);
    if (form.is_private !== ((detail.isPrivate === 1 ? 1 : 0) as 0 | 1)) out.is_private = form.is_private;
    if (form.active_status !== ((detail.activeStatus === 0 ? 0 : 1) as 0 | 1)) {
      out.active_status = form.active_status;
    }
    return out;
  })();

  const changed = Object.keys(diff).length;

  /**
   * The payload must be a JSON object.
   * Catch it before submitting: discovering "one extra comma" only when the kernel errors out happens after the user has left the page.
   */
  const payloadError = (() => {
    if (!form || !("payload" in diff)) return "";
    /* ★ The local variable must not be called t — it would shadow the translation function (t is a reserved name in i18n-ready files) */
    const raw = form.payload.trim();
    if (!raw) return "";
    try {
      const v = JSON.parse(raw);
      if (typeof v !== "object" || v === null || Array.isArray(v)) {
        return t("taskEdit.errPayloadNotObject");
      }
      return "";
    } catch (e) {
      return t("taskEdit.errPayloadBadJson", { error: (e as Error).message });
    }
  })();

  const priorityError =
    "priority" in diff && !Number.isFinite(diff.priority) ? t("taskEdit.errPriorityNumber") : "";

  const submit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (payloadError) return setError(payloadError);
    if (priorityError) return setError(priorityError);
    if (!changed) return setError(t("editCommon.noChanges"));
    setBusy(true);
    setError("");
    try {
      const res = await updateTask(id, diff);
      toast({
        title: t("taskEdit.updated"),
        description: `${detail?.name || id} · ${(res.fields || []).join(" / ")}`,
        variant: "success",
      });
      void refreshPageData();
      navigate("/tasks");
    } catch (err) {
      const e2 = err as { message?: string };
      setError(e2.message || String(err));
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-strong bg-card p-8 text-sm text-muted-foreground" data-testid="task-edit-loading">
        <Loader2 className="h-4 w-4 animate-spin" /> {t("taskEdit.loading")}
      </div>
    );
  }

  if (loadError || !detail || !form) {
    return (
      <div className="flex flex-col" data-testid="task-edit-missing">
        <div className="flex flex-col items-center gap-3 rounded-xl border border-strong bg-card p-12 text-center shadow-sm">
          <SearchX className="h-8 w-8 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            {loadError
              ? t("taskEdit.notFoundWithError", { id: id || t("editCommon.emptyId"), error: loadError })
              : t("taskEdit.notFoundGone", { id: id || t("editCommon.emptyId") })}
          </p>
          <Button variant="back" size="sm" onClick={goBack}>
            <ArrowLeft className="mr-1 h-4 w-4" />{t("editCommon.backToList")}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col" data-testid="page-task-edit">
      <form
        onSubmit={submit}
        className="flex flex-col gap-4 rounded-xl border border-strong bg-card p-4 shadow-sm"
        data-testid="content-frame"
      >
        <EditPageHeader
          title={t("taskEdit.title")}
          subtitle={<>{detail.name} · {detail.id}</>}
          subtitleClassName="min-w-0 break-all font-mono text-xs text-muted-foreground"
          onBack={goBack}
          backTestId="task-edit-back"
          saveTestId="task-edit-save"
          busy={busy}
          saveDisabled={!changed || !!payloadError || !!priorityError}
        />

        <div className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
          <span>{t("taskEdit.kernelHint")}</span>
          <span>·</span>
          <span>{t("editCommon.submitIdentity")} <span className="font-mono">{EDITOR_AGENT_ID}</span></span>
          <span>·</span>
          <span>{t("taskEdit.ownerAgent")} <span className="font-mono">{detail.agentName || detail.agentId}</span></span>
        </div>

        {/* Spec: the main dish of this page, full width */}
        <div className="flex min-w-0 flex-col gap-1">
          <Label htmlFor="task-xdesc" className="text-sm font-medium">
            {t("taskEdit.xdescLabel")}
          </Label>
          <Textarea
            id="task-xdesc"
            value={form.x_desc}
            onChange={(e) => set("x_desc", e.target.value)}
            disabled={busy}
            rows={18}
            className="font-mono text-xs leading-relaxed"
            data-testid="task-edit-xdesc"
          />
          <span className="text-[11px] text-muted-foreground">
            {t("taskEdit.xdescHint")}
          </span>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <FieldRow label={t("taskEdit.fPriority")} hint={t("taskEdit.fPriorityHint")}>
            <Input
              value={form.priority}
              onChange={(e) => set("priority", e.target.value)}
              disabled={busy}
              inputMode="numeric"
              className="font-mono text-xs"
              data-testid="task-edit-priority"
            />
          </FieldRow>
          <FieldRow label={t("taskEdit.fTarget")} hint={t("taskEdit.fTargetHint")}>
            <Input
              value={form.target_object}
              onChange={(e) => set("target_object", e.target.value)}
              disabled={busy}
              data-testid="task-edit-target"
            />
          </FieldRow>
          <FieldRow label={t("taskEdit.fVisibility")} hint={t("taskEdit.fVisibilityHint")}>
            <select
              value={form.is_private}
              onChange={(e) => set("is_private", (Number(e.target.value) === 1 ? 1 : 0) as 0 | 1)}
              disabled={busy}
              className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
              data-testid="task-edit-private"
            >
              <option value={0}>{t("taskEdit.visibilityShared")}</option>
              <option value={1}>{t("taskEdit.visibilityPrivate")}</option>
            </select>
          </FieldRow>
          <FieldRow label={t("taskEdit.fStatus")} hint={t("taskEdit.fStatusHint")}>
            <select
              value={form.active_status}
              onChange={(e) => set("active_status", (Number(e.target.value) === 0 ? 0 : 1) as 0 | 1)}
              disabled={busy}
              className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
              data-testid="task-edit-active"
            >
              <option value={1}>{t("taskEdit.statusEnabled")}</option>
              <option value={0}>{t("taskEdit.statusDisabled")}</option>
            </select>
          </FieldRow>
        </div>

        <div className="flex min-w-0 flex-col gap-1">
          <Label htmlFor="task-payload" className="text-sm font-medium">
            {t("taskEdit.payloadLabel")}
          </Label>
          <Textarea
            id="task-payload"
            value={form.payload}
            onChange={(e) => set("payload", e.target.value)}
            disabled={busy}
            rows={8}
            className="font-mono text-xs leading-relaxed"
            placeholder={t("taskEdit.payloadPlaceholder")}
            data-testid="task-edit-payload"
          />
          <span className={payloadError ? "text-[11px] text-destructive" : "text-[11px] text-muted-foreground"}>
            {payloadError || t("taskEdit.payloadHint")}
          </span>
        </div>

        {error && (
          <div
            className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
            data-testid="task-edit-error"
          >
            {error}
          </div>
        )}

        <div className="flex items-center justify-between gap-2 border-t border-border pt-3 text-xs text-muted-foreground">
          <span data-testid="task-edit-diff">
            {changed
              ? t("editCommon.diffSummary", { count: changed, fields: Object.keys(diff).join(" / ") })
              : t("editCommon.noDiff")}
          </span>
          <span>{t("editCommon.refreshAfterSave")}</span>
        </div>
      </form>
    </div>
  );
}

function FieldRow({
  label, hint, required, children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  const cid = React.useId();
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <Label htmlFor={cid} className="flex items-center gap-0.5 text-sm font-medium">
        {required && <span className="text-destructive">*</span>}
        {label}
      </Label>
      {children}
      {hint && <span className="text-[11px] leading-snug text-muted-foreground">{hint}</span>}
    </div>
  );
}
