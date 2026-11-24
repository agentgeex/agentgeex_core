/**
 * components/agentgeex/project-edit-dialog.tsx
 * ────────────────────────────────────────────────────────────────
 * [2026-10-26 Feng] "The project list needs an edit, and the edit must call the MCP API"
 *
 * Conventions:
 *   - Writes go **only** through `PATCH /v1/projects/{id}` → BFF → MCP `agx_update_project`
 *     (the core is the only authoritative write entry: field grading / ownership checks live in the core; the
 *     frontend does not copy the rules)
 *   - Submit **only changed fields** (diff) → form empty values never overwrite existing DB content
 *   - Permission denied (403) shows the reason the core gives, as-is: no swallowing, no downgrading
 *
 * Acting identity (caller_agent_id):
 *   `agx_update_project` requires L5 → only project_manager / workshop_manager qualify
 *   (see agx_mcp/permissions.py ROLE_ALLOWED_LEVELS). So the dropdown lists only those two,
 *   defaulting to the project's admin_agent_id — otherwise the user's first save would surely get a 403.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select";
import { useToast } from "@/components/ui/toast";
import { projectsApi, type ProjectDetail, type ProjectUpdateInput } from "@/providers/api";
import { listAgentOptions, agentOptionText } from "@/providers/agentgeex";
import { apiErrorText } from "@/lib/api-error";

/** L5 roles (can edit projects) — aligned with the core's ROLE_ALLOWED_LEVELS, no guessing */
const L5_ROLES = new Set(["project_manager", "workshop_manager"]);

interface FormState {
  project_name: string;
  project_abbr: string;
  project_logo: string;
  source_dir: string;
  x_desc: string;
}

const EMPTY: FormState = {
  project_name: "",
  project_abbr: "",
  project_logo: "",
  source_dir: "",
  x_desc: "",
};

export function ProjectEditDialog({
  project,
  open,
  onOpenChange,
  onSaved,
}: {
  project: ProjectDetail;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  /** Success callback (the parent uses it to re-fetch the snapshot/detail) */
  onSaved?: (fields: string[]) => void;
}) {
  const { t } = useTranslation();
  const { toast } = useToast();
  const [form, setForm] = React.useState<FormState>(EMPTY);
  const [caller, setCaller] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");

  // Candidate acting identities: only L5 roles; project managers preferred
  const callers = React.useMemo(
    /* [2026-11-07] All candidates (incl. deactivated), narrowed only by role — a deactivated PM must still be selectable back.
       Templates are still excluded: a template is not an assignable entity, choosing it as PM only corrupts the project config. */
    () =>
      listAgentOptions().filter(
        (a) =>
          L5_ROLES.has(a.role) &&
          !(a.isTemplate || a.kind === "template" || a.deployStatus === "template"),
      ),
    [],
  );

  // Fill in current values on open (reset every time, so nothing from the last edit lingers)
  React.useEffect(() => {
    if (!open) return;
    setForm({
      project_name: project.name ?? "",
      project_abbr: project.abbr ?? "",
      project_logo: project.logoUrl ?? "",
      source_dir: project.sourceDir ?? "",
      x_desc: project.xDesc ?? "",
    });
    const preferred = callers.find((a) => a.id === project.adminAgentId) ?? callers[0];
    setCaller(preferred?.id ?? "");
    setError("");
  }, [open, project, callers]);

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((f) => ({ ...f, [k]: v }));

  /** Only changed fields — do not use form values to overwrite untouched DB content */
  const diff = React.useMemo<ProjectUpdateInput>(() => {
    const out: ProjectUpdateInput = {};
    if (form.project_name !== (project.name ?? "")) out.project_name = form.project_name.trim();
    if (form.project_abbr !== (project.abbr ?? "")) out.project_abbr = form.project_abbr.trim();
    if (form.project_logo !== (project.logoUrl ?? "")) out.project_logo = form.project_logo.trim();
    if (form.source_dir !== (project.sourceDir ?? "")) out.source_dir = form.source_dir.trim();
    if (form.x_desc !== (project.xDesc ?? "")) out.x_desc = form.x_desc;
    return out;
  }, [form, project]);

  const changed = Object.keys(diff).length;
  const nameEmpty = !form.project_name.trim();

  const submit = async () => {
    if (nameEmpty) {
      setError(t("projectEdit.errNameEmpty"));
      return;
    }
    if (!changed) {
      setError(t("projectEdit.errNoChanges"));
      return;
    }
    if (!caller) {
      setError(t("projectEdit.errActorMissing"));
      return;
    }
    setBusy(true);
    setError("");
    try {
      const res = await projectsApi.update(project.id, { ...diff, caller_agent_id: caller });
      toast({
        title: t("projectEdit.updated"),
        description: `${project.id} · ${res.fields.join(" / ")}`,
        variant: "success",
      });
      onSaved?.(res.fields);
      onOpenChange(false);
    } catch (e) {
      const err = e as { status?: number; code?: string; message?: string };
      // Show the core's rejection reason as-is (403=permission/ownership, 400=argument)
      setError(apiErrorText(e, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !busy && onOpenChange(v)}>
      <DialogContent className="max-w-3xl" data-testid="project-edit-dialog">
        <DialogHeader>
          <DialogTitle>{t("projectEdit.title")}</DialogTitle>
        </DialogHeader>

        <div className="flex min-w-0 flex-col gap-3 py-1">
          <div className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
            <span className="font-mono">{project.id}</span>
            <span>·</span>
            <span>{t("projectEdit.kernelHint")}</span>
          </div>

          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <FieldRow label={t("projectEdit.fName")} required>
              <Input
                value={form.project_name}
                onChange={(e) => set("project_name", e.target.value)}
                disabled={busy}
                data-testid="project-edit-name"
              />
            </FieldRow>
            <FieldRow label={t("projectEdit.fAbbr")} hint={t("projectEdit.fAbbrHint")}>
              <Input
                value={form.project_abbr}
                onChange={(e) => set("project_abbr", e.target.value)}
                disabled={busy}
                className="font-mono"
                data-testid="project-edit-abbr"
              />
            </FieldRow>
            <FieldRow label={t("projectEdit.fLogo")} hint={t("projectEdit.fLogoHint")}>
              <Input
                value={form.project_logo}
                onChange={(e) => set("project_logo", e.target.value)}
                disabled={busy}
                className="font-mono text-xs"
                data-testid="project-edit-logo"
              />
            </FieldRow>
            <FieldRow label={t("projectEdit.fSourceDir")}>
              <Input
                value={form.source_dir}
                onChange={(e) => set("source_dir", e.target.value)}
                disabled={busy}
                className="font-mono text-xs"
                data-testid="project-edit-srcdir"
              />
            </FieldRow>
          </div>

          <FieldRow label={t("projectEdit.fXdesc")} hint={t("projectEdit.fXdescHint")}>
            <Textarea
              value={form.x_desc}
              onChange={(e) => set("x_desc", e.target.value)}
              disabled={busy}
              rows={10}
              className="min-h-[220px] font-mono text-xs leading-relaxed"
              data-testid="project-edit-xdesc"
            />
          </FieldRow>

          <FieldRow label={t("projectEdit.fActor")} hint={t("projectEdit.fActorHint")}>
            <Select value={caller} onValueChange={setCaller} disabled={busy}>
              <SelectTrigger className="h-9" data-testid="project-edit-caller">
                <SelectValue placeholder={t("projectEdit.actorPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                {callers.map((a) => (
                  <SelectItem key={a.id} value={a.id} data-testid={`project-edit-caller-opt-${a.id}`}>
                    {agentOptionText(a, { withRole: true })}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FieldRow>

          {error && (
            <div
              className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
              data-testid="project-edit-error"
            >
              {error}
            </div>
          )}
        </div>

        <DialogFooter className="items-center justify-between gap-2 sm:justify-between">
          <span className="text-xs text-muted-foreground" data-testid="project-edit-diff">
            {changed
              ? t("editCommon.diffSummary", { count: changed, fields: Object.keys(diff).join(" / ") })
              : t("editCommon.noDiff")}
          </span>
          <div className="flex items-center gap-2">
            <Button variant="secondary" onClick={() => onOpenChange(false)} disabled={busy}>
              {t("projectEdit.cancel")}
            </Button>
            <Button onClick={submit} disabled={busy || !changed || nameEmpty} data-testid="project-edit-save">
              {busy && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
              {t("projectEdit.save")}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Label on top, control below (same form convention as the send-message dialog) */
function FieldRow({
  label, hint, required, children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  const id = React.useId();
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <Label htmlFor={id} className="flex items-center gap-0.5 text-sm font-medium">
        {required && <span className="text-destructive">*</span>}
        {label}
      </Label>
      {children}
      {hint && <span className="text-[11px] leading-snug text-muted-foreground">{hint}</span>}
    </div>
  );
}
