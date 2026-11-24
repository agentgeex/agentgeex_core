/**
 * components/agentgeex/quick-cmd-dialog.tsx — quick command **parameter renderer** (Schema-driven)
 *
 * ★ 2026-11-04 Feng: the run dialog has been merged into the generic 'message send' window (MessageSendDialog);
 *   the entry wrapper lives in `quick-cmd-send-dialog.tsx`. This file no longer hosts a Dialog of its own;
 *   it keeps only three reusable pieces: initial values / slot assembly preview / parameter control dispatch (ParamField).
 *   The point is to **keep a single parameter-rendering implementation** — with two dialogs each writing their own
 *   parameter form, sooner or later one supports showIf and the other does not.
 *
 * Basis: 'Quick command data spec v2.1' §3 'rendering contract'
 *   - params[] drives the 5-branch ParamField dispatcher:
 *       text → Input / textarea → Textarea / enum → Select /
 *       number → InputNumber / bool → Switch / date|datetime → DatePicker
 *   - Live preview: assemble locally from composed_instruction's {slot} for an instant preview (preview only)
 *   - Submit: **pass only {cmd_id, params}** → the backend authoritatively validates the role + assembles + dispatches
 *           (the frontend does no authoritative assembly/validation, to prevent tampering; the preview is only for looks)
 *
 * No invented UI: Dialog / Input / Select / Switch / InputNumber / DatePicker all reuse the
 * existing ui/ base; FormRow reuses ui/form-field.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { InputNumber } from "@/components/ui/input-number";
import { DatePicker } from "@/components/ui/date-picker";
import { OnlineDot } from "@/components/ui/online-dot";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  listAgentOptions, agentOptionText, agentStateLabel, listProjects, listHosts, listTasks,
  sortByQuickProject, projectNameWithPin,
  type QuickCmd, type QuickCmdParam,
} from "@/providers/agentgeex";

/* ------------------------- source → dropdown candidates ------------------------- */
/** Enumerable in-system value sources → [{value (submitted ID), label (readable name)}].
 * Contract: label shows the readable name (agent/host/project/task name); value submits the corresponding ID.
 * Empty source or unknown source → returns null (the caller falls back to free input).
 *
 * @param hostFilter when source="agent" and a host is selected, return only agents with a matching host_id
 *                   (Feng 2026-10-14: owning host → agent cascading filter).
 */
function optionsForSource(
  source: string,
  hostFilter?: string,
): { value: string; label: string; online?: boolean; state?: string }[] | null {
  switch (source) {
    case "agent":
      /* [2026-11-04 Feng] Text carries a [host_id] prefix; ordering follows listSelectableAgents'
         host ordering (**no re-sort by id**, otherwise host grouping gets shuffled). */
      /* [2026-11-07 Feng] Listed in full (including offline / template) — the parameters must be able to pick the
          agent used for 'looking up history'. Whether it can dispatch is decided by the backend; the dropdown
          does not pre-filter. Status is annotated as a suffix by agentOptionText. */
      return listAgentOptions()
        .filter((a) => !hostFilter || a.host_id === hostFilter)
        .map((a) => ({ value: a.id, label: agentOptionText(a), state: agentStateLabel(a) }));
    case "host":
      /* [2026-11-07 Feng] Mark online in the host dropdown: green dot if present, none if not.
          The parameter dropdowns are on the same chain, so it is the same visual convention as the list page filter. */
      return listHosts()
        .map((h) => ({
          value: h.id,
          label: h.alias && h.alias !== h.id ? `${h.alias} (${h.id})` : h.id,
          online: h.online === true,
        }))
        .sort((x, y) => x.value.localeCompare(y.value));
    case "project":
      /* [2026-11-17 15:07] Pinned project goes first (★ marker). */
      return sortByQuickProject(
        listProjects()
          .map((p) => ({ value: p.id, label: projectNameWithPin(p.id, p.name ? `${p.name} (${p.id})` : p.id) }))
          .sort((x, y) => x.value.localeCompare(y.value)),
        (x) => x.value,
      );
    case "task":
      return listTasks()
        .map((t) => ({ value: t.name, label: t.description ? `${t.name} — ${t.description}` : t.name }))
        .sort((x, y) => x.value.localeCompare(y.value));
    default:
      return null;
  }
}

/* --------------------------------- Utils --------------------------------- */

/** Parameter initial value: default wins (prefill the real value), otherwise a neutral empty value by type.
 */
export function initialValue(p: QuickCmdParam): unknown {
  const d = p.default;
  switch (p.type) {
    case "bool":
      if (typeof d === "boolean") return d;
      if (typeof d === "string") return d === "true";
      return false;
    case "number":
      if (d !== "" && d !== null && d !== undefined && !Number.isNaN(Number(d))) return Number(d);
      return null;
    case "date":
    case "datetime":
      if (typeof d === "string" && d) {
        const dt = new Date(d.replace(" ", "T"));
        if (!Number.isNaN(dt.getTime())) return dt;
      }
      return undefined;
    default:
      return d === null || d === undefined ? "" : String(d);
  }
}

/** Format a parameter value into the string used for assembly (consistent with the backend compose convention). */
export function valueToSlot(p: QuickCmdParam, v: unknown): string {
  if (v === null || v === undefined) return "";
  switch (p.type) {
    case "bool":
      return v ? "true" : "false";
    case "date": {
      const d = v as Date;
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, "0");
      const day = String(d.getDate()).padStart(2, "0");
      return `${y}-${m}-${day}`;
    }
    case "datetime": {
      const d = v as Date;
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, "0");
      const day = String(d.getDate()).padStart(2, "0");
      const hh = String(d.getHours()).padStart(2, "0");
      const mm = String(d.getMinutes()).padStart(2, "0");
      return `${y}-${m}-${day} ${hh}:${mm}`;
    }
    default:
      return String(v);
  }
}

/** Local preview assembly (preview only; the authoritative assembly is on the backend). */
export function localPreview(cmd: QuickCmd, values: Record<string, unknown>): string {
  const map: Record<string, string> = {};
  for (const p of cmd.params) map[p.name] = valueToSlot(p, values[p.name]);
  return cmd.composedInstruction.replace(/\{([a-zA-Z0-9_]+)\}/g, (m, key: string) =>
    key in map ? map[key] : m
  );
}

/* ------------------------------ ParamField ------------------------------ */

interface ParamFieldProps {
  param: QuickCmdParam;
  value: unknown;
  onChange: (v: unknown) => void;
  disabled?: boolean;
  /** Current value of a sibling parameter (used for the host → agent cascading filter). */
  allValues?: Record<string, unknown>;
}

/* [2026-11-16 13:08 Feng] 'you did not set this dropdown to the current host, so it looks selected to me,
   while actually nothing is selected ... just treat the default option as none and add a "please select" hint or similar'.
   ★ Root cause: Radix's `SelectValue` shows `placeholder` when nothing is selected, and in the parameter schema
     `placeholder` was written as an **example value** (`test_host_mac`, `proj_xxxx`, `e.g. ch06`) —
     so the box plainly shows `test_host_mac`, looking selected, while `value` is actually empty and submit stays grayed out.
   ★ Fix: the dropdown's unselected state only says 'please select X'. Example values belong only to text inputs
     (a gray 'example' hint in a text input is clear) and never go into a dropdown — a string in a dropdown must be
     'a value that is really selected'. */
const SELECT_HINT: Record<string, string> = {
  agent: "quickCmdDialog.selectAgent",
  host: "quickCmdDialog.selectHost",
  project: "quickCmdDialog.selectProject",
  task: "quickCmdDialog.selectTask",
};

export function ParamField({ param, value, onChange, disabled, allValues }: ParamFieldProps) {
  const { t } = useTranslation();
  const { type, placeholder, options, source } = param;

  // Enumerable in-system value (source non-empty) → Select (dropdown)
  //   Contract: label shows the readable name, value submits the corresponding ID.
  //   Overrides fields 'declared as text but with an in-system value source' (the core of this round's request).
  //   If a host is already picked → agent candidates are filtered by host_id (cascade).
  const selectedHost = allValues?.host_id == null ? "" : String(allValues.host_id);
  const srcOptions = optionsForSource(source, source === "agent" ? selectedHost : undefined);
  if (srcOptions) {
    return (
      <Select
        value={value === null || value === undefined || value === "" ? undefined : String(value)}
        onValueChange={(v: string) => onChange(v)}
        disabled={disabled}
      >
        <SelectTrigger data-testid={`qc-param-${param.name}`}>
          <SelectValue placeholder={SELECT_HINT[source] ? t(SELECT_HINT[source]) : t("quickCmdDialog.select")} />
        </SelectTrigger>
        <SelectContent>
          {srcOptions.length === 0 ? (
            <div className="px-3 py-2 text-xs text-muted-foreground">
              {t("quickCmdDialog.noOptions")}
            </div>
          ) : (
            srcOptions.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                <span className="inline-flex items-center gap-1.5">
                  <OnlineDot show={o.online} title={o.online ? t("agentsPage.status.online") : undefined} />
                  {o.label}
                  {o.state && (
                    <span className="shrink-0 rounded border border-border px-1 py-px text-[10px] font-normal leading-4 text-muted-foreground">
                      {o.state}
                    </span>
                  )}
                </span>
              </SelectItem>
            ))
          )}
        </SelectContent>
      </Select>
    );
  }

  // enum → Select (label shows the option, value is submitted)
  if (type === "enum") {
    return (
      <Select
        value={value === null || value === undefined || value === "" ? undefined : String(value)}
        onValueChange={(v: string) => onChange(v)}
        disabled={disabled}
      >
        <SelectTrigger data-testid={`qc-param-${param.name}`}>
          <SelectValue placeholder={t("quickCmdDialog.select")} />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  }

  // bool → Switch
  if (type === "bool") {
    return (
      <div className="flex items-center gap-2">
        <Switch
          data-testid={`qc-param-${param.name}`}
          checked={!!value}
          onCheckedChange={(c) => onChange(c)}
          disabled={disabled}
        />
        <span className="text-xs text-muted-foreground">{value ? t("quickCmdDialog.yes") : t("quickCmdDialog.no")}</span>
      </div>
    );
  }

  // number → InputNumber
  if (type === "number") {
    return (
      <InputNumber
        data-testid={`qc-param-${param.name}`}
        value={(value as number | null) ?? null}
        onChange={(v) => onChange(v)}
        placeholder={placeholder || t("quickCmdDialog.inputNumber")}
        disabled={disabled}
        className="w-full"
      />
    );
  }

  // date|datetime → DatePicker
  if (type === "date" || type === "datetime") {
    return (
      <DatePicker
        data-testid={`qc-param-${param.name}`}
        value={(value as Date | undefined) ?? undefined}
        onChange={(d) => onChange(d)}
        placeholder={
          placeholder ||
          (type === "datetime" ? t("datePicker.chooseDateTime") : t("datePicker.chooseDate"))
        }
        disabled={disabled}
        className="w-full justify-start"
      />
    );
  }

  // textarea → Textarea
  if (type === "textarea") {
    return (
      <Textarea
        data-testid={`qc-param-${param.name}`}
        value={(value as string) ?? ""}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder || t("quickCmdDialog.input")}
        disabled={disabled}
        rows={3}
      />
    );
  }

  // text (including source candidates: MVP uses a plain input for now)
  return (
    <Input
      data-testid={`qc-param-${param.name}`}
      value={(value as string) ?? ""}
      onChange={(e) => onChange(e.target.value)}
      placeholder={
                placeholder ||
                (source ? t("quickCmdDialog.sourceHint", { source }) : t("quickCmdDialog.input"))
              }
      disabled={disabled}
    />
  );
}

/** show_if evaluation (minimal expression): supports "name=='literal'" / "name!='literal'".
 *  Used only for branch visibility (e.g. scope=='single' → show the project param); if parsing fails, always show.
 */
function evalShowIf(expr: string | undefined, values: Record<string, unknown>): boolean {
  if (!expr) return true;
  const m = expr.match(/^\s*([a-zA-Z0-9_]+)\s*(==|!=)\s*'([^']*)'\s*$/);
  if (!m) return true;
  const [, name, op, rhs] = m;
  const lv = values[name];
  const lvs = lv === null || lv === undefined ? "" : String(lv);
  return op === "==" ? lvs === rhs : lvs !== rhs;
}
