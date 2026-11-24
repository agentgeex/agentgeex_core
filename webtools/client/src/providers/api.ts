/**
 * providers/api.ts — Win3 BFF REST client (the single outbound gateway)
 *
 * Positioning (continues agent_doc/architecture.md §3):
 *   - This file is the **only exit** from the frontend to the backend. Page layer never fetches directly;
 *     data flows through `providers/agentgeex.ts` (semantic hook) → this file → BFF.
 *   - Backend address comes from env var `VITE_API_URL` (default http://127.0.0.1:8090/v1).
 *   - Auth: after login the JWT is stored in localStorage, requests auto-carry `Authorization: Bearer`.
 *
 * This round (2026-10-01) is 'read-only wiring': GET only; write operations wait for the second batch.
 */

import { readKey, writeKey, removeKey } from "@/lib/storage-keys";

export const API_BASE: string =
  (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ||
  "http://127.0.0.1:8090/v1";

/** Service root (without the /v1 prefix) — for root-level endpoints such as /readyz, /healthz. */
export const API_ORIGIN: string = API_BASE.replace(/\/v\d+$/, "");

/* Key names and migration strategy are registered in one place: lib/storage-keys.ts (old keys from the win3 era are still readable → upgrading does not drop the login state). */
export function getToken(): string {
  return readKey("token") || "";
}

export function setSession(token: string, user: unknown): void {
  writeKey("token", token);
  writeKey("user", JSON.stringify(user ?? {}));
}

export function clearSession(): void {
  removeKey("token");
  removeKey("user");
}

/**
 * [2026-10-20] Locally decide whether the JWT has already expired.
 *
 * Why it is needed: `ensureDemoSession()` previously only checked 'is there a token',
 * not 'is it expired' → 12h later the token is still in localStorage but the server answers 401,
 * the startup flow still skips login → /snapshot 401 → silent fallback to seed mock (the 'only 4 projects' Feng saw).
 *
 * @param skewSeconds early margin (default 60s), so requests sitting exactly on the boundary are not wasted.
 * @returns true=expired, or unparseable and clearly expired; unparseable (bad format) is treated as 'not expired' and left to the server.
 */
export function isTokenExpired(skewSeconds = 60): boolean {
  const token = getToken();
  if (!token) return true;
  const part = token.split(".")[1];
  if (!part) return false; // Not a JWT (placeholder token) → do not block, let the server decide
  try {
    const b64 = part.replace(/-/g, "+").replace(/_/g, "/");
    const json = JSON.parse(atob(b64 + "=".repeat((-b64.length % 4 + 4) % 4)));
    const exp = Number(json?.exp ?? 0);
    if (!exp) return false; // No exp → do not block
    return exp - skewSeconds <= Math.floor(Date.now() / 1000);
  } catch {
    return false; // Parse failed → do not block
  }
}

export function getUser<T = any>(): T | null {
  const raw = readKey("user");
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export interface ApiError extends Error {
  status: number;
  code: string;
  details?: unknown;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (init.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    let code = `HTTP_${res.status}`;
    let message = res.statusText;
    let details: unknown;
    try {
      const body = await res.json();
      if (body?.error) {
        code = body.error.code ?? code;
        message = body.error.message ?? message;
        /* [2026-11-06] Error details must come through too — for some codes the **same message does not hold
           across different causes**: e.g. 409 STATE_MISMATCH is reported both when it was 'already approved'
           and when it 'never needed confirmation' — the former means 'all good, leave it', the latter means
           'your expectation was wrong'. Without the details the UI can only say vaguely
           'someone may have approved it first', and people go chasing a problem that does not exist. */
        details = body.error.details;
      }
    } catch {
      /* Non-JSON error body, keep the default */
    }
    const err = new Error(message) as ApiError;
    err.status = res.status;
    err.code = code;
    err.details = details;
    throw err;
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** GET, returns `{ data, total }` (total comes from X-Total-Count). */
export async function getList<T = any>(path: string): Promise<{ data: T[]; total: number }> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}${path}`, { headers });
  if (!res.ok) {
    const err = new Error(`GET ${path} → ${res.status}`) as ApiError;
    err.status = res.status;
    err.code = `HTTP_${res.status}`;
    throw err;
  }
  const total = Number(res.headers.get("X-Total-Count") ?? "0");
  const data = (await res.json()) as T[];
  return { data, total };
}

/**
 * Speech to text.
 * Accepts **raw audio binary** (no multipart wrapper): there is only one field, a form just adds another failure surface.
 * Backend `POST /v1/audio/transcribe` → `{ text, duration_ms, bytes, engine }`.
 */
export const audioApi = {
  transcribe: (blob: Blob) =>
    request<{ text: string; duration_ms: number; bytes: number; engine?: string }>(
      "/audio/transcribe",
      {
        method: "POST",
        body: blob,
        headers: { "Content-Type": blob.type || "application/octet-stream" },
      }
    ),
  /* Reads the BFF background probe result (does not hit the service) — fields in server/app/asr.py::snapshot */
  health: () =>
    request<{
      ok: boolean;
      state: "unknown" | "up" | "down";
      url: string;
      error?: string;
      fail_streak?: number;
      checked_ago_ms?: number | null;
      up_ago_ms?: number | null;
    }>("/audio/health"),
};

/**
 * Voice hosts (multiple).
 *
 * ★ Why 'which ones exist' and 'which one is used' are separated:
 *     The host list lives in server-side `.env` (a deployment fact; restart after changes);
 *     the current selection lives in a local preference file (takes effect the moment a human clicks).
 *   The frontend reads both, but **only the selection is writable** — the UI must not conjure a host out of thin air.
 */
export interface AsrHost {
  id: string;
  name: string;
  url: string;
  note: string;
  /** Whether it is the current selection (computed server-side; the frontend no longer compares ids itself) */
  active?: boolean;
  ok: boolean;
  state: "unknown" | "up" | "down";
  error: string;
  fail_streak: number;
  checked_ago_ms?: number | null;
  up_ago_ms?: number | null;
}

export interface AsrHostsResult {
  active: string;
  active_url: string;
  hosts: AsrHost[];
}

export const api = {
  get: <T = any>(path: string) => request<T>(path),
  post: <T = any>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  put: <T = any>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body ? JSON.stringify(body) : undefined }),
  patch: <T = any>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }),
  delete: <T = any>(path: string) => request<T>(path, { method: "DELETE" }),
};

/* ----------------------------- Endpoint wrappers ----------------------------- */

export interface LoginResult {
  token: string;
  user: { id: string; name: string; role: string };
}

export const authApi = {
  login: (username: string, password: string) =>
    api.post<LoginResult>("/auth/login", { username, password }),
  logout: () => api.post<void>("/auth/logout"),
  me: () => api.get<{ id: string; name: string; role: string; tenant_id?: string }>("/auth/me"),
};

export const healthApi = {
  readyz: () => api.get<{ status: string; kernel: { mysql: boolean; redis: boolean } }>("/readyz"),
};

/* [2026-10-29 Feng] MCP version self-check: login dropdown → 'test'.
   What matters is 'which version is the MCP process the BFF is connected to right now', not the file mtime.
   mcp_version is a constant in the kernel code → if the process was not restarted it stays the old string, visible at a glance. */
export interface McpServicesInfo {
  mysql_check_info?: Record<string, unknown> & {
    status?: string; message?: string; database?: string;
    server_version?: string; table_check?: string; endpoint?: string;
  };
  redis_check_info?: Record<string, unknown> & {
    status?: string; message?: string; redis_version?: string; endpoint?: string;
    db_index?: number;
    cache_stats?: Record<string, number>;
    /** Hand-written release note (a human's note) — does not follow the code; do not use it to judge new vs old */
    mcp_version?: string;
    mcp_sha?: string;
    /* [2026-10-29 22:06 kernel bd84c39] Automatic build fingerprint: the authoritative answer to
       'which build is running in production'. Uses the short hash when git is available; marks dirty if the
       worktree is dirty; degrades to mtime without git.
       ★ A different axis from mcp_version: that one is 'what was intentionally released last time',
       this one is 'what is on disk right now'. */
    build_sha?: string;
    /** true=uncommitted changes exist (not running HEAD); false=clean; null/undefined=unknowable */
    build_dirty?: boolean | null;
    /** git | nogit */
    build_source?: string;
    /** mtime of this file */
    build_at?: string | null;
    /** One-line plain-language description (e.g. 'bd84c39, code matches HEAD') */
    build_detail?: string;
    permissions_sha?: string;
    permissions_updated_at?: string;
    runtime_timestamp?: string;
  };
  /** Fallback when the kernel return value is not a JSON object (no silent degradation: show what exists, as is) */
  raw?: unknown;
}

/** New/old judgement of 'kernel code vs MCP process' — far more reliable than the mcp_version constant */
export interface McpCodeFreshness {
  kernel_file: string;
  /** Last modification time of the kernel source file */
  kernel_mtime: string | null;
  /** Key names sse_* are legacy; semantics = 'the MCP process the BFF is actually connected to' */
  sse_pid: number | null;
  /** Start time of that MCP process */
  sse_started_at: string | null;
  /** Endpoint the BFF actually connects to (since 2026-11-04 a shared gateway, no longer a self-hosted SSE instance) */
  endpoint?: string;
  port?: number;
  /** Where the pid comes from: port=found via port (trusted) / pidfile=legacy fallback */
  pid_source?: "port" | "pidfile";
  /** Listening processes on the same port (>1 means old and new coexist; cleanup needed) */
  listeners?: number[];
  /** true=process is older than the code (old code in memory); null=cannot tell */
  stale: boolean | null;
  verdict: string;
}

export interface McpCheckResult {
  /** Time the BFF fetched this result (not the MCP process start time) */
  checked_at: string;
  checked_by: string;
  /** Key name kept for old callers; value = the MCP endpoint the BFF is currently connected to */
  mcp_sse_url: string;
  mcp_transport?: string;
  services: McpServicesInfo;
  code_freshness?: McpCodeFreshness;
}

export const mcpApi = {
  checkServices: () => api.get<McpCheckResult>("/mcp/check-services"),
};

/**
 * Service settings (today only voice hosts; more services later).
 *
 * ★ Why not `/audio/*`: this endpoint takes the 'settings window' view (what I can choose from, which one is
 *   selected), while `/audio/health` takes the 'can the voice component record right now' view. Both read the
 *   same in-memory state, but they mean different things.
 */
export const settingsApi = {
  /** All voice hosts + each one's availability (reads the background probe result, does not hit the service) */
  asrHosts: () => api.get<AsrHostsResult>("/settings/asr"),
  /** Switch which host is used (the server also probes once on the way and returns the new state) */
  setAsrActive: (hostId: string) => api.put<AsrHostsResult>("/settings/asr", { host_id: hostId }),
  /** Force-probe one host (a single WS handshake) */
  probeAsrHost: (hostId: string) =>
    api.post<AsrHostsResult>(`/settings/asr/${encodeURIComponent(hostId)}/probe`),
};

/* --------------------------- Operations (writes / actions) --------------------------- */
export interface RunTaskResult {
  dispatched: boolean;
  run_uuid: string;
  session_key: string;
  /** [2026-10-11] Whether this is a new session (frontend picks 'new session' → no receiver session id passed, the system creates one) */
  new_session?: boolean;
  send_session_key?: string;
  msg_id?: string;
  queue?: string;
  queue_length?: number | null;
  target_agent: string;
  caller_agent_id?: string;
  actor_user?: string;
  task_ref: { task_id: string; task_name: string };
}

export interface RunTaskInput {
  /** Receiver session (must belong to the [receiver agent] = the detail-page agent; where the task lands).
   *  [2026-10-11] Empty string = new session: no receiver session id passed, the system creates a new session. */
  session_key: string;
  instruction: string;
  task_id: string;
  task_name?: string;
  /** [2026-10-10 Super Terminal] Sender agent identity (default = receiver agent) */
  caller_agent_id?: string;
  /** [2026-10-10] Sender session (default = sender main session) */
  send_session_key?: string;
  /** Optional params shared by agx_dispatch_task */
  needs_reply?: number;
  /** [2026-10-10] Explicitly disable the receipt: takes precedence over task.reply_mode template derivation.
   *  ⚠️ [2026-10-19 P5 deprecated] New callers use `reply` (the unified facade absorbs the patch);
   *  this field is kept only for old dialogs that have not migrated. */
  force_no_reply?: boolean;
  /** [2026-10-19 P5] Receipt forced to one of two values: "yes" | "no".
   *  When passed, the kernel facade agx_universal_send is used (needs_reply/force_no_reply are converted inside it). */
  reply?: "yes" | "no";
  /** [2026-10-19 P2] Idempotency key: retrying the same intent reuses the same value, no duplicate dispatch. */
  client_request_id?: string;
  /** [2026-11-06 Feng] Human approval: 1 = on completion land in awaiting_confirm, done only after a human clicks approve.
   *  0/not passed = not needed (default). The semantics are defined in the kernel; the frontend only passes this switch through. */
  needs_confirm?: number;
  timeout_seconds?: number;
  exec_name?: string;
  /** [2026-10-18] Skills this task references (list of agx_skill.skill_id, active skills only).
   *  The server renders a 'reference skills' note appended to instruction; an invalid/disabled id → 400 SKILL_NOT_FOUND. */
  skill_ids?: string[];
}

/**
 * Execute a task: wrap it as a task (agx_dispatch_task) and deliver it to the target session,
 * where AgentGeeX merges the task template x_desc (task spec) with instruction into
 * exec_instruction, creates an execution bound to that session, and the daemon runs it in an isolated session.
 * (Plain text is no longer sent directly; the direct-send entry is on the 'sessions page'.)
 */
export interface SendTextResult {
  sent: boolean;
  session_key: string;
  target_agent: string;
  text_len: number;
  /** [2026-11-17] The body that actually got sent (including the 'reference skills' block, assembled by the kernel).
   *  Use this for the local echo, not the raw text in the input box — the raw text has no skills block, so it looks like 'the skills were not attached'. */
  text_full?: string;
}

export interface SendTextInput {
  session_key: string;
  text: string;
  /** [2026-10-18] Skills referenced along with the message (active skills only): the server appends a 'reference skills' block to the body. */
  skill_ids?: string[];
  /** [2026-10-10 Super Terminal] Sender agent identity (default = path agent) */
  caller_agent_id?: string;
}

/**
 * Execute a task: wrap it as a task (agx_dispatch_task) and deliver it to the target session,
 * where AgentGeeX merges the task template x_desc (task spec) with instruction into
 * exec_instruction, creates an execution bound to that session, and the daemon runs it in an isolated session.
 * (Plain text is no longer sent directly; the direct-send entry is on the 'sessions page'.)
 */
/* ── Temporary dispatch exemption (agx_set_dispatch_exempt) ───────────────────
   Semantics: the worker role is below L5, so by default it cannot call agx_dispatch_task / agx_schedule_task /
   agx_quick_cmd_send. Open the door temporarily — **it closes by itself on expiry** (the field is a timestamp, not a switch). */
export interface DispatchExemptInput {
  /** Relative duration (minutes); 0 = do not use a relative duration */
  minutes?: number;
  /** Absolute expiry "YYYY-MM-DD HH:MM:SS" (mutually exclusive with minutes) */
  until?: string;
  /** Why it was opened (written to the kernel audit log) */
  reason?: string;
}

export interface DispatchExemptResult {
  ok: boolean;
  agentId: string;
  agentRole?: string;
  until: string;
  remainingSeconds: number;
  cleared: boolean;
  /** The kernel reads the agent's actual value back to judge: is it really in effect right now */
  effectiveNow: boolean;
  permission?: string;
  writtenBy?: string;
  reason?: string;
  scope?: string;
  /** [2026-11-01] Whether the 'you can dispatch now' notice was delivered to its own main session.
   *  Not notified ≠ not authorized: the write already succeeded, only that message failed to send (see notifyError). */
  notified?: boolean;
  notifyError?: string;
  notifySession?: string;
}

/** Agent profile (detail page only) — beyond a list row it adds the role spec, the 8 personality files, size counts, etc.
    2026-10-31 Feng: 'you cannot even tell what state an agent is in' → hence a dedicated detail page. */
/** Origin and truthfulness of one path (used by 'file locations' on the profile page).
 *  source: db=real value in the DB / derived=derived from runtime conventions / project=taken from the project / empty=unknown
 *  exists: true=verified present / false=verified absent / null=not on this machine or undecidable */
export interface AgentPathSlot {
  value: string;
  source: "db" | "derived" | "project" | "";
  exists: boolean | null;
}

export interface AgentPaths {
  root: AgentPathSlot;
  workspace: AgentPathSlot;
  /** Project source directory (the code an agent works on lives in the project, not in its own workspace) */
  projectSource: AgentPathSlot;
  hostId: string;
  /** Whether the agent is on the BFF host — existence can only be verified on the same machine */
  checkedOnThisHost: boolean;
  workDir: string;
}

export interface PersonaFile {
  key: string;
  label: string;
  chars: number;
  filled: boolean;
  body: string;
}
export interface AgentProfile {
  id: string;
  name: string;
  role: string;
  agentType: string;
  agentLabs: string;
  kind: "template" | "clone" | "native";
  kindLabel: string;
  isTemplate: boolean;
  originId: string;
  avatarUrl: string;
  description: string;
  projectId: string;
  projectName: string;
  projectAdminId: string;
  hostId: string;
  hostAlias: string;
  scope: string;
  isProjectAdmin: boolean;
  model: string;
  status: string;
  activeStatus: boolean;
  deployStatus: string;
  defaultSessionId: string;
  workspaceDir: string;
  rootDir: string;
  /** File locations (2026-11-01): the DB fields are empty most of the time → derive from conventions and label the origin */
  paths: AgentPaths;
  createdAt: string;
  lastActiveAt: string;
  counts: {
    sessions: number;
    tasks: number;
    executions: number;
    execOk: number;
    execFail: number;
    assistants: number;
  };
  personaFiles: PersonaFile[];
  skillsAllowlist: string[];
  mcpConfig: Record<string, unknown>;
  xDesc: string;
  systemPrompt: string;
  dispatchExemptUntil: string;
  dispatchExemptActive: boolean;
  dispatchExemptRemainingSeconds: number;
  dispatchExemptApplicable: boolean;
}

/** [2026-11-07 Feng] Agent editing (via MCP agx_update_agent).
 *  All fields are open; the kernel decides what may change; **only submit fields that changed** (not passed = not changed). */
export interface AgentUpdateInput {
  agent_name?: string;
  x_desc?: string;
  agent_role?: string;
  host_id?: string;
  system_prompt?: string;
  active_status?: string;
  mcp_config?: Record<string, unknown>;
  skills_allowlist?: string[];
  agent_email?: string;
  agent_telegram_token?: string;
  agent_feishu_token?: string;
}

export interface AgentUpdateResult {
  ok: boolean;
  agentId: string;
  fields: string[];
  message: string;
}

export const agentsApi = {
  /** Profile details (with long text), separate from the list API: the list does not carry the weight of x_desc / personality files. */
  profile: (agentId: string) =>
    api.get<AgentProfile>(`/agents/${encodeURIComponent(agentId)}/profile`),
  runTask: (agentId: string, body: RunTaskInput) =>
    api.post<RunTaskResult>(`/agents/${encodeURIComponent(agentId)}/actions/run-task`, body),
  /** Send plain text straight to a session (agx_send_text): talk, without wrapping a task or creating an execution. */
  sendText: (agentId: string, body: SendTextInput) =>
    api.post<SendTextResult>(`/agents/${encodeURIComponent(agentId)}/actions/send-text`, body),
  /** Temporary dispatch exemption: enable (minutes / until) or disable (pass neither). */
  setDispatchExempt: (agentId: string, body: DispatchExemptInput) =>
    api.post<DispatchExemptResult>(`/agents/${encodeURIComponent(agentId)}/actions/dispatch-exempt`, body),
  /** Edit agent (PATCH → MCP agx_update_agent). Fields not passed = unchanged. */
  update: (agentId: string, body: AgentUpdateInput) =>
    api.patch<AgentUpdateResult>(`/agents/${encodeURIComponent(agentId)}`, body),
};

/* --------------------------- Manual nudge --------------------------- */
/** Nudge type: auto is decided by the kernel from the current state; urge → executor; ack_remind → originator. */
export type NudgeKind = "auto" | "urge" | "ack_remind";

export interface NudgeInput {
  kind: NudgeKind;
  note?: string;
}

export interface NudgeResult {
  ok: boolean;
  kind: string;
  runUuid: string;
  /** Which session it was delivered to */
  deliveredTo: string;
  /** Who it was sent to (urge=executor / ack_remind=originator) */
  toAgent: string;
  eventId: string;
  state: Record<string, unknown>;
}

/** Execution edit input — ★ excludes run_status / reply_read (the former is the authoritative result field, the latter goes through acknowledgment) */
export interface ExecutionUpdateInput {
  exec_name?: string;
  timeout_seconds?: number;
  exec_instruction?: string;
  alert_note?: string;
}

/** Human approve / reject: only meaningful for tasks awaiting human confirmation.
 *  ★ The two actions are **mutually exclusive and irreversible** — approve → succeeded (advances downstream),
 *    reject → failed (notifies the originator). So the UI must show a confirmation dialog first; it must not change on a single click. */
export type ConfirmAction = "approve" | "reject";

export interface ConfirmResult {
  ok: boolean;
  runUuid: string;
  action: ConfirmAction;
  /** Real state after persisting (succeeded / failed) */
  status: string;
  /** 2 = confirmed, 3 = rejected (kernel needs_confirm values) */
  needsConfirm: number | null;
  message: string;
}

export const executionsApi = {
  /** Manual nudge: urge / ack_remind. Creates no execution, does not change run_status. */
  nudge: (runUuid: string, body: NudgeInput) =>
    api.post<NudgeResult>(`/executions/${encodeURIComponent(runUuid)}/nudge`, body),
  /** [2026-11-06 Feng] Human approve / reject — passes through to kernel agx_confirm_task. */
  confirm: (runUuid: string, action: ConfirmAction) =>
    api.post<ConfirmResult>(`/executions/${encodeURIComponent(runUuid)}/confirm`, { action }),
  /** [2026-11-01 Feng] 'execution records should be editable' — title / timeout / instruction / alert note */
  update: (runUuid: string, body: ExecutionUpdateInput) =>
    api.patch<WriteResult>(`/executions/${encodeURIComponent(runUuid)}`, body),
};

/* ------------------------------ Shared skill library (editable) ------------------------------ */
/** Skill edit input — matches the kernel agx_skill_contribute input (except skill_id) */
export interface SkillUpdateInput {
  skill_name?: string;
  category?: string;
  description?: string;
  tags?: string;
  content?: string;
}

export const skillsApi = {
  /** [2026-11-01 Feng] Skill detail turned into editing: detail via row click, editing via kernel upsert */
  update: (skillId: string, body: SkillUpdateInput) =>
    api.patch<WriteResult>(`/skills/${encodeURIComponent(skillId)}`, body),
};

/* --------------------------- Plan flow (Plan Flow) --------------------------- */
export interface PlanGraphNode {
  run_uuid: string;
  task_id?: string;
  task_name?: string;
  agent_id: string;
  agent_name?: string;
  instruction?: string;
  exec_name?: string;
  timeout_seconds?: number | null;
  scheduled_at?: string | null;
  prev_task?: string | null;
  next_task?: string | null;
  children?: string[];
  n_children?: number;
  run_status?: string;
  /* 2026-11-03: the detail dialog must be able to 'click into the session and see the content'. Empty string when not dispatched (no execution yet). */
  session_id?: string;
  started_at?: string;
  finished_at?: string;
}
export interface PlanGraphEdge {
  from: string;
  to: string;
  type: "children" | "next" | "prev";
}
export interface PlanGraph {
  planId: string;
  projectId: string;
  description: string;
  planStatus: string;
  createdAt: string;
  nNodes: number;
  nEdges: number;
  hasRealStatus: boolean;
  nodes: PlanGraphNode[];
  edges: PlanGraphEdge[];
}

/** Plan realtime state (for polling; only the status map + counts, no node structure) */
export interface PlanLiveStatus {
  planId: string;
  /** Server refresh time (for the 'last refreshed' hint) */
  refreshedAt: string;
  /** Data source: redis = kernel hot cache */
  source: string;
  statuses: Record<string, string>;
  /** run_uuid → start time ("YYYY-MM-DD HH:MM"). Added 2026-11-03:
   *  the flow chart must 'put the most recently started step in the very middle', and status alone cannot tell which is newer.
   *  Taken from agx_task_execution.started_at, falling back to dispatched_at when empty
   *  (started_at is lazily written by the kernel and is often empty while running). */
  startedAt?: Record<string, string>;
  counts: {
    total: number;
    running: number;
    succeeded: number;
    failed: number;
    pending: number;
    cancelled: number;
    unknown: number;
  };
  /** The first 50 runs with 'no status in Redis' (not dispatched / cache expired) */
  missing: string[];
}

/** System status board (realtime). Server-side is aggregate SQL; a 5s poll suffices, no full snapshot pull. */
/** Session realtime state: look up its runtime state on **every host** by session_key (read-only Redis projection).
 *  [2026-11-13 21:39 Feng] The message-send dialog must tell 'is the receiver session running right now',
 *   so query precisely by key through this entry — do not pull the whole session table for one session.
 *  ★ perHost may hold several entries for the same key (same name across hosts); 'running' means **any host is running**. */
export interface SessionRuntimeLookup {
  key: string;
  hosts: string[];
  perHost: Record<string, import("@/mock/agentgeex").SessionRuntime>;
  conflictHosts: string[];
}

/* ---------------------- Session chat history (2026-11-14) ---------------------- */
/**
 * One history record = one question-answer pair (the archive granularity of `agx_user_message`).
 *
 * ★ `id` is the table's pkid, used as the paging cursor (not a business ID) —
 *   messages keep flowing in, so OFFSET paging skips or repeats rows; only cursor paging works.
 */
/** Execution info filled in from the dispatch envelope (BFF fetches it in bulk from `agx_task_execution`, not stored in the message table) */
export interface SessionDispatchInfo {
  /** `run_xxx` (extracted from content; empty string if not found) */
  runUuid: string;
  /** dispatch / receipt / empty */
  msgType: string;
  /** send_agent from the envelope (the sender agent) */
  sendAgent: string;
  /** Executor agent (agx_task_execution.agent_id) */
  agentId: string;
  /** Originator agent (agx_task_execution.send_agent_id) */
  sendAgentId: string;
  /** Execution short title (Chinese; more readable than run_uuid) */
  execName: string;
  runStatus: string;
    /**
   * Instruction body.
   * ★ [2026-11-14 20:52] The list holds a **preview** (first 600 chars; `instructionTruncated=true`
   *   means there is more); open the detail dialog and use `sessionMessagesApi.instruction(runUuid)` for the full text.
   *   The single source of truth for the full text is still `agx_task_execution.exec_instruction`, not duplicated in the message table.
   */
  instruction: string;
  /** Real total length of the instruction body (may be far larger than `instruction.length`) */
  instructionLen?: number;
  /** Whether `instruction` in the list was truncated (true = the detail dialog must fetch the full text) */
  instructionTruncated?: boolean;
}

/** Return of `GET /v1/executions/{run_uuid}/instruction` (used by the envelope detail dialog) */
export interface ExecutionInstruction {
  runUuid: string;
  agentId: string;
  sendAgentId: string;
  execName: string;
  runStatus: string;
  instruction: string;
  instructionLen: number;
  summary: string;
  startedAt: string;
  finishedAt: string;
  durationSeconds: number;
  sessionId: string;
  taskId: string;
}

export interface SessionMessageItem {
  id: number;
  sessionKey: string;
  sessionLabel: string;
  agentId: string;
  hostId: string;
  kind: "human" | "envelope" | "notice" | "system" | "command";
  /**
   * Origin marker (collector since `2560ca3`):
   * `1` = confirmed to be the person / `0` = confirmed not the person (system-injected, already stripped by the backend) / `null` = no marker
   * (before 2026-10 the gateway did not write this field, or the archive JSONL stripped `__openclaw` entirely)
   */
  fromOwner: 0 | 1 | null;
  /**
   * Only present for `kind='envelope'`: which execution this dispatch/receipt refers to and what the instruction body is.
   * The body is not in the message table — messages only carry the envelope `{task_run_uuid, send_agent, msg_type}`,
   * and the BFF fetches the body from `agx_task_execution` by run_uuid (single source of truth, no duplication).
   */
  dispatch: SessionDispatchInfo | null;
  at: string;
  /** Full-precision time (`2026-11-14T12:33:06.205000`) — used as the paging cursor; `at` only goes to the minute and would collide */
  atIso: string;
  content: string;
  reply: string;
  replyAt: string;
  /** The answer was merged from several text blocks; >1 means a tool call sat in between */
  replyParts: number;
  contentLen: number;
  replyLen: number;
}

export interface SessionMessagesPage {
  items: SessionMessageItem[];
  hasMore: boolean;
  total: number;
}

export const sessionMessagesApi = {
  list: (
    key: string,
    opts: { limit?: number; before?: number; beforeAt?: string; kinds?: string[] } = {},
  ) => {
    const qs = new URLSearchParams({ key });
    if (opts.limit) qs.set("limit", String(opts.limit));
    if (opts.before) qs.set("before", String(opts.before));
    if (opts.beforeAt) qs.set("before_at", opts.beforeAt);
    if (opts.kinds?.length) qs.set("kinds", opts.kinds.join(","));
    return api.get<SessionMessagesPage>(`/sessions/messages?${qs.toString()}`);
  },
  /** Fetch the full instruction by run_uuid (the list carries only a 600-char preview, see SessionDispatchInfo.instruction) */
  instruction: (runUuid: string) =>
    api.get<ExecutionInstruction>(`/executions/${encodeURIComponent(runUuid)}/instruction`),
};

export const sessionRuntimeApi = {
  lookup: (key: string) =>
    api.get<SessionRuntimeLookup>(`/sessions/runtime/lookup?key=${encodeURIComponent(key)}`),
};

/* ------------------------- Quick access ------------------------- */
/** [2026-11-13 22:03 Feng] Quick marker for project/agent/session/plan.
 *  ★ [22:25 scope change] The marker lives in the core table's `is_quick` column, one global copy, no owner. */
export interface QuickEntryItem {
  entryId: string;
  entryType: "project" | "agent" | "session" | "plan";
  entryKey: string;
  label: string;
  ownerAgentId?: string;
  sortOrder?: number;
  createdAt?: string;
}

export const quickEntryApi = {
  list: () => api.get<{ entries: QuickEntryItem[]; owner: string }>("/quick-entries"),
  add: (entry_type: QuickEntryItem["entryType"], entry_key: string, label = "") =>
    api.post<{ entry: QuickEntryItem }>("/quick-entries", { entry_type, entry_key, label }),
  remove: (entry_type: QuickEntryItem["entryType"], entry_key: string) =>
    api.delete<{ removed: boolean }>(
      `/quick-entries?entry_type=${encodeURIComponent(entry_type)}&entry_key=${encodeURIComponent(entry_key)}`,
    ),
};

export const dashboardApi = {
  stats: () => api.get<import("@/mock/agentgeex").DashboardStats>("/dashboard/stats"),
};

export const plansApi = {
  graph: (planId: string) =>
    api.get<PlanGraph>(`/plans/${encodeURIComponent(planId)}/graph`),
  /** [2026-10-27 Feng] Flow chart realtime state (Redis hot cache) */
  status: (planId: string) =>
    api.get<PlanLiveStatus>(`/plans/${encodeURIComponent(planId)}/status`),
};

/* ------------------------- Quick command ------------------------- */
/** Parameter type enum (spec v2.1). */
export type QuickCmdParamType =
  | "text" | "textarea" | "enum" | "number" | "bool" | "date" | "datetime";

/** source candidate enum (empty = free input).
 * [2026-10-14 contract ruling ③] Naming unified to the new contract: source (formerly value_source).
 * number_hint is unused this round (removed); branch visibility is unified as show_if.
 * [2026-10-14 dropdown rework] source non-empty → the frontend uses a dropdown (enumerated system values);
 *   agent/host/project/task map to the enumerable lists inside the system respectively.
 */
export type QuickCmdValueSource = "" | "agent" | "host" | "project" | "task";

export interface QuickCmdParam {
  name: string;
  label: string;
  required: boolean;
  type: QuickCmdParamType;
  placeholder: string;
  default: string | number | boolean | null;
  options: { value: string; label: string }[];
  source: QuickCmdValueSource;
  /** Branch visibility expression, e.g. "scope=='single'" (empty = always shown). */
  show_if?: string;
}

export interface QuickCmdRoleLimit {
  roles: string[];
  enforce: "backend" | "ui" | "both" | string;
  note: string;
}

export interface QuickCmd {
  cmdId: string;
  title: string;
  ownerAgent: string;
  targetTask: string;
  delegateTo: string;
  semanticSkeleton: string;
  params: QuickCmdParam[];
  composedInstruction: string;
  exampleFilled: string;
  notes: string;
  callerRoleLimit: QuickCmdRoleLimit;
  /** Table extension fields (agx_quick_cmd) */
  category?: string;
  activeStatus?: number;
  /** Reserved: session_key form (empty = default main session). */
  runSessionId?: string;
  createdAt?: string;
  updatedAt?: string;
  /** [2026-10-19 P1] Receipt semantics of the target task (result / confirm / silent), used for UI annotation only. */
  replyMode?: string;
  /** [2026-10-19 P1] Default receipt computed by the backend (yes/no) — the frontend does not compute it, it is used directly as the preselection. */
  defaultReply?: "yes" | "no";
  /** [2026-10-26] 0=private (bound to target_agent_id, sent to the specified agent); 1=public (not bound to an agent;
   *  target = the caller who clicked this command, ownerAgent returns an empty string). */
  isPublic?: number;
  /** [2026-11-14 Feng] Two kinds of commands:
   *  · `task` = dispatch a task with parameters (original behavior, creates an execution)
   *  · `text` = a stored canned phrase; one click only copies `composedInstruction` verbatim into the input box,
   *    **no execution is produced** (`targetTask` empty, `params` empty).
   *  A missing field is treated as `task` (legacy data compatibility). */
  sendMode?: "task" | "text";
  /** Manual ordering within a group, smaller value first (default 100). */
  sortOrder?: number;
  /** Cumulative use count (+1 per copy/fill for the text type). */
  useCount?: number;
  lastUsedAt?: string;
}

/** Create/update payload (frontend contract, submitted to the BFF) */
export interface QuickCmdUpsert {
  cmdId?: string;
  title: string;
  ownerAgent: string;
  targetTask: string;
  delegateTo?: string;
  semanticSkeleton?: string;
  params?: QuickCmdParam[];
  composedInstruction: string;
  exampleFilled?: string;
  notes?: string;
  callerRoleLimit?: QuickCmdRoleLimit;
  category?: string;
  runSessionId?: string;
  activeStatus?: number;
  /** [2026-10-26] 0=private (ownerAgent required); 1=public (ownerAgent must be empty/omitted). */
  isPublic?: number;
  /** [2026-11-14 Feng] For `text` the backend forcibly clears targetTask / params. */
  sendMode?: "task" | "text";
  sortOrder?: number;
}

export interface QuickCmdMeta {
  group: string;
  group_name: string;
  spec_version: string;
  collected_at: string;
}

export interface QuickCmdListResult {
  meta: QuickCmdMeta;
  userRoles: string[];
  commands: QuickCmd[];
}

export interface QuickCmdRunInput {
  cmd_id: string;
  params: Record<string, unknown>;
  /** [Sender agent identity] The operating identity selected in the frontend (human≠agent; not bound to the login account).
   *  Default empty → the kernel L5 gate rejects naturally (fail-closed). */
  caller_agent_id?: string;
  /** [Sender session] Receipt routing (may be empty → the kernel falls back to agent:{caller}:main). */
  self_session_id?: string;
  /** [2026-10-17 delivery target session] Where the task lands.
   *  Empty (default) = new session (the kernel derives oneshot, same behavior as before);
   *  non-empty = an existing session (must belong to the target agent; the kernel checks ownership, else SESSION_NOT_OWNED).
   *  ★ Orthogonal to self_session_id: one is 'where it runs', the other is 'where it was sent from'. */
  target_session_key?: string;
  /** [2026-10-17 execution param] Watchdog timeout (seconds), empty=default. Already supported by kernel agx_quick_cmd_send. */
  timeout_seconds?: number;
  /** [2026-10-17 execution param] Execution instance short title, empty=auto. */
  exec_name?: string;
  /** [2026-10-17] Receipt requirement 0/1; default = derived from the target task template's reply_mode (supported by kernel agx_quick_cmd_send).
   *  ⚠️ [2026-10-19 P5 deprecated] New callers use `reply` (the unified facade absorbs the patch). */
  needs_reply?: number;
  /** [2026-10-17] Explicitly disable the receipt (highest precedence; int default 0 cannot distinguish 'explicit 0' from 'not passed').
   *  ⚠️ [2026-10-19 P5 deprecated] New callers use `reply`. */
  force_no_reply?: boolean;
  /** [2026-10-19 P5] Receipt forced to one of two values: "yes" | "no" (goes through kernel facade agx_universal_send). */
  reply?: "yes" | "no";
  /** [2026-10-19 P2] Idempotency key: retrying the same intent reuses the same value, no duplicate dispatch. */
  client_request_id?: string;
  /** [2026-10-18] Skills referenced by this task (active skills only); the kernel appends a 'reference skills' note after assembling. */
  skill_ids?: string[];
}

export interface QuickCmdRunResult {
  dispatched: boolean;
  cmd_id: string;
  title: string;
  run_uuid: string;
  msg_id?: string;
  queue?: string;
  queue_length?: number | null;
  session_key: string;
  target_agent: string;
  task_ref: { task_id: string; task_name: string };
  instruction: string;
}

export interface QuickCmdByTaskResult {
  found: boolean;
  cmd: QuickCmd | null;
}

export const quickCmdApi = {
  /** List (with meta + [selected sender identity] roles, for grouping/filtering/greying out).
   *  @param agentId filter by owner (used by the agent detail page)
   *  @param callerAgentId [sender agent identity] (the operating identity chosen in the frontend, used to compute userRoles) */
  /**
   * List. By default only active items are returned (active_only=true) —
   * [2026-10-27] archived (soft-deleted) commands must not appear in any list or runnable entry;
   * previously the param was not passed → the backend defaulted to False → the admin page counted 3 archived ones in the total (63 instead of 60).
   */
  list: (agentId?: string, callerAgentId?: string, activeOnly = true, includeSharedText = false) => {
    const q = new URLSearchParams();
    if (agentId) q.set("agent_id", agentId);
    if (callerAgentId) q.set("caller_agent_id", callerAgentId);
    if (activeOnly) q.set("active_only", "true");
    /* [2026-11-14 Feng] Canned phrases must show everyone's text snippets:
       a text snippet dispatches nothing; the owning agent is only 'who wrote it'. Only this entry passes true. */
    if (includeSharedText) q.set("include_shared_text", "true");
    const qs = q.toString();
    return api.get<QuickCmdListResult>(`/quick-cmd${qs ? `?${qs}` : ""}`);
  },
  /** Single-item detail. */
  get: (cmdId: string) => api.get<QuickCmd>(`/quick-cmd/${encodeURIComponent(cmdId)}`),
  /** Look up a quick command by task identifier (ownership rework: task→run split). found=false, cmd=null when unmatched. */
  byTask: (p: { agent_id?: string; task_labs?: string; task_name?: string }) => {
    const q = new URLSearchParams();
    if (p.agent_id) q.set("agent_id", p.agent_id);
    if (p.task_labs) q.set("task_labs", p.task_labs);
    if (p.task_name) q.set("task_name", p.task_name);
    return api.get<QuickCmdByTaskResult>(`/quick-cmd/by-task?${q.toString()}`);
  },
  /** Submit: **pass only {cmd_id, params}**; validation/assembly/dispatch are always performed authoritatively by the backend. */
  run: (body: QuickCmdRunInput) => api.post<QuickCmdRunResult>("/quick-cmd/run", body),
  /** Create (table agx_quick_cmd). */
  create: (body: QuickCmdUpsert) => api.post<QuickCmd>("/quick-cmd", body),
  /** Update (by cmd_id). */
  update: (cmdId: string, body: QuickCmdUpsert) =>
    api.put<QuickCmd>(`/quick-cmd/${encodeURIComponent(cmdId)}`, body),
  /** Archive (soft delete). */
  remove: (cmdId: string) =>
    api.delete<{ cmd_id: string; activeStatus: number }>(`/quick-cmd/${encodeURIComponent(cmdId)}`),
};

/* --------------------------- Project (detail / edit) --------------------------- */

/** Project detail (with x_desc project spec / source_dir / size counts) */
export interface ProjectDetail {
  id: string;
  name: string;
  abbr: string;
  platform: string;
  status: string;
  activeStatus: number;
  adminAgentId: string;
  logoUrl: string;
  xDesc: string;
  sourceDir: string;
  createdAt: string;
  counts: { agents: number; tasks: number; sessions: number };
}

/** Project edit input (= the operational field whitelist of kernel agx_update_project) */
export interface ProjectUpdateInput {
  project_name?: string;
  project_abbr?: string;
  project_logo?: string;
  x_desc?: string;
  source_dir?: string;
  /** Operating identity (the kernel uses it for field tiering + ownership checks); empty=fall back to the logged-in user */
  caller_agent_id?: string;
}

export interface ProjectUpdateResult {
  ok: boolean;
  projectId: string;
  fields: string[];
  message: string;
}

export interface RevealResult {
  ok: boolean;
  projectId: string;
  path: string;
  opener: string;
  host: string;
}

export const projectsApi = {
  detail: (projectId: string) =>
    api.get<ProjectDetail>(`/projects/${encodeURIComponent(projectId)}`),
  /** Open source_dir in the file manager of the BFF host (by default only triggerable on that machine) */
  reveal: (projectId: string) =>
    api.post<RevealResult>(`/projects/${encodeURIComponent(projectId)}/actions/reveal`),
  /** Edit — the backend passes through to MCP `agx_update_project` (the BFF never writes the DB directly) */
  update: (projectId: string, body: ProjectUpdateInput) =>
    api.patch<ProjectUpdateResult>(`/projects/${encodeURIComponent(projectId)}`, body),
};

/* --------------------------- Compute hosts (edit) --------------------------- */

/** Host edit input: pass only changed fields (aligned with BFF PATCH /hosts/{id}). */
/** Access method of one agent platform on a host (one item of agx_host_inventory.platforms) */
export interface HostPlatformInput {
  platform: string;
  name?: string;
  mode: "process" | "container";
  port?: number;
  /** Required only for mode=container (the container owns its address); process is built from endpoint_ip+port */
  addr?: string;
  base_path?: string;
  /** OpenClaw profile name (required for multiple instances, empty string for the main instance); other platforms may ignore it */
  profile?: string;
  note?: string;
}

export interface HostUpdateInput {
  host_alias?: string;
  endpoint_ip?: string;
  ssh_port?: number;
  os_platform?: string;
  /** [2026-11-04 Feng] 'platforms is a json field, it should be editable in the host table'
   *  Pass an array; the BFF passes it through unchanged to kernel agx_update_host (_JSON_COLUMNS serializes automatically). */
  platforms?: HostPlatformInput[];
  task_slots_limit?: number;
  x_desc?: string;
}

export interface HostUpdateResult {
  ok: boolean;
  hostId: string;
  fields: string[];
  message: string;
}

/** Compute host write entry (reads still go through the snapshot store; writes only via MCP agx_update_host). */
export const hostsApi = {
  update: (hostId: string, body: HostUpdateInput) =>
    api.patch<HostUpdateResult>(`/hosts/${encodeURIComponent(hostId)}`, body),
};

/* --------------------------- Task (detail) --------------------------- */

/** Task detail (with the full x_desc task spec / payload) */
export interface TaskDetail {
  id: string;
  name: string;
  labs: string;
  description: string;
  xDesc: string;
  agentId: string;
  agentName: string;
  payload: unknown;
  targetObject: string;
  priority: number;
  isPrivate: number;
  activeStatus: number;
  dedicatedSession: number;
  taskType: string;
  replyMode: string;
  logoUrl: string;
  createdAt: string;
  updatedAt: string;
}

/** Task edit input (submit only changed fields) — consistent with the kernel agx_update_task whitelist */
export interface TaskUpdateInput {
  x_desc?: string;
  target_object?: string;
  /** Payload: may be an object directly or JSON text (the BFF parses it and rejects invalid JSON) */
  payload?: unknown;
  priority?: number;
  is_private?: 0 | 1;
  active_status?: 0 | 1;
}

export interface WriteResult {
  ok: boolean;
  message?: string;
  fields?: string[];
}

export const tasksApi = {
  detail: (taskId: string) => api.get<TaskDetail>(`/tasks/${encodeURIComponent(taskId)}`),
  /** [2026-11-01 Feng] Inline 'detail' becomes 'edit' — detail via row click, editing needs its own entry */
  update: (taskId: string, body: TaskUpdateInput) =>
    api.patch<WriteResult>(`/tasks/${encodeURIComponent(taskId)}`, body),
};
