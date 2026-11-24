/**
 * pages/agentgeex/host-edit.tsx — compute host editing (page-style, not a dialog)
 * ────────────────────────────────────────────────────────────────
 * [2026-10-30 Feng] 'no dialog, open the window right under the current nav menu item'
 *   → editing becomes a **dedicated route page** `/hosts/:id/edit` attached to the `/hosts` tab
 *     (sharing one work window with the list, tab-meta tabKey="/hosts"), no longer a Dialog.
 *
 * Conventions:
 *   - Writes go only through `PATCH /v1/hosts/{id}` → BFF → MCP `agx_update_host`;
 *     **the submit identity is fixed to agx_core** (workshop_manager, sufficient for that tool's L6).
 *   - Only changed fields (diff) are submitted; form empty values never overwrite existing DB content.
 *   - Save success → toast → re-pull the snapshot (the list reflects it at once) → back to the list.
 *   - Kernel/gateway rejections are shown as is (403 permission / 400 param / 404 host not found), never silenced.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, SearchX, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EditPageHeader } from "@/components/agentgeex/edit-page-header";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select";
import { useToast } from "@/components/ui/toast";
import { useDetailCrumb } from "@/lib/breadcrumb";
import { listHosts, refreshPageData, updateHost } from "@/providers/agentgeex";
import type { HostPlatformInput, HostUpdateInput } from "@/providers/api";

/** Submit identity (fixed on the BFF side; shown here only so the user knows 'who is changing it') */
const EDITOR_AGENT_ID = "agx_core";

interface FormState {
  host_alias: string;
  endpoint_ip: string;
  ssh_port: string;
  os_platform: string;
  task_slots_limit: string;
  x_desc: string;
  /**
   * [2026-11-04 21:31 Feng] 'platforms is a json field, this should be editable in the host table'
   *
   * ★ Why a draft (all strings) instead of storing the raw object:
   *   While typing, "1", "1a", "" are all legitimate intermediate states; storing numbers flattens them
   *   on the spot, so the user cannot even delete and retype a port (deleting instantly turns it into 0).
   */
  platforms: PlatformDraft[];
}

/** Edit state of one platform entry (all strings; typed only on submit) */
interface PlatformDraft {
  platform: string;
  name: string;
  mode: "process" | "container";
  port: string;
  addr: string;
  base_path: string;
  profile: string;
  note: string;
}

/** Object from the DB → edit state (missing fields become empty strings; no 'null displays as null') */
function toDraft(p: HostPlatformInput): PlatformDraft {
  return {
    platform: p.platform ?? "",
    name: p.name ?? "",
    mode: p.mode === "container" ? "container" : "process",
    port: p.port === undefined || p.port === null ? "" : String(p.port),
    addr: p.addr ?? "",
    base_path: p.base_path ?? "",
    profile: p.profile ?? "",
    note: p.note ?? "",
  };
}

/**
 * Edit state → submit body. **Emits only keys that have a value**:
 * an empty string is treated as 'explicitly delete this key', not 'write an empty string' —
 * otherwise process mode would gain a gratuitous `addr: ""`, conflicting with the kernel spec (addr must not appear for process).
 */
function fromDraft(d: PlatformDraft): HostPlatformInput {
  const out: HostPlatformInput = {
    platform: d.platform.trim(),
    mode: d.mode,
  };
  if (d.name.trim()) out.name = d.name.trim();
  if (d.base_path.trim()) out.base_path = d.base_path.trim();
  if (d.profile.trim()) out.profile = d.profile.trim();
  if (d.note.trim()) out.note = d.note.trim();
  if (d.mode === "process") {
    const n = Number(d.port);
    if (d.port.trim() !== "" && Number.isFinite(n)) out.port = n;
    // OpenClaw multi-instance separates config dirs by profile (~/.openclaw-{profile}).
    // The main instance writes an **empty string rather than omitting the key** by convention — readers need not handle both 'key with no value' and 'no key'.
    if (d.platform.trim().toLowerCase() === "openclaw") out.profile = d.profile.trim();
    // ★ Carry addr out as is: in process mode the edit UI **does not show** this field (the kernel spec says process
    //   builds the address from endpoint_ip+port), but the frontend's resolvePlatformEndpoint supports it for
    //   host-level overrides. Not visible ≠ safe to drop — silently erasing a value someone configured is worse than not offering editing.
    if (d.addr.trim()) out.addr = d.addr.trim();
  } else if (d.addr.trim()) {
    // container: the address is self-owned, port is only for display/fallback (not used in assembly)
    out.addr = d.addr.trim();
    const n = Number(d.port);
    if (d.port.trim() !== "" && Number.isFinite(n)) out.port = n;
  }
  return out;
}

/** For structural comparison: fixed key order, so 'same content but different key order' is not judged as a change */
function canon(list: HostPlatformInput[]): string {
  return JSON.stringify(
    list.map((p) => [
      p.platform, p.name ?? "", p.mode, p.port ?? "", p.addr ?? "",
      p.base_path ?? "", p.profile ?? "", p.note ?? "",
    ]),
  );
}

/** Validate item by item, returning plain-language errors (empty array = pass) */
function validatePlatforms(drafts: PlatformDraft[], t: (k: string, o?: Record<string, unknown>) => string): string[] {
  const errs: string[] = [];
  const seen = new Set<string>();
  drafts.forEach((d, i) => {
    const tag = t("hostEdit.validate.tag", { n: i + 1 });
    const pid = d.platform.trim();
    if (!pid) errs.push(t("hostEdit.validate.platformEmpty", { tag }));
    else if (seen.has(pid.toLowerCase())) errs.push(t("hostEdit.validate.platformDup", { tag, pid }));
    else seen.add(pid.toLowerCase());

    if (d.mode === "process") {
      const n = Number(d.port);
      if (!d.port.trim()) errs.push(t("hostEdit.validate.portRequired", { tag }));
      else if (!Number.isInteger(n) || n < 1 || n > 65535) errs.push(t("hostEdit.validate.portRange", { tag }));
    } else {
      if (!d.addr.trim()) errs.push(t("hostEdit.validate.addrRequired", { tag }));
    }
  });
  return errs;
}

export default function HostEditPage() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { toast } = useToast();
  const { t } = useTranslation();

  // The host comes from the store snapshot loaded at startup (synchronous read, zero page wait)
  const host = React.useMemo(() => listHosts().find((h) => h.id === id), [id]);
  useDetailCrumb(host?.alias);

  const [form, setForm] = React.useState<FormState | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");

  // Fill in current values the first time the host is available (refill after switching host / refresh)
  React.useEffect(() => {
    if (!host) return;
    setForm({
      host_alias: host.alias ?? "",
      endpoint_ip: host.ip ?? "",
      ssh_port: String(host.sshPort ?? ""),
      os_platform: host.os ?? "",
      task_slots_limit: String(host.slotsLimit ?? ""),
      x_desc: host.xDesc ?? "",
      platforms: (host.platforms ?? []).map(toDraft),
    });
    setError("");
  }, [host]);

  const goBack = () => navigate("/hosts");

  if (!host) {
    return (
      <div className="flex flex-col" data-testid="host-edit-missing">
        <div className="flex flex-col items-center gap-3 rounded-xl border border-strong bg-card p-12 text-center shadow-sm">
          <SearchX className="h-8 w-8 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            {t("hostEdit.missing.text", { id: id || t("hostEdit.missing.emptyId") })}
          </p>
          <Button variant="back" size="sm" onClick={goBack}>
            <ArrowLeft className="mr-1 h-4 w-4" />
            {t("hostEdit.missing.back")}
          </Button>
        </div>
      </div>
    );
  }

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((f) => (f ? { ...f, [k]: v } : f));

  /* platforms add/edit/delete — always immutable updates, and only effective when the form exists */
  const patchPlatform = (i: number, patch: Partial<PlatformDraft>) =>
    setForm((f) =>
      f ? { ...f, platforms: f.platforms.map((p, j) => (j === i ? { ...p, ...patch } : p)) } : f,
    );
  const addPlatform = () =>
    setForm((f) =>
      f
        ? {
            ...f,
            platforms: [
              ...f.platforms,
              // Defaults use the most common combination (same-host process + OpenClaw + /chat),
              // so a newly added row does not leave 6 empty boxes with no clue which are required
              { platform: "openclaw", name: "", mode: "process", port: "", addr: "", base_path: "/chat", profile: "", note: "" },
            ],
          }
        : f,
    );
  const removePlatform = (i: number) =>
    setForm((f) => (f ? { ...f, platforms: f.platforms.filter((_, j) => j !== i) } : f));

  /** Take only changed fields — never overwrite untouched DB content with form values */
  const diff: HostUpdateInput = (() => {
    if (!form) return {};
    const out: HostUpdateInput = {};
    if (form.host_alias !== (host.alias ?? "")) out.host_alias = form.host_alias.trim();
    if (form.endpoint_ip !== (host.ip ?? "")) out.endpoint_ip = form.endpoint_ip.trim();
    if (form.ssh_port !== String(host.sshPort ?? "")) out.ssh_port = Number(form.ssh_port);
    if (form.os_platform !== (host.os ?? "")) out.os_platform = form.os_platform.trim();
    if (form.task_slots_limit !== String(host.slotsLimit ?? "")) {
      out.task_slots_limit = Number(form.task_slots_limit);
    }
    if (form.x_desc !== (host.xDesc ?? "")) out.x_desc = form.x_desc;
    // platforms is a JSON array: compare the whole group, no per-item merge (the backend also overwrites the whole column)
    const before = (host.platforms ?? []).map((p) =>
      fromDraft(toDraft(p as HostPlatformInput)),
    );
    const after = form.platforms.map(fromDraft);
    if (canon(before) !== canon(after)) out.platforms = after;
    return out;
  })();

  const changed = Object.keys(diff).length;
  const aliasEmpty = !form?.host_alias.trim();
  const badPort =
    "ssh_port" in diff &&
    !(Number.isInteger(diff.ssh_port) && diff.ssh_port! >= 1 && diff.ssh_port! <= 65535);
  const badSlots =
    "task_slots_limit" in diff &&
    !(Number.isInteger(diff.task_slots_limit) && diff.task_slots_limit! >= 0);
  /* If platforms changed at all, validate the **whole group** (not just the changed entry):
     a combination like deleting one entry and breaking another would slip through if only changed items were validated. */
  const platformErrs = form ? validatePlatforms(form.platforms, t) : [];
  const badPlatforms = "platforms" in diff && platformErrs.length > 0;

  const submit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (aliasEmpty) return setError(t("hostEdit.error.aliasEmpty"));
    if (badPort) return setError(t("hostEdit.error.portRange"));
    if (badSlots) return setError(t("hostEdit.error.slotsInvalid"));
    if (badPlatforms) return setError(platformErrs.join(t("hostEdit.validate.joiner")));
    if (!changed) return setError(t("hostEdit.error.noChange"));
    setBusy(true);
    setError("");
    try {
      const res = await updateHost(host.id, diff);
      toast({
        title: t("hostEdit.toast.updated"),
        description: `${host.id} · ${res.fields.join(" / ")}`,
        variant: "success",
      });
      // Re-pull the BFF snapshot → version +1 → the list remounts and re-reads the store (new values visible immediately)
      void refreshPageData();
      navigate("/hosts");
    } catch (err) {
      const e2 = err as { status?: number; code?: string; message?: string };
      setError(e2.message || String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col" data-testid="page-host-edit">
      <form
        onSubmit={submit}
        className="flex flex-col gap-4 rounded-xl border border-strong bg-card p-4 shadow-sm"
        data-testid="content-frame"
      >
        {/* Page header: first row inside the white frame, same style as the detail page (left = title + subtitle, right = actions) */}
        <EditPageHeader
          title={t("hostEdit.header.edit")}
          subtitle={host.id}
          onBack={goBack}
          backTestId="host-edit-back"
          saveTestId="host-edit-save"
          busy={busy}
          saveDisabled={!changed || aliasEmpty || badPort || badSlots || badPlatforms}
        />

        <div className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
          <span>{t("hostEdit.note.kernel")}</span>
          <span>·</span>
          <span>
            {t("hostEdit.note.editor")} <span className="font-mono">{EDITOR_AGENT_ID}</span>
          </span>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <FieldRow label={t("hostEdit.field.alias")} required>
            <Input
              value={form?.host_alias ?? ""}
              onChange={(e) => set("host_alias", e.target.value)}
              disabled={busy}
              data-testid="host-edit-alias"
            />
          </FieldRow>
          <FieldRow label={t("hostEdit.field.ip")}>
            <Input
              value={form?.endpoint_ip ?? ""}
              onChange={(e) => set("endpoint_ip", e.target.value)}
              disabled={busy}
              className="font-mono text-xs"
              data-testid="host-edit-ip"
            />
          </FieldRow>
          <FieldRow label={t("hostEdit.field.sshPort")} hint={t("hostEdit.field.sshPortHint")}>
            <Input
              value={form?.ssh_port ?? ""}
              onChange={(e) => set("ssh_port", e.target.value)}
              disabled={busy}
              inputMode="numeric"
              className="font-mono text-xs"
              data-testid="host-edit-sshport"
            />
          </FieldRow>
          <FieldRow label={t("hostEdit.field.os")}>
            <Input
              value={form?.os_platform ?? ""}
              onChange={(e) => set("os_platform", e.target.value)}
              disabled={busy}
              data-testid="host-edit-os"
            />
          </FieldRow>
          <FieldRow label={t("hostEdit.field.slots")} hint={t("hostEdit.field.slotsHint")}>
            <Input
              value={form?.task_slots_limit ?? ""}
              onChange={(e) => set("task_slots_limit", e.target.value)}
              disabled={busy}
              inputMode="numeric"
              className="font-mono text-xs"
              data-testid="host-edit-slots"
            />
          </FieldRow>
          <FieldRow label={t("hostEdit.field.hostId")} hint={t("hostEdit.field.hostIdHint")}>
            <Input value={host.id} disabled className="font-mono text-xs" data-testid="host-edit-id" />
          </FieldRow>
        </div>

        {/* ────────── Available platforms (agx_host_inventory.platforms, JSON array) ──────────
            [2026-11-04 21:31 Feng] 'platforms is a json field, this should be editable in the host table'
            Previously this column could only be changed in the DB (or by hand-writing JSON elsewhere), yet it is the only
            source for 'what this host can run and how to reach it' — the agent page's 'open' builds the address from it. */}
        <div className="flex flex-col gap-2 border-t border-border pt-3" data-testid="host-edit-platforms">
          <div className="flex items-center justify-between gap-2">
            <div className="flex flex-col">
              <span className="text-sm font-medium">{t("hostEdit.platforms.title")}</span>
              <span className="text-[11px] text-muted-foreground">{t("hostEdit.platforms.subtitle")}</span>
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="gap-1"
              disabled={busy}
              onClick={addPlatform}
              data-testid="host-edit-platform-add"
            >
              <Plus className="h-4 w-4" />
              {t("hostEdit.platforms.add")}
            </Button>
          </div>

          {form?.platforms.length === 0 && (
            <div
              className="rounded-md border border-dashed px-3 py-4 text-center text-xs text-muted-foreground"
              data-testid="host-edit-platform-empty"
            >
              {t("hostEdit.platforms.empty")}
            </div>
          )}

          {form?.platforms.map((pf, i) => (
            <div
              key={i}
              className="flex flex-col gap-2 rounded-lg border bg-muted/20 p-3"
              data-testid={`host-edit-platform-${i}`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium text-muted-foreground">
                  {t("hostEdit.platforms.itemNo", { n: i + 1 })}
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-7 gap-1 text-destructive hover:text-destructive"
                  disabled={busy}
                  onClick={() => removePlatform(i)}
                  data-testid={`host-edit-platform-${i}-remove`}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  {t("hostEdit.platforms.remove")}
                </Button>
              </div>

              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                <FieldRow
                  label={t("hostEdit.platformField.id")}
                  required
                  hint={t("hostEdit.platformField.idHint")}
                >
                  <Input
                    value={pf.platform}
                    onChange={(e) => patchPlatform(i, { platform: e.target.value })}
                    disabled={busy}
                    className="font-mono text-xs"
                    data-testid={`host-edit-platform-${i}-platform`}
                  />
                </FieldRow>
                <FieldRow label={t("hostEdit.platformField.name")} hint={t("hostEdit.platformField.nameHint")}>
                  <Input
                    value={pf.name}
                    onChange={(e) => patchPlatform(i, { name: e.target.value })}
                    disabled={busy}
                    data-testid={`host-edit-platform-${i}-name`}
                  />
                </FieldRow>
                <FieldRow
                  label={t("hostEdit.platformField.mode")}
                  required
                  hint={t("hostEdit.platformField.modeHint")}
                >
                  <Select
                    value={pf.mode}
                    onValueChange={(v) => patchPlatform(i, { mode: v as PlatformDraft["mode"] })}
                    disabled={busy}
                  >
                    <SelectTrigger className="h-9" data-testid={`host-edit-platform-${i}-mode`}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="process" data-testid={`host-edit-platform-${i}-mode-process`}>
                        {t("hostEdit.platformField.process")}
                      </SelectItem>
                      <SelectItem value="container" data-testid={`host-edit-platform-${i}-mode-container`}>
                        {t("hostEdit.platformField.container")}
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </FieldRow>

                <FieldRow
                  label={t("hostEdit.platformField.port")}
                  required={pf.mode === "process"}
                  hint={
                    pf.mode === "process"
                      ? t("hostEdit.platformField.portHintProcess")
                      : t("hostEdit.platformField.portHintContainer")
                  }
                >
                  <Input
                    value={pf.port}
                    onChange={(e) => patchPlatform(i, { port: e.target.value })}
                    disabled={busy}
                    inputMode="numeric"
                    className="font-mono text-xs"
                    data-testid={`host-edit-platform-${i}-port`}
                  />
                </FieldRow>
                <FieldRow label={t("hostEdit.platformField.basePath")} hint={t("hostEdit.platformField.basePathHint")}>
                  <Input
                    value={pf.base_path}
                    onChange={(e) => patchPlatform(i, { base_path: e.target.value })}
                    disabled={busy}
                    className="font-mono text-xs"
                    data-testid={`host-edit-platform-${i}-basepath`}
                  />
                </FieldRow>
                <FieldRow label={t("hostEdit.platformField.profile")} hint={t("hostEdit.platformField.profileHint")}>
                  <Input
                    value={pf.profile}
                    onChange={(e) => patchPlatform(i, { profile: e.target.value })}
                    disabled={busy}
                    className="font-mono text-xs"
                    data-testid={`host-edit-platform-${i}-profile`}
                  />
                </FieldRow>
              </div>

              {/* Only container needs addr: the container is itself a host, so its address cannot be built from the host IP */}
              {pf.mode === "container" && (
                <FieldRow
                  label={t("hostEdit.platformField.addr")}
                  required
                  hint={t("hostEdit.platformField.addrHint")}
                >
                  <Input
                    value={pf.addr}
                    onChange={(e) => patchPlatform(i, { addr: e.target.value })}
                    disabled={busy}
                    className="font-mono text-xs"
                    data-testid={`host-edit-platform-${i}-addr`}
                  />
                </FieldRow>
              )}

              <FieldRow label={t("hostEdit.platformField.note")} hint={t("hostEdit.platformField.noteHint")}>
                <Input
                  value={pf.note}
                  onChange={(e) => patchPlatform(i, { note: e.target.value })}
                  disabled={busy}
                  data-testid={`host-edit-platform-${i}-note`}
                />
              </FieldRow>
            </div>
          ))}

          {badPlatforms && (
            <div
              className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
              data-testid="host-edit-platform-error"
            >
              {platformErrs.join(t("hostEdit.validate.joiner"))}
            </div>
          )}
        </div>

        <FieldRow label={t("hostEdit.field.desc")}>
          <Textarea
            value={form?.x_desc ?? ""}
            onChange={(e) => set("x_desc", e.target.value)}
            disabled={busy}
            rows={5}
            className="text-xs leading-relaxed"
            data-testid="host-edit-xdesc"
          />
        </FieldRow>

        {error && (
          <div
            className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
            data-testid="host-edit-error"
          >
            {error}
          </div>
        )}

        <div className="flex items-center justify-between gap-2 border-t border-border pt-3 text-xs text-muted-foreground">
          <span data-testid="host-edit-diff">
            {changed
              ? t("hostEdit.diff.submit", { n: changed, fields: Object.keys(diff).join(" / ") })
              : t("hostEdit.diff.none")}
          </span>
          <span>{t("hostEdit.diff.autoRefresh")}</span>
        </div>
      </form>
    </div>
  );
}

/** Label on top, control below (form convention consistent with the message-send dialog) */
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
