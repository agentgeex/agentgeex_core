/**
 * Quick command · create/edit form (standalone dialog)
 *
 * [2026-11-14 Feng] His words:
 *   "Just add a button here... an add button that goes straight to adding... this button is always there,
 *     just pop that quick command open and let me add it myself"
 *   "Jumping over to that side (the quick command page) and opening it there is too slow"
 *
 * Why extract it: the "frequently used phrases" in the writing window must be able to **create a phrase in
 * place**; you cannot make people close the window, jump page, create, then find their way back -- that is
 * three round trips.
 * The form itself is the **same one** as the management page (not a copy), otherwise validation rules will
 * diverge sooner or later.
 *
 * `defaults`: used for prefilling in the create-in-place case (we already know this is a text snippet and
 * which Agent it belongs to, no need to make people choose again).
 */
import * as React from "react";
import { Save, Loader2, Plus, Trash2 } from "lucide-react";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FormRow } from "@/components/ui/form-field";
import { Switch } from "@/components/ui/switch";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  createQuickCommand, updateQuickCommand,
  listAgentOptions,
  type QuickCmd, type QuickCmdParam,
} from "@/providers/agentgeex";
import { AgentOptionLabel } from "@/components/agentgeex/agent-option";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";

/** Category -> locale key (the vocabulary lives in `quickCmdForm.category`, shared by the management filter and the badge)
 *  ★ Keys only, no Chinese: hooks are unavailable at module load. Always resolve through `categoryLabel(c, t)`. */
export const CATEGORY_KEY: Record<string, string> = {
  network: "quickCmdForm.category.network",
  system: "quickCmdForm.category.system",
  notice: "quickCmdForm.category.notice",
  agent: "quickCmdForm.category.agent",
  host: "quickCmdForm.category.host",
  project: "quickCmdForm.category.project",
  log: "quickCmdForm.category.log",
  config: "quickCmdForm.category.config",
  /* [2026-11-14 Feng] Add dev (dev frequently used phrases). Without adding it to the table it renders as a bare key "dev". */
  dev: "quickCmdForm.category.dev",
  other: "quickCmdForm.category.other",
};

/** Category name (unknown category shown as-is; empty falls to "other") */
export function categoryLabel(c: string, t: (k: string) => string): string {
  const key = CATEGORY_KEY[c];
  if (key) return t(key);
  return c || t("quickCmdForm.category.other");
}

export interface FormState {
  cmdId: string;
  title: string;
  /** [2026-10-26] 0=private (owning Agent required); 1=public (owning Agent hidden, ownerAgent not submitted). */
  isPublic: number;
  ownerAgent: string;
  targetTask: string;
  category: string;
  semanticSkeleton: string;
  composedInstruction: string;
  notes: string;
  /** Param rows (simple editing: name/label/required/type/default) */
  params: QuickCmdParam[];
  /** [2026-11-14 Feng] task=dispatch a task / text=text snippet */
  sendMode: "task" | "text";
  sortOrder: number;
}

export const EMPTY_FORM: FormState = {
  cmdId: "", title: "", isPublic: 0, ownerAgent: "", targetTask: "", category: "other",
  semanticSkeleton: "", composedInstruction: "", notes: "",
  params: [],
  sendMode: "task",
  sortOrder: 100,
};

export function toForm(cmd: QuickCmd): FormState {
  return {
    cmdId: cmd.cmdId,
    title: cmd.title,
    isPublic: cmd.isPublic ? 1 : 0,
    ownerAgent: cmd.ownerAgent,
    targetTask: cmd.targetTask,
    category: cmd.category || "other",
    semanticSkeleton: cmd.semanticSkeleton || "",
    composedInstruction: cmd.composedInstruction || "",
    notes: cmd.notes || "",
    params: cmd.params || [],
    sendMode: cmd.sendMode === "text" ? "text" : "task",
    sortOrder: cmd.sortOrder ?? 100,
  };
}

export function toUpsert(f: FormState) {
  return {
    cmdId: f.cmdId || undefined,
    title: f.title,
    // Public: not bound to an Agent (backend requires ownerAgent empty/omitted); when is_public=1 the DB also requires target_agent_id IS NULL
    isPublic: f.isPublic ? 1 : 0,
    ownerAgent: f.isPublic ? "" : f.ownerAgent.trim(),
    targetTask: f.sendMode === "text" ? "" : f.targetTask,
    semanticSkeleton: f.semanticSkeleton,
    composedInstruction: f.composedInstruction,
    notes: f.notes,
    category: f.category,
    params: f.sendMode === "text" ? [] : f.params,
    exampleFilled: "",
    callerRoleLimit: { roles: [], enforce: "backend", note: "" },
    sendMode: f.sendMode,
    sortOrder: f.sortOrder,
    activeStatus: 1,
  };
}

export function QuickCmdForm({
  open, initial, defaults, onOpenChange, onSaved,
}: {
  open: boolean;
  initial: QuickCmd | null;
  /** [2026-11-14 Feng] Prefill for create-in-place (e.g. entered from "frequently used phrases" = text + known owner).
   *  Applies only to "create"; editing always trusts initial (prefill must not override DB facts). */
  defaults?: Partial<FormState>;
  onOpenChange: (o: boolean) => void;
  /** Save success callback: carries the row just saved (needed immediately after create-in-place) */
  onSaved: (saved?: QuickCmd) => void;
}) {
  const [form, setForm] = React.useState<FormState>(EMPTY_FORM);
  const [saving, setSaving] = React.useState(false);
  const [err, setErr] = React.useState("");

  React.useEffect(() => {
    if (open) {
      setForm(initial ? toForm(initial) : { ...EMPTY_FORM, ...(defaults || {}) });
      setErr("");
      setSaving(false);
    }
  }, [open, initial]);

  const isEdit = !!initial;
  const isPublic = form.isPublic === 1;
  /* [2026-11-14 23:30 Feng] Owner Agent becomes a dropdown: candidates are all agents (including offline/template),
     historical commands owned by an offline Agent must still be editable. */
  const agents = React.useMemo(() => listAgentOptions(), []);
  const ownerInList = agents.some((a) => a.id === form.ownerAgent);

  /**
   * [2026-10-26 public/private] Toggling the switch:
   *   public -> hide "owner Agent" and make it optional (submit ownerAgent as an empty string);
   *   public -> private -> clear the owner Agent and let the user re-choose,
   *   avoiding submitting the cleared empty value (rejected by backend VALIDATION / DB CHECK).
   */
  function setVisibility(next: number) {
    setForm((s) => {
      if ((s.isPublic ? 1 : 0) === next) return s;
      if (next === 0) return { ...s, isPublic: 0, ownerAgent: "" };
      return { ...s, isPublic: 1 };
    });
    setErr("");
  }

  const { t } = useTranslation();

  async function save() {
    if (saving) return;
    setErr("");
    const isText = form.sendMode === "text";
    const missingOwner = !isPublic && !form.ownerAgent.trim();
    if (
      !form.title.trim() || missingOwner ||
      (!isText && !form.targetTask.trim()) ||
      !form.composedInstruction.trim()
    ) {
      setErr(
        isText
          ? isPublic
            ? t("quickCmdForm.error.requiredTextPublic")
            : t("quickCmdForm.error.requiredTextPrivate")
          : isPublic
            ? t("quickCmdForm.error.requiredTaskPublic")
            : t("quickCmdForm.error.requiredTaskPrivate"),
      );
      return;
    }
    try {
      setSaving(true);
      const saved = isEdit
        ? await updateQuickCommand(initial!.cmdId, toUpsert(form))
        : await createQuickCommand(toUpsert(form));
      onSaved(saved);
      onOpenChange(false);
    } catch (e) {
      const x = e as { message?: string };
      setErr(x.message || t("quickCmdForm.error.saveFailed"));
    } finally {
      setSaving(false);
    }
  }

  function setParam(i: number, patch: Partial<QuickCmdParam>) {
    setForm((s) => ({ ...s, params: s.params.map((p, idx) => (idx === i ? { ...p, ...patch } : p)) }));
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !saving && onOpenChange(o)}>
      {/* [2026-11-14 23:30 Feng] "Make this box a bit longer" -- widen the form itself one step too (3xl->4xl) */}
      <DialogContent className="max-w-4xl max-h-[92vh] overflow-y-auto" data-testid="quick-cmd-form">
        <DialogHeader>
          <DialogTitle>{isEdit ? t("quickCmdForm.title.edit") : t("quickCmdForm.title.new")}</DialogTitle>
          <DialogDescription>
            {form.sendMode === "text" ? t("quickCmdForm.desc.text") : t("quickCmdForm.desc.task")}
          </DialogDescription>

          {/* [2026-11-14 Feng] Two kinds of command: this switch decides the shape of the whole form below.
              Choose the type first, then fill in content -- different types have completely different
              required fields and interactions. */}
          <FormRow label={t("quickCmdForm.field.type")} required hint={t("quickCmdForm.field.typeHint")}>
            <div className="flex flex-wrap items-center gap-2" data-testid="quick-cmd-sendmode">
              {([
                {
                  v: "task",
                  label: t("quickCmdForm.sendMode.task.label"),
                  desc: t("quickCmdForm.sendMode.task.desc"),
                },
                {
                  v: "text",
                  label: t("quickCmdForm.sendMode.text.label"),
                  desc: t("quickCmdForm.sendMode.text.desc"),
                },
              ] as const).map((o) => (
                <button
                  key={o.v}
                  type="button"
                  onClick={() => setForm((s) => ({
                    ...s,
                    sendMode: o.v,
                    /* Switch to text: clear the task template and params (meaningless for text, they would only be cleared by the
                        backend anyway);
                       switch back to task: leave existing content alone, otherwise one misclick deletes the
                       params already filled in. */
                    ...(o.v === "text" ? { targetTask: "", params: [] } : {}),
                  }))}
                  className={cn(
                    "flex flex-col items-start rounded-md border px-3 py-1.5 text-left text-xs transition-colors",
                    form.sendMode === o.v
                      ? "border-primary bg-primary/10 text-foreground"
                      : "border-border text-muted-foreground hover:bg-muted",
                  )}
                  data-testid={`quick-cmd-sendmode-${o.v}`}
                  data-active={form.sendMode === o.v ? "1" : "0"}
                >
                  <span className="font-medium">{o.label}</span>
                  <span className="text-[11px] text-muted-foreground">{o.desc}</span>
                </button>
              ))}
            </div>
          </FormRow>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-2 gap-3">
            <FormRow label={t("quickCmdForm.field.title")} required>
              <Input value={form.title} onChange={(e) => setForm((s) => ({ ...s, title: e.target.value }))} placeholder={t("quickCmdForm.field.titlePh")} data-testid="quick-cmd-title-input" />
            </FormRow>
            <FormRow
              label={t("quickCmdForm.field.cmdId")}
              hint={isEdit ? t("quickCmdForm.field.cmdIdHintEdit") : t("quickCmdForm.field.cmdIdHintNew")}
            >
              <Input value={form.cmdId} disabled={isEdit} onChange={(e) => setForm((s) => ({ ...s, cmdId: e.target.value }))} placeholder={t("quickCmdForm.field.cmdIdPh")} data-testid="quick-cmd-id-input" />
            </FormRow>
          </div>

          {/* [2026-10-26] Public/private switch: private by default (same as the DB default).
              [2026-11-04 Feng] "Visibility" used to be a full-width bordered color band, clashing with the
              "title + control" sections above (title / owner Agent / semantic skeleton).
              -> Changed to FormRow as well (title on the left, control on the right); a consistent structure
              aligns naturally.
              ★ Interaction (three-state Switch) and both explanatory texts stay **unchanged**, only the
              presentation changes. */}
          <FormRow label={t("quickCmdForm.field.visibility")} data-testid="quick-cmd-visibility-row">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
              <label className="inline-flex items-center gap-2 text-sm">
                <Switch
                  checked={isPublic}
                  onCheckedChange={(v: boolean) => setVisibility(v ? 1 : 0)}
                  data-testid="quick-cmd-visibility-switch"
                />
                <span className={isPublic ? "font-medium text-foreground" : "text-muted-foreground"}>
                  {isPublic ? t("quickCmdForm.field.public") : t("quickCmdForm.field.private")}
                </span>
              </label>
              <span className="text-[11px] leading-snug text-muted-foreground">
                {isPublic ? t("quickCmdForm.field.publicHint") : t("quickCmdForm.field.privateHint")}
              </span>
            </div>
          </FormRow>

          {/* [2026-11-14 23:30 Feng] Widen all three fields:
              "Make the owner Agent a dropdown" "Make this box a bit longer, it is too short" "Make the
              category a bit longer too"
              Previously three equal columns, each left with only tens of pixels after the 8em label column --
              both Agent names and category names were hard-truncated.
              Now: owner Agent / target task in two columns (owner wider), category on its own row with full width. */}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
            {/* Public -> the whole "owner Agent" item is hidden (not grayed out), and skipped in required validation */}
            {!isPublic && (
              <FormRow label={t("quickCmdForm.field.owner")} required>
                {/* 2026-11-14: typing agent_id by hand becomes a dropdown -- ownership is **selected**, not memorized.
                    Candidates = all Agents (including offline/template): when editing a historical command,
                    its owning Agent may have gone offline long ago; if it is not in the dropdown you cannot edit. */}
                <Select
                  value={form.ownerAgent || undefined}
                  onValueChange={(v) => setForm((s) => ({ ...s, ownerAgent: v }))}
                >
                  <SelectTrigger
                    className="w-full min-w-0 [&>span]:max-w-full [&>span]:truncate"
                    title={form.ownerAgent || undefined}
                    data-testid="quick-cmd-owner-select"
                  >
                    <SelectValue placeholder={t("quickCmdForm.field.ownerPh")} />
                  </SelectTrigger>
                  <SelectContent className="max-h-[22rem] max-w-[var(--radix-popper-available-width)]">
                    {/* Fallback: when the stored owner is not among the candidates (deleted/renamed) its current value must still
                        be visible, otherwise the dropdown shows blank and one save silently drops the owner. */}
                    {!!form.ownerAgent && !ownerInList && (
                      <SelectItem value={form.ownerAgent}>
                        {form.ownerAgent}
                        {t("quickCmdForm.field.ownerMissing")}
                      </SelectItem>
                    )}
                    {agents.map((a) => (
                      <SelectItem key={a.id} value={a.id}>
                        <AgentOptionLabel agent={a} />
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </FormRow>
            )}
            {/* [2026-11-14] Text snippets have no task template: hide the whole item (not gray it out), and it does not take part in required validation */}
            {form.sendMode !== "text" && (
              <FormRow label={t("quickCmdForm.field.targetTask")} required>
                <Input value={form.targetTask} onChange={(e) => setForm((s) => ({ ...s, targetTask: e.target.value }))} placeholder={t("quickCmdForm.field.targetTaskPh")} data-testid="quick-cmd-task-input" />
              </FormRow>
            )}
          </div>

          <FormRow label={t("quickCmdForm.field.category")}>
            <Select value={form.category} onValueChange={(v) => setForm((s) => ({ ...s, category: v }))}>
              {/* Category names are at most 4 characters ("dev"/"notice"), but the cell was squeezed to tens of pixels, so
                  even 2 characters did not fit. Give it a stable width, no longer squeezed by sibling fields. */}
              <SelectTrigger className="w-[14rem]" data-testid="quick-cmd-category-select">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.keys(CATEGORY_KEY).map((k) => (
                  <SelectItem key={k} value={k}>
                    {categoryLabel(k, t)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FormRow>

          <FormRow label={t("quickCmdForm.field.skeleton")}>
            <Input value={form.semanticSkeleton} onChange={(e) => setForm((s) => ({ ...s, semanticSkeleton: e.target.value }))} placeholder={t("quickCmdForm.field.skeletonPh")} />
          </FormRow>

          <FormRow
            label={
              form.sendMode === "text"
                ? t("quickCmdForm.field.templateText")
                : t("quickCmdForm.field.templateTask")
            }
            required
            hint={
              form.sendMode === "text"
                ? t("quickCmdForm.field.templateHintText")
                : t("quickCmdForm.field.templateHintTask")
            }
          >
            <Textarea
              value={form.composedInstruction}
              onChange={(e) => setForm((s) => ({ ...s, composedInstruction: e.target.value }))}
              /* [2026-11-14 23:35 Feng] Placeholders must be typed:
                 His words: "If you choose a text snippet, then inside this text there is a default
                            'please do action X on bracket t', that wording should not be there"
                 `{target}/{action}` is a placeholder semantic that **dispatching a task** alone has;
                 showing it in a text snippet induces people to insert placeholders into plain prose. */
              placeholder={
                form.sendMode === "text"
                  ? t("quickCmdForm.field.templatePhText")
                  : t("quickCmdForm.field.templatePhTask")
              }
              data-testid="quick-cmd-template-input"
              className="min-h-[80px] font-mono text-xs"
            />
          </FormRow>

          <FormRow label={t("quickCmdForm.field.notes")}>
            <Input
              value={form.notes}
              onChange={(e) => setForm((s) => ({ ...s, notes: e.target.value }))}
              placeholder={t("quickCmdForm.field.notesPh")}
            />
          </FormRow>

          {/* Param schema (simple editing) -- only the dispatch-task type has params */}
          {form.sendMode !== "text" && (
          <div className="flex flex-col gap-2 rounded-md border p-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-muted-foreground">
                {t("quickCmdForm.params.title", { n: form.params.length })}
              </span>
              <Button
                type="button" size="sm" variant="outline"
                onClick={() => setForm((s) => ({ ...s, params: [...s.params, { name: "", label: "", required: false, type: "text", placeholder: "", default: "", options: [], source: "" }] }))}
              >
                <Plus className="mr-1 h-3.5 w-3.5" />
                {t("quickCmdForm.params.add")}
              </Button>
            </div>
            {form.params.length === 0 && <p className="text-xs text-muted-foreground">{t("quickCmdForm.params.empty")}</p>}
            {form.params.map((p, i) => (
              <div key={i} className="grid grid-cols-[1fr_1fr_100px_80px_32px] items-center gap-2">
                <Input value={p.name} onChange={(e) => setParam(i, { name: e.target.value })} placeholder="name" className="font-mono text-xs" />
                <Input value={p.label} onChange={(e) => setParam(i, { label: e.target.value })} placeholder={t("quickCmdForm.params.labelPh")} />
                <Select value={p.type} onValueChange={(v) => setParam(i, { type: v as QuickCmdParam["type"] })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {["text", "textarea", "enum", "number", "bool", "date", "datetime"].map((t) => (
                      <SelectItem key={t} value={t}>{t}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <label className="inline-flex items-center gap-1 text-xs">
                  <input type="checkbox" checked={p.required} onChange={(e) => setParam(i, { required: e.target.checked })} />
                  {t("quickCmdForm.params.required")}
                </label>
                <Button type="button" size="icon" variant="ghost" className="h-7 w-7" onClick={() => setForm((s) => ({ ...s, params: s.params.filter((_, idx) => idx !== i) }))}>
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            ))}
          </div>
          )}
        </div>

        {err && <p className="text-xs text-destructive">{err}</p>}

        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)} disabled={saving}>
            {t("quickCmdForm.cancel")}
          </Button>
          <Button onClick={save} disabled={saving} data-testid="quick-cmd-save">
            {saving ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Save className="mr-1 h-4 w-4" />}
            {isEdit ? t("quickCmdForm.save") : t("quickCmdForm.create")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
