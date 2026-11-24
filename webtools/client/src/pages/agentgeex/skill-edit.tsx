/**
 * pages/agentgeex/skill-edit.tsx — edit a shared skill (page, not a dialog)
 * ────────────────────────────────────────────────────────────────
 * [2026-11-01 Feng] "Drop the detail on the skill sharing side too, build an edit instead."
 *
 * Why not a dialog: consistent with tasks / executions / hosts — [2026-10-30 Feng] settled it:
 * "no dialogs, open the window under the current navigation item". A skill body often runs to thousands of words;
 * editing long text inside a dialog is the most painful interaction there is.
 *
 * Why the write path is agx_skill_contribute rather than raw SQL:
 * the skill library's signal fields (use_count / helpful_count), network ownership and the Redis cache
 * all live in the kernel. A direct UPDATE bypasses them: it looks like the edit succeeded while the cache stays stale.
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
import { listSkills, refreshPageData, updateSkill } from "@/providers/agentgeex";
import type { SkillRow } from "@/mock/agentgeex";
import type { SkillUpdateInput } from "@/providers/api";

const EDITOR_AGENT_ID = "agx_core";

interface FormState {
  skill_name: string;
  category: string;
  description: string;
  tags: string;
  content: string;
}

export default function SkillEditPage() {
  const { id = "" } = useParams<{ id: string }>();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { toast } = useToast();

  const row: SkillRow | undefined = React.useMemo(
    () => listSkills().find((s) => s.id === id),
    [id],
  );
  useDetailCrumb(row?.name);

  const [form, setForm] = React.useState<FormState | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");

  const baseline: FormState | null = React.useMemo(
    () =>
      row
        ? {
            skill_name: row.name ?? "",
            category: row.category ?? "",
            description: row.description ?? "",
            tags: row.tags ?? "",
            content: row.content ?? "",
          }
        : null,
    [row],
  );

  React.useEffect(() => {
    setForm(baseline);
    setError("");
  }, [baseline]);

  const goBack = () => navigate("/skills");

  if (!row || !form || !baseline) {
    return (
      <div className="flex flex-col" data-testid="skill-edit-missing">
        <div className="flex flex-col items-center gap-3 rounded-xl border border-strong bg-card p-12 text-center shadow-sm">
          <SearchX className="h-8 w-8 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            {t("skillEdit.notFound", { id: id || t("editCommon.emptyId") })}
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

  const diff: SkillUpdateInput = (() => {
    const out: SkillUpdateInput = {};
    if (form.skill_name !== baseline.skill_name) out.skill_name = form.skill_name.trim();
    if (form.category !== baseline.category) out.category = form.category.trim();
    if (form.description !== baseline.description) out.description = form.description.trim();
    if (form.tags !== baseline.tags) out.tags = form.tags.trim();
    if (form.content !== baseline.content) out.content = form.content;
    return out;
  })();

  const changed = Object.keys(diff).length;
  const nameEmpty = !form.skill_name.trim();
  const contentEmpty = !form.content.trim();

  const submit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (nameEmpty) return setError(t("skillEdit.errNameEmpty"));
    if (contentEmpty) return setError(t("skillEdit.errContentEmpty"));
    if (!changed) return setError(t("editCommon.noChanges"));
    setBusy(true);
    setError("");
    try {
      const res = await updateSkill(row.id, diff);
      toast({
        title: t("skillEdit.updated"),
        description: `${diff.skill_name || row.name} · ${(res.fields || []).join(" / ")}`,
        variant: "success",
      });
      void refreshPageData();
      navigate("/skills");
    } catch (err) {
      setError((err as { message?: string })?.message || String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col" data-testid="page-skill-edit">
      <form
        onSubmit={submit}
        className="flex flex-col gap-4 rounded-xl border border-strong bg-card p-4 shadow-sm"
        data-testid="content-frame"
      >
        <EditPageHeader
          title={t("skillEdit.title")}
          subtitle={<>{t("skillEdit.subtitle", { id: row.id, name: row.sourceAgentName || row.sourceAgentId || "—" })}</>}
          subtitleClassName="min-w-0 break-all font-mono text-xs text-muted-foreground"
          onBack={goBack}
          backTestId="skill-edit-back"
          saveTestId="skill-edit-save"
          busy={busy}
          saveDisabled={!changed || nameEmpty || contentEmpty}
        />

        <div className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
          <span>{t("skillEdit.kernelHint")}</span>
          <span>·</span>
          <span>{t("editCommon.submitIdentity")} <span className="font-mono">{EDITOR_AGENT_ID}</span></span>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <FieldRow label={t("skillEdit.fName")} required hint={t("skillEdit.fNameHint")}>
            <Input
              value={form.skill_name}
              onChange={(e) => set("skill_name", e.target.value)}
              disabled={busy}
              data-testid="skill-edit-name"
            />
          </FieldRow>
          <FieldRow label={t("skillEdit.fCategory")} hint={t("skillEdit.fCategoryHint")}>
            <Input
              value={form.category}
              onChange={(e) => set("category", e.target.value)}
              disabled={busy}
              className="font-mono text-xs"
              data-testid="skill-edit-category"
            />
          </FieldRow>
          <FieldRow label={t("skillEdit.fDesc")} hint={t("skillEdit.fDescHint")}>
            <Input
              value={form.description}
              onChange={(e) => set("description", e.target.value)}
              disabled={busy}
              data-testid="skill-edit-desc"
            />
          </FieldRow>
          <FieldRow label={t("skillEdit.fTags")} hint={t("skillEdit.fTagsHint")}>
            <Input
              value={form.tags}
              onChange={(e) => set("tags", e.target.value)}
              disabled={busy}
              data-testid="skill-edit-tags"
            />
          </FieldRow>
        </div>

        <div className="flex min-w-0 flex-col gap-1">
          <Label htmlFor="skill-content" className="text-sm font-medium">
            <span className="mr-1 text-destructive">*</span>{t("skillEdit.contentLabel")}
          </Label>
          <Textarea
            id="skill-content"
            value={form.content}
            onChange={(e) => set("content", e.target.value)}
            disabled={busy}
            rows={20}
            className="font-mono text-xs leading-relaxed"
            data-testid="skill-edit-content"
          />
          <span className="text-[11px] text-muted-foreground">
            {t("skillEdit.contentHint")}
          </span>
        </div>

        {error && (
          <div
            className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
            data-testid="skill-edit-error"
          >
            {error}
          </div>
        )}

        <div className="flex items-center justify-between gap-2 border-t border-border pt-3 text-xs text-muted-foreground">
          <span data-testid="skill-edit-diff">
            {changed
              ? t("editCommon.diffSummary", { count: changed, fields: Object.keys(diff).join(" / ") })
              : t("editCommon.noDiff")}
          </span>
          <span>{t("skillEdit.footerHint")}</span>
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
