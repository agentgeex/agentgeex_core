/**
 * Role name reading (the only entry)
 * ─────────────────────────────────────────────
 * Why this file exists: role names were previously defined in four separate key tables
 * (`miscUi.role` / `agentProfile.role` / `agentsPage.role` / `agentEditDialog.role`),
 * the same `project_manager` written three ways, and in English as `Project admin` / `Project manager` — two sets.
 * Changing one word meant four edits — guaranteed drift.
 *
 * Now: only `roles.*` in the language pack (see i18n/locales/*\/roles.ts),
 * the whole site goes through here. **Adding a role touches roles.ts only.**
 *
 * Why a pure function taking `t`: module-level constants are evaluated at module load, when the hook is unavailable,
 * so they would not follow a language switch (an old pit hit 6 times this round).
 */
import type { TFunction } from "i18next";
import i18n from "@/i18n";

/** Five system roles — consistent with the core's `agx_agent_registry.agent_role` */
export const AGENT_ROLES = [
  "worker",
  "project_manager",
  "host_manager",
  "agent_manager",
  "workshop_manager",
] as const;

export type AgentRole = (typeof AGENT_ROLES)[number];

const ROLE_KEY: Record<string, string> = Object.fromEntries(
  AGENT_ROLES.map((r) => [r, `roles.${r}`]),
);

/** Role short name. Unknown roles fall back to the raw value (no swallowing, no error — a future role in the core must not white-screen). */
export function roleLabel(role: string, t: TFunction): string {
  const key = ROLE_KEY[role];
  return key ? t(key) : role;
}

/**
 * The "enum value (role name)" form — edit dialog only: that dropdown changes the `agent_role` field itself,
 * and letting the operator see the real enum value is safer than seeing Chinese only.
 *
 * When the two are equivalent, drop the parentheses (English worker → Worker), otherwise you get junk like `worker (Worker)`.
 */
export function roleTagLabel(role: string, t: TFunction): string {
  const label = roleLabel(role, t);
  if (!label || label.toLowerCase() === role.toLowerCase()) return role;
  return i18n.language.startsWith("zh") ? `${role}（${label}）` : `${role} (${label})`;
}
