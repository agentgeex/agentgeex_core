/**
 * Role names — the single site-wide authoritative wording
 * ─────────────────────────────────────────────────────────────
 * The Chinese names of the five system roles (= the kernel's agx_agent_registry.agent_role enum) exist **only here**.
 * Callers always go through `roleLabel()` / `roleTagLabel()` in `@/lib/role-label`;
 * do not create a second table — before 2026-11-19 there were four (member list / Agent profile / agents page / edit dialog),
 * and the same project_manager was written as 'Project Admin / Project Management / Project Manager' three ways. Feng: unify.
 *
 * ★★★ Do not merge 'Project Manager' with 'Project Admin' — they are two concepts (PM pointed this out 2026-11-19) ★★★
 *
 *   agent_role = project_manager   →  **Project Manager** (a role enum value)
 *   agx_project.admin_agent_id     →  **Project Admin** (the owner of a project; an ownership relation)
 *
 *   They are not the same thing: admin_agent_id may be **any** agent, not necessarily a project_manager;
 *   the kernel's permission check `caller == project.admin_agent_id` uses the latter.
 *
 *   So the following places **keep using 'Project Admin'; do not change them along with this file**:
 *     · entityDetail.field.admin        (the 'Project Admin' field on project detail)
 *     · agentProfile.field.projectAdmin / isProjectAdmin
 *     · dispatchExempt.permissionHint   ('requires a Project Admin or workshop_manager to enable')
 *     · taskEdit.fVisibilityHint        ('private = visible only to the owning Agent and Project Admins')
 *
 *   Criterion: is the word describing 'an agent's role', or 'a project's owner'?
 *   The former goes into this file; the latter stays where it is.
 */
export default {
  worker: "执行者",
  project_manager: "项目经理",
  host_manager: "主机管理",
  agent_manager: "Agent 管理",
  workshop_manager: "车间管理",
} as const;
