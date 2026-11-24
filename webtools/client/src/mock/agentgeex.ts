/**
 * mock/agentgeex.ts — AgentGeeX business-domain mock data (single source of truth)
 *
 * Basis: docs/agent_docs/agentgeex_ui.md (design notes by Feng, 2026-09-30)
 *
 * Design conventions:
 *   - This round is "UI only": all data comes from the in-memory mock here;
 *   - When wiring the backend, page code changes zero (single data exit is providers/dataProvider.ts);
 *   - Entity naming follows AgentGeeX real concepts as closely as possible
 *     (agent / session / task / execution / project / plan),
 *     so fields are easy to map when connecting to the BFF later.
 *
 * ⚠️ Quick Commands fields are not implemented for now — Feng is still designing them (2026-09-30 feedback).
 */

/* ------------------------------------------------------------------ *
 * 1. Agent
 * ------------------------------------------------------------------ */
export type AgentStatus = "online" | "offline" | "error" | "busy";
export type AgentRole =
  | "worker"
  | "project_manager"
  | "host_manager"
  | "workshop_manager"
  | "agent_manager";

/** Agent category tags (2026-10-05 Feng's request: tag by type/status) */
export type AgentKind = "template" | "clone" | "native";

/** Deploy status (real column deploy_status) */
export type DeployStatus = "template" | "deployed" | "pending" | "deploying" | "failed";

export interface AgentRow {
  id: string;
  name: string;              // Display name
  role: AgentRole;
  status: AgentStatus;
  model: string;
  /** Owning project id (used to group by project) */
  project_id: string;
  host_id: string;
  /** Total sessions */
  sessionCount: number;
  /** Total tasks */
  taskCount: number;
  /** Total executions (real backend stat, optional) */
  execCount?: number;
  /** Successful executions (real backend stat, optional) */
  execSucceeded?: number;
  /** Failed executions (real backend stat, optional) */
  execFailed?: number;
  /** Total token consumption */
  tokens: number;
  /** Last active time */
  lastActiveAt: string;

  /* ── Tag dimensions (real backend fields, optional so mock can fall back) ── */
  /** Deploy status (template/deployed/pending/deploying/failed) */
  deployStatus?: DeployStatus;
  /** Category: template / clone instance / native */
  kind?: AgentKind;
  /** Category label (Chinese) */
  kindLabel?: string;
  /** Whether this is a clone template (clone master) */
  isTemplate?: boolean;
  /** Whether in use (non-template and enabled) */
  inUse?: boolean;
  /** Source agent_id of the clone (empty for native) */
  originId?: string;
  /** Agent platform type (openclaw / claude_code / custom) */
  agentType?: string;
  /** Avatar URL (`agx_agent_registry.agent_avatar`; empty → frontend falls back to initials) */
  avatarUrl?: string;

  /* ── Temporary dispatch exemption (agx_agent_registry.dispatch_exempt_until) ──
     The three states are decided by the **server** and passed through;
     the frontend no longer compares against the browser clock:
       never granted   until empty + active=false + remaining=0
       active          active=true  + remaining>0
       expired         active=false + remaining<0 (**has a value but expired**, unlike "never granted") */
  /** Expiry moment "YYYY-MM-DD HH:MM:SS"; never granted = empty string */
  dispatchExemptUntil?: string;
  /** Whether it is really in effect right now (= until > now, decided by the server) */
  dispatchExemptActive?: boolean;
  /** Remaining seconds; negative when expired — use it to tell "expired" from "none" */
  dispatchExemptRemainingSeconds?: number;
  /** Whether this role needs this gate (decided by the server).
      worker levels {0,1,2,3} cannot reach L5 → true;
      PM / host_manager / workshop_manager / agent_manager can dispatch by themselves → false,
      **no exemption UI is given to them** (giving one would be a fake move: "clicked, but permission unchanged"). */
  dispatchExemptApplicable?: boolean;
}

/* ------------------------------------------------------------------ *
 * 2. Project / ProjectGroup
 * ------------------------------------------------------------------ */
export type ProjectStatus = "active" | "paused" | "archived";

export interface ProjectRow {
  id: string;                // proj_xxx
  name: string;
  abbr: string;
  platform: string;          // openclaw | claude_code | custom
  status: ProjectStatus;
  agentCount: number;
  adminAgentId: string;
  /** Member details (admin first, isAdmin=true) — since 2026-11-04 the list merges them into one "project group members" column */
  members?: { id: string; name: string; role: string; isAdmin?: boolean }[];
  /** Project logo (agx_project.project_logo, exposed by backend; empty → local image / initials fallback) */
  logoUrl?: string;
  /** [2026-11-17] Pinned (agx_project.is_quick=1) — sorted first in every project dropdown, with a star */
  isQuick?: number;
  /** Pin order (agx_project.quick_order, smaller comes first) */
  quickOrder?: number;
  groupId: string;           // Owning project group
  /** Creation time (agx_project.created_at) — [2026-11-04 Feng] the old "updated at" column was empty
   *  (agx_project has no updated column), so it is replaced by this real, existing creation time. */
  createdAt?: string;
  updatedAt: string;
}

export interface ProjectGroupRow {
  id: string;                // group_xxx
  name: string;
  memberCount: number;
  /** Member details (template Agents, listed directly in the table, no need to open details; 2026-11-04) */
  members?: { id: string; name: string; role: string; isAdmin?: boolean }[];
  /** Hosts the members live on (comma separated, derived by BFF; for multi-host filtering, 2026-10-26) */
  hostIds?: string;
  projectCount: number;
  status: ProjectStatus;
  xDesc: string;
  updatedAt: string;
}

/* ------------------------------------------------------------------ *
 * 3. Session
 * ------------------------------------------------------------------ */
export type SessionStatus = "active" | "idle" | "archived";

/* ── Live session state (2026-11-05) ─────────────────────────
 * Source: Redis `agx:gw:session:<key>` (written by the collector of each of the 6 OpenClaw instances).
 * Different from agx_session: MySQL holds "registered sessions" (incl. 4145 archived rows),
 * Redis holds "sessions that truly exist on the gateway right now" (2405 rows) → on mismatch runtime=null.
 *
 * ★ contextRatio null = "unknown", not 0:
 *   about 1241 historical sessions have total_tokens=0 (the gateway simply has no token data),
 *   showing 0% would be read as "context barely used", which is the opposite of the fact.
 */
export interface SessionRuntime {
  status: "running" | "idle" | "failed" | "stalled" | "unknown";
  hasActiveRun: boolean;
  /** null = unknown (no token data); 0.6949 = 69.49% */
  contextRatio: number | null;
  totalTokens: number;
  contextTokens: number;
  lastEventAt: number;
  lastEventType?: string;
  hostId: string;
  agentId?: string;
  model?: string;
  activeRunId?: string;
  lastRunId?: string;
  errorCount?: number;
  lastError?: string;
  /** true = this row comes from the cross-host duplicate quarantine area (agx:gw:conflict:<host>:<key>) */
  fromConflict: boolean;
}

export interface SessionRow {
  id: string;                // agent:xxx:main form
  name: string;
  agentId: string;
  agentName: string;
  /** Session type (provided by BFF; mock may omit it). ★ 2026-11-12 added `assistant`:
   *  assistants have been an independent type since 2026-10-21 (no longer borrowing `case`), so the type description must match the DB. */
  sessionType?: "main" | "task" | "case" | "oneshot" | "assistant";
  /** [2026-11-06] Host where the session is stored (`agx_session.host_id`).
   *  Feng added this column and backfilled it; it matches the Agent's host, but **a session has its own ownership**:
   *  after an Agent moves to another host, historical sessions should not drift along. Host filtering uses this field. */
  hostId?: string;
  status: SessionStatus;
  messageCount: number;
  lastActiveAt: string;
  /* ── Assistants (case sessions with is_assistant=1; provided by BFF) ────────────
   * An "assistant" is essentially just a session: same table, same field set,
   * distinguished only by the is_assistant flag. So assistant detail = session detail. */
  isAssistant?: boolean;     // Whether assistant
  assistantSlug?: string;    // Assistant slug (e.g. ui_design)
  avatar?: string;           // Assistant avatar (URL or local path; empty → initials of the name)
  creatorAgentId?: string;   // Creator Agent ID
  creatorName?: string;      // Creator display name
  createdAt?: string;        // Creation time
  /** Live state on the gateway; not found in Redis → null (shows "—", not 0) */
  runtime?: SessionRuntime | null;
  /** When this session_key appears on several hosts (cross-host duplicate), list each host */
  runtimeHosts?: string[];
}

/* ------------------------------------------------------------------ *
 * 3.9 Quick Access
 * ------------------------------------------------------------------ */
/** [2026-11-13 22:03 Feng] Mark frequently used project/Agent/session/plan as quick so the quick access entry jumps straight there.
 *  ★ [2026-11-13 22:25 Feng changed the rule] plans are not included (only those three kinds);
 *    the flag sits directly on the `is_quick` column of the core table, **one global copy**, no longer bucketed per person.
 *    The name is read from the entity itself (no snapshot stored) — it follows renames. */
export type QuickEntryType = "project" | "agent" | "session" | "plan";

export interface QuickEntryRow {
  entryId: string;
  entryType: QuickEntryType;
  /** Target unique key: project_id / agent_id / session_id / plan_id */
  entryKey: string;
  /** Display name (read from the target table now, not a snapshot) */
  label: string;
  ownerAgentId?: string;
  sortOrder?: number;
  createdAt?: string;
}

/* ------------------------------------------------------------------ *
 * 4. Task definition
 * ------------------------------------------------------------------ */
export type TaskLabs =
  | "universal"
  | "general"
  | "agent_create"
  | "agent_deploy"
  | "ui_lead"
  | "project_create";

export interface TaskRow {
  id: string;                // task_xxx
  name: string;              // task_name (stable identifier)
  description: string;
  agentId: string;
  agentName: string;
  labs: TaskLabs;
  /** Task logo (agx_task_base.task_logo; shared/deduped by task_labs) */
  logoUrl?: string;
  isPrivate: 0 | 1;
  activeStatus: 0 | 1;
  updatedAt: string;
  /** [2026-10-19 P1] Receipt semantics of a task template (result / confirm / silent).
   *  Used only for UI labeling (e.g. silent hints "this task is designed to be silent"), not for frontend derivation. */
  replyMode?: string;
  /** [2026-10-19 P1] Default receipt (yes/no) computed by the backend.
   *  ★ The mapping rule (result/confirm → yes, others → no) lives in one place only, the backend; the frontend does not compute it. */
  defaultReply?: "yes" | "no";
}

/* ------------------------------------------------------------------ *
 * 5. Execution task records
 * ------------------------------------------------------------------ */
export type RunStatus =
  | "draft"
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  /** [2026-11-06 Feng] Awaiting human confirmation — when `needs_confirm=1` the kernel `agx_finish_task`
   *  **does not write succeeded**, and lands in this state instead: the work is done, but it counts as complete only
   *  after a human reviews it and clicks "approve"; downstream chaining does not advance and the watchdog does not count time.
   *  ★ The value is `awaiting_confirm` (the only value the kernel writes).
   *  `pending_confirm` below is an early design name; the kernel `agx_confirm_task` still reads it for compatibility,
   *  but **the only write point is awaiting_confirm** — both values mean the same thing, so the UI must use one word set. */
  | "awaiting_confirm"
  | "pending_confirm"
  /** Archived (legacy state, 78 rows in the DB).
   *  [2026-11-03] The BFF used to rewrite any unknown status to pending,
   *  so archived executions all counted as "queued" — the missing value in this type was the source of that. */
  | "archived";

/** "What can be done with this run right now", computed by the server — the frontend only renders, never compares times.
   *  (When the frontend clock disagrees with the server, comparing locally yields "a button that should exist but does not") */
export interface NudgeAction {
  kind: "urge" | "ack_remind";
  label: string;
}

export interface ExecutionRow {
  run_uuid: string;          // run_xxx
  execName: string;          // Execution short title
  taskId: string;
  taskName: string;
  /** Receiver (who executes) */
  agentId: string;
  agentName: string;
  /** Sender (who dispatched this task; empty = old data not recorded) */
  sendAgentId?: string;
  sessionId: string;
  /** [2026-11-03] Owning project of the execution (`agx_task_execution.project_id`).
   *  Taken from the execution's own row, not reverse-looked-up via the agent — agents switch projects, historical executions should not drift. */
  projectId?: string;
  /** [2026-11-06] Host where the execution is stored (`agx_task_execution.host_id`).
   *  = the machine where this work actually ran, fixed at dispatch time.
   *  Different from "the Agent's host" (an agent may have moved hosts; historical executions should not drift).
   *  Empty = 54 old rows not recorded; filtering falls back to the agent's host. */
  hostId?: string;
  /** [2026-10-29] Owning plan / previous / next execution (already returned by BFF; type-only addition, for related-link jumps in the detail page) */
  planId?: string;
  prevTask?: string;
  nextTask?: string;
  /** [2026-11-03] Scheduled run time (`agx_task_execution.scheduled_at`).
   *  Non-empty = scheduled by `agx_schedule_task` (not fired immediately by a human clicking "run").
   *  Empty = dispatched immediately. Judge a "scheduled task" by this field, not by task_type. */
  scheduledAt?: string;
  status: RunStatus;
  /** [2026-11-06] Execution record creation time (`agx_task_execution.created_at`).
   *  Different from startedAt: queued/draft never started, but was definitely created.
   *  Time-based filters like "last 7 days" must use it to match the dashboard SQL semantics. */
  createdAt?: string;
  startedAt: string;
  finishedAt: string;
  durationSeconds: number;
  instruction: string;
  summary: string;
  /** Live output log (mock; the frontend appends on a timer to simulate) */
  log: string[];
  /* ── Manual intervention (computed by BFF, the frontend does not compute) ─────────
     The watchdog's wake/alert/resend each have a count limit; once used up and the task is still running,
     nobody pushes it anymore. These fields decide whether to show the "urge / remind to acknowledge" buttons. */
  /** Interventions available now (empty = no button) */
  availableActions?: NudgeAction[];
  /** Status bucket: a within expectation / b near timeout / c timed out / d receipt awaiting acknowledge / e alerted / - undeterminable */
  nudgeState?: string;
  /** Why this bucket (plain words, put straight into the tooltip) */
  nudgeReason?: string;
  /** Seconds since dispatched_at */
  ageSeconds?: number | null;
  /** Base field for age: dispatched_at / created_at (fallback for old data) / empty = undeterminable */
  ageBasis?: string;
  timeoutSeconds?: number;
  /** ageSeconds / timeoutSeconds */
  progressRatio?: number | null;
  /** How many times a **human** has urged it (event table manual_nudge, excluding the watchdog) */
  nudgeCount?: number;
  /** Time of the most recent manual urge 'YYYY-MM-DD HH:MM' */
  nudgeLastAt?: string;
}

/* ------------------------------------------------------------------ *
 * 6. Plan
 * ------------------------------------------------------------------ */
/* ★ 2026-11-03: aligned with the real source agx_plan.plan_status (verified in agentgeex_agent code).
   The old value running is never written by the code (it is actually called activated); pending is never written either
   (it used to be the DEFAULT). Nor can the fallback be "miss → treat as draft" — that would dress activated up as draft. */
export type PlanStatus =
  | "draft"         // Created, not activated
  | "activated"     // Activated, steps being released (running in the old design)
  | "cancelling"    // Cancelling
  | "cancelled"     // Cancelled
  | "completed"     // All steps closed
  | "archived"      // Archived (legacy)
  | "failed";       // Reserved, currently no write path

export interface PlanRow {
  id: string;                // plan_xxx
  name: string;
  projectId: string;
  projectName: string;
  owner: string;             // Owner agent_id
  ownerName: string;         // Owner display name
  trigger: string;           // Cron expression or "manual"
  status: PlanStatus;
  totalSteps: number;
  doneSteps: number;
  nextRunAt: string;
  updatedAt: string;
}

/* ------------------------------------------------------------------ *
 * 7. Skill shared library (reusable skills shared between Agents, agx_skill)
 * ------------------------------------------------------------------ */
export interface SkillRow {
  id: string;                 // skill_xxx
  name: string;               // Skill name
  category: string;           // Category (github/browser/search/...)
  description: string;        // One-line description
  content: string;            // Body (commands/usage/pitfalls/examples)
  tags: string;               // Tags (comma separated)
  sourceAgentId: string;      // Contributor agent_id
  sourceAgentName: string;    // Contributor name
  useCount: number;           // Use count
  helpfulCount: number;       // Helpful votes
  createdAt: string;
  updatedAt: string;
}

/* ------------------------------------------------------------------ *
 * 7.1 Host compute machine (agx_host_inventory)
 * ------------------------------------------------------------------ */
/**
 * [2026-11-04] Availability and access method of one agent platform on one host.
 * Source: `agx_host_inventory.platforms` (JSON array).
 *
 * ★ `mode` is the reason this structure exists, not decoration:
 *   - `process`   a process on the same host. Address = host endpoint_ip + port.
 *   - `container` container / separate network namespace. It **is a host of its own**;
 *                 connect to addr directly, **never concatenate endpoint_ip**.
 */
export interface HostPlatform {
  /** Platform identifier, same set as agent_type: openclaw | dsh | codex | claude_code | custom */
  platform: string;
  /** Display name (optional, defaults to platform) */
  name?: string;
  /** Address source: process=host address+port / container=use addr directly */
  mode: "process" | "container";
  /** Port when mode=process */
  port?: number;
  /**
   * Address. Both usages share this field:
   *   - mode=container: full address, e.g. `172.17.0.5:18789` (may include scheme)
   *   - mode=process  : optional "host part override", e.g. `10.0.0.11` or `test-host-mac.local`;
   *                    if empty, use host.endpoint_ip.
   *   ★ Why process needs it too: a host with endpoint_ip=127.0.0.1 is unreachable from other machines,
   *     and changing endpoint_ip would affect other readers; writing addr here has the smallest blast radius.
   */
  addr?: string;
  /** Session page path prefix, e.g. /chat */
  base_path?: string;
  /** Note (optional) */
  note?: string;
}

export interface HostRow {
  id: string;              // host_id (e.g. test_host_mac)
  alias: string;           // Host alias
  ip: string;              // endpoint_ip
  sshPort: number;         // ssh_port
  os: string;              // os_platform
  /** [2026-11-04] Platforms available on this host and how to reach them; unconfigured = [] (no address guessing) */
  platforms?: HostPlatform[];
  slotsLimit: number;      // task_slots_limit
  activeTasks: number;     // active_task_count
  active: boolean;         // active_status
  online: boolean;         // presence_status
  /** [2026-10-28] Number of active Agents on this host (backend counts from agx_agent_registry) */
  agentCount?: number;
  xDesc: string;           // Description
}

/* ------------------------------------------------------------------ *
 * 7.2 Notice Agent announcements / work logs (agx_agent_notice)
 * ------------------------------------------------------------------ */
export interface NoticeRow {
  id: string;              // notice_id（note_xxx）
  projectId: string;
  projectName: string;
  agentId: string;
  agentName: string;
  taskId: string;
  runUuid: string;
  type: string;            // log/decision/error/handoff/config/other
  title: string;
  content: string;         // Body (Markdown)
  visibility: string;      // private/project/network
  createdAt: string;
}

/* ================================================================== *
 *                            Seed data
 * ================================================================== */

export const projectGroupsSeed: ProjectGroupRow[] = [
  { id: "group_01", name: "出海电商组", memberCount: 7, projectCount: 2, status: "active",
    xDesc: "面向跨境出海电商的项目集群，含选品、文案、投放全链路。", updatedAt: "2026-09-28" },
  { id: "group_02", name: "内容工厂组", memberCount: 5, projectCount: 1, status: "active",
    xDesc: "内容生产项目集群：选题、撰写、审校流水线。", updatedAt: "2026-09-26" },
  { id: "group_03", name: "平台运维组", memberCount: 4, projectCount: 1, status: "paused",
    xDesc: "AgentGeeX 内核与主机运维项目集群。", updatedAt: "2026-09-20" },
];

export const projectsSeed: ProjectRow[] = [
  { id: "proj_b2ed70d5ac17", name: "赢立方 AgentGeeX", abbr: "ggx", platform: "openclaw",
    status: "active", agentCount: 8, adminAgentId: "agx_pm", groupId: "group_01", updatedAt: "2026-09-29" },
  { id: "proj_c31a9f04bb22", name: "选品与渠道分析", abbr: "sel", platform: "openclaw",
    status: "active", agentCount: 6, adminAgentId: "sel_pm", groupId: "group_01", updatedAt: "2026-09-27" },
  { id: "proj_d47e12ac9031", name: "内容工厂", abbr: "cnt", platform: "openclaw",
    status: "active", agentCount: 5, adminAgentId: "cnt_pm", groupId: "group_02", updatedAt: "2026-09-26" },
  { id: "proj_e55b80fd7744", name: "平台运维", abbr: "ops", platform: "custom",
    status: "paused", agentCount: 4, adminAgentId: "ops_pm", groupId: "group_03", updatedAt: "2026-09-20" },
];

export const agentsSeed: AgentRow[] = [
  { id: "agx_ui_lead", name: "UI 前端负责人", role: "worker", status: "online",
    model: "deepseek/deepseek-v4-flash", project_id: "proj_b2ed70d5ac17", host_id: "test_host_mac",
    sessionCount: 12, taskCount: 1, tokens: 88400, lastActiveAt: "2026-09-30 11:05" },
  { id: "agx_pm", name: "项目经理", role: "project_manager", status: "online",
    model: "deepseek/deepseek-v4-pro", project_id: "proj_b2ed70d5ac17", host_id: "test_host_mac",
    sessionCount: 34, taskCount: 6, tokens: 210300, lastActiveAt: "2026-09-30 10:58" },
  { id: "agx_core", name: "内核服务", role: "worker", status: "busy",
    model: "deepseek/deepseek-v4-pro", project_id: "proj_b2ed70d5ac17", host_id: "test_host_mac",
    sessionCount: 88, taskCount: 12, tokens: 1542000, lastActiveAt: "2026-09-30 11:10" },
  { id: "agx_host", name: "主机管理", role: "host_manager", status: "online",
    model: "deepseek/deepseek-v4-flash", project_id: "proj_b2ed70d5ac17", host_id: "test_host_mac",
    sessionCount: 9, taskCount: 3, tokens: 42300, lastActiveAt: "2026-09-30 09:41" },
  { id: "sel_pm", name: "选品项目经理", role: "project_manager", status: "online",
    model: "deepseek/deepseek-v4-pro", project_id: "proj_c31a9f04bb22", host_id: "test_host_mac",
    sessionCount: 21, taskCount: 5, tokens: 132900, lastActiveAt: "2026-09-30 10:20" },
  { id: "sel_hunter", name: "选题猎手", role: "worker", status: "online",
    model: "gpt-4o", project_id: "proj_c31a9f04bb22", host_id: "test_host_mac",
    sessionCount: 128, taskCount: 4, tokens: 421000, lastActiveAt: "2026-09-30 10:44" },
  { id: "sel_channel", name: "海外渠道运营", role: "worker", status: "offline",
    model: "qwen-max", project_id: "proj_c31a9f04bb22", host_id: "test_host_mac",
    sessionCount: 54, taskCount: 3, tokens: 210300, lastActiveAt: "2026-09-28 17:12" },
  { id: "cnt_writer", name: "文案生成器", role: "worker", status: "error",
    model: "claude-3.5", project_id: "proj_d47e12ac9031", host_id: "test_host_mac",
    sessionCount: 96, taskCount: 4, tokens: 882000, lastActiveAt: "2026-09-30 08:30" },
  { id: "cnt_review", name: "合规审核", role: "worker", status: "online",
    model: "gpt-4o-mini", project_id: "proj_d47e12ac9031", host_id: "test_host_mac",
    sessionCount: 12, taskCount: 2, tokens: 54000, lastActiveAt: "2026-09-30 09:55" },
  { id: "ops_clean", name: "数据清洗", role: "worker", status: "offline",
    model: "qwen-max", project_id: "proj_e55b80fd7744", host_id: "test_host_mac",
    sessionCount: 301, taskCount: 3, tokens: 1560000, lastActiveAt: "2026-09-25 14:02" },
];

export const sessionsSeed: SessionRow[] = [
  { id: "agent:agx_ui_lead:main", name: "UI 前端负责人·主会话", agentId: "agx_ui_lead",
    agentName: "UI 前端负责人", status: "active", messageCount: 142, lastActiveAt: "2026-09-30 11:05" },
  { id: "agent:agx_core:main", name: "内核服务·主会话", agentId: "agx_core",
    agentName: "内核服务", status: "active", messageCount: 388, lastActiveAt: "2026-09-30 11:10" },
  { id: "agent:sel_hunter:main", name: "选题猎手·主会话", agentId: "sel_hunter",
    agentName: "选题猎手", status: "active", messageCount: 216, lastActiveAt: "2026-09-30 10:44" },
  { id: "agent:cnt_writer:main", name: "文案生成器·主会话", agentId: "cnt_writer",
    agentName: "文案生成器", status: "idle", messageCount: 97, lastActiveAt: "2026-09-30 08:30" },
  { id: "agent:sel_channel:main", name: "海外渠道运营·主会话", agentId: "sel_channel",
    agentName: "海外渠道运营", status: "archived", messageCount: 63, lastActiveAt: "2026-09-28 17:12" },
  { id: "agent:agx_pm:main", name: "项目经理·主会话", agentId: "agx_pm",
    agentName: "项目经理", status: "active", messageCount: 254, lastActiveAt: "2026-09-30 10:58" },
];

export const tasksSeed: TaskRow[] = [
  { id: "task_1001", name: "ui_lead", description: "UI 前端负责人角色任务：Win3 商业控制台前端建设",
    agentId: "agx_ui_lead", agentName: "UI 前端负责人", labs: "ui_lead", isPrivate: 0, activeStatus: 1, updatedAt: "2026-09-30" },
  { id: "task_1002", name: "project_manage", description: "项目管理：项目巡检、成员调度、日志汇总",
    agentId: "agx_pm", agentName: "项目经理", labs: "general", isPrivate: 1, activeStatus: 1, updatedAt: "2026-09-29" },
  { id: "task_1003", name: "agent_create", description: "创建 / 克隆 / 配置 Agent",
    agentId: "agx_core", agentName: "内核服务", labs: "agent_create", isPrivate: 0, activeStatus: 1, updatedAt: "2026-09-28" },
  { id: "task_1004", name: "agent_deploy", description: "将 Agent 配置物化到文件系统并注册 OpenClaw",
    agentId: "agx_host", agentName: "主机管理", labs: "agent_deploy", isPrivate: 0, activeStatus: 1, updatedAt: "2026-09-28" },
  { id: "task_1005", name: "daily_topic", description: "每日选题生成：抓取热榜并产出标题候选",
    agentId: "sel_hunter", agentName: "选题猎手", labs: "general", isPrivate: 0, activeStatus: 1, updatedAt: "2026-09-30" },
  { id: "task_1006", name: "copy_batch", description: "商品文案批量生成",
    agentId: "cnt_writer", agentName: "文案生成器", labs: "general", isPrivate: 0, activeStatus: 1, updatedAt: "2026-09-30" },
  { id: "task_1007", name: "compliance_check", description: "文案合规检查：敏感词与违禁表述审查",
    agentId: "cnt_review", agentName: "合规审核", labs: "general", isPrivate: 0, activeStatus: 1, updatedAt: "2026-09-29" },
  { id: "task_1008", name: "data_dedup", description: "类目数据去重与归一",
    agentId: "ops_clean", agentName: "数据清洗", labs: "general", isPrivate: 0, activeStatus: 0, updatedAt: "2026-09-25" },
];

export const executionsSeed: ExecutionRow[] = [
  { run_uuid: "run_a1b2c3", execName: "每日选题生成", taskId: "task_1005", taskName: "daily_topic",
    agentId: "sel_hunter", agentName: "选题猎手", sessionId: "agent:sel_hunter:main",
    status: "succeeded", startedAt: "2026-09-30 09:12", finishedAt: "2026-09-30 09:12:42",
    durationSeconds: 42, instruction: "抓取今日热榜，产出 10 条标题候选", summary: "产出 10 条候选，已写入会话。",
    log: ["[09:12:01] 开始执行…", "[09:12:10] 抓取热榜 120 条", "[09:12:38] 生成候选 10 条", "[09:12:42] 完成"] },
  { run_uuid: "run_d4e5f6", execName: "商品文案批量生成", taskId: "task_1006", taskName: "copy_batch",
    agentId: "cnt_writer", agentName: "文案生成器", sessionId: "agent:cnt_writer:main",
    status: "running", startedAt: "2026-09-30 09:20", finishedAt: "",
    durationSeconds: 0, instruction: "为 20 个 SKU 生成商品详情文案", summary: "生成中…",
    log: ["[09:20:03] 开始执行…", "[09:21:40] 已完成 8/20", "[09:22:55] 已完成 13/20"] },
  { run_uuid: "run_g7h8i9", execName: "文案合规检查", taskId: "task_1007", taskName: "compliance_check",
    agentId: "cnt_review", agentName: "合规审核", sessionId: "agent:cnt_writer:main",
    status: "failed", startedAt: "2026-09-30 09:22", finishedAt: "2026-09-30 09:22:08",
    durationSeconds: 8, instruction: "审查上一步产出的文案合规性", summary: "失败：前置任务未完成，输入为空。",
    log: ["[09:22:00] 开始执行…", "[09:22:08] 错误：输入为空 (EMPTY_INPUT)"] },
  { run_uuid: "run_j1k2l3", execName: "广告出价调优", taskId: "task_1005", taskName: "daily_topic",
    agentId: "sel_channel", agentName: "海外渠道运营", sessionId: "agent:sel_channel:main",
    status: "succeeded", startedAt: "2026-09-30 08:55", finishedAt: "2026-09-30 08:56:12",
    durationSeconds: 72, instruction: "根据昨日投放数据调整出价", summary: "完成，平均出价下调 6%。",
    log: ["[08:55:00] 开始执行…", "[08:56:12] 完成"] },
  { run_uuid: "run_m4n5o6", execName: "类目数据去重", taskId: "task_1008", taskName: "data_dedup",
    agentId: "ops_clean", agentName: "数据清洗", sessionId: "agent:ops_clean:main",
    status: "succeeded", startedAt: "2026-09-30 08:40", finishedAt: "2026-09-30 08:45:01",
    durationSeconds: 301, instruction: "对 3.2 万条类目记录去重", summary: "去重 1204 条，归一 380 条。",
    log: ["[08:40:00] 开始执行…", "[08:45:01] 完成"] },
  { run_uuid: "run_p7q8r9", execName: "UI 计划编排", taskId: "task_1001", taskName: "ui_lead",
    agentId: "agx_ui_lead", agentName: "UI 前端负责人", sessionId: "agent:agx_ui_lead:main",
    status: "pending_confirm", startedAt: "2026-09-30 11:00", finishedAt: "2026-09-30 11:06:00",
    durationSeconds: 360, instruction: "输出 AgentGeeX 管理后台 UI 开发计划，待产品负责人确认",
    summary: "计划已产出，等待人工确认后开工。",
    log: ["[11:00:00] 开始…", "[11:06:00] 产出计划，等待确认"] },
];

export const plansSeed: PlanRow[] = [
  { id: "plan_001", name: "每日选题流水线", projectId: "proj_c31a9f04bb22", projectName: "选品与渠道分析", owner: "agx_ui_lead", ownerName: "UI 前端负责人",
    trigger: "0 9 * * *", status: "activated", totalSteps: 4, doneSteps: 2,
    nextRunAt: "2026-10-01 09:00", updatedAt: "2026-09-30" },
  { id: "plan_002", name: "文案生产-审校闭环", projectId: "proj_d47e12ac9031", projectName: "内容工厂", owner: "agx_ui_lead", ownerName: "UI 前端负责人",
    trigger: "0 */4 * * *", status: "activated", totalSteps: 6, doneSteps: 3,
    nextRunAt: "2026-09-30 12:00", updatedAt: "2026-09-30" },
  { id: "plan_003", name: "平台巡检", projectId: "proj_e55b80fd7744", projectName: "平台运维", owner: "agx_core", ownerName: "平台内核",
    trigger: "0 2 * * *", status: "cancelled", totalSteps: 3, doneSteps: 1,
    nextRunAt: "—", updatedAt: "2026-09-20" },
  { id: "plan_004", name: "Win3 前端建设", projectId: "proj_b2ed70d5ac17", projectName: "赢立方 AgentGeeX", owner: "agx_ui_lead", ownerName: "UI 前端负责人",
    trigger: "手动", status: "draft", totalSteps: 7, doneSteps: 0,
    nextRunAt: "—", updatedAt: "2026-09-30" },
];

export const skillsSeed: SkillRow[] = [
  { id: "skill_eeefd7bf5024", name: "GitHub CLI (gh) 仓库与 CI 查询", category: "github",
    description: "用 gh 命令查询仓库、PR、CI 状态", content: "`gh pr list` / `gh run view`",
    tags: "github,cli,ci", sourceAgentId: "agx_core", sourceAgentName: "agx_core",
    useCount: 2, helpfulCount: 3, createdAt: "2026-10-07 11:50", updatedAt: "2026-10-07 11:50" },
  { id: "skill_352c74f23337", name: "Tavily CLI (tvly) 联网检索", category: "search",
    description: "联网搜索 / 网页正文提取 / 多源深度研究", content: "`tvly search \"关键词\"` / `tvly extract URL`",
    tags: "search,tavily,research", sourceAgentId: "agx_core", sourceAgentName: "agx_core",
    useCount: 2, helpfulCount: 2, createdAt: "2026-10-07 11:50", updatedAt: "2026-10-07 11:50" },
  { id: "skill_2ce1eae06786", name: "Playwright 无头浏览器", category: "browser",
    description: "无头浏览器自动化与截图验证", content: "node -e '...chromium.launch()...'",
    tags: "browser,playwright,test", sourceAgentId: "agx_core", sourceAgentName: "agx_core",
    useCount: 0, helpfulCount: 1, createdAt: "2026-10-06 22:30", updatedAt: "2026-10-06 22:30" },
];

export const hostsSeed: HostRow[] = [
  { id: "test_host_mac", alias: "test_host_mac", ip: "10.0.0.11", sshPort: 22,
    os: "Darwin arm64", slotsLimit: 8, activeTasks: 0, active: true, online: true, xDesc: "本机开发/运行主机" },
];

export const noticesSeed: NoticeRow[] = [
  { id: "note_seed_01", projectId: "proj_b2ed70d5ac17", projectName: "赢立方 AgentGeeX",
    agentId: "agx_ui_lead", agentName: "UI 前端负责人", taskId: "", runUuid: "",
    type: "log", title: "Win3 技能共享库上线", content: "技能共享库页面完成。",
    visibility: "project", createdAt: "2026-10-08 10:00" },
];

/* ================================================================== *
 *                            Dashboard aggregate stats
 * ================================================================== */
/** Live session state summary (BFF /v1/dashboard/stats → sessions.runtime) */
export interface SessionRuntimeStats {
  /** Entity count = primary key area + cross-host quarantine area (the same key on multiple machines is a different entity) */
  total: number;
  /** Deduplicated session_key count */
  uniqueKeys: number;
  /** Quarantine row count (cross-host duplicate names) */
  conflicts: number;
  byStatus: { running: number; idle: number; failed: number; stalled: number; unknown: number };
  byHost: {
    hostId: string;
    total: number;
    running: number; idle: number; failed: number; stalled: number; unknown: number;
  }[];
  contextBuckets: { key: string; label: string; count: number }[];
  topRisk: {
    sessionKey: string;
    sessionName: string;
    hostId: string;
    agentId: string;
    contextRatio: number;
    status: string;
  }[];
  hosts: string[];
  generatedAt: string;
  cacheSeconds?: number;
  /** "redis" = ok; "unavailable" = Redis unreadable (all numbers are 0, do not read it as "no sessions") */
  backend?: string;
  error?: string;
}

/* ── Host scale stats (2026-11-05 Feng) ─────────────────────
 * "Every host has agent, session and executed task data on it"
 *
 * ★ The three columns come from three different tables with different semantics; the UI must let people tell them apart:
 *   Agent   = agx_agent_registry.host_id (direct ownership)
 *   Session = derived from agx_session via agent (that table has **no** host_id column)
 *   Task    = agx_task_execution.host_id (execution host, not via agent)
 * ★ sessionCount (registered, incl. archived) and liveSessions (gateway online) are two different measures;
 *   measured 4145 vs 2406 — not a data error, but two questions: "how many were ever registered" vs "how many exist now".
 */
export interface HostStat {
  hostId: string;
  alias: string;
  platform: string;
  /** Raw DB column presence_status. ★ Unreliable: the daemon only writes 1, never 0, so it is always true.
   *  The real online criterion is heartbeatAgeSeconds. */
  online: boolean;
  active: boolean;
  /** [2026-11-06] Last heartbeat time (ISO). The daemon writes it every 30s. */
  lastHeartbeat?: string;
  /** [2026-11-06] Seconds since the last heartbeat (computed by server NOW(), avoiding frontend timezone error).
   *  null = never heartbeated (not "0 seconds ago"). Green dot rule: <=90s online / <=3600s unreachable / else offline. */
  heartbeatAgeSeconds?: number | null;
  /** Concurrent task slot limit */
  slots: number;
  agentCount: number;
  pmCount: number;
  /** Agents with unfinished deployment (deploy_status=pending) */
  pendingDeploy: number;
  /** Registered session count (MySQL, incl. archived) */
  sessionCount: number;
  /** Online session count on the gateway side (Redis) */
  liveSessions: number;
  liveRunning: number;
  liveIdle: number;
  liveFailed: number;
  taskCount: number;
  runningTasks: number;
  failedTasks24h: number;
}

export interface HostStatsBlock {
  total: number;
  /** presence_status semantics (always equals total, unreliable, kept only for old code) */
  online: number;
  /** Machines with a heartbeat in the last 90 seconds (truly online) */
  onlineByHeartbeat?: number;
  byHost: HostStat[];
  totals: {
    agentCount: number;
    sessionCount: number;
    liveSessions: number;
    taskCount: number;
    runningTasks: number;
    slots: number;
  };
  /** Historical tasks with empty host_id (ownership unknown, listed separately, not pushed into any host) */
  unassignedTasks: number;
  note: string;
}

/* ── Org stats (2026-11-05 Feng) ─────────────────────────────
 * "Org, org sessions, executed tasks, agent — all of this needs stats"
 *
 * ★ An org's "members" are **template Agents**; the ones that do real work are their clone instances (peer_agent_id).
 *   Counting only templates → 0 sessions, 0 tasks (templates never work), and the stats become useless.
 *   So agentCount = templates + all derived instances (computed in the closure).
 * ★ Members can span orgs (one template belongs to several orgs) → per-org numbers sum to more than totals; that is correct.
 */
export interface OrgStat {
  groupId: string;
  groupName: string;
  /** Built-in core org (agx-core-group), viewed separately from business orgs */
  builtin: boolean;
  /** Present in the member table but no row in the main table agx_agent_group (org master record missing, needs fixing) */
  orphan: boolean;
  /** All Agents that can actually work = templates + derived instances */
  agentCount: number;
  templateCount: number;
  instanceCount: number;
  sessionCount: number;
  taskCount: number;
  runningTasks: number;
  members: { id: string; name: string; role: string; hostId: string }[];
  instances: { id: string; name: string; hostId: string }[];
}

export interface OrgStatsBlock {
  total: number;
  builtin: number;
  business: number;
  /** Org ids missing a master record */
  orphan: string[];
  byOrg: OrgStat[];
  /** Global dedup (not equal to the sum of per-org numbers) */
  totals: { agentCount: number; sessionCount: number; taskCount: number; runningTasks: number };
  /** Agents belonging to >1 org — why per-org sums exceed the global total */
  sharedAgents: number;
  overlapNote: string;
}

export interface DashboardStats {
  projects: { active: number; paused: number; archived: number; healthScore: number; total?: number };
  /* ★ 2026-11-03: the field switched to the real source agx_plan.plan_status (the old running meant
     "plans that have running executions", a different thing from the plan's own status; removed). */
  plans: {
    activated: number;
    cancelling: number;
    draft: number;
    completed: number;
    cancelled: number;
    archived: number;
    failed: number;
    total?: number;
    nextRunAt?: string;
  };
  tasks: {
    /** [2026-11-03 21:33] True values in one-to-one correspondence with agx_task_execution.run_status.
     *  [2026-11-06] added awaiting_confirm (awaiting human confirmation) → 8 values. */
    byStatus?: {
      pending: number; running: number;
      awaiting_confirm: number;
      succeeded: number;
      failed: number; cancelled: number; archived: number; draft: number;
    };
    /** Internal breakdown of pending (plan A): queued for the previous step vs waiting for the scheduled time */
    pendingSplit?: { queued: number; scheduled: number };
    pending: number; succeeded24h: number; failed24h: number; running: number;
  };
  /** Host-dimension stats (2026-11-05, main dashboard display) */
  hostStats?: HostStatsBlock;
  /** Org-dimension stats (2026-11-05; no longer takes dashboard space after Feng corrected his slip, the API still provides it) */
  organizations?: OrgStatsBlock;
  agentMetrics: {
    online: number; idle?: number; busy: number; offline: number; error: number; total?: number;
    /** [2026-11-06] top entries also carry agentId — clicking a ranking cell filters execution records by agent;
     *  reverse lookup by name would hit the wrong person due to duplicate/renamed names. */
    top: { agentId?: string; name: string; calls: number }[];
  };
  sessions: {
    /** Active session count in MySQL (historical measure, incl. archived sessions, not necessarily equal to runtime.total) */
    active: number;
    trend: { date: string; count: number }[];
    /** Real-time summary on the gateway side (added 2026-11-05) */
    runtime?: SessionRuntimeStats;
  };
  /** Execution trend for the last 7 days */
  runsTrend: { date: string; runs: number; tokens: number }[];
  /** Agent role distribution */
  roleDistribution: { name: string; value: number }[];
  /** [2026-10-28] Live dashboard: server generation time */
  generatedAt?: string;
  /** Data source diagnostics (engine / Redis running cross-check) */
  sources?: { engine?: string; redisRunning?: number | null; redisIndexPartial?: boolean };
}

export const dashboardStatsSeed: DashboardStats = {
  projects: { active: 3, paused: 1, archived: 0, healthScore: 92 },
  plans: { activated: 2, cancelling: 0, draft: 1, completed: 0, cancelled: 1, archived: 0, failed: 0 },
  tasks: {
    byStatus: { pending: 12, running: 3, awaiting_confirm: 2, succeeded: 148, failed: 6, cancelled: 4, archived: 1, draft: 9 },
    pendingSplit: { queued: 9, scheduled: 3 },
    pending: 12, succeeded24h: 148, failed24h: 6, running: 3,
  },
  agentMetrics: {
    online: 7, busy: 1, offline: 2, error: 1,
    top: [
      { agentId: "agx_core", name: "内核服务", calls: 388 },
      { name: "选题猎手", calls: 216 },
      { name: "项目经理", calls: 254 },
      { name: "文案生成器", calls: 97 },
      { name: "UI 前端负责人", calls: 142 },
    ],
  },
  sessions: {
    active: 4,
    trend: [
      { date: "09-24", count: 12 }, { date: "09-25", count: 15 }, { date: "09-26", count: 11 },
      { date: "09-27", count: 18 }, { date: "09-28", count: 16 }, { date: "09-29", count: 21 },
      { date: "09-30", count: 19 },
    ],
  },
  runsTrend: [
    { date: "09-24", runs: 18, tokens: 12400 }, { date: "09-25", runs: 22, tokens: 15100 },
    { date: "09-26", runs: 19, tokens: 13900 }, { date: "09-27", runs: 27, tokens: 19800 },
    { date: "09-28", runs: 24, tokens: 17600 }, { date: "09-29", runs: 30, tokens: 22100 },
    { date: "09-30", runs: 26, tokens: 17400 },
  ],
  roleDistribution: [
    { name: "worker", value: 6 },
    { name: "project_manager", value: 2 },
    { name: "host_manager", value: 1 },
    { name: "workshop_manager", value: 0 },
    { name: "agent_manager", value: 1 },
  ],
};

/* ================================================================== *
 * Runtime data store (Store)
 * ================================================================== *
 * Design (2026-10-01 "read-only wiring to the real backend"):
 *   - The *Seed arrays above = fallback seed data (used when the backend is unreachable, so the UI can still render).
 *   - The `store` below = live runtime data. At SPA startup, providers/agentgeex.ts
 *     calls hydrateFromApi() to load real data from the BFF.
 *   - Pages still read the same-named arrays/functions; **page code changes zero**.
 */
export const store = {
  agents: [...agentsSeed] as AgentRow[],
  sessions: [...sessionsSeed] as SessionRow[],
  tasks: [...tasksSeed] as TaskRow[],
  executions: [...executionsSeed] as ExecutionRow[],
  projects: [...projectsSeed] as ProjectRow[],
  projectGroups: [...projectGroupsSeed] as ProjectGroupRow[],
  plans: [...plansSeed] as PlanRow[],
  skills: [...skillsSeed] as SkillRow[],
  hosts: [...hostsSeed] as HostRow[],
  notices: [...noticesSeed] as NoticeRow[],
  quickEntries: [] as QuickEntryRow[],
  dashboardStats: { ...dashboardStatsSeed } as DashboardStats,
  /** Data source: "mock" | "api" */
  source: "mock" as "mock" | "api",
  hydratedAt: "",
};

/** Replace runtime data with the BFF snapshot. Keep the seed fallback for fields that are missing. */
export interface Snapshot {
  agents?: AgentRow[];
  sessions?: SessionRow[];
  tasks?: TaskRow[];
  executions?: ExecutionRow[];
  projects?: ProjectRow[];
  projectGroups?: ProjectGroupRow[];
  plans?: PlanRow[];
  skills?: SkillRow[];
  hosts?: HostRow[];
  notices?: NoticeRow[];
  quickEntries?: QuickEntryRow[];
  dashboard?: Partial<DashboardStats>;
}

export function hydrate(snap: Snapshot): void {
  // Only overwrite when the backend really returns the field (undefined = API did not provide it, keep the previous state);
  // An empty array [] is a real result (truly no data) and should be overwritten as-is; seed mock must not fake data.
  if (snap.agents) store.agents = snap.agents;
  if (snap.sessions) store.sessions = snap.sessions;
  if (snap.tasks) store.tasks = snap.tasks;
  if (snap.executions) store.executions = snap.executions;
  if (snap.projects) store.projects = snap.projects;
  if (snap.projectGroups) store.projectGroups = snap.projectGroups;
  if (snap.plans) store.plans = snap.plans;
  if (snap.skills) store.skills = snap.skills;
  if (snap.hosts) store.hosts = snap.hosts;
  if (snap.notices) store.notices = snap.notices;
  if (snap.quickEntries) store.quickEntries = snap.quickEntries;
  if (snap.dashboard) store.dashboardStats = { ...store.dashboardStats, ...snap.dashboard };
  store.source = "api";
  store.hydratedAt = new Date().toISOString();
}

/* ------------------------------------------------------------------ *
 * Convenience queries (assembled by the provider) — read the runtime store
 * ------------------------------------------------------------------ */
export function sessionsOf(agentId: string): SessionRow[] {
  return store.sessions.filter((s) => s.agentId === agentId);
}
export function tasksOf(agentId: string): TaskRow[] {
  return store.tasks.filter((t) => t.agentId === agentId);
}
export function executionsOf(agentId: string): ExecutionRow[] {
  return store.executions.filter((e) => e.agentId === agentId);
}
export function projectName(id: string): string {
  // [2026-10-28 Feng] "A project id means nothing, show the project name"
  //   if not found return an empty string and let the caller decide the fallback text — never show proj_xxx as a name.
  return store.projects.find((p) => p.id === id)?.name ?? "";
}

/* Compatibility aliases: existing references `agents` / `sessions` … point to the live runtime data (getters, updated by hydrate) */
export const agents = { get current(): AgentRow[] { return store.agents; } };
export const sessions = { get current(): SessionRow[] { return store.sessions; } };
export const tasks = { get current(): TaskRow[] { return store.tasks; } };
export const executions = { get current(): ExecutionRow[] { return store.executions; } };
export const projects = { get current(): ProjectRow[] { return store.projects; } };
export const projectGroups = { get current(): ProjectGroupRow[] { return store.projectGroups; } };
export const plans = { get current(): PlanRow[] { return store.plans; } };
export const skills = { get current(): SkillRow[] { return store.skills; } };
export const hosts = { get current(): HostRow[] { return store.hosts; } };
export const notices = { get current(): NoticeRow[] { return store.notices; } };
export const quickEntries = { get current(): QuickEntryRow[] { return store.quickEntries; } };
