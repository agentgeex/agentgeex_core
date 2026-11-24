/**
 * components/agentgeex/agent-edit-dialog.tsx
 * ────────────────────────────────────────────────────────────────
 * [2026-11-07 Feng]
 *   'the agent seems to be uneditable, but some data really should be editable.
 *     First build the entry, make every field editable for now; I will tell you later which ones cannot be.
 *     Editing must go through one MCP method.'
 *
 * ## Three conventions (all about 'do not outsmart yourself')
 *
 * (1) **Writes take exactly one path**: `PATCH /v1/agents/{id}` → BFF → MCP `agx_update_agent`.
 *     Never touch the DB directly. Field tiering (operational / activation / contract fields) and ownership checks live
 *     in the kernel; neither the frontend nor the BFF duplicates those rules — a copied rule sooner or later outranks the real one.
 *
 * (2) **Submit only changed fields (diff)**. Submitting the whole record looks convenient but actually writes
 *     'columns this form does not show' as empty values. `agx_update_agent` is 'change what you receive',
 *     so one extra empty string is one accidental delete.
 *
 * (3) **An empty sensitive field = do not change**. Token fields are never echoed by the detail API (and should not be),
 *     so the input is always empty. Treating 'empty is also submitted' means a user who only wanted to rename
 *     would erase two tokens along the way — the most typical kind of 'a failed save counted as success'.
 *
 * ## Why every field is exposed
 * Feng's words were 'make every field editable first; I will tell you later which ones cannot be'.
 * So nothing is subtracted here on your behalf: what may change is the kernel's answer. When the kernel refuses,
 * **show its exact words** ('needs L4 permission / can only change members of this project') instead of the frontend
 * guessing first and guessing wrong.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Loader2, ChevronDown, ChevronRight } from "lucide-react";
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
import { agentsApi, type AgentProfile } from "@/providers/api";
import { listHosts } from "@/providers/agentgeex";
import { OnlineDot } from "@/components/ui/online-dot";
import { AGENT_ROLES, roleTagLabel } from "@/lib/role-label";
import { apiErrorText } from "@/lib/api-error";

/* Role enum (AGENT_ROLES) — consistent with kernel agx_update_agent validation (the BFF validates the same copy).
   The vocabulary lives in @/lib/role-label; do not list it again here. */

interface FormState {
  agent_name: string;
  agent_role: string;
  host_id: string;
  active_status: string;
  x_desc: string;
  system_prompt: string;
  skills_allowlist: string;
  mcp_config: string;
  agent_email: string;
  agent_telegram_token: string;
  agent_feishu_token: string;
}

const toForm = (p: AgentProfile): FormState => ({
  agent_name: p.name || "",
  agent_role: p.role || "worker",
  host_id: p.hostId || "",
  active_status: p.activeStatus ? "1" : "0",
  x_desc: p.xDesc || "",
  system_prompt: p.systemPrompt || "",
  skills_allowlist: (p.skillsAllowlist || []).join("\n"),
  mcp_config: p.mcpConfig && Object.keys(p.mcpConfig).length ? JSON.stringify(p.mcpConfig, null, 2) : "",
  // The detail API does not echo these three — an empty string here means 'do not change', not 'clear'
  agent_email: "",
  agent_telegram_token: "",
  agent_feishu_token: "",
});

export function AgentEditDialog({
  profile,
  open,
  onOpenChange,
  onSaved,
}: {
  profile: AgentProfile;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSaved?: (fields: string[]) => void;
}) {
  const { t } = useTranslation();
  const { toast } = useToast();
  const [form, setForm] = React.useState<FormState>(() => toForm(profile));
  const [base, setBase] = React.useState<FormState>(() => toForm(profile));
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const [advanced, setAdvanced] = React.useState(false);

  // Reset from the latest profile on open: the detail page may have refreshed while the dialog was closed (status/deploy changed)
  React.useEffect(() => {
    if (open) {
      const f = toForm(profile);
      setForm(f);
      setBase(f);
      setError("");
      setAdvanced(false);
    }
  }, [open, profile]);

  const hosts = React.useMemo(() => listHosts(), []);
  const set = (k: keyof FormState, v: string) => setForm((s) => ({ ...s, [k]: v }));

  /** The diff actually to be sent (only changed keys) */
  const changes = React.useMemo(() => {
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(form) as (keyof FormState)[]) {
      if (form[k] === base[k]) continue;
      const v = form[k];
      // Token / email: empty means the user did not touch it → not submitted (empty = do not change)
      if (["agent_email", "agent_telegram_token", "agent_feishu_token"].includes(k) && !v.trim()) continue;
      out[k] = v;
    }
    return out;
  }, [form, base]);

  const dirty = Object.keys(changes).length > 0;

  const submit = async () => {
    setError("");
    if (!dirty) {
      toast({
        title: t("agentEditDialog.toast.noChangeTitle"),
        description: t("agentEditDialog.toast.noChangeDesc"),
      });
      return;
    }
    // JSON columns are parsed here: handing the backend a broken JSON string only earns a 422, with no clue where it broke
    const body: Record<string, unknown> = { ...changes };
    if (typeof body.skills_allowlist === "string") {
      const items = body.skills_allowlist.split(/[\n,]/).map((s) => s.trim()).filter(Boolean);
      body.skills_allowlist = items;
    }
    if (typeof body.mcp_config === "string") {
      const raw = body.mcp_config.trim();
      if (!raw) {
        body.mcp_config = {};
      } else {
        try {
          body.mcp_config = JSON.parse(raw);
        } catch {
          setError(t("agentEditDialog.error.invalidJson"));
          setAdvanced(true);
          return;
        }
      }
    }

    setBusy(true);
    try {
      const r = await agentsApi.update(profile.id, body);
      toast({
        title: t("agentEditDialog.toast.saved"),
        description: t("agentEditDialog.toast.savedDesc", {
          n: r.fields.length,
          fields: r.fields.join(" / "),
        }),
      });
      onSaved?.(r.fields);
      onOpenChange(false);
    } catch (e) {
      // Show the kernel's refusal reason verbatim: it is the only place that knows 'why it cannot change'
      setError(apiErrorText(e, t));
    } finally {
      setBusy(false);
    }
  };

  const field = (
    label: string,
    key: keyof FormState,
    opts: { placeholder?: string; rows?: number; mono?: boolean; hint?: string } = {},
  ) => (
    <div className="grid gap-1.5">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      {opts.rows ? (
        <Textarea
          rows={opts.rows}
          value={form[key]}
          onChange={(e) => set(key, e.target.value)}
          placeholder={opts.placeholder}
          className={opts.mono ? "font-mono text-xs" : undefined}
          data-testid={`agent-edit-${key}`}
        />
      ) : (
        <Input
          value={form[key]}
          onChange={(e) => set(key, e.target.value)}
          placeholder={opts.placeholder}
          className={opts.mono ? "font-mono text-xs" : undefined}
          data-testid={`agent-edit-${key}`}
        />
      )}
      {opts.hint && <p className="text-[11px] text-muted-foreground">{opts.hint}</p>}
    </div>
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {t("agentEditDialog.title", { name: profile.name || profile.id })}
          </DialogTitle>
        </DialogHeader>

        <div className="grid gap-4 py-2">
          {/* (1) Identity and ownership */}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {field(t("agentEditDialog.field.name"), "agent_name", {
              placeholder: t("agentEditDialog.field.namePh"),
            })}
            <div className="grid gap-1.5">
              <Label className="text-xs text-muted-foreground">{t("agentEditDialog.field.role")}</Label>
              <Select value={form.agent_role} onValueChange={(v) => set("agent_role", v)}>
                <SelectTrigger data-testid="agent-edit-agent_role">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {AGENT_ROLES.map((r) => (
                    <SelectItem key={r} value={r}>
                      {/* This changes the agent_role field itself, so it shows 'enum value (role name)' */}
                      {roleTagLabel(r, t)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label className="text-xs text-muted-foreground">{t("agentEditDialog.field.host")}</Label>
              <Select value={form.host_id} onValueChange={(v) => set("host_id", v)}>
                <SelectTrigger data-testid="agent-edit-host_id">
                  <SelectValue placeholder={t("agentEditDialog.field.hostPh")} />
                </SelectTrigger>
                <SelectContent>
                  {hosts.map((h) => (
                    <SelectItem key={h.id} value={h.id}>
                      <span className="inline-flex items-center gap-1.5">
                        {/* Same convention as the site-wide host dropdown: green dot only when online */}
                        <OnlineDot show={h.online} title={h.online ? t("agentsPage.status.online") : undefined} />
                        {h.alias || h.id}
                        <span className="text-[11px] text-muted-foreground">{h.ip}</span>
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label className="text-xs text-muted-foreground">{t("agentEditDialog.field.status")}</Label>
              <Select value={form.active_status} onValueChange={(v) => set("active_status", v)}>
                <SelectTrigger data-testid="agent-edit-active_status">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="1">{t("agentEditDialog.field.statusOn")}</SelectItem>
                  <SelectItem value="0">{t("agentEditDialog.field.statusOff")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* (2) Spec and prompt */}
          {/* Heights are allocated by **actual content length**, not split evenly: the spec is usually two or three lines,
              the system prompt is a whole personality block — the previous version gave both 5 rows, leaving the short field
              with a large blank and the long field scrolling; that is 'layout that ignored the content'. */}
          {field(t("agentEditDialog.field.xdesc"), "x_desc", {
            rows: 3,
            placeholder: t("agentEditDialog.field.xdescPh"),
          })}
          {field(t("agentEditDialog.field.prompt"), "system_prompt", {
            rows: 8,
            mono: true,
            placeholder: t("agentEditDialog.field.promptPh"),
          })}

          {/* (3) Advanced (collapsed by default: daily editing does not need it, and expanding makes people think they must fill it) */}
          <button
            type="button"
            onClick={() => setAdvanced((v) => !v)}
            className="flex items-center gap-1 self-start text-xs text-muted-foreground hover:text-foreground"
            data-testid="agent-edit-advanced-toggle"
          >
            {advanced ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
            {t("agentEditDialog.advanced")}
          </button>

          {advanced && (
            <div className="grid gap-3 rounded-lg border bg-muted/20 p-3">
              {field(t("agentEditDialog.field.skills"), "skills_allowlist", {
                rows: 4,
                mono: true,
                placeholder: t("agentEditDialog.field.skillsPh"),
                hint: t("agentEditDialog.field.skillsHint"),
              })}
              {field(t("agentEditDialog.field.mcp"), "mcp_config", {
                rows: 5,
                mono: true,
                placeholder: "{}",
                hint: t("agentEditDialog.field.mcpHint"),
              })}
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {field(t("agentEditDialog.field.email"), "agent_email", {
                  placeholder: t("agentEditDialog.field.emailPh"),
                })}
                {field(t("agentEditDialog.field.telegram"), "agent_telegram_token", {
                  placeholder: t("agentEditDialog.field.telegramPh"),
                })}
                {field(t("agentEditDialog.field.feishu"), "agent_feishu_token", {
                  placeholder: t("agentEditDialog.field.feishuPh"),
                })}
              </div>
              <p className="text-[11px] text-muted-foreground">
                {t("agentEditDialog.tokenNote.part1")}
                <b>{t("agentEditDialog.tokenNote.part2")}</b>
                {t("agentEditDialog.tokenNote.part3")}
                <b className="font-medium text-foreground">{t("agentEditDialog.tokenNote.strong")}</b>
                {t("agentEditDialog.tokenNote.part4")}
              </p>
            </div>
          )}

          {error && (
            <div
              className="rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive"
              data-testid="agent-edit-error"
            >
              {error}
            </div>
          )}

          <p
            className="text-[11px] text-muted-foreground"
            title={t("agentEditDialog.footer.noteTitle")}
          >
            {t("agentEditDialog.footer.note")}
            <b className="font-medium text-foreground">{t("agentEditDialog.footer.noteStrong")}</b>
            {t("agentEditDialog.footer.noteTail")}
          </p>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
            {t("agentEditDialog.footer.cancel")}
          </Button>
          <Button onClick={submit} disabled={busy || !dirty} data-testid="agent-edit-save">
            {busy && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
            {dirty
              ? t("agentEditDialog.footer.saveDirty", { n: Object.keys(changes).length })
              : t("agentEditDialog.footer.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
