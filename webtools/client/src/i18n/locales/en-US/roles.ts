/**
 * Role names — the single authoritative source
 * ─────────────────────────────────────────────────────────────
 * The five system roles (= kernel enum `agx_agent_registry.agent_role`) are named
 * here only. Call sites must go through `roleLabel()` / `roleTagLabel()` in
 * `@/lib/role-label` — do not create a second table. Before 2026-11-19 there were
 * four tables and `project_manager` was rendered three different ways.
 *
 * ★★★ Do NOT merge "project manager" with "project admin" — two different things ★★★
 *
 *   agent_role = project_manager   →  **Project manager** (a role enum value)
 *   agx_project.admin_agent_id     →  **Project admin**   (a project's owner: an ownership relation)
 *
 *   They are not the same: admin_agent_id can be *any* agent, not necessarily a
 *   project_manager; the kernel's ownership check `caller == project.admin_agent_id`
 *   uses the latter.
 *
 *   These places keep "Project admin" and must not follow this file:
 *     · entityDetail.field.admin
 *     · agentProfile.field.projectAdmin / isProjectAdmin
 *     · dispatchExempt.permissionHint
 *     · taskEdit.fVisibilityHint
 *
 *   Test: is the word naming "an agent's role", or "a project's owner"?
 *   The former belongs in this file; the latter stays where it is.
 */
export default {
  worker: "Worker",
  project_manager: "Project manager",
  host_manager: "Host manager",
  agent_manager: "Agent manager",
  workshop_manager: "Workshop manager",
} as const;
