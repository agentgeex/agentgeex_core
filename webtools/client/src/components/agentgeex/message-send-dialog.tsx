/**
 * components/agentgeex/universal-input-bar.tsx — message send (formerly "unified input bar", renamed 2026-11-04)
 *
 * ★ Name set by Feng on 2026-11-04 22:06: "We can't call it that anymore, it was a temporary name... anyway, it's about sending messages".
 *   Its positioning was clarified with it: **it does not only serve the agent page** — project, plan, send-message
 *   and other scenarios all reuse it; only some values are locked per scenario (e.g. entering from a project, the
 *   project in business attributes is already fixed). So nothing here should assume "the sender is always the
 *   current Agent of some Agent page".
 *
 * Basis: notice note_03cb822ee346 "Unified Message Input UI — construction notes for agx_ui_lead" (2026-10-17)
 *
 * Four vertical layers:
 *   ① Context Selector         who · to whom (send subject / sender session / target Agent)
 *   ② Execution Mode Selector  how to execute (text / Task / quick command, mutually exclusive) + session policy (new / existing)
 *   ③ Business Attributes      business attributes (**reserved this round**: slots only, zero implementation)
 *   ④ Prompt Input Bar         what to say (text body / task instruction / quick command params)
 *
 * ★ Design points (Feng's "build the full set first, then wrap the three entry points with it"):
 *   From day one this component has been **controlled + trimmable**:
 *     - `initial`  prefilled values (already fixed externally)
 *     - `locked`   locked items (control disabled, value shown read-only)
 *     - `hidden`   hidden items (block not rendered at all, more thorough than locked)
 *   The three entry points later only need "prefill + lock + hide"; no need to rewrite this component.
 *
 * ★ Semantic boundaries (do not confuse the three directions):
 *   senderAgent / senderSession  → from whom, from which session it is [sent] (receipt routing)
 *   targetAgent                 → which Agent to send to
 *   targetSession               → which session to [execute] in (landing point, empty = new session)
 *
 * ★ Hard constraint (notice §4): text mode uses agx_send_text, and can only send to **the send subject's own session**
 *   (platform "no Agent-to-Agent chat" red line) → in text mode the target Agent collapses to the send subject, and
 *   an existing session must be chosen.
 *
 * UI is not invented: Dialog/Select/Input/Textarea/Button/FormRow all reuse existing bases;
 * the quick command param form reuses ParamField from quick-cmd-dialog (same implementation, not copied).
 */
import { AgentOptionLabel } from "@/components/agentgeex/agent-option";
import * as React from "react";
import {
  Send, Loader2, AlertTriangle, Info, Lock, MessageSquare, ListChecks, Zap, Layers,
  Check, X, Sparkles, RefreshCw, ExternalLink, Maximize2, Minimize2, History, Star, ClipboardList,
  Plus,
} from "lucide-react";
/* [2026-11-14 Feng] Create a common phrase inline — shares the same form with the management page */
import { QuickCmdForm } from "@/components/agentgeex/quick-cmd-form-dialog";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { PortalContainerProvider } from "@/components/ui/portal-container";
import { OnlineDot } from "@/components/ui/online-dot";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { VoiceInput, voiceBlocksSend, type VoicePhase } from "@/components/agentgeex/voice-input";
import { readKey, writeKey, removeKey } from "@/lib/storage-keys";
import { FieldLabel } from "@/components/ui/form-field";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  ParamField, initialValue, localPreview,
} from "@/components/agentgeex/quick-cmd-dialog";
import {
  listAgents, listAgentOptions, listSelectableAgents, agentOptionText, isAgentSelectable, isAgentTemplate,
  listTasks, listSkills, sessionsOf, fetchQuickCommands,
  listProjects, projectName, sortByQuickProject, projectNameWithPin,
  runQuickCommand, agentsApi, sessionRuntimeApi,
  isQuickEntry, addQuickEntry, removeQuickEntry, refreshQuickEntries,
  type QuickCmd, type SessionRow, type TaskRow, type SkillRow, type QuickCmdRunResult,
} from "@/providers/agentgeex";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";
import { refreshStoreOnce } from "@/lib/fresh-data";
import { isSessionRunning } from "@/components/agentgeex/session-runtime-badge";
import { SessionHistory, type SessionHistoryHandle } from "@/components/agentgeex/session-history";
/* [2026-11-15 23:58] The "Open" in the header goes to the site-wide entity detail dialog, does not open a new page */
import { useDetailPopup } from "@/components/agentgeex/detail-popup";
/* The single place mapping session → OpenClaw address (do not hand-build /chat/... inside components) */
import { sessionDeepLink } from "@/lib/openclaw";
import { apiErrorText } from "@/lib/api-error";

/* ────────────────────────────── Type contract ────────────────────────────── */

export type UniversalMode = "text" | "task" | "quickcmd";

/** Field keys that can be prefilled / locked / hidden (for the three entry points to "wrap" later). */
export type UniversalField =
  | "senderAgent" | "senderSession" | "targetAgent"
  | "mode" | "sessionMode" | "targetSession"
  | "task" | "quickcmd" | "params" | "text"
  | "execParams" | "skills"
  | "business";

export interface UniversalInputValue {
  /** Send subject (sender Agent identity). */
  senderAgent: string;
  /** Sender session (receipt routing). */
  senderSession: string;
  /** Target Agent (receiver). */
  targetAgent: string;
  /** Execution mode (text / Task / quick command, mutually exclusive). */
  mode: UniversalMode;
  /** Session policy: new session / existing session. */
  sessionMode: "new" | "existing";
  /** Selected key when using an existing session. */
  targetSession: string;
  /** task_id for Task mode. */
  taskId: string;
  /** cmd_id for quick command mode. */
  cmdId: string;
  /** Quick command param values. */
  params: Record<string, unknown>;
  /** Text body / task instruction. */
  text: string;
  /** Execution param: whether a reply is needed (forced either/or, no middle state). */
  needsReply?: "0" | "1";
  /** Execution param: whether human confirmation is needed (task-level attribute, default "0").
   *  [2026-11-06 15:45 Feng] "Some tasks need a human reply; the task is told at creation time that it needs a human reply".
   *  Semantics = finishing does **not** count as done; it turns successful only after a human has looked and released it. */
  needsConfirm?: "0" | "1";
  /** Execution param: watchdog timeout (seconds, string so it can stay empty). */
  timeoutSec?: string;
  /** Execution param: short title of the execution. */
  execName?: string;
}

/* ────────────────────── Unsent draft (closing the window keeps content) ────────────────────── */

/** [2026-11-13 15:40 Feng]
 *  "If this message is not sent and I close the window, can it be saved? So the next time I open it the message comes back.
 *    In case I close it by accident and go handle other things, when I come back what I typed before is gone."
 *
 * ★ Why localStorage instead of memory/parent state:
 *   Memory dies with the component; parent state does not survive a refresh. localStorage survives even
 *   "page refresh / closing the browser" — Feng's "close it and go handle other things" includes exactly
 *   "might close the browser along the way".
 * ★ Key names and old-key migration are registered in `lib/storage-keys.ts` (the old key win3.send.draft.v1 is
 *   still readable, so upgrading does not lose a draft the user has not finished).
 * ★ Only **editable content** is stored: locked / hidden are decided by the entry point, and a draft has no right
 *   to override them (see restore logic).
 */
/** How long input idles before persisting (ms). Closing the window persists immediately, without waiting. */
const DRAFT_DEBOUNCE_MS = 1000;

export interface SendDraft {
  mode: UniversalMode;
  sessionMode: "new" | "existing";
  senderAgent: string;
  senderSession: string;
  targetAgent: string;
  targetSession: string;
  taskId: string;
  cmdId: string;
  params: Record<string, unknown>;
  text: string;
  needsReply: "0" | "1";
  needsConfirm: "0" | "1";
  timeoutSec: string;
  execName: string;
  skillIds: string[];
  /** Persist time (the dialog shows "restored content from 15:32", so people know it is not their hallucination) */
  at: number;
}

/** Multi-draft container.
 *  [2026-11-13 15:53 Feng] "add" — multiple drafts must coexist: write half to A, then half to B, each keeps its own.
 *
 *  ★ Why bucket by **receiver**: the receiver is the natural boundary of "who this is being sent to".
 *    Session / mode / task are all details inside the same thing; bucketing by them would explode into
 *    dozens of fragments.
 *  ★ `last` = the most recently edited bucket: when the top bar opens (no receiver specified) it decides which one
 *    to restore first, otherwise "there are 3 drafts" just means "no idea which one should appear".
 */
interface DraftStore {
  v: 1;
  last: string;
  items: Record<string, SendDraft>;
}

const emptyDraftStore = (): DraftStore => ({ v: 1, last: "", items: {} });

/** Bucket key: receiver Agent. When no receiver is selected it goes to `_` — write first, pick the person later, content should not be lost. */
function draftBucketOf(targetAgent: string) {
  return targetAgent || "_";
}

function readDraftStore(): DraftStore {
  try {
    const raw = readKey("sendDraft");
    if (!raw) return emptyDraftStore();
    const p = JSON.parse(raw) as Partial<DraftStore> & Partial<SendDraft>;
    /* Compatible with the old version (single draft): has a text field, no items → treat it as "the only draft of
        some receiver" and migrate it into a bucket. It was live for less than half an hour, but someone may have saved one. */
    if (p && typeof p === "object" && !p.items && typeof p.text === "string") {
      const one = p as SendDraft;
      const k = draftBucketOf(one.targetAgent || "");
      return { v: 1, last: k, items: { [k]: one } };
    }
    if (p && typeof p === "object" && p.items && typeof p.items === "object") {
      return {
        v: 1,
        last: typeof p.last === "string" ? p.last : "",
        items: p.items as Record<string, SendDraft>,
      };
    }
    return emptyDraftStore();
  } catch {
    /* Storage disabled / JSON broken by someone — treat as no draft.
       ★ Never throw here: a draft is a nice-to-have; "failed to read draft" must not make the whole dialog fail to open. */
    return emptyDraftStore();
  }
}

function writeDraftStore(s: DraftStore) {
  /* readKey/writeKey already swallow private mode and quota errors internally — losing a draft is acceptable, breaking send is not. */
  if (Object.keys(s.items).length === 0) removeKey("sendDraft");
  else writeKey("sendDraft", JSON.stringify(s));
}

/** Write/overwrite one bucket; pass null = delete this bucket. */
function saveDraftToBucket(bucket: string, d: SendDraft | null) {
  const s = readDraftStore();
  if (d && draftHasContent(d)) {
    s.items[bucket] = d;
    s.last = bucket;
  } else {
    delete s.items[bucket];
    /* The bucket pointed to by last was deleted → fall through to any remaining bucket, do not leave a dangling pointer */
    if (s.last === bucket) s.last = Object.keys(s.items)[0] || "";
  }
  writeDraftStore(s);
}

function loadDraftFromBucket(bucket: string): SendDraft | null {
  const d = readDraftStore().items[bucket];
  return d && draftHasContent(d) ? d : null;
}

/** Does the draft "really have something"?
 *  An empty form should not occupy a bucket — otherwise every open pops a "restored" hint with nothing inside. */
function draftHasContent(d: Pick<SendDraft, "text" | "cmdId" | "taskId" | "execName" | "skillIds" | "params">) {
  return !!(
    d.text.trim() || d.cmdId || d.taskId || d.execName.trim() ||
    d.skillIds.length || Object.keys(d.params || {}).length
  );
}

function draftHhmm(ts: number) {
  const d = new Date(ts);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export interface UniversalSendResult {
  mode: UniversalMode;
  /** The session key that really got persisted (backend authoritative echo; for quick command the kernel echo). */
  session_key: string;
  /** Execution id (absent in text mode). */
  run_uuid?: string;
  target_agent: string;
  task_name?: string;
}

/* [2026-11-14 20:31 Feng] "Do not open another box below, it is unnecessary and takes up space,
   just add the message you sent into the history conversation above."
   → The whole "sent N items" stream (`SentRecord` + `uib-result`) is removed.
     Its job is taken over by the "pending ingest" row of session-history: that message first appears as a local
     placeholder, and after the collector writes it into `agx_user_message` it is automatically replaced by the real row.
   ★ What is deleted is the **temporary cache** of "send stream within this session", not the message itself —
     messages have a single source of truth in the DB, more trustworthy than here. */

export interface MessageSendDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Prefilled values (values already fixed externally). */
  initial?: Partial<UniversalInputValue>;
  /**
   * Project preselection for the top "quick locate" (project id).
   *
   * [2026-11-13 23:16 Feng] When coming in from a quick entry, the project dropdown must show
   * **the project the object belongs to**:
   *   "This window that sends a message from the shortcut side, at this moment the project is the project
   *    the session belongs to."
   * If not given, keep the original behavior (all projects) — the default behavior of other entry points
   * is not changed at all.
   */
  initialProject?: string;
  /** Locked items: control disabled, read-only display (already fixed externally, user cannot change). */
  locked?: Partial<Record<UniversalField, boolean>>;
  /** Hidden items: block not rendered at all (more thorough than locked). */
  hidden?: UniversalField[];
  /**
   * Quick command object given directly from outside (used when entering from a specific command).
   *
   * ★ Why not just pass cmdId and let the component find it in the candidate list: the candidate list is
   *   **fetched by the current target Agent**, and for public commands the executor = send subject — once the user
   *   changes the send subject, the list refetches, and this command may not be in the new list (that Agent has no
   *   task with the same name) → selectedCmd becomes null, so the param table and command title all disappear, and
   *   the user sees it as "the command is lost". If we already hold the full object of this command, we should not
   *   look for it elsewhere again.
   */
  fixedCmd?: QuickCmd | null;
  /** Submit success callback. */
  onSent?: (r: UniversalSendResult) => void;
}

/* ────────────────────────────── Small helpers ────────────────────────────── */

/** Default execution timeout for Task / quick command (seconds). Feng set 3600 on 2026-10-19 12:05; backend default is 1800. */
const DEFAULT_TIMEOUT_SEC = "3600";

/** The "no filter" option in the top "Project" dropdown.
 *  Radix Select does not accept a SelectItem with value="", so use a sentinel string instead of an empty one. */
const QUICK_ALL_PROJECTS = "__all__";

const SESSION_TYPE_KEY: Record<string, string> = {
  main: "sendDialog.sessionType.main",
  case: "sendDialog.sessionType.case",
  task: "sendDialog.sessionType.task",
  oneshot: "sendDialog.sessionType.oneshot",
  assistant: "sendDialog.sessionType.assistant",
};

/** [2026-11-12 15:32 Feng] The session dropdown has three groups, each sorted by recent activity descending:
 *   ① Main session (sessionType=main) — always first (and the default value, unchanged)
 *   ② Assistants (isAssistant=1; also accepts sessionType=assistant)
 *   ③ All other sessions (task / oneshot / case…) — time descending, most recent on top
 *
 *  Feng's words: "The first is the main session, then assistants, then the other sessions,
 *            in time descending order, the most recent on top."
 *
 *  ★ Judge assistants by the `isAssistant` field, not by sessionType alone —
 *    an assistant is essentially a session with is_assistant=1 (same table), and the BFF prefers to expose that boolean;
 *    accepting both is so that different snapshot sources (old data / mock) do not push assistants into the third group. */
function sessionGroup(s: SessionRow): number {
  if (s.sessionType === "main") return 0;
  if (s.isAssistant || s.sessionType === "assistant") return 1;
  return 2;
}

function orderSessions(arr: SessionRow[]): SessionRow[] {
  return [...arr].sort((a, b) => {
    const ga = sessionGroup(a);
    const gb = sessionGroup(b);
    if (ga !== gb) return ga - gb;
    /* Sorted uniformly by recent activity descending within a group (main session / assistants usually have one each, so this fallback is unambiguous). */
    return (b.lastActiveAt || "").localeCompare(a.lastActiveAt || "");
  });
}

/**
 * Session display name (Feng 2026-10-20): `session_id · session_name`
 *
 * - session_id: take the part after the last colon (`agent:x:main` → `main`, `agent:x:run_abc` → `run_abc`)
 * - session_name: truncate to 20 by **display width** (CJK counts 2, English 1, i.e. about 10 CJK chars),
 *   add an ellipsis when too long. This way long names do not break the dropdown layout.
 * - With no session_name, show only the id; session_type is appended at the end as a fallback.
 */
const SESSION_NAME_MAX_WIDTH = 20;

/** Truncate by display width (CJK / full-width counts 2 columns, the rest 1). */
function truncateByWidth(text: string, maxWidth: number): string {
  if (!text) return "";
  let width = 0;
  let out = "";
  for (const ch of text) {
    const w = /[\u1100-\u115F\u2E80-\u303E\u3041-\u33FF\u3400-\u4DBF\u4E00-\u9FFF\uA000-\uA4CF\uAC00-\uD7A3\uF900-\uFAFF\uFE30-\uFE6F\uFF00-\uFF60\uFFE0-\uFFE6]/.test(ch) ? 2 : 1;
    if (width + w > maxWidth) return out + "…";
    width += w;
    out += ch;
  }
  return out;
}

/** Take only the part of session_id after the last colon. */
function sessionShortId(id: string): string {
  if (!id) return "";
  const i = id.lastIndexOf(":");
  return i >= 0 ? id.slice(i + 1) : id;
}

/**
 * "Is this session running something right now"
 *
 * [2026-11-07 Feng] "If the session is running, add a green dot in front; if not, no need."
 *
 * ★ [2026-11-14 10:40 Feng] Criterion narrowed to **only `status === "running"`** (dropped `|| hasActiveRun`).
 *   This used to be a **local copy**, a second implementation alongside `session-runtime-badge.isSessionRunning`;
 *   changing two places separately inevitably drifts, so this function now **delegates directly** to that single criterion.
 *   runtime being null means **not found in Redis** (no data after dedup), not "not running",
 *   but we can only treat it as "no running signal" — so "draw no dot", not "draw a grey dot".
 */
function sessionRunning(s: SessionRow): boolean {
  return isSessionRunning(s.runtime);
}

/**
 * "Running" small badge (green dot + text).
 *
 * [2026-11-13 23:29 Feng] "Everywhere this session appears, its status should be marked...
 *   you select this session and it is running, you can add a marker after it."
 * ★ Make it a component instead of copying everywhere: the dropdown selected value, two lines of the summary bar,
 *   and other places later all use the same one, otherwise "dot size / copy / animation" will drift in three places.
 */
function RunningMark({
  show,
  runId,
  testId,
  className,
}: {
  show: boolean;
  runId?: string;
  testId?: string;
  className?: string;
}) {
  const { t } = useTranslation();
  if (!show) return null;
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 whitespace-nowrap text-[10px] font-medium text-success",
        className,
      )}
      title={runId ? t("sendDialog.running.titleWithRun", { id: runId }) : t("sendDialog.running.title")}
      data-testid={testId}
    >
      <span className="relative flex h-1.5 w-1.5">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-60" />
        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-success" />
      </span>
      {t("sendDialog.running.label")}
    </span>
  );
}

/** Check whether a session is currently running (running on any host counts; same criterion as the dropdown dot).
 *  Not found → treat as "not running": better to let it through than let a network blip permanently mark the UI as running.
 *  [2026-11-14 10:40 Feng] Only `status === "running"` counts, `hasActiveRun` is no longer consulted
 *  (reason in `session-runtime-badge.isSessionRunning`). */
async function probeSessionRunning(key: string): Promise<{ running: boolean; runId: string }> {
  if (!key) return { running: false, runId: "" };
  try {
    const r = await sessionRuntimeApi.lookup(key);
    const list = Object.values(r?.perHost || {});
    const hit = list.find((x) => x && x.status === "running");
    return { running: !!hit, runId: hit?.activeRunId || "" };
  } catch {
    return { running: false, runId: "" };
  }
}

/** [2026-11-15 15:42 Feng] The main session must always be selectable — add it even if the snapshot lacks it
 *
 *  Quote: "I see there is no main session to select in here"
 *  (Scene: on the grid page click "message send" for some Agent; both the send-session / receive-session dropdowns are empty)
 *
 *  Root cause: dropdown candidates only come from rows of `agx_session`. **An Agent never chatted with has no main row**
 *  (measured: book_writer / expo_developer ... a batch like this), so candidates are empty → both dropdowns show the placeholder.
 *  But `agent:{id}:main` is a platform convention: it is created when the first message is sent.
 *  It should not be unselectable in the UI because "the DB has no such row yet" — that leaks storage details to the user.
 */
function withMainSession(list: SessionRow[], agentId: string, t: (k: string) => string): SessionRow[] {
  if (!agentId) return list;
  const key = `agent:${agentId}:main`;
  if (list.some((s) => s.id === key)) return list;
  const m: SessionRow = {
    id: key,
    name: t("sendDialog.sessionType.main"),
    agentId,
    agentName: "",
    sessionType: "main",
    status: "idle",
    messageCount: 0,
    lastActiveAt: "",
  };
  return [m, ...list];
}

function sessionLabel(s: SessionRow, t: (k: string) => string): string {
  const shortId = sessionShortId(s.id);
  const rawName = (s.name || "").trim();
  if (!rawName || rawName === s.id) {
    const typeKey = s.sessionType ? SESSION_TYPE_KEY[s.sessionType] : undefined;
    return typeKey ? `${shortId} · ${t(typeKey)}` : shortId;
  }
  const name = truncateByWidth(rawName, SESSION_NAME_MAX_WIDTH);
  return `${shortId} · ${name}`;
}

/** Agent display name: `name (id)`; if no name, show only the id. */
function agentText(id: string): string {
  if (!id) return "";
  const a = listAgents().find((x) => x.id === id);
  // [2026-11-04 Feng] Dropdown text is uniformly prefixed with [host_id] (see providers agentOptionText)
  return a ? agentOptionText(a) : id;
}

/** In-layer form row (Feng's second revision 2026-10-19: label directly above the input)
 *
 * Why not use FormRow with left/right layout:
 *   FormRow's label column is fixed at 8em (≈112px). In-layer labels are only 4~6 CJK chars (≈48~72px),
 *   and the leftover blank + gap leaves a 60~80px hole between "label → input", which looks scattered.
 *   Change to [stacked]: label hugs the input's top edge (6px), and the input fills the whole row width.
 * Hierarchy: label 12px / normal / muted; block title 14px / semibold / foreground.
 */
function URow({
  label, htmlFor, required, hint, error, children, className, layout = "lr", hideLabel = false, ...rest
}: {
  label: string;
  htmlFor?: string;
  required?: boolean;
  hint?: string;
  error?: string;
  children: React.ReactNode;
  /** lr = label left, control right (default, list-style, saves height); tb = label on top, control below (for large textarea) */
  layout?: "lr" | "tb";
  /** [2026-11-13 00:23 Feng] No label for the large textarea —
      "That input box under the content, drop the content label, it is obvious at a glance"
      ★ Only hide the label block, do not delete URow itself: data-testid / data-row-layout are still there,
        the acceptance script locates by these two attributes, so deleting the container would also dismantle
        someone else's test. */
  hideLabel?: boolean;
} & React.HTMLAttributes<HTMLDivElement>) {
  const isTb = layout === "tb";
  return (
    <div
      className={cn("flex min-w-0 flex-col gap-1", isTb && "gap-1.5", className)}
      data-testid={`row-${label}`}
      data-row-layout={layout}
      {...rest}
    >
      {/* [2026-11-13 15:10 Feng] "The label is left-aligned, but there is still a gap between the dropdown and
          the label; the box should hug the label, no space in between."
          ★ Root cause: lr used to be `grid-cols-[6em_1fr]` — the label column was **fixed 96px**.
            A label is only 4 chars (about 56px), but the column width was pinned by the widest one, "timeout (s)",
            so every short label left 30+ px of blank after it and the box looked "far away".
          ★ Fix: grid → flex, the label `shrink-0` occupies its own text width,
            the control `flex-1` follows, with only 8px between them (gap-x-2) — that is a "gap",
            not a "blank column". The column width is no longer hardcoded, so a font-size change cannot misalign it.
          ★ The four labels in the left column have the same character count (send subject / sender session /
            receive subject / receive session), so their box left edges still form a vertical line and do not
            stagger just because the fixed column is gone.
          ★ `grid`'s `col-span-2` (error wrapping) does not work under flex, so error is changed to
            its own row (the outer flex-col wraps naturally). */}
      {/* ★ The tb variant (label on top, large textarea) must be `min-h-0 flex-1`:
          this middle wrapper was added on 2026-11-13 15:10 for "label hugs control",
          but at the time only `flex-col gap-1.5` was written and it was not hooked into the outer flex chain —
          the result was the outer layer stretched to the full column height while the inner one only grew by content
          (Textarea's min-h-160), leaving a big blank under the body box (Feng 19:20: "this box is smaller than the
          column frame, what is the point of leaving blank below").
          `flex-1` is the only thing that hooks it back into that chain, not an optional style. */}
      <div className={cn("flex min-w-0", isTb ? "min-h-0 flex-1 flex-col gap-1.5" : "items-center gap-x-2")}>
      {/* lr and tb are both left-aligned now.
          [2026-11-13 14:37 Feng] "The dropdown titles under sender and receiver are all the same length now,
          change to left-aligned, there is blank on the left now."
          ★ lr used to be **right-aligned** (label column 6em, right edge forms a vertical line). When the labels on
            the two sides have different lengths (send subject / sender session / receive subject / receive session,
            or a 5-char one like "timeout (s)"), a short label leaves a blank on its left, looking unaligned.
          ★ After unifying to left alignment: all labels start at the same x, a clean vertical line on the left;
            the length difference falls on the right side, visually steadier than "right-aligned + ragged left".
          ★ The 6em column width is kept: the dropdown itself still starts at the same x and will not shift because
            of the alignment change. */}
      {!hideLabel && (
      <div className="flex min-w-0 flex-col items-start gap-0.5">
        <FieldLabel
          label={label}
          htmlFor={htmlFor}
          required={required}
          className="justify-start text-sm font-medium leading-none"
        />
        {hint ? (
          <span className="text-[11px] leading-tight text-muted-foreground">{hint}</span>
        ) : null}
      </div>
      )}
        <div className={cn("flex min-w-0 flex-col gap-1.5", isTb ? "min-h-0 flex-1" : "flex-1")}>
          {children}
        </div>
      </div>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  );
}

/** Read-only display of a locked item (with a lock icon, telling "already fixed externally"). */
function LockedValue({ text, mono }: { text: string; mono?: boolean }) {
  const { t } = useTranslation();
  return (
    <div className="flex h-9 items-center gap-1.5 rounded-md border bg-muted/40 px-3 text-sm text-muted-foreground">
      <Lock className="h-3.5 w-3.5 shrink-0" />
      {/* [2026-11-14 21:45] The "open session" button was added on the right, and after giving width to the button the
          value is easier to truncate — add a title so hover shows the full name (what is truncated is the display,
          not the info). */}
      <span className={mono ? "truncate font-mono text-xs" : "truncate"} title={text || undefined}>
        {text || t("sendDialog.lockedUnset")}
      </span>
    </div>
  );
}

/** "Open session" — the button after the session dropdown.
 *
 *  [2026-11-08 21:14 Feng] "The session is a dropdown, I want another button after this dropdown,
 *  clicking it opens this session."
 *
 *  ★ Why not make the dropdown item itself a link: clicking a dropdown item means "select" (it changes the landing
 *    point of this send), while "open" is another matter that changes nothing. Squeezing two actions onto the same
 *    hit target, a mis-touch costs "thought I selected it, actually it navigated away".
 *  ★ Why it must be a new tab: this is a "take a peek" before sending. In-place navigation would lose the body
 *    already typed, the chosen subject / task / skills — a far higher cost than one extra tab.
 *    (Same convention as the site-wide "open session" `OpenClawSessionLink`: `target=_blank`.)
 *  ★ No session selected / this Agent's platform has no session page → the button **stays in place but greyed out**,
 *    never disappears. A disappearing button = row width jumps: button absent before selection, appears after,
 *    controls on the right shift left/right, and the first reaction is "what did I click wrong".
 */
/** [2026-11-15 23:50 Feng] Add an "Open" after send subject / receive subject each —
 *   "Just now I wanted to send a message to this agent, but I saw it has a problem, and closing the window and running
 *    over there (the agent page) is too much trouble. You can open this agent right here, I can just take a look."
 *
 * [2026-11-15 23:58 Feng correction] "Do not open a new page, pop a window right in this page,
 *    there is already this popup feature here — pop another window on top of this, close it after, no need to
 *    open a new page, this popup is not a new web page."
 *
 * So it goes through the **site-wide unified entity detail dialog** (`DetailPopupProvider` → `useDetailPopup().open`),
 * not `<a target=_blank>`:
 *   · stacked on the send window, closing returns to the original place, the body / subject / task are all kept;
 *   · the detail body is `EntityDetailBody("agent")` → `AgentDetailPage embedded`,
 *     the **same component** as /agents/{id}, not a separately drawn one;
 *   · inside the popup you can keep drilling down (execution → session → …), closing layer by layer.
 * Only when there is no Provider (e.g. bare render in unit tests) does it degrade to a link, per the existing
 * detail-popup convention.
 */
function AgentOpenButton({
  agentId,
  testId,
  hint,
}: {
  /** Agent ID. Empty = not selected yet. */
  agentId?: string;
  testId: string;
  hint?: string;
}) {
  const { t } = useTranslation();
  const popup = useDetailPopup();
  const id = (agentId || "").trim();
  const base =
    "inline-flex h-9 shrink-0 items-center gap-1 whitespace-nowrap rounded-md border px-2 text-xs font-medium transition-colors";

  if (!id) {
    return (
      <button
        type="button"
        disabled
        title={hint || t("sendDialog.open.agentDisabled")}
        data-testid={`${testId}-disabled`}
        aria-disabled="true"
        className={cn(base, "cursor-not-allowed border-input bg-muted/40 text-muted-foreground/60")}
      >
        <ExternalLink className="h-3.5 w-3.5" />
        {t("sendDialog.open.agent")}
      </button>
    );
  }

  if (!popup) {
    return (
      <a
        href={`/agents/${id}`}
        target="_blank"
        rel="noreferrer noopener"
        title={t("sendDialog.open.agentTitle", { id })}
        data-testid={testId}
        className={cn(base, "border-input bg-card text-primary hover:bg-accent hover:underline underline-offset-2")}
      >
        <ExternalLink className="h-3.5 w-3.5" />
        {t("sendDialog.open.agent")}
      </a>
    );
  }

  return (
    <button
      type="button"
      /* from="message-send": tells the detail "you were opened from the send window" → the detail hides its own "message send" button */
      onClick={() => popup.open({ kind: "agent", id, from: "message-send" })}
      title={t("sendDialog.open.agentPopupTitle", { id })}
      data-testid={testId}
      className={cn(base, "border-input bg-card text-primary hover:bg-accent hover:underline underline-offset-2")}
    >
      <ExternalLink className="h-3.5 w-3.5" />
      {t("sendDialog.open.agent")}
    </button>
  );
}

function SessionOpenButton({
  sessionId, testId, hint,
}: {
  /** Full session id (agent:{id}:{slug}). Empty = not selected yet. */
  sessionId?: string;
  testId: string;
  /** Explain in grey state why it cannot be clicked (do not just say "unavailable") */
  hint?: string;
}) {
  const { t } = useTranslation();
  const sid = (sessionId || "").trim();
  const href = sid ? sessionDeepLink(sid) : null;

  const base =
    "inline-flex h-9 shrink-0 items-center gap-1 whitespace-nowrap rounded-md border px-2 text-xs font-medium transition-colors";

  if (!href) {
    return (
      <button
        type="button"
        disabled
        title={
          sid
            ? t("sendDialog.open.sessionNoPage")
            : hint || t("sendDialog.open.sessionDisabled")
        }
        data-testid={`${testId}-disabled`}
        aria-disabled="true"
        className={cn(base, "cursor-not-allowed border-input bg-muted/40 text-muted-foreground/60")}
      >
        <ExternalLink className="h-3.5 w-3.5" />
        {t("sendDialog.open.session")}
      </button>
    );
  }

  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      title={t("sendDialog.open.sessionTitle", { href })}
      data-testid={testId}
      className={cn(base, "border-input bg-card text-primary hover:bg-accent hover:underline underline-offset-2")}
    >
      <ExternalLink className="h-3.5 w-3.5" />
      {t("sendDialog.open.session")}
    </a>
  );
}

/** Layered colors (Feng 2026-10-19 13:11): each layer's border + title share a color, blocks are distinguishable at a glance.
 *  Only design tokens (info / chart-* / success / primary / muted), no raw palette. */
type LayerAccent = "info" | "violet" | "orange" | "success" | "pink" | "primary" | "muted";

const LAYER_ACCENT: Record<LayerAccent, { border: string; title: string; badge: string }> = {
  info:    { border: "border-info/40",             title: "text-info",             badge: "bg-info/10 text-info" },
  violet:  { border: "border-chart-1/40",          title: "text-chart-1",          badge: "bg-chart-1/10 text-chart-1" },
  orange:  { border: "border-chart-2/45",          title: "text-chart-2",          badge: "bg-chart-2/10 text-chart-2" },
  success: { border: "border-success/40",          title: "text-success",          badge: "bg-success/10 text-success" },
  pink:    { border: "border-chart-4/40",          title: "text-chart-4",          badge: "bg-chart-4/10 text-chart-4" },
  primary: { border: "border-primary/40",          title: "text-primary",          badge: "bg-primary/10 text-primary" },
  muted:   { border: "border-muted-foreground/25", title: "text-muted-foreground", badge: "bg-muted text-muted-foreground" },
};

function LayerTitle({
  n, title, hint, accent = "primary",
}: {
  n: string;
  title: string;
  hint?: React.ReactNode;
  accent?: LayerAccent;
}) {
  const a = LAYER_ACCENT[accent];
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
      <span className={cn("flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold", a.badge)}>
        {n}
      </span>
      <span className={cn("text-base font-semibold leading-tight tracking-tight", a.title)}>{title}</span>
      {hint ? <span className="text-[11px] text-muted-foreground">{hint}</span> : null}
    </div>
  );
}

/* [2026-11-16 13:08 Feng] "This owning-host dropdown, you default to the current Agent's host, but you did not set
   the dropdown option to this current host, so it looks to me like I selected it, while actually nothing is selected".
   ★ This is **two things stacked**: ① when the dropdown is unselected it shows the example value from the schema
     (looks selected, see the SELECT_HINT comment of ParamField); ② owning host already has a definite answer
     (this very machine), yet the user is left to pick it every time.
   ★ This fixes ②: for a required `source=host` param, default to "current host" —
     the send subject's host → else the receiver's host → else leave empty (do not invent).
     Only fill **required** ones: optional host params (e.g. "empty = local") stay empty, do not turn
     "not expressed" into "expressed". */
function buildInitParams(
  cmd: { params: { name: string; source?: string; required?: boolean; type?: string; default?: unknown }[] } | null,
  senderAgent: string,
  targetAgent: string,
): Record<string, unknown> {
  const init: Record<string, unknown> = {};
  if (!cmd) return init;
  const agents = listAgents();
  const hostOf = (id: string) => agents.find((a) => a.id === id)?.host_id || "";
  const currentHost = hostOf(senderAgent) || hostOf(targetAgent);
  for (const p of cmd.params) {
    init[p.name] = initialValue(p as never);
    if (p.source === "host" && p.required && !init[p.name] && currentHost) {
      init[p.name] = currentHost;
    }
  }
  return init;
}

const MODE_META: Record<
  UniversalMode,
  { labelKey: string; icon: React.ComponentType<{ className?: string }>; hintKey: string }
> = {
  text: { labelKey: "sendDialog.mode.text.label", icon: MessageSquare, hintKey: "sendDialog.mode.text.hint" },
  task: { labelKey: "sendDialog.mode.task.label", icon: ListChecks, hintKey: "sendDialog.mode.task.hint" },
  quickcmd: { labelKey: "sendDialog.mode.quickcmd.label", icon: Zap, hintKey: "sendDialog.mode.quickcmd.hint" },
};

/* ─────────────────── Body toolbar (common phrases / voice), shared by both places ─────────────────── */
/** [2026-11-17 10:19 Feng]
 *  "After enlarging it, the quick buttons below must come along, including common phrases and voice, they must come...
 *    those buttons below, bring them all."
 *
 * ★ Why extract a component instead of redrawing the JSX in the main box:
 *   the two places must be the **same set of buttons, same conventions** (common phrases list only text snippets,
 *   voice inserts at the cursor, clickable decided by editable). Copying = leaving two branches that will
 *   inevitably diverge.
 * ★ Why `scope`: the enlarged window is an overlay and the main box is still in the DOM — two instances coexist.
 *   data-testid must have different prefixes, otherwise Playwright's strict mode matches both at once.
 */
function TextToolsBar({
  scope = "main",
  editable,
  senderAgent,
  phrases,
  phrasesLoading,
  phraseOwn,
  phraseShared,
  open,
  onOpenChange,
  onPick,
  onAddPhrase,
  onVoice,
  onVoicePhase,
}: {
  scope?: "main" | "zoom";
  editable: boolean;
  senderAgent: string;
  phrases: QuickCmd[];
  phrasesLoading: boolean;
  phraseOwn: QuickCmd[];
  phraseShared: QuickCmd[];
  open: boolean;
  onOpenChange: (v: boolean) => void;
  /** A common phrase selected → the caller decides "which input's cursor to insert at" */
  onPick: (c: QuickCmd) => void;
  onAddPhrase: () => void;
  onVoice: (text: string) => void;
  /** Recording stage report (decides whether sending is possible). At `recording` the body is not final; at `transcribing` the text has not landed yet. */
  onVoicePhase: (phase: VoicePhase) => void;
}) {
  const { t } = useTranslation();
  const tid = (name: string) => (scope === "main" ? `uib-${name}` : `zoom-${name}`);
  const renderItem = (c: QuickCmd, shared = false) => (
    <button
      key={c.cmdId}
      type="button"
      className="flex w-full flex-col gap-0.5 rounded px-2 py-1.5 text-left hover:bg-muted"
      onClick={() => {
        onPick(c);
        onOpenChange(false);
      }}
      data-testid={`${tid("phrase")}-${c.cmdId}`}
      data-shared={shared ? "1" : "0"}
    >
      <span className="flex items-center gap-1.5 text-xs font-medium">
        {c.title || c.cmdId}
        <span className="rounded bg-muted px-1 text-[10px] font-normal text-muted-foreground">
          {t("sendDialog.phrases.chars", { n: (c.composedInstruction || "").length })}
        </span>
        {shared && c.ownerAgent && (
          <span className="ml-auto font-mono text-[10px] font-normal text-muted-foreground/80">
            {c.ownerAgent}
          </span>
        )}
      </span>
      <span className="line-clamp-2 text-[11px] leading-snug text-muted-foreground">
        {c.composedInstruction}
      </span>
    </button>
  );

  return (
    <>
      {/* [2026-11-14 Feng] Common phrases: list only commands with `sendMode='text'`,
          selecting inserts at the cursor — **does not send, does not create an execution**. */}
      <div className="pointer-events-auto">
        <Popover open={open} onOpenChange={onOpenChange}>
          <PopoverTrigger asChild>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-7 gap-1 px-2 text-xs text-muted-foreground hover:text-foreground"
              disabled={!editable || !senderAgent}
              title={t("sendDialog.phrases.open")}
              data-testid={tid("phrase-open")}
            >
              <ClipboardList className="h-3.5 w-3.5" />
              {t("sendDialog.phrases.title")}
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-[420px] p-0" data-testid={tid("phrase-pop")}>
            {/* [2026-11-14 Feng] "Add an add button... this button should always be there" —
                whether the list is empty or has this item, the button is in the top-right corner,
                clicking it opens the create form directly (create in place, no trip to the "quick command" page). */}
            <div className="flex items-center justify-between gap-2 border-b px-3 py-2">
              <span className="text-xs font-medium">{t("sendDialog.phrases.title")}</span>
              <div className="flex items-center gap-2">
                <span className="font-mono text-[10px] text-muted-foreground">{senderAgent || t("sendDialog.phrases.noSender")}</span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-6 gap-1 px-1.5 text-[11px]"
                  disabled={!senderAgent}
                  title={t("sendDialog.phrases.addTitle")}
                  data-testid={tid("phrase-add")}
                  onClick={() => {
                    onOpenChange(false);
                    onAddPhrase();
                  }}
                >
                  <Plus className="h-3 w-3" />
                  {t("sendDialog.phrases.add")}
                </Button>
              </div>
            </div>
            {/* [2026-11-26 Feng bug report: the phrase list could not be scrolled] Root cause measured, not guessed:
                this list really is a scroll container (overflow-y:auto, scrollHeight 395 > clientHeight 360), but the
                modal dialog's scroll lock (react-remove-scroll inside Radix Dialog) treats a **portaled** popover as
                "outside the lock" and calls preventDefault() on every wheel event -- the event reaches the div with
                defaultPrevented=true and scrollTop never moves.
                Fix: if the native scroll did not move by the next frame, apply the wheel delta ourselves. The guard
                means we never double-scroll in a context where the native path still works. */}
            <div
              className="max-h-[360px] overflow-y-auto p-1"
              data-testid={tid("phrase-scroll")}
              onWheel={scrollWhenNativeBlocked}
            >
              {phrasesLoading ? (
                <p className="flex items-center gap-1.5 px-2 py-3 text-xs text-muted-foreground">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  {t("sendDialog.phrases.loading")}
                </p>
              ) : phrases.length === 0 ? (
                <p className="px-2 py-3 text-xs leading-relaxed text-muted-foreground">
                  {t("sendDialog.phrases.empty")}
                </p>
              ) : (
                <>
                  {phraseOwn.length > 0 && (
                    <div data-testid={tid("phrase-group-own")}>
                      <p className="px-2 pb-1 pt-1.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                        {t("sendDialog.phrases.ownGroup")}
                      </p>
                      {phraseOwn.map((c) => renderItem(c))}
                    </div>
                  )}
                  {phraseShared.length > 0 && (
                    <div data-testid={tid("phrase-group-shared")}>
                      <p className="px-2 pb-1 pt-1.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                        {t("sendDialog.phrases.sharedGroup")}
                      </p>
                      {phraseShared.map((c) => renderItem(c, true))}
                    </div>
                  )}
                </>
              )}
            </div>
          </PopoverContent>
        </Popover>
      </div>
      <div className="pointer-events-auto">
        <VoiceInput
          disabled={!editable}
          onText={onVoice}
          onPhaseChange={onVoicePhase}
          testIdPrefix={scope === "main" ? "uib-voice" : "zoom-voice"}
        />
      </div>
    </>
  );
}

/* [2026-11-26 Feng bug: "the phrase list cannot be scrolled"] Measured root cause, not guessed:
   the list really is a scroll container (overflow-y:auto, scrollHeight 576 > clientHeight 360), yet every wheel event
   arrived with defaultPrevented=true -- the modal dialog's scroll lock (react-remove-scroll inside Radix Dialog)
   treats a **portaled** popover as "outside the lock" and unconditionally preventDefault()s wheel there, so scrollTop
   never moved.
   Fix: after the frame, if the native scroll did NOT move, apply the accumulated wheel delta ourselves.
   Why accumulate in a WeakMap instead of one shot per event: several wheel events can land in the same frame
   (fast wheel / trackpad); without accumulation the first rAF sets scrollTop and the later callbacks see
   "it already moved" and drop their delta -- measured to halve the scroll distance. */
const wheelAcc = new WeakMap<HTMLElement, { acc: number; base: number; queued: boolean }>();

function scrollWhenNativeBlocked(e: React.WheelEvent<HTMLElement>) {
  const el = e.currentTarget;
  const delta = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
  let state = wheelAcc.get(el);
  if (!state) {
    state = { acc: 0, base: el.scrollTop, queued: false };
    wheelAcc.set(el, state);
  }
  state.acc += delta;
  if (state.queued) return;
  state.queued = true;
  const cur = state;
  requestAnimationFrame(() => {
    if (el.scrollTop === cur.base) el.scrollTop = cur.base + cur.acc;
    wheelAcc.delete(el);
  });
}

/* ────────────────────────────── Main component ────────────────────────────── */

export function MessageSendDialog({
  open,
  onOpenChange,
  initial,
  initialProject = "",
  locked = {},
  hidden = [],
  fixedCmd = null,
  onSent,
}: MessageSendDialogProps) {
  const { t } = useTranslation();
  /* [2026-11-14 10:57 Feng] The "receiver lock" brought in by the entry point is only an **initial constraint**,
     not a permanent one. Feng's words: "It can be locked at the moment of entry. But once something above changes,
     the lock below should be released, it should follow the change above... otherwise this thing is not flexible.
     If I now want to send a message to someone else, I can only pick from below."
     → The user actively changing project / Agent = changing context, the originally pinned receive session no longer
       belongs to the new context, keeping it locked would trap the person in this session. So below `effLocked`
       removes the targetAgent / targetSession items from the lock table (other locked items are unaffected). */
  const [targetLockBroken, setTargetLockBroken] = React.useState(false);
  const effLocked = React.useMemo<Partial<Record<UniversalField, boolean>>>(() => {
    if (!targetLockBroken) return locked;
    const next: Partial<Record<UniversalField, boolean>> = { ...locked };
    delete next.targetAgent;
    delete next.targetSession;
    return next;
  }, [locked, targetLockBroken]);
  const isLocked = (k: UniversalField) => !!effLocked[k];
  /* [2026-11-13 22:49 Feng] The star in the title bar becomes "favorite the current landing point", no longer jumps
     to a quick entry: "Clicking this button should favorite this session, or unfavorite it. Jumping to a quick entry
     from this button is meaningless, right? Because this window is used the most, favorite it here."
     ★ Favorite target = the current **receive landing point**: if a receive session is selected, favorite the session,
       otherwise favorite the receive Agent. A session is more specific than an Agent; with multiple sessions under
       the same Agent, what people want to pin is that one. */
  const isHidden = (k: UniversalField) => hidden.includes(k);

  /* [2026-11-06 22:30 Feng] "Forgot to add a refresh button to this Agent popup, it still needs a refresh".
     All dropdowns in this popup are store data (Agent / session / task template / skill / quick command),
     a newly added Agent or task never appears without a refetch.
     ★ Refresh only makes the **data source recompute** (tick enters dependencies), it does not remount the form —
       remounting would clear what the person has already filled in. */
  const [tick, setTick] = React.useState(0);

  /* Quick favorite: target = receive session (preferred) or receive Agent */
  const [quickOn, setQuickOn] = React.useState(false);
  const [quickBusy, setQuickBusy] = React.useState(false);
  /* [2026-11-13 00:17 Feng] The dialog must be able to go fullscreen.
     [00:21 Feng correction] "It should be like the flow chart, filling the whole screen — you are only filling the
     browser's display area"
     → What is wanted is **native browser fullscreen** (Fullscreen API), not enlarging the dialog to fill the viewport.
     Approach aligned with `pages/agentgeex/plan-flow.tsx`'s `toggleFullscreen` (the verified one):
       · setMaximized(true) first to apply CSS → then requestFullscreen (so a failure does not leave a white screen)
       · when disabled, do not error, silently degrade to the CSS overlay
       · listen to fullscreenchange: when the user exits via the browser's own way, sync the state back */
  const [maximized, setMaximized] = React.useState(false);
  const dlgRef = React.useRef<HTMLDivElement | null>(null);
  /* [2026-11-13 08:07 Feng] After going fullscreen **none of the dropdowns can be opened**.
     Root cause: Radix overlays (Select / Popover / Tooltip) mount to `document.body` by default,
     while native browser fullscreen only renders the fullscreen element and its subtree — body is outside it →
     the overlay lands off-screen, which looks like "clicking does nothing" (the DOM is there, just invisible/not clickable).
     Fix: store the dialog's own node and use it as the overlay mount point while fullscreen (see ui/portal-container.tsx).
     ★ Store state instead of only reading a ref: the Provider needs the value to pass it down at render time. */
  const [dlgNode, setDlgNode] = React.useState<HTMLDivElement | null>(null);
  const attachDlgRef = React.useCallback((node: HTMLDivElement | null) => {
    dlgRef.current = node;
    setDlgNode(node);
  }, []);

  const toggleFullscreen = React.useCallback(async () => {
    if (!maximized) {
      setMaximized(true);
      try {
        await dlgRef.current?.requestFullscreen?.();
      } catch {
        /* Browser refuses (not a user gesture / Safari restriction) → fall back to the CSS overlay */
      }
    } else {
      if (document.fullscreenElement) {
        try { await document.exitFullscreen(); } catch { /* ignore */ }
      }
      setMaximized(false);
    }
  }, [maximized]);

  /* Native fullscreen exited externally (Esc / F11 / gesture) → sync state, otherwise the button icon disagrees with the screen */
  React.useEffect(() => {
    const onChange = () => { if (!document.fullscreenElement) setMaximized(false); };
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  /* Pressing Esc while fullscreen: **exit fullscreen first, do not also close the dialog** —
     otherwise the person wants to "exit fullscreen" and loses the half-filled window along with it.
     ★ capture=true: grab Esc before Radix Dialog's document listener. */
  React.useEffect(() => {
    if (!maximized) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      /* [2026-11-13 08:20] While a dropdown is open, Esc means "close the dropdown", not "exit fullscreen".
         Without this gate, the person opens a long dropdown in fullscreen and presses Esc to collapse it —
         and the whole window exits fullscreen while the dropdown stays open.
         Criterion uses Radix overlay containers (Select / Popover / Tooltip all go through it). */
      if (document.querySelector("[data-radix-popper-content-wrapper]")) return;
      if (document.fullscreenElement) void document.exitFullscreen?.().catch(() => {});
      setMaximized(false);
      e.stopPropagation();
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [maximized]);

  /* Closing the window also exits **native** fullscreen — just setMaximized(false) does not exit fullscreen,
     which would leave a deadlock of "dialog gone but the screen is still locked". */
  React.useEffect(() => {
    if (open) return;
    setMaximized(false);
    if (document.fullscreenElement) void document.exitFullscreen?.().catch(() => {});
  }, [open]);
  const [refreshing, setRefreshing] = React.useState(false);
  const [staleMsg, setStaleMsg] = React.useState("");
  const doRefresh = React.useCallback(async () => {
    setRefreshing(true);
    setStaleMsg("");
    const r = await refreshStoreOnce(true);
    if (!r.ok) setStaleMsg(r.message || t("sendDialog.refreshFailed"));
    setRefreshing(false);
    setTick((t) => t + 1);
  }, []);

  /* [2026-11-07 Feng] Send subject dropdown = all Agents (including offline / templates).
     "I might want to query" — only by selecting it can its history sessions appear in the dropdown below; filtering
     it out makes it unqueryable forever. Whether it can send is decided by the backend; the offline one carries
     "· offline" in its own text, so people will not mistakenly think it can dispatch work. */
  const allAgents = React.useMemo(() => listAgentOptions(), [tick]);

  /* [2026-11-12 Feng] Archived projects: the project dropdown and all "sendable" candidates in this window must exclude them.
     "Archived projects should not appear anymore, because selecting an archived project is useless."

     ★ The definition must be placed before quickAgents (the snapshot is evaluated in order; placing it later throws TDZ). */
  const activeProjects = React.useMemo(
    () => listProjects().filter((p) => p.status !== "archived"),
    [tick],
  );
  const activeProjectIds = React.useMemo(
    () => new Set(activeProjects.map((p) => p.id)),
    [activeProjects],
  );

  /* [2026-11-11 / 11-12 Feng] The Agent dropdown candidates of the top "quick locate" = **only those that can really work**:
     ① no offline ones ("an offline one can do nothing")
     ② no templates ("these are not registered in Openclaw" — not registered = no session, no history)
     ③ no ones belonging to **archived projects** ("selecting an archived project is useless")
     100 → 84 (−7 offline −8 templates −1 medicine_gpt_doctor)

     ★ Why also remove "people of archived projects" (no longer because the count matches):
       the project they belong to is archived, and that project no longer exists in the project dropdown.
       Keeping the person in the Agent dropdown produces "can select him, yet cannot find which project he belongs
       to" — the same defunct project, half gone and half still there.

     ★ Note: **this variable does not participate in the count of "all projects (N)"**.
       That N is the **project count** (= projectOptions.length; Feng 2026-11-12 15:08
       explicitly said "84 is the agent count, others read it as 84 projects, too ambiguous").
       This variable is only used for the Agent dropdown candidate set.

     ★ Why collapse at the top, while ①③ only drop templates and not offline:
       top = "who do I send to" = send target candidates → do not show ones that cannot send.
       ①③ = "send subject / receiver" = history entry points → offline people **have history** to query, keep them
       (the 2026-11-07 conclusion); templates **have no history**, keeping them queries nothing, so drop them. */
  const quickAgents = React.useMemo(
    () => allAgents.filter((a) =>
      isAgentSelectable(a) &&
      /* People with no affiliation are not counted as "people of an archived project", keep them; with an affiliation it must be a non-archived project */
      (!a.project_id || activeProjectIds.has(a.project_id)),
    ),
    [allAgents, activeProjectIds],
  );


  /* Skills (agx_skill, valid status only) — skills this task should reference, multi-select */
  const [skillIds, setSkillIds] = React.useState<string[]>([]);
  const [skillQuery, setSkillQuery] = React.useState("");

  /* Skill library (store comes from the /v1/skills snapshot; the BFF already filtered by active_status=1 → only "valid" skills).
     Here we filter locally once more by keyword, for easier search. */
  const allSkills = React.useMemo<SkillRow[]>(() => listSkills(), [open, tick]);
  const skillById = React.useMemo(
    () => new Map(allSkills.map((s) => [s.id, s])),
    [allSkills],
  );
  const skillOptions = React.useMemo<SkillRow[]>(() => {
    const kw = skillQuery.trim().toLowerCase();
    if (!kw) return allSkills;
    return allSkills.filter((s) =>
      `${s.name} ${s.description} ${s.tags} ${s.category}`.toLowerCase().includes(kw),
    );
  }, [allSkills, skillQuery]);
  const selectedSkills = React.useMemo(
    () => skillIds.map((id) => skillById.get(id)).filter((x): x is SkillRow => !!x),
    [skillIds, skillById],
  );
  const toggleSkill = React.useCallback((id: string) => {
    setSkillIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }, []);

  /* ── Recording stage (each of the two TextToolsBar reports one) ──
     ★ Why store separated by scope and then OR them: the main box and the enlarged window are two **independent
       instances** of VoiceInput, and their recording states are unrelated. Storing a single boolean means the one
       mounted later overwrites the former — the main box is recording, the enlarged window opens and reports idle,
       and the lock is gone. */
  const [voicePhase, setVoicePhase] = React.useState<Record<string, VoicePhase>>({});
  const voiceLocked = voiceBlocksSend(voicePhase.main ?? "idle") || voiceBlocksSend(voicePhase.zoom ?? "idle");
  const voiceBlockReason =
    voicePhase.main === "recording" || voicePhase.zoom === "recording"
      ? t("sendDialog.voice.recording")
      : voiceLocked
        ? t("sendDialog.voice.transcribing")
        : "";

  /* ── State (controlled component: external initial decides the initial value / lock decides mutability) ── */
  const [senderAgent, setSenderAgent] = React.useState("");
  const [senderSession, setSenderSession] = React.useState("");
  const [targetAgent, setTargetAgent] = React.useState("");

  /* ── [2026-11-10 Feng] Top of the window "quick locate": project + Agent ──
     "To send different messages, opening one window is enough" — no need to close and reopen to switch person.
     ★ The project is used **only** to narrow the Agent dropdown above; the Agent dropdowns in ①③ remain full.
       Reason: the sender may belong to another project (cross-project messaging is normal); narrowing those too
       would produce "this person is in the DB, yet cannot be found in the dropdown". Narrowing must be undoable,
       must not take effect silently. */
  const [projFilter, setProjFilter] = React.useState("");

  const projectOptions = React.useMemo(() => {
    const n = new Map<string, number>();
    /* [convention] This memo produces two things with different meanings, do not mix them:
         · `length` / item count = **project count** → used by the top "all projects (N)" (Feng 2026-11-12)
         · `p.n`               = **usable agent count under that project** → shown right of the project name, meaningful
       So the counting convention of n below (counted by how many the Agent dropdown can select, excluding
       offline/templates) only affects the small number on the right of each item, and **no longer affects the number
       in the "all projects" parentheses**. */
    for (const a of quickAgents) {
      const pid = a.project_id || "";
      if (pid) n.set(pid, (n.get(pid) || 0) + 1);
    }
    /* [2026-11-17 15:07 Feng] Pinned projects first — the rest still sorted by "usable Agent count in the project".
       Use the shared comparator: the convention is written once in providers (see the sortByQuickProject comment). */
    return sortByQuickProject(
      activeProjects
        .map((p) => ({ id: p.id, name: p.name || projectName(p.id) || p.id, n: n.get(p.id) || 0 }))
        .sort((a, b) => b.n - a.n || a.name.localeCompare(b.name)),
      (p) => p.id,
    );
  }, [tick, quickAgents, activeProjects]);

  /** Who the top Agent dropdown shows: prefer "receiver", otherwise "sender" (in text mode the two are equal). */
  const quickAgentValue = targetAgent || senderAgent;

  /** Whether the top dropdown is usable: when both places are locked (the session-entry kind) it has nothing to change, and being greyed out beats making people click for nothing. */
  const quickAgentUsable = !isLocked("targetAgent") || !isLocked("senderAgent");

  /** The PM (project manager) of a project. If none, fall back to the project's admin_agent_id, then null.
   *  [2026-11-11] Only search among "not offline" ones — otherwise switching projects selects an offline PM,
   *  and the top dropdown, to avoid a blank, puts him back into the list, which is effectively "filtered him out
   *  and then invited him back". */
  const pmOfProject = React.useCallback((pid: string) => {
    const inProj = quickAgents.filter((a) => a.project_id === pid);
    return (
      inProj.find((a) => a.role === "project_manager") ||
      inProj.find((a) => a.id === (listProjects().find((x) => x.id === pid)?.adminAgentId || "")) ||
      null
    );
  }, [quickAgents, tick]);

  const quickAgentOptions = React.useMemo(() => {
    const base = projFilter ? quickAgents.filter((a) => a.project_id === projFilter) : quickAgents;
    /* [2026-11-10 09:31 Feng] "Put the PM first" —
       the first person one should see in a project is the PM: he is the interface of this project, and most messages
       go to him first. sort is stable, so the relative order of the rest (host grouping) is unchanged. */
    const sorted = [...base].sort(
      (a, b) => Number(b.role === "project_manager") - Number(a.role === "project_manager"),
    );
    /* ★ The currently selected one must be in the list: otherwise the dropdown shows blank,
       what the person sees is "not selected" while the value is still there — that is deceptive.
       Note: switching projects **actively changes it to that project's PM** (see changeQuickProject), so this only
       falls back when "the Agent was changed elsewhere (e.g. the sender in ① below)".
       [2026-11-11] Fallback goes to allAgents (including offline): a locked value / an offline Agent brought in from
       outside must still be displayed, it is just no longer actively listed as a candidate. */
    if (quickAgentValue && !sorted.some((a) => a.id === quickAgentValue)) {
      const cur = allAgents.find((a) => a.id === quickAgentValue);
      if (cur) return [cur, ...sorted];
    }
    return sorted;
  }, [quickAgents, allAgents, projFilter, quickAgentValue]);
  const [mode, setMode] = React.useState<UniversalMode>("text");
  const [sessionMode, setSessionMode] = React.useState<"new" | "existing">("new");
  const [targetSession, setTargetSession] = React.useState("");
  const [taskId, setTaskId] = React.useState("");
  const [cmdId, setCmdId] = React.useState("");
  const [params, setParams] = React.useState<Record<string, unknown>>({});
  const [text, setText] = React.useState("");
  /* ── [2026-11-14 22:40 Feng] Text from speech must be inserted at [the cursor], not appended to the end ──
     Quote: "Every time the speech-recognized text, you put it at the very end... can it go at the cursor,
             wherever my cursor is, the generated text is inserted there, that might be a bit more convenient."
     ★ The cursor needs a separate snapshot: clicking the "voice" button blurs the textarea, and recognition takes
       seconds, during which the person may click another box in the dialog. Blurring does not clear selectionStart,
       but what must be read is "the position before the voice button was clicked", so store it every time the cursor
       moves in the body. */
  const textRef = React.useRef<HTMLTextAreaElement | null>(null);
  const caretRef = React.useRef<{ start: number; end: number } | null>(null);
  /* [2026-11-17 10:19 Feng] The enlarged window also has "common phrases / voice", and what gets inserted must be
     **the enlarged window's own cursor**. The two places each keep their own cursor and do not cross-contaminate
     (click the 3rd char in the main box, go to the end of the enlarged window, then insert — it should be at the end). */
  const zoomTextRef = React.useRef<HTMLTextAreaElement | null>(null);
  const zoomCaretRef = React.useRef<{ start: number; end: number } | null>(null);
  /** Latest value of the body (for fallback when el is absent; read the DOM whenever el is stable) */  const textValueRef = React.useRef(text);
  React.useEffect(() => {
    textValueRef.current = text;
  }, [text]);
  /** Cursor position to restore (after insertion, put it back once React has committed).
   *  `where` says which input to put it in — the two places share the same text, and the cursor must return to
   *  "the one the person is typing in". */
  const pendingCaretRef = React.useRef<{ pos: number; where: "main" | "zoom" } | null>(null);
  /* ★ Cannot use requestAnimationFrame for this: measured, rAF runs **before** React commits,
     so setting the cursor against the old value, then React writes the new value → the cursor is pushed to the end
     again (measured 8 instead of 6). It must be hooked on layout effect: that is after the DOM has the new text and
     before the browser paints. */
  React.useLayoutEffect(() => {
    const pending = pendingCaretRef.current;
    if (!pending) return;
    pendingCaretRef.current = null;
    const pos = pending.pos;
    const el = pending.where === "zoom" ? zoomTextRef.current : textRef.current;
    if (!el) return;
    /* ★ Only grab focus when "the person has not moved to another input box".
       The criterion cannot be "focus must be on the body or the voice button": Radix Dialog's FocusScope
       pulls focus into the dialog container when the voice button is replaced (measured), which would misjudge a
       normal refocus as "the person already left" (measured cursor stops at the end 8 instead of 6).
       Real "the person left" = focus landed on another editable element. */
    const ae = document.activeElement;
    const editable = ae instanceof HTMLElement &&
      (ae.tagName === "INPUT" || ae.tagName === "TEXTAREA" || ae.tagName === "SELECT" || ae.isContentEditable);
    if (editable && ae !== el) return;
    el.focus();
    el.setSelectionRange(pos, pos);
    (pending.where === "zoom" ? zoomCaretRef : caretRef).current = { start: pos, end: pos };
  }, [text]);
  /** Capture cursor snapshot (mouse click, keyboard move, typing all go through select) */
  const rememberCaret = React.useCallback(() => {
    const el = textRef.current;
    if (!el) return;
    caretRef.current = { start: el.selectionStart, end: el.selectionEnd };
  }, []);
  /** Cursor snapshot of the enlarged window (same convention as rememberCaret, just another input) */
  const rememberZoomCaret = React.useCallback(() => {
    const el = zoomTextRef.current;
    if (!el) return;
    zoomCaretRef.current = { start: el.selectionStart, end: el.selectionEnd };
  }, []);
  /** Insert a piece of text at the cursor (replace the selection if any — standard editor semantics). */
  const insertInto = React.useCallback((
    insert: string,
    el: HTMLTextAreaElement | null,
    caret: { start: number; end: number } | null,
    where: "main" | "zoom",
  ) => {
    /* ★ Read the DOM value, not the closure text: the voice callback was captured seconds ago when recording started;
       using the closure value would lose "the few chars typed while recording". */
    const prev = el ? el.value : textValueRef.current;
    /* Only fall back to the end when there is no cursor signal at all (the body was never clicked) — keep the original
       feel, do not mysteriously insert at the very front just because selectionStart defaults to 0. */
    const saved = caret;
    const start = Math.min(Math.max(saved ? saved.start : prev.length, 0), prev.length);
    const end = Math.min(Math.max(saved ? saved.end : start, start), prev.length);
    /* Deliberately do not add spaces/newlines: insert exactly where it goes. Being clever and adding separators
       would produce an unwanted character when he wants to speak continuously. */
    const next = prev.slice(0, start) + insert + prev.slice(end);
    const pos = start + insert.length;
    (where === "zoom" ? zoomCaretRef : caretRef).current = { start: pos, end: pos };
    pendingCaretRef.current = { pos, where };
    setText(next);
  }, []);
  const insertAtCaret = React.useCallback(
    (insert: string) => insertInto(insert, textRef.current, caretRef.current, "main"),
    [insertInto],
  );
  const insertAtCaretZoom = React.useCallback(
    (insert: string) => insertInto(insert, zoomTextRef.current, zoomCaretRef.current, "zoom"),
    [insertInto],
  );

  /* ── [2026-11-14 Feng] "Common phrases" picker ──
     Quote: "The original quick command takes params; this one is the new type, it just copies text directly"
     → commands with `sendMode='text'` do not enter the dispatch flow, but act as a ready-made phrase,
       selecting inserts it at **the cursor** (reusing insertAtCaret), whether to send is decided by the person.
     ★ Filter by sender: a phrase is "words said by someone", not a global lexicon. */
  const [phraseOpen, setPhraseOpen] = React.useState(false);
  const [phrases, setPhrases] = React.useState<QuickCmd[]>([]);
  const [phrasesLoading, setPhrasesLoading] = React.useState(false);
  /** [2026-11-14 Feng] Create in place: no closing the window, no page jump, just pop the same form */
  const [phraseFormOpen, setPhraseFormOpen] = React.useState(false);
  /* [2026-11-17 10:07 Feng] "Once the content grows, this small box has to scroll up and down, hard to read... add a
     button at the top-right of the input box, click it to open a larger window and carry the content over".
     ★ Why "a second view of the same state" instead of "copy the content over and take it back":
       copy/write-back inevitably produces sync bugs like "changed in the big window, not synced on close / synced
       twice and overwritten". Two Textareas bound to the same `text` — whoever changes it changes the same value:
       closing keeps it, no reclaim step. */
  const [textZoom, setTextZoom] = React.useState(false);
  /* The enlarged window's "common phrases" popover has its own open state — sharing one state
     would make the main box's (buried underneath) open along with it, and Radix would stack both contents at the same coordinates. */
  const [zoomPhraseOpen, setZoomPhraseOpen] = React.useState(false);
  const loadPhrases = React.useCallback(async () => {
    if (!senderAgent) {
      setPhrases([]);
      return;
    }
    setPhrasesLoading(true);
    try {
      /* includeSharedText=true: text snippets do not dispatch work, the owning Agent is just "who wrote it",
         everyone's phrases should be usable (Feng 2026-11-14: "it belongs to everyone, must be listed too"). */
      const r = await fetchQuickCommands(senderAgent, undefined, true);
      setPhrases((r.commands || []).filter((c) => c.sendMode === "text" && (c.activeStatus ?? 1) === 1));
    } catch {
      setPhrases([]);
    } finally {
      setPhrasesLoading(false);
    }
  }, [senderAgent]);
  /* Refetch on every open: the person very likely just added one in "quick command" and does not want to refresh the page */
  React.useEffect(() => {
    /* [2026-11-17 10:19] The enlarged window also has "common phrases" — the one it opens is `zoomPhraseOpen`.
       Watching only `phraseOpen` would make the enlarged window pop "no text snippets yet" (measured),
       because the phrases were never fetched at all. */
    if (phraseOpen || zoomPhraseOpen) void loadPhrases();
  }, [phraseOpen, zoomPhraseOpen, loadPhrases]);

  /* [2026-11-14 Feng] Grouping: ① the current sender's own ② the rest (shared)
     "Just two kinds, one belongs to it, one is the shared one that does not" */
  const phraseOwn = React.useMemo(
    () => phrases.filter((c) => (c.ownerAgent || "") === senderAgent),
    [phrases, senderAgent],
  );
  const phraseShared = React.useMemo(
    () => phrases.filter((c) => (c.ownerAgent || "") !== senderAgent),
    [phrases, senderAgent],
  );

  /** A phrase: click to insert at the cursor (**does not send**).
   *  shared=true marks the author — the shared section is all written by others, without a mark you do not know
   *  whose mouth it fills. */
  /* Execution params (optional, shared by all tasks; aligned with RunTaskDialog "⑤ execution params")
     [2026-10-19 P5] Reply is forced either/or: "1"=needed / "0"=not needed; no empty state.
     Default "1"; the actual preselected value is overridden by the defaultReply of the selected task/cmd. */
  const [needsReply, setNeedsReply] = React.useState<"0" | "1">("1");
  /* [2026-11-17 10:19] The remembered "whether to reply" must override the template default **at the moment of opening**.
     Why two refs are needed: there is an effect below "on task/cmd/target change reset reply per template",
     which runs immediately after opening and washes out the remembered value (measured: after a page refresh,
     no-reply → needs-reply). Record the identity set at open time (target|task|command): as long as the identity is
     unchanged, trust the memory; once the person changes a selection, the memory is void and we return to the
     template default. */
  const replyMemoRef = React.useRef<"" | "0" | "1">("");
  const replyIdentRef = React.useRef("");
  /* [2026-11-06 15:45 Feng] Added "whether human confirmation is needed", default "0" (not needed, same as old behavior).
     Position: on the same row next to "whether to reply" (Feng: "whether to reply is too long, put two on this row"). */
  const [needsConfirm, setNeedsConfirm] = React.useState<"0" | "1">("0");
  /* Execution timeout: Task / quick command default 3600 s (Feng 2026-10-19 12:05; backend default is 1800, so set it explicitly here) */
  const [timeoutSec, setTimeoutSec] = React.useState<string>(DEFAULT_TIMEOUT_SEC);
  const [execName, setExecName] = React.useState("");

  /* ── State of the unsent draft ── */
  /** Draft persist time (0 = no draft), used by the hint bar to show "restored content from 15:32" */
  const [draftAt, setDraftAt] = React.useState(0);
  const [draftRestored, setDraftRestored] = React.useState(false);
  /* ── [2026-11-13 21:39 Feng] Running state of the receiver session ──
     "If this receiver's session is running, add a marker (make the text green or something);
       if it is running, the send button should be locked, right."
     ★ Data comes from the Redis read-only projection (BFF /sessions/runtime/lookup),
       the criterion is the gateway side's `status === "running"` ([2026-11-14] narrowed, no longer looks at
       hasActiveRun) — not MySQL's "executed recently" (the latter stays true after a task ends, making people
       think it is always running). */
  const [rtRunning, setRtRunning] = React.useState(false);
  /* [2026-11-13 23:29 Feng] The running state of the send session must be marked too (not just the receiver) */
  const [senderRtRunning, setSenderRtRunning] = React.useState(false);
  const [senderRtRunId, setSenderRtRunId] = React.useState("");
  const [rtRunId, setRtRunId] = React.useState("");
  /** Session key of the most recent send: new-session mode has no key before sending, only after — use it to keep observing */
  const [lastSentKey, setLastSentKey] = React.useState("");

  /** How many other drafts exist besides this one (the hint bar tells "other receivers also keep content") */
  const [otherDrafts, setOtherDrafts] = React.useState(0);
  /** Restore-complete gate.
   *  ★ Must use **state** not ref: setState in the restore effect → triggers re-render →
   *    the save effect reads "the restored value" afterwards.
   *    Using ref would hit "the save effect runs first in the same commit and overwrites the draft with useState's
   *    default value". */
  const [draftReady, setDraftReady] = React.useState(false);

  const [cmds, setCmds] = React.useState<QuickCmd[]>([]);
  const [cmdsLoading, setCmdsLoading] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);
  /* [2026-10-19 P2 idempotency key] Generated when the dialog opens, cleared after a successful submit.
     ★ Retrying the same intent reuses the same value → the kernel returns the first result (idempotent=true), no
       duplicate dispatch.
     ★ Text mode has no idempotency (agx_send_text creates no execution, no duplicate side-effect risk).
     ★ On failure it does **not** regenerate — that is exactly the "user clicking again must not run twice" case. */
  const requestIdRef = React.useRef<string>("");
  const [error, setError] = React.useState("");
  /* ── [2026-11-07 Feng] Result bar → send records (accumulating) ──
     Quote: "After clicking send the content box clears, then put the sent content down in the sent records below,
             sending continuously records, keep clicking and it scrolls below".
     So: no longer maintain a "last result", but a **stream**; after sending, clear the input and append this one to
     the bottom of the list.
     ★ Why drop the "invalidate the result when content changes" set: that was designed for a "single result".
       Records are history; changing what to send next should not erase what already happened. */
  /* [2026-11-14 20:31 Feng] After sending, no longer open a "sent N items" box below,
     instead insert this message into the history conversation above (ref-driven). */
  const histApiRef = React.useRef<SessionHistoryHandle | null>(null);
  /** Record list container: a new record coming in must scroll to the **top** (newest is first) */
  /** Whether it stays at the bottom of the list (when scrolled up, hint "there is earlier content below") */


  /* [2026-11-16 12:45 Feng] "I filled in all params, yet the send button stays locked"
   ★ The top "message send" is a **global entry** and does not know whose identity to send as — it used to be empty
     here, so after choosing a command and filling params, submit was still grey and the person could only guess.
   ★ Remember the last used send subject: the identity is chosen by the person, giving it back on reopen is not
     deciding for him, just saving the reselect each time (the quick command dialog did this long ago: caller memory).
   ★ Only **fallback**: if the entry gave it explicitly / the draft has a value, those always win. */
/** Last send subject. The old key agx_web.uib.sender is auto-migrated by storage-keys. */
function readLastSender(): string {
  return readKey("sendSender") || "";
}

/* [2026-11-17 10:19 Feng]
 * "The project and agent above, and the options below, all need memory... often I close this and reopen it,
 *   and it goes back to default. When I open it, keep whatever was last."
 *
 * ★ Division from "draft" (the two cannot substitute for each other; this is the criterion of this change):
 *     draft = **content** (body/instruction/params/title), bucketed by receiver, **cleared once sent**;
 *     memory = **settings** (project narrowing, sender/receiver, session, execution mode, reply/confirm/timeout),
 *              unrelated to content, **still there after sending** — because "send three in a row to the same
 *              person" is the norm.
 *   Previously there was only a draft: once sent there was no content, so the next open fell back to all defaults,
 *   which is exactly the phenomenon he described.
 * ★ Priority: value given explicitly by the entry > memory > default.
 *   If the entry says "send to this session", that is it, memory may not change its mind (otherwise entering from a
 *   specific place would still leak to the last person).
 * ★ Key names and migration strategy are in `lib/storage-keys.ts` (the old key agx_web.uib.state.v1 is still readable).
 */
interface UibLastState {
  projFilter?: string;
  senderAgent?: string;
  senderSession?: string;
  targetAgent?: string;
  targetSession?: string;
  mode?: UniversalMode;
  sessionMode?: "new" | "existing";
  needsReply?: "0" | "1";
  needsConfirm?: "0" | "1";
  timeoutSec?: string;
  /** Last used task template / quick command (taken per mode, see the comment at initialization) */
  taskId?: string;
  cmdId?: string;
}
function readLastState(): UibLastState {
  try {
    const raw = readKey("sendState");
    if (!raw) return {};
    const p = JSON.parse(raw) as unknown;
    return p && typeof p === "object" ? (p as UibLastState) : {};
  } catch {
    /* Storage disabled / broken by someone — treat as no memory. Memory is a nice-to-have; it must not make the dialog fail to open. */
    return {};
  }
}
function writeLastState(s: UibLastState) {
  writeKey("sendState", JSON.stringify(s));
}

/* ── Initialize by initial on open (prefill = values already fixed externally) ── */
  React.useEffect(() => {
    if (!open) return;
    const v: UniversalInputValue = {
      senderAgent: "",
      senderSession: "",
      targetAgent: "",
      mode: "text",
      sessionMode: "new",
      targetSession: "",
      taskId: "",
      cmdId: "",
      params: {},
      text: "",
      ...initial,
    };
    /* [2026-11-17 10:19] This open first reads the "last settings" once; each item below takes the value in three
       levels: "did the entry give it explicitly → memory → default". */
    const last = readLastState();
    /* Entry did not specify a send subject → use the last used one (see the comment above LAST_SENDER_KEY),
       if still empty leave it to the user to choose (do not guess the project, do not guess the first Agent). */
    const sender = v.senderAgent || (isLocked("senderAgent") ? "" : (last.senderAgent || readLastSender()));
    setSenderAgent(sender);
    /* Send session: entry > memory (and belonging to the same send subject, otherwise it is like hanging A's session on B) > that subject's main. */
    const rememberedSession = last.senderSession && sender && last.senderSession.startsWith(`agent:${sender}:`)
      ? last.senderSession
      : "";
    setSenderSession(v.senderSession || rememberedSession || (sender ? `agent:${sender}:main` : ""));
    /* ★ Judge "did the entry give it" with `initial?.x`, not `v.x`: defaults in v (text/new/"")
       cannot distinguish "the entry wants text" from "the entry said nothing" — that would necessarily override
       the memory. */
    if (!initial?.targetAgent && !isLocked("targetAgent")) setTargetAgent(last.targetAgent || v.targetAgent);
    else setTargetAgent(v.targetAgent);
    const nextMode: UniversalMode = initial?.mode ?? last.mode ?? v.mode;
    setMode(nextMode);
    setSessionMode(initial?.sessionMode ?? last.sessionMode ?? v.sessionMode);
    if (!initial?.targetSession && !isLocked("targetSession")) setTargetSession(last.targetSession || v.targetSession);
    else setTargetSession(v.targetSession);
    /* Task template / quick command are remembered too — otherwise it only remembers "the mode is Task" but forgets
       "which Task", and after opening it is an empty selection, more annoying than returning to plain text.
       ★ Only take it when the mode matches: a taskId of a Task in memory should not be brought out in quick command mode. */
    if (!initial?.taskId && !isLocked("task")) setTaskId(nextMode === "task" ? (last.taskId || v.taskId) : v.taskId);
    else setTaskId(v.taskId);
    if (!initial?.cmdId && !isLocked("quickcmd")) setCmdId(nextMode === "quickcmd" ? (last.cmdId || v.cmdId) : v.cmdId);
    else setCmdId(v.cmdId);
    setParams(v.params || {});
    setText(v.text);
    setSkillIds([]);
    setSkillQuery("");
    setNeedsReply(v.needsReply || last.needsReply || "1");
    setNeedsConfirm(v.needsConfirm || last.needsConfirm || "0");
    setTimeoutSec(v.timeoutSec || last.timeoutSec || DEFAULT_TIMEOUT_SEC);
    setExecName(v.execName || "");
    /* Top project narrowing: [2026-11-15 15:42 Feng]
       "There is no selection of the project this agent belongs to in here"
       — if the entry gave it explicitly use it; if not, **default to the sender's project**.
       When entering from the grid / detail page nobody passes initialProject, so it stays at "all projects" forever. */
    /* Project narrowing: entry-specified > the project of the Agent brought by the entry > last project > all.
       ★ Only "follow the person" when **the entry gave senderAgent**: if the sender was restored from memory,
         then using it to override the project in memory would erase "last time I was looking at all projects". */
    setProjFilter(
      initialProject
      || (v.senderAgent ? (listAgents().find((a) => a.id === v.senderAgent)?.project_id || "") : "")
      || last.projFilter
      || "",
    );
    replyMemoRef.current = (initial?.needsReply || last.needsReply || "") as "" | "0" | "1";
    replyIdentRef.current = `${v.targetAgent || last.targetAgent || ""}|${
      nextMode === "task" ? (last.taskId || v.taskId || "") : (v.taskId || "")
    }|${nextMode === "quickcmd" ? (last.cmdId || v.cmdId || "") : (v.cmdId || "")}`;
    setError("");
    setSubmitting(false);
    requestIdRef.current = `uib_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  /* [2026-11-17 10:19 Feng] Persist the settings memory — unrelated to the draft, still kept after a successful send.
     ★ 400ms debounce: changing one dropdown triggers several renders in a row, no need to write localStorage every time.
     ★ Only write **while open**: after closing these states no longer change, so one write is enough (the closing
       write is also covered by "persist on close" below); more importantly, avoid "state rollback in the close
       animation" corrupting the memory. */
  React.useEffect(() => {
    if (!open) return;
    const t = window.setTimeout(() => {
      writeLastState({
        projFilter, senderAgent, senderSession, targetAgent, targetSession,
        mode, sessionMode, needsReply, needsConfirm, timeoutSec, taskId, cmdId,
      });
    }, 400);
    return () => window.clearTimeout(t);
  }, [open, projFilter, senderAgent, senderSession, targetAgent, targetSession,
      mode, sessionMode, needsReply, needsConfirm, timeoutSec, taskId, cmdId]);

  /* ── [2026-11-13 15:40 / 15:53 Feng] Unsent draft: restore / persist (multi-bucket version) ──
     ★ The declaration order is deliberately placed **after** the "initialize by initial" effect:
       initialization writes the initial values first, then here we "fill the draft into cells that were not
       specified"; if the order were reversed the draft would be overwritten by the initial values.
     ★ The dependency array only has [open]: the draft is restored only once, at "the moment of opening".
       If entry props were also included, changing references every render would repeatedly apply the draft and
       overwrite what the user just changed — a worse experience than "not restored". */

  /** Snapshot the current form into a draft object */
  const snapshotDraft = React.useCallback((): SendDraft => ({
    mode, sessionMode, senderAgent, senderSession,
    targetAgent, targetSession, taskId, cmdId, params, text,
    needsReply, needsConfirm, timeoutSec, execName, skillIds, at: Date.now(),
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [mode, sessionMode, senderAgent, senderSession, targetAgent,
       targetSession, taskId, cmdId, params, text, needsReply, needsConfirm,
       timeoutSec, execName, skillIds]);

  /** Lay a draft onto the form.
   *  ★ Whatever initial explicitly gave wins — that is the entry's intent (e.g. "send to this session").
   *  ★ Never touch locked fields: the control is a read-only display, writing the draft in would be faking
   *  "already fixed externally".
   *  ★ keepTarget: when switching buckets the receiver has already been set to the new value by the caller, and
   *  must not be changed back by the old value in the draft. */
  const applyDraftToForm = React.useCallback((d: SendDraft, keepTarget = false) => {
    if (!initial?.senderAgent && !isLocked("senderAgent")) setSenderAgent(d.senderAgent || "");
    if (!initial?.senderSession && !isLocked("senderSession")) setSenderSession(d.senderSession || "");
    if (!keepTarget && !initial?.targetAgent && !isLocked("targetAgent")) setTargetAgent(d.targetAgent || "");
    if (!initial?.targetSession && !isLocked("targetSession")) setTargetSession(d.targetSession || "");
    if (!initial?.mode && !isLocked("mode")) setMode(d.mode || "text");
    if (!initial?.sessionMode) setSessionMode(d.sessionMode === "existing" ? "existing" : "new");
    if (!initial?.taskId && !isLocked("task")) setTaskId(d.taskId || "");
    if (!initial?.cmdId && !isLocked("quickcmd")) setCmdId(d.cmdId || "");
    if (!initial?.text) setText(d.text || "");
    setParams(d.params && typeof d.params === "object" ? d.params : {});
    if (Array.isArray(d.skillIds)) setSkillIds(d.skillIds.filter((x): x is string => typeof x === "string"));
    setNeedsReply(d.needsReply === "0" ? "0" : "1");
    setNeedsConfirm(d.needsConfirm === "1" ? "1" : "0");
    if (d.timeoutSec) setTimeoutSec(d.timeoutSec);
    setExecName(d.execName || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial, effLocked]);

  /** Only lay the draft's **content** on (body / params / title / skills), **touch no setting**.
   *
   *  ★ Why this "content only" entry is needed (the problem Feng reported 2026-11-17 22:39):
   *    "I select the project and Agent, exit and come back in, the Agent becomes another one (UI frontend lead),
   *      no matter which one I pick it is the same."
   *    Root cause: draft restore walks "the most recently edited bucket" (`store.last`), and restoring lays the
   *    **header along with it**. So as long as an old draft of someone else sits in storage, every open changes the
   *    receiver back to that person — and this changed-back value is then persisted by "settings memory", corrupting
   *    the memory itself, wrong forever after.
   *  ★ Division criterion (same sentence as the memory comment): **content belongs to the draft, settings belong
   *    to memory.** Why should a draft decide "who to send to"? It does not even know when it is from.
   */
  const applyDraftContent = React.useCallback((d: SendDraft) => {
    if (!initial?.text) setText(d.text || "");
    setParams(d.params && typeof d.params === "object" ? d.params : {});
    if (Array.isArray(d.skillIds)) setSkillIds(d.skillIds.filter((x): x is string => typeof x === "string"));
    setExecName(d.execName || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial]);

  /** Clear only "content", keep the context (used when switching buckets / discarding a draft).
   *  Context (who to send to, which mode) is a shared setting for sending multiple in a row, and should not be
   *  cleared with the content. */
  const clearDraftContent = React.useCallback(() => {
    setText("");
    setExecName("");
    setSkillIds([]);
    setParams({});
  }, []);

  /** Switching receiver = switching draft bucket.
   *  ① First persist the current content into **the old bucket** (otherwise it is lost the moment you switch)
   *  ② New bucket has a draft → lay it on; none → clear the content
   *     (A's content must not look like "half written for B")
   *  ★ Text mode does not switch: in that path the receiver always equals the send subject (platform bans
   *    Agent-to-Agent chat); switching only gets pulled back by the constraint effect below, pointlessly disturbing
   *    content. */
  const switchDraftBucket = React.useCallback((nextTarget: string, prevTarget: string) => {
    if (nextTarget === prevTarget) return;
    const d = snapshotDraft();
    saveDraftToBucket(draftBucketOf(prevTarget), draftHasContent(d) ? d : null);
    const next = loadDraftFromBucket(draftBucketOf(nextTarget));
    if (next) {
      applyDraftToForm(next, true);
      setDraftAt(next.at || Date.now());
      setDraftRestored(true);
    } else {
      clearDraftContent();
      setDraftRestored(false);
      setDraftAt(0);
    }
    /* "N others" must update with bucket switch — otherwise after switching the number still reflects the previous receiver */
    const remain = Object.keys(readDraftStore().items).length;
    setOtherDrafts(Math.max(0, remain - (next ? 1 : 0)));
  }, [snapshotDraft, applyDraftToForm, clearDraftContent]);

  /* changeTargetAgent / applyQuickAgent are defined above (earlier than this block),
      so calling switchDraftBucket there can only go through a ref — otherwise the whole callback would have to move
      down, which would disturb the dependency order of it, risk greater than benefit. */
  const switchBucketRef = React.useRef<((next: string, prev: string) => void) | null>(null);
  switchBucketRef.current = switchDraftBucket;

  React.useEffect(() => {
    if (!open) {
      /* Close = persist immediately. Within the 1-second debounce the user may well have already clicked the X —
          waiting for the timer means dropping "the last input", and that one is often the one he wants to keep. */
      if (draftReady) {
        const d = snapshotDraft();
        saveDraftToBucket(draftBucketOf(d.targetAgent), draftHasContent(d) ? d : null);
      }
      setDraftReady(false);
      setDraftRestored(false);
      setDraftAt(0);
      setOtherDrafts(0);
      return;
    }

    /* Which bucket to restore?
       · The entry specified a receiver → that bucket (other buckets must not be applied blindly, otherwise
         "send to A but bring out the body written for B")
       · Not specified → take the bucket **by the receiver in memory**. Memory is the authority on "who I sent to last".
         ★ Cannot use `store.last` (the most recently edited bucket): if there is any old draft of another receiver
           in storage, deleting the current (empty) bucket on close makes `last` fall back to it → every open gets
           hijacked by that old draft, and it lays the header along with it, changing the memory to the old person
           too (measured: select agx_platform_claudecode, on reopen it becomes agx_ui_lead again, and it can never
           be changed back).
       · Neither (old browser, memory not established yet) → fall back to `store.last`, keeping old behavior
         unchanged. */
    const store = readDraftStore();
    const last = readLastState();
    const modeGuess = initial?.mode ?? last.mode ?? "text";
    /* In text mode the receiver always equals the send subject — taking the bucket must follow this invariant, otherwise it takes the "last time sent to someone else" bucket. */
    const remembered = (modeGuess === "text" ? last.senderAgent || last.targetAgent : last.targetAgent) || "";
    const settingTarget = initial?.targetAgent || remembered;
    const bucket = settingTarget ? draftBucketOf(settingTarget) : store.last;
    const d = bucket ? store.items[bucket] : undefined;
    if (d && draftHasContent(d)) {
      /* Memory present = settings already decided → the draft only fills content; no memory = old version → restore the whole thing (old behavior). */
      if (settingTarget) applyDraftContent(d);
      else applyDraftToForm(d);
      setDraftAt(d.at || Date.now());
      setDraftRestored(true);
      setOtherDrafts(Math.max(0, Object.keys(store.items).length - 1));
    } else {
      setDraftRestored(false);
      setDraftAt(0);
      setOtherDrafts(0);
    }
    setDraftReady(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  /* ── Input change → debounced persist (covers "did not close the window, refreshed the page directly") ── */
  React.useEffect(() => {
    if (!open || !draftReady) return;
    const t = window.setTimeout(() => {
      const d = snapshotDraft();
      saveDraftToBucket(draftBucketOf(d.targetAgent), draftHasContent(d) ? d : null);
    }, DRAFT_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [open, draftReady, snapshotDraft]);

  /* ── Receiver session running state: poll while open (4s, same convention as the system status page) ──
     ★ Only query when there is **a definite session_key**:
       · existing session mode → targetSession
       · just sent (especially a new session) → the session_key echoed by the backend (lastSentKey)
       The latter wins: after sending a new session its key differs from targetSession.
     ★ Changing receiver / session must clear lastSentKey — otherwise it keeps watching the previous session's run,
       showing as "changed the person but the button is still locked". */
  const watchSessionKey = lastSentKey || (sessionMode === "existing" ? targetSession : "");

  React.useEffect(() => {
    /* Receiver / landing point changed → the previous observation is void */
    setLastSentKey("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetAgent, targetSession, sessionMode]);

  React.useEffect(() => {
    if (!open || !watchSessionKey) {
      setRtRunning(false);
      setRtRunId("");
      return;
    }
    let alive = true;
    const tick = async () => {
      const r = await probeSessionRunning(watchSessionKey);
      if (!alive) return;
      setRtRunning(r.running);
      setRtRunId(r.runId);
    };
    void tick();
    const t = window.setInterval(() => void tick(), 4000);
    return () => { alive = false; window.clearInterval(t); };
  }, [open, watchSessionKey]);

  /* Running state of the send session: same frequency as the receiver (4s), same criterion */
  React.useEffect(() => {
    if (!open || !senderSession) {
      setSenderRtRunning(false);
      setSenderRtRunId("");
      return;
    }
    let alive = true;
    const tick = async () => {
      const r = await probeSessionRunning(senderSession);
      if (!alive) return;
      setSenderRtRunning(r.running);
      setSenderRtRunId(r.runId);
    };
    void tick();
    const t = window.setInterval(() => void tick(), 4000);
    return () => { alive = false; window.clearInterval(t); };
  }, [open, senderSession]);

  /* ── Switch to Task / quick command → backfill default 3600 when timeout is empty ── */
  React.useEffect(() => {
    if (mode === "text") return;
    setTimeoutSec((prev) => (prev.trim() ? prev : DEFAULT_TIMEOUT_SEC));
  }, [mode]);

  /* ── Sender switch → sender session returns to that identity's main session ── */
  React.useEffect(() => {
    if (!senderAgent) return;
    setSenderSession((prev) => (prev && prev.startsWith(`agent:${senderAgent}:`) ? prev : `agent:${senderAgent}:main`));
  }, [senderAgent]);

  /* ── Downstream cleanup on "change target / change mode" ──
     ★ Changed on 2026-11-04 to clear **explicitly in the handler**, no longer guessing with useEffect.
     Reason (measured): on mount these effects get useState's default values, while the real initial values are
     written by the initialization effect; both run in the same commit, and the cleanup logic wipes out the values
     just written. Measured: entering from a quick command (initial.mode=quickcmd, initial.cmdId=xxx), the mount-time
     "mode switch" effect saw mode still at the default "text" → cleared cmdId → the UI kept the command locked and
     the param table (from fixedCmd), but validation kept saying "please select a quick command", and the submit
     button stayed grey forever, looking like "the feature is unfinished".
     Using an effect to distinguish "the user changed it" from "initialization is not done" is impossible (cannot
     tell within the same commit), and these two things should happen **where the user action happens**. */
  const changeTargetAgent = React.useCallback((v: string, forceRelease = false) => {
    const prev = targetAgent;
    /* forceRelease: the caller (top project/Agent dropdown) has decided "the entry lock is void".
       This render's effLocked does not yet reflect setTargetLockBroken, so override via the param,
       otherwise "unlock then switch person" in the same handler would not take effect because it reads the old value. */
    const lk = forceRelease ? {} : effLocked;
    setTargetAgent(v);
    setTaskId("");
    if (!lk.quickcmd) { setCmdId(""); setParams({}); }
    if (!lk.targetSession) { setTargetSession(""); setSessionMode("new"); }
    /* [2026-11-13 15:53] Switching receiver = switching draft bucket.
       ★ Must be placed **after** the lines above: bucket switching overwrites taskId/cmdId/params with the new
         bucket's content; placing it earlier would be cleared by those lines (reversed order looks like "draft not
         restored").
       ★ `draftReady` gate: the initialization setTargetAgent must not count as "the user switched person",
         otherwise the not-yet-restored draft would be moved away by default values. */
    if (draftReady) switchBucketRef.current?.(v, prev);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effLocked, targetAgent, draftReady]);

  /* Top Agent dropdown (Feng: "both the sender and receiver below"):
     set both at once — this is the most convenient expression of "I want to send something to this person".
     To send under a different identity, go change the sender separately in ① (this only takes effect when he
     presses the dropdown, and does not override afterwards). */
  const applyQuickAgent = React.useCallback((v: string) => {
    if (!isLocked("senderAgent")) setSenderAgent(v);
    /* [2026-11-14 10:57 Feng] "Once something above changes, the lock below should be released"
       — switching project/person at the top = the user explicitly wants to change context; the receiver lock brought
       in by the entry is void from here on, and both the receiver and the receive session follow the new selection
       (otherwise you can only select below, which traps the person). */
    const releaseTarget = isLocked("targetAgent") || isLocked("targetSession");
    if (releaseTarget) setTargetLockBroken(true);
    if (releaseTarget || !isLocked("targetAgent")) {
      changeTargetAgent(v, releaseTarget);
      /* Text mode always uses an existing session (same rule as changeMode);
         after unlocking the target session is cleared, and staying at new would contradict "text must land in an
         existing session". */
      if (releaseTarget && mode === "text") setSessionMode("existing");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effLocked, changeTargetAgent, mode]);

  /* [2026-11-10 09:31 Feng]
     "After switching the project in the project dropdown, the Agent of the original project that was selected is
      still in there (ticked). Since you cleared it, clear it all, then switch to the new project's first one.
      For a new project, default to its PM first, then everything below changes."
     → Switching project = **switching context**, not "adding a filter layer on top of the original context".
       Keeping the old project's people in the list, still ticked, makes people think "this person belongs to this project". */
  const changeQuickProject = React.useCallback((pid: string) => {
    setProjFilter(pid);
    if (!pid) return; // Switch back to "all projects": keep the current selection, do not guess
    /* [2026-11-14 10:57 Feng] "Changing the project above... the lock below should be released"
       ★ Release must be unconditional: even if the new project temporarily has no candidate Agent (pick empty),
         we must not keep pinning the receive session to the old project's session — then the user can neither change
         person nor stop sending with the old context, more confusing than an error. */
    const releaseTarget = isLocked("targetAgent") || isLocked("targetSession");
    if (releaseTarget) setTargetLockBroken(true);
    /* PM preferred; a project without a PM falls back to that project's first Agent.
       ★ The old value must be **replaced**: only clearing the filter and leaving the old project's person in the
         list still ticked makes people think "this person belongs to this project". */
    const pick = pmOfProject(pid) || quickAgents.find((a) => a.project_id === pid) || null;
    if (pick) {
      applyQuickAgent(pick.id);
      return;
    }
    if (releaseTarget) {
      /* Project empty: unlock + clear the old context, hand the choice back to the user (do not guess a default value) */
      setTargetAgent("");
      setTargetSession("");
      setTaskId("");
    }
  }, [pmOfProject, applyQuickAgent, quickAgents, effLocked]);

  const changeMode = React.useCallback((m: UniversalMode) => {
    setMode(m);
    if (m !== "task") setTaskId("");
    if (m !== "quickcmd") { setCmdId(""); setParams({}); }
    if (effLocked.targetSession) { setSessionMode("existing"); return; }
    if (m === "text") setSessionMode("existing");
    else { setSessionMode("new"); setTargetSession(""); }
  }, [effLocked]);

  /* ── Hard constraint (notice §4): text mode can only send to the send subject's own session ── */
  //   ★ This effect must be placed 【after】 the two clearing effects "target Agent switch / mode switch",
  //     otherwise the latter clears targetSession (in text mode target = send subject, needs to auto-land in main session).
  React.useEffect(() => {
    if (mode !== "text") return;
    if (!isLocked("targetAgent") && senderAgent && targetAgent !== senderAgent) {
      /* [2026-11-13 15:53] In text mode "receiver" always equals the send subject —
         changing the send subject equals changing the recipient, and the draft must switch bucket too,
         otherwise the body written for A is treated as "half written for B".
         ★ Also gated by draftReady: the initialization does not count as the user switching person. */
      if (draftReady) switchBucketRef.current?.(senderAgent, targetAgent);
      setTargetAgent(senderAgent);
    }
    /* ★ `if (senderAgent)` is not a redundant null check — it is the criterion of "is initialization done".
       On mount this effect gets useState's default values (senderAgent="", mode still default text);
       if sessionMode were changed to existing then, it would overwrite the "new" that the initialization effect just
       set for the quick command entry → it becomes "must select an existing session", while the target session is
       empty, submit greyed out forever.
       Having a send subject = initial values in place = this time it should count. */
    if (senderAgent) setSessionMode("existing");
    /* Locked session (session entry): the landing point is fixed by the entry, here it must not be rewritten to "the send subject's own main session". */
    if (isLocked("targetSession")) return;
    setTargetSession((prev) => {
      if (prev && prev.startsWith(`agent:${senderAgent}:`)) return prev;
      /* [2026-11-15 15:42] The main session always exists (see withMainSession) —
         the old code wrote `own.includes(main) ? main : own[0] || ""`:
         when the DB has no main row it falls back to own[0] (which may be some task session), and own empty leaves
         it blank. Text mode is inherently "send to my own main session", there is no reason to give way. */
      return `agent:${senderAgent}:main`;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, senderAgent, targetAgent]);

  /* ── Quick command candidates (filtered by target Agent, from BFF /quick-cmd) ── */
  React.useEffect(() => {
    if (!open || mode !== "quickcmd") return;
    let alive = true;
    setCmdsLoading(true);
    fetchQuickCommands(targetAgent || undefined)
      .then((r) => { if (alive) setCmds(r.commands || []); })
      .catch(() => { if (alive) setCmds([]); })
      .finally(() => { if (alive) setCmdsLoading(false); });
    return () => { alive = false; };
  }, [open, mode, targetAgent]);

  /* ── Text mode feasibility (a platform hard constraint, not a frontend-added limit) ──
     `agx_send_text` can only send into **the caller's own** session (no Agent-to-Agent chat).
     So when "send subject ≠ receiver", text mode is semantically invalid:
     either the backend rejects with 400 SESSION_NOT_OWNED, or we explain it clearly here.
     Choose to explain — an error makes people think the system is broken; explaining makes them know to switch to Task. */
  const textImpossible = mode === "text" && !!senderAgent && !!targetAgent && senderAgent !== targetAgent;

  const senderSessions = React.useMemo(
    () => withMainSession(senderAgent ? orderSessions(sessionsOf(senderAgent)) : [], senderAgent, t),
    [senderAgent, tick],
  );
  const targetSessions = React.useMemo(
    () => withMainSession(targetAgent ? orderSessions(sessionsOf(targetAgent)) : [], targetAgent, t),
    [targetAgent, tick],
  );

  /* The dropdown trigger must show "the state of the selected session" → get the object first (if not found, just show the id) */
  const senderSel = React.useMemo(
    () => senderSessions.find((x) => x.id === senderSession) || null,
    [senderSessions, senderSession],
  );
  const targetSel = React.useMemo(
    () => targetSessions.find((x) => x.id === targetSession) || null,
    [targetSessions, targetSession],
  );

  /* Display text of a locked session: found in that Agent's session table → unified format (id · name);
     not found (just created, snapshot not refreshed) → show the id directly. ★ Do not show "(unset)" — that is
     bluffing: the value is obviously given by the entry, it is just not in the list. */
  const lockedSessionText = React.useMemo(() => {
    if (!targetSession) return "";
    const hit = targetSessions.find((x) => x.id === targetSession);
    return hit ? sessionLabel(hit, t) : targetSession;
  }, [targetSession, targetSessions]);

  /* [2026-11-13 09:31 Feng] "Add two more blocks after the message body: the sending agent and session,
      the receiving agent and session; add a label and a color, to be more intuitive."
     ★ Why add it: sending to the wrong target has happened several times. The three layers on the left
       (sender/execution mode/receiver) carry a lot of info, but **nowhere before pressing send is "who this one is
       from and who it goes to" laid out side by side**. Next to the body box is where people's eyes rest, so the
       summary goes here, not in the left column. */
  const sumFromAgent = senderAgent ? agentText(senderAgent) : "";
  /* [2026-11-13 09:35 Feng] "For the session behind, just keep the session id, drop the session note behind"
     — take only the last segment (main / run_xxx / case_xxx), no longer concatenate name and type.
     ★ The summary bar is "a glance to confirm", not "read the detail"; the name must be short so it does not crowd
       out the Agent name.
     ★ Do not write "—" for empty: a dash in a Chinese UI does not read as "not selected yet", it reads like
       "there is no such thing". Both lines use the same convention: "unselected". */
  const sumFromSession = React.useMemo(
    () => (senderSession ? sessionShortId(senderSession) : t("sendDialog.summaryUnselected")),
    [senderSession],
  );
  const sumToAgent = targetAgent ? agentText(targetAgent) : "";
  const sumToSession = React.useMemo(() => {
    if (mode === "text") return targetSession ? sessionShortId(targetSession) : t("sendDialog.summaryUnselected");
    /* Entry lock: what is locked is the full key, also take only the last segment id */
    if (isLocked("targetSession")) return targetSession ? sessionShortId(targetSession) : t("sendDialog.summaryUnselected");
    /* New session: must not show "unselected" here — the real semantics is "not specified, the kernel creates one", say it as it is. */
    if (sessionMode === "new" || !targetSession) return t("sendDialog.summaryNewSession");
    return sessionShortId(targetSession);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, targetSession, sessionMode]);

  /* [2026-11-04 Feng] The order is exactly the host order of listAgentOptions — it used to re-sort by id here,
     which scattered the upstream host grouping again (Agents on the same host no longer adjacent).
     [2026-11-11] Drop templates (not registered in OpenClaw → no session, no history, selecting them yields an
     empty dropdown); **offline ones are kept** — what is wanted here is being able to look up its history. */
  const agentOptions = React.useMemo(
    () => allAgents.filter((a) => !isAgentTemplate(a)),
    [allAgents],
  );
  const taskOptions = React.useMemo<TaskRow[]>(
    () => (targetAgent ? listTasks().filter((t) => t.agentId === targetAgent) : []),
    [targetAgent, tick],
  );
  /* ── Quick command candidates: when the delivery landing point is locked, keep only those "that can really be delivered into this session" ──
     The kernel decides the target Agent from the command's resolution result:
       · private command (ownerAgent non-empty) → target = the command's own target_agent_id
       · public command (ownerAgent empty) → target = caller (= send subject)
     Both must equal the receiver, otherwise the kernel surely returns SESSION_NOT_OWNED.
     Listing it and then having it rejected means making the user fill params for nothing — just do not list it. */
  const cmdOptions = React.useMemo(() => {
    if (!isLocked("targetSession")) return cmds;
    return cmds.filter((c) => (c.ownerAgent ? c.ownerAgent === targetAgent : senderAgent === targetAgent));
  }, [cmds, targetAgent, senderAgent, effLocked]);   // effLocked: candidates must be recomputed after unlocking (2026-11-14)

  const selectedTask = taskOptions.find((t) => t.id === taskId) || null;
  const selectedCmd = fixedCmd || cmdOptions.find((c) => c.cmdId === cmdId) || null;

  /* ── The quick command's "receiver" is decided by kernel semantics, it is not about whether we show it ──
     Private (ownerAgent non-empty): the kernel delivers by the command's own target_agent_id → executor = owner,
       the user cannot change it, and we should not pretend it can be changed (changing it would make the frontend
       list the wrong Agent's sessions, and submit would surely be SESSION_NOT_OWNED).
     Public (ownerAgent empty): in the kernel `is_public` branch target_agent_id = caller_agent_id
       → executor = send subject, the two are inherently equal. Keep only one that is selectable (send subject),
       show the receiver as "= send subject", do not show two controls making people think they can be set
       separately. */
  const targetDefinedByCmd =
    mode === "quickcmd" && !!selectedCmd && !!selectedCmd.ownerAgent;
  const targetDerivedFromSender =
    mode === "quickcmd" && !!selectedCmd && !selectedCmd.ownerAgent;

  /* ── Public command: executor = send subject (kernel delivers by caller) ──
     The two fields are not "separately settable", but two names for the same thing; keep them equal here. */
  React.useEffect(() => {
    if (!targetDerivedFromSender) return;
    if (isLocked("targetAgent")) return;
    if (senderAgent && targetAgent !== senderAgent) setTargetAgent(senderAgent);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetDerivedFromSender, senderAgent, targetAgent]);


  /* Quick command selected → initialize params by schema + backfill the owning Agent (notice §7.4) */
  React.useEffect(() => {
    if (!selectedCmd) return;
    setParams(buildInitParams(selectedCmd, senderAgent, targetAgent));
    if (!isLocked("targetAgent") && selectedCmd.ownerAgent && selectedCmd.ownerAgent !== targetAgent) {
      setTargetAgent(selectedCmd.ownerAgent);
    }
    /* [2026-11-16 12:45 Feng] "I filled in all params, yet the send button stays locked" —
       the reproduced path is: entering from the top "message send", **the send subject is empty to begin with**
       (that is a global entry and does not know whose identity to send as). After selecting a private quick
       command, the receiver is auto-backfilled to the command owner, while the send subject is still empty → all
       params can be filled, the button stays grey, and the only hint is in the small grey text at the bottom of the
       left column.
       ★ A private command's owner is exactly who **must** send this command (the kernel delivers by target_agent_id),
         so fill it to the same value here — not guessing an identity, but filling the one the command semantics
         already hardcoded.
       ★ Public commands have no owner, so the user is still required to choose (we must not invent an identity
         for him). */
    if (!isLocked("senderAgent") && !senderAgent && selectedCmd.ownerAgent) {
      setSenderAgent(selectedCmd.ownerAgent);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cmdId]);

  /* Required host params (e.g. "owning host") → fill "current host", **only fill empty, do not override the user's choice**.
     ★ A separate effect instead of stuffing into the one above: that one only watches [cmdId], while the send
       subject / receiver may only be decided after selecting the command (this is how the top global entry works:
       select the command first, then fill the identity), and only then is the host known.
     ★ Only write when the value is empty. If the user manually picked a host, it should not be overwritten when the
       send subject changes later. */
  React.useEffect(() => {
    if (mode !== "quickcmd" || !selectedCmd) return;
    const agents = listAgents();
    const hostOf = (id: string) => agents.find((a) => a.id === id)?.host_id || "";
    const currentHost = hostOf(senderAgent) || hostOf(targetAgent);
    if (!currentHost) return;
    setParams((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const p of selectedCmd.params) {
        if (p.source === "host" && p.required && !next[p.name]) {
          next[p.name] = currentHost;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [mode, selectedCmd, senderAgent, targetAgent]);

  /* Param summary of the quick command (recorded into the send stream): `(date=2026-11-07, scope=all)`.
     Do not record the whole rendered result — that stream is "which command I sent, with which params",
     the fully assembled command is too long, stuffing it into one record line makes it harder to read. */
  const digestParams = React.useCallback(() => {
    const pairs = Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== null && String(v).trim() !== "")
      .map(([k, v]) => `${k}=${String(v)}`);
    return pairs.length ? `（${pairs.join("，")}）` : "";
  }, [params]);

    const preview = React.useMemo(
    () => (selectedCmd ? localPreview(selectedCmd, params) : ""),
    [selectedCmd, params],
  );

  /* ── [2026-10-19 P5] Reply default follows the template of the selected task / command ──
     ★ The default is decided by the defaultReply **computed by the backend** (result/confirm → yes, silent → no),
       the frontend does not implement the reply_mode → needs_reply conversion (avoid two implementations of the
       same business rule).
     ★ Reset on switching task / cmd / targetAgent — the previous template's default no longer applies. */
  const taskDefaultReply = selectedTask?.defaultReply || selectedCmd?.defaultReply || "1";
  React.useEffect(() => {
    /* The selection at the moment of opening = the setting left last time, trust it (see the replyMemoRef comment);
       once the person changes the target/task/command, the identity changes, the memory is void, back to the
       template default. */
    const ident = `${targetAgent}|${taskId}|${cmdId}`;
    if (replyMemoRef.current && replyIdentRef.current === ident) {
      setNeedsReply(replyMemoRef.current);
      return;
    }
    replyMemoRef.current = "";
    setNeedsReply(taskDefaultReply === "no" ? "0" : "1");
  }, [taskDefaultReply, targetAgent, taskId, cmdId]);

  /* silent task hint: let the user understand "why no-reply is preselected" (rather than thinking it is broken) */
  const replyModeOf = selectedTask?.replyMode || selectedCmd?.replyMode || "";
  const silentHint = replyModeOf === "silent" ? t("sendDialog.silentHint") : "";

  /* [2026-11-19 Feng / kernel 2b5e3e5] Text direct-send to an assistant session:
     `agx_send_text` auto-injects an [assistant identity hint] pointer block (about 500 chars) before the body.
     This is kernel behavior, not a user action; the UI only shows "what was sent is longer than what I wrote".
     So say it here — not telling the person to add it manually, but telling him "what you wrote is unchanged, the
     system adds another block".
     ★ Assistant detection also accepts both fields (isAssistant / sessionType), consistent with sessionGroup(). */
  const assistantAutoHint =
    mode === "text" && (targetSel?.isAssistant || targetSel?.sessionType === "assistant")
      ? t("sendDialog.assistantAutoHint")
      : "";

  /* ── Validation (the frontend only guides "can it be sent", authoritative validation is in the backend/kernel) ── */
  const problem = React.useMemo(() => {
    if (!senderAgent) return t("sendDialog.validate.sender");
    if (!targetAgent) return t("sendDialog.validate.target");
    /* [2026-11-17 22:32 Feng] "While recording the send button must not be activatable, only after clicking done
       can you click send"
       ★ Why it must be blocked: the sentence being recorded has not entered the input box yet, so what gets sent is
         **a body missing one sentence**, while the person thinks he has said it — this kind of mistake cannot be
         fixed by "sending once more", the other side already received half a sentence.
       ★ Placed first in validation: recording is the most definite "not now", no need to let it compete with
         "body is empty". */
    if (voiceLocked) return voiceBlockReason;
    // [2026-10-19 P5] Reply forced either/or: final fallback validation (normally will not trigger —
    //   it is already either/or + has a default; kept to protect against "code broken / default lost")
    if (mode !== "text" && needsReply !== "0" && needsReply !== "1") {
      return t("sendDialog.validate.reply");
    }
    if (mode === "text") {
      /* Placed first: this is not "missing input" but "this path does not work", say it clearly before other validations. */
      if (textImpossible) {
        return t("sendDialog.validate.textImpossible");
      }
      if (!text.trim()) return t("sendDialog.validate.textEmpty");
      if (!targetSession) return t("sendDialog.validate.textSession");
      return "";
    }
    if (skillIds.length > 8) return t("sendDialog.validate.skillsMax");
    if (mode === "task") {
      if (!taskId) return t("sendDialog.validate.task");
      if (sessionMode === "existing" && !targetSession) return t("sendDialog.validate.session");
      return "";
    }
    /* [2026-11-16 12:45] The criterion changed from `cmdId` to `selectedCmd` (= cmdId hit, or fixedCmd given directly
       by the entry). It used to only accept cmdId: when the entry passed fixedCmd and cmdId was not synced, the
       param table rendered and was fillable as usual, but here it always said "please select a quick command" → the
       button stayed grey forever, and the reported reason contradicted what was on screen. */
    if (!selectedCmd) return t("sendDialog.validate.quickcmd");
    // [2026-10-17] Quick command params: the frontend only "guides filling", authoritative validation is still in the kernel (PARAM_MISSING).
    const missing = selectedCmd?.params.find((p) => {
      if (!p.required) return false;
      const v = params[p.name];
      return v === undefined || v === null || (typeof v === "string" && !v.trim());
    });
    if (missing) {
      return t("sendDialog.validate.paramRequired", { name: missing.label || missing.name });
    }
    if (sessionMode === "existing" && !targetSession) return t("sendDialog.validate.session");
    return "";
  }, [senderAgent, targetAgent, mode, text, targetSession, taskId, sessionMode, cmdId, selectedCmd, params, skillIds, needsReply, textImpossible, voiceLocked, voiceBlockReason]);

  /* [2026-11-13 00:23 Feng] When the body is empty, no longer pop "please enter the message body" —
     empty + grey button is obvious at a glance; more explanation is worse.
     ★ Affects **display** only: `problem` itself is unchanged, the disable threshold does not move. */
  const problemHint = problem === t("sendDialog.validate.textEmpty") ? "" : problem;

  /* After sending this one, "restore the input area to the state of waiting for the next one".
     ★ Clear only "content", not "context" — target Agent / session / task are shared settings for sending multiple
       in a row, re-selecting each time is what is really unusable. */
  const resetContent = React.useCallback(() => {
    setText("");
    if (mode === "quickcmd" && selectedCmd) {
      // Params back to schema defaults (equivalent to the state right after selecting this command)
      setParams(buildInitParams(selectedCmd, senderAgent, targetAgent));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, selectedCmd]);

  /** [2026-11-13 15:40] "Clear" the restored draft: delete storage + empty the content cells (context kept).
   *  Why not just "reopen the window": the user may only want to drop this old content and keep using the current
   *  receiver settings. */
  const discardDraft = React.useCallback(() => {
    /* Delete only the **current receiver's** bucket: the half written for A should not be carried away by "clear B". */
    saveDraftToBucket(draftBucketOf(targetAgent), null);
    setDraftAt(0);
    setDraftRestored(false);
    setText("");
    setExecName("");
    setSkillIds([]);
    if (mode === "quickcmd" && selectedCmd) {
      setParams(buildInitParams(selectedCmd, senderAgent, targetAgent));
    } else {
      setParams({});
    }
    /* How many remain (the "N others" in the hint bar must follow, must not still show the old number) */
    setOtherDrafts(Object.keys(readDraftStore().items).length);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, selectedCmd, targetAgent]);

  /* Record it when the send subject changes — bring it next time the window opens (keys in lib/storage-keys.ts). */
  React.useEffect(() => {
    if (!senderAgent) return;
    writeKey("sendSender", senderAgent);
  }, [senderAgent]);

  /* Record a sent item into the stream and clear the input area */
  const pushSent = React.useCallback(
    (out: UniversalSendResult, content: string) => {
      /* [2026-11-14 20:31 Feng] "Just add the message you sent into the history conversation above"
         → immediately insert a "pending ingest" row at the end of the history list, replacing it with the real row
         after the collector writes to the DB.
         [2026-11-14 22:52 Feng] "It goes by session, each session is different".
         ★ Originally the idea here was "pending hangs on this component, refetching on session switch follows
           along" — wrong. The component does not remount when sessionKey changes, so the state is carried into
           another session's history. Now the **real landing point at send time** is passed down, and the history
           component groups by session itself. */
      /* [2026-11-14 21:12 Feng] "What you put inside seems different from what I sent"
         → for task/quick command pass `runUuid`: that placeholder in history will take from the execution record
           **the full command the kernel really dispatched** (session context + this instruction + task spec),
           not the few dozen chars in the input box.
         ★ Text mode has no run_uuid (`agx_send_text` creates no execution), so it can only show the body itself —
           that body is what was sent, semantically consistent. */
      histApiRef.current?.appendSent(content, {
        mode: mode === "text" ? "text" : mode === "task" ? "task" : "quickcmd",
        note: mode === "text" ? out.target_agent || "" : out.task_name || "",
        runUuid: out.run_uuid || "",
        /* ★ Go by **the real returned session**: in new-session mode targetSession is still empty,
           while this message really landed in out.session_key. */
        sessionKey: out.session_key || targetSession,
      });
      resetContent();
      /* Sent = this content is no longer "unsent", the draft is void immediately.
         ★ Do not rely on the debounce timer to clear: within that 1 second the user may have closed the window,
           and the next open would show a "restored" hint + content that was clearly already sent. */
      saveDraftToBucket(draftBucketOf(targetAgent), null);
      setDraftAt(0);
      setDraftRestored(false);
      setOtherDrafts(Object.keys(readDraftStore().items).length);
      /* [2026-11-13 21:39] Record the landing point to keep observing the running state —
         in new-session mode targetSession is empty, the real key is only known after sending. */
      setLastSentKey(out.session_key || "");
      /* The idempotency key only protects "the same intent": void once successful, the next one must be a new key,
         otherwise the kernel treats it as a retry and returns the first result (the second one never gets sent). */
      requestIdRef.current = `uib_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
      onSent?.(out);
    },
    [resetContent, onSent]
  );

  async function handleSubmit() {
    /* Sending is not allowed while running — the button is already disabled, this is the second gate (against the Enter key bypass). */
    /* [2026-11-13 23:47 Feng] "Cancel the feature that locks the send button while the session is running for now,
       the session state is wrong now, it shows running when it is clearly not, and the lock gets in the way."
       → Remove rtRunning from the guard: **state only hints, it does not block action**.
       Consider adding it back once the criterion is trustworthy again. */
    if (submitting || problem) return;
    setError("");
    setSubmitting(true);
    try {
      if (mode === "text") {
        const r = await agentsApi.sendText(targetAgent, {
          session_key: targetSession,
          text,
          caller_agent_id: senderAgent,
          ...(skillIds.length ? { skill_ids: skillIds } : {}),
        });
        /* [2026-11-17 15:41 Feng] "I select skills then send... the page does not show content with the skills,
           at least right after sending it is not updated."
           → For the echo use **text_full echoed by the backend** (including the "reference skills" block), not the
             input's original text. Assembly happens in the kernel, the frontend does not copy the rule; here it is
             only responsible for "showing what was really sent". */
        pushSent(
          { mode, session_key: r.session_key, target_agent: r.target_agent },
          (r.text_full || text).trim()
        );
      } else if (mode === "task") {
        const task = selectedTask;
        const r = await agentsApi.runTask(targetAgent, {
          session_key: sessionMode === "new" ? "" : targetSession,
          instruction: text || task?.description || "",
          task_id: taskId,
          task_name: task?.name,
          caller_agent_id: senderAgent,
          send_session_key: senderSession,
          // [2026-10-19 P5] Reply forced either/or: only pass reply, force_no_reply is already internal to the facade
          reply: needsReply === "1" ? "yes" : "no",
          ...(requestIdRef.current ? { client_request_id: requestIdRef.current } : {}),
          ...(timeoutNumber != null ? { timeout_seconds: timeoutNumber } : {}),
          ...(execName.trim() ? { exec_name: execName.trim() } : {}),
          ...(skillIds.length ? { skill_ids: skillIds } : {}),
          /* [2026-11-06 16:40 wired through] Human confirmation (needs_confirm):
             facade agx_universal_send → BFF actions.run_task → kernel agx_dispatch_task
             all three already pass it through, so it is safe to really submit here.
             Only pass it when 1 (passing 0 → exactly the same as before, no extra field). */
          ...(needsConfirm === "1" ? { needs_confirm: 1 } : {}),
        });
        const out: UniversalSendResult = {
          mode,
          session_key: r.session_key,
          run_uuid: r.run_uuid,
          target_agent: targetAgent,
          task_name: r.task_ref?.task_name || task?.name,
        };
        pushSent(
          out,
          text.trim() ||
            t("sendDialog.emptyInstruction", { name: task?.name || taskId }),
        );
      } else {
        const r: QuickCmdRunResult = await runQuickCommand({
          cmd_id: cmdId,
          params,
          caller_agent_id: senderAgent,
          self_session_id: senderSession,
          target_session_key: sessionMode === "new" ? "" : targetSession,
          // [2026-10-19 P5] Reply forced either/or: only pass reply, force_no_reply is already internal to the facade
          reply: needsReply === "1" ? "yes" : "no",
          ...(requestIdRef.current ? { client_request_id: requestIdRef.current } : {}),
          ...(timeoutNumber != null ? { timeout_seconds: timeoutNumber } : {}),
          ...(execName.trim() ? { exec_name: execName.trim() } : {}),
          ...(skillIds.length ? { skill_ids: skillIds } : {}),
        });
        const out: UniversalSendResult = {
          mode,
          session_key: r.session_key,
          run_uuid: r.run_uuid,
          target_agent: r.target_agent,
          task_name: r.task_ref?.task_name,
        };
        pushSent(out, `${selectedCmd?.title || selectedCmd?.cmdId || cmdId}${digestParams()}`);
      }
    } catch (e) {
      const err = e as { message?: string; code?: string; status?: number };
      const friendly =
        err.code === "PERMISSION_DENIED" || err.status === 403
          ? t("sendDialog.error.permission")
          : err.code === "SESSION_NOT_OWNED"
            ? t("sendDialog.error.notOwned")
            : err.code === "REPLY_REQUIRED"
              ? t("sendDialog.error.replyRequired")
              : err.code === "TASK_NOT_FOUND"
                ? t("sendDialog.error.taskNotFound")
                : err.code === "CMD_NOT_FOUND"
                  ? t("sendDialog.error.cmdNotFound")
                  : apiErrorText(e, t);
      setError(friendly);
    } finally {
      setSubmitting(false);
    }
  }

  // [2026-10-20 Feng feedback] It used to be `!submitting && !result` → after a successful send all 19 controls in the dialog became disabled,
  // even the body input box could not be clicked, only "Close" worked = the user's "the whole UI is locked".
  // Now: disable only while this request is in flight; the result state stays editable (changing content auto-clears the result bar, back to sendable).
  const editable = !submitting;

  /* Execution param availability by mode (authoritative validation is in BFF/kernel; here only guidance) */
  // [2026-10-17 Feng] Quick command also supports "whether a reply is needed" (kernel agx_quick_cmd_send already has this param).
  //   Text = direct send, no execution created → none of the three apply.
  const execParamSupported = {
    needsReply: mode === "task" || mode === "quickcmd",
    /* [2026-11-06] Human confirmation is a **task-level attribute** (kernel agx_dispatch_task's needs_confirm),
       and only meaningful in Task mode: quick command goes another path (agx_quick_cmd_send, no such param). */
    needsConfirm: mode === "task",
    timeout: mode !== "text",
    execName: mode !== "text",
  };
  const timeoutNumber = timeoutSec.trim() && !Number.isNaN(Number(timeoutSec))
    ? Number(timeoutSec)
    : undefined;

  /* ── Quick favorite (title bar ★) ─────────────────────────────────────────────
     Favorite target = the current receive landing point: if there is a receive session, favorite the session,
     otherwise favorite the receive Agent.
     Why prefer the session: the same Agent often has multiple sessions, and what people want to pin is "this very
     session"; favoriting only the Agent makes them take one more step next time. */
  const quickType: "session" | "agent" | "" = targetSession
    ? "session"
    : targetAgent
      ? "agent"
      : "";
  const quickKey = targetSession || targetAgent;

  /* Align with the real state when the window opens / the landing point changes (the store may have been changed elsewhere). */
  React.useEffect(() => {
    if (!open || !quickKey || !quickType) { setQuickOn(false); return; }
    let alive = true;
    refreshQuickEntries()
      .then(() => { if (alive) setQuickOn(isQuickEntry(quickType, quickKey)); })
      .catch(() => { if (alive) setQuickOn(false); });
    return () => { alive = false; };
  }, [open, quickType, quickKey]);

  const toggleQuick = async () => {
    if (!quickKey || !quickType || quickBusy) return;
    setQuickBusy(true);
    try {
      if (quickOn) await removeQuickEntry(quickType, quickKey);
      else await addQuickEntry(quickType, quickKey, "");
      setQuickOn(!quickOn);
    } catch {
      /* Favorite failure does not interrupt sending: the button returns to unselected, the person can click again */
    } finally {
      setQuickBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => onOpenChange(o)}>
      <DialogContent
        ref={attachDlgRef}
        /* [2026-11-10 09:04 Feng] "The wireframe of this page, this big frame, should it be a bit thicker?
           Outside is already thicker, yours is still the old one, hard to tell apart."
           → 2px: the window boundary must be separable from the page behind it. Only this window, do not touch
             ui/dialog's global default (other dialogs were not mentioned, changing it decides for everyone); if the
             whole site is confirmed to need it, change that one line. */
        /* [2026-11-13] Fullscreen toggle: normal size ↔ fills the viewport.
           ★ Use `!inset-0 + !w-auto !h-auto` instead of `!w-screen !h-screen`:
             `100vw` includes the scrollbar width → going fullscreen adds 15px of horizontal scroll;
             inset spreads over the **usable viewport**, no overflow. */
        className={cn(
          "!flex flex-col gap-0 overflow-hidden border-2 p-0",
          maximized
            ? "!inset-0 !h-auto !w-auto !max-h-none !max-w-none !translate-x-0 !translate-y-0 !rounded-none"
            : "h-[min(96vh,56rem)] max-h-[96vh] w-[min(97vw,106rem)] max-w-[min(97vw,106rem)]",
        )}
        /* ★ testid keeps the historical identifiers `universal-input-bar` / `uib-*`:
           8 acceptance scripts, about 180 references to it. Renaming would break all scripts at once,
           and it is invisible to humans — keep it for now, switch uniformly once this round of features is final. */
        data-testid="universal-input-bar"
      >
        {/* [2026-11-13 08:07] Overlay mount point: in fullscreen render all dropdowns/overlays **inside the dialog**
           (instead of document.body) — otherwise under native fullscreen they are all off-screen and cannot be opened.
           Passing null when not fullscreen = restore mounting to body by default, behavior exactly as before. */}
        <PortalContainerProvider container={maximized ? dlgNode : null}>
        {/* [2026-11-06 22:30] Add "refresh" at the top-right of the title bar — right next to the site-wide X (right-4),
           one step to the left. Same position and same semantics as the board dialog (PageDialog): when people want
           to confirm "is this the latest", there is something they can definitely press. */}
        <button
          type="button"
          data-testid="uib-maximize"
          aria-label={maximized ? t("sendDialog.toolbar.restore") : t("sendDialog.toolbar.maximize")}
          title={maximized ? t("sendDialog.toolbar.restoreTitle") : t("sendDialog.toolbar.maximizeTitle")}
          onClick={() => void toggleFullscreen()}
          className="absolute right-[4.5rem] top-3.5 z-20 rounded-sm p-0.5 opacity-70 transition-opacity hover:opacity-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {maximized ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
        </button>
        <button
          type="button"
          data-testid="uib-quick-star"
          aria-label={quickOn ? t("sendDialog.toolbar.starOff") : t("sendDialog.toolbar.starOn")}
          aria-pressed={quickOn}
          data-quick={quickOn ? "1" : "0"}
          title={
            !quickKey
              ? t("sendDialog.toolbar.starBlocked")
              : quickOn
                ? t(quickType === "session" ? "sendDialog.toolbar.starRemoveSession" : "sendDialog.toolbar.starRemoveAgent")
                : t(quickType === "session" ? "sendDialog.toolbar.starAddSession" : "sendDialog.toolbar.starAddAgent")
          }
          disabled={!quickKey || quickBusy}
          onClick={() => void toggleQuick()}
          className={cn(
            "absolute right-[6.5rem] top-3.5 z-20 rounded-sm p-0.5 transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-30",
            quickOn ? "text-warning" : "opacity-70 hover:opacity-100",
          )}
        >
          {quickBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Star className={cn("h-4 w-4", quickOn && "fill-current")} />}
        </button>
        <button
          type="button"
          data-testid="uib-refresh"
          aria-label={t("sendDialog.toolbar.refresh")}
          title={t("sendDialog.toolbar.refreshTitle")}
          disabled={refreshing}
          onClick={() => void doRefresh()}
          className="absolute right-11 top-3.5 z-20 rounded-sm p-0.5 opacity-70 transition-opacity hover:opacity-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40"
        >
          <RefreshCw className={refreshing ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
        </button>
        {/* Fixed title bar: does not scroll with content; the close button is an absolutely positioned child of DialogContent, also fixed */}
        {/* [2026-11-10 08:53 Feng] Give this title line to the two dropdowns:
            "You can drop the 'message send' line, it is useless, no need to say so much, looking at this makes it
             clear. Then add two dropdowns to its right, one to select the project, one to select the Agent."
            — The explanation line (who is the sender → how to execute → …) is dropped as well: it describes the
              layout, and the layout is right in front of you; keeping it only takes space, and every year it has
              to be changed along with the flow.

            ★ The title is not deleted, it becomes `sr-only`: Dialog gets its name via aria-labelledby.
              After deleting it a screen reader would only say "dialog" — removing text is a visual decision, it
              should not casually cut accessibility info. */}
        <DialogHeader className="shrink-0 gap-1 border-b px-4 py-2 pr-24" data-testid="uib-header">
          {/* [2026-11-10 09:04 Feng] "Keep the 'message send' title, the title at the top-left is still useful."
              — The previous version made it sr-only (screen readers only). Now restore it to a visible title, and
                move the two dropdowns to its right, still on the same row. ★ The title is the only anchor of "which
                window I am in": the window content changes (sender/receiver/mode), the title does not; delete it and
                people can only guess where they are from the layout. */}
          {/* Spacing in two tiers: 6px within a group (label next to its dropdown), 20px between groups ("the project
               group / the Agent group"). When both tiers are the same width, people cannot tell whether the "Agent"
               label belongs to the previous cell or the next one. */}
          <div className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-1.5" data-testid="uib-quick-scope">
            {/* Use DialogTitle's own font size (text-lg 18px): the previous version squeezed it to text-base,
                and the screenshot review said "the title drowns in the controls" — with 8 elements on one row, the
                hierarchy can only be carried by font size/weight, not by position (position is already occupied by
                controls). */}
            <DialogTitle className="flex shrink-0 items-center gap-2">
              <Layers className="h-4 w-4" />
              {t("sendDialog.title")}
            </DialogTitle>
            <span aria-hidden="true" className="h-4 w-px shrink-0 bg-border" />
            <div className="flex min-w-0 items-center gap-1.5">
              <span className="shrink-0 text-xs text-muted-foreground">{t("sendDialog.project")}</span>
              <Select
                value={projFilter || QUICK_ALL_PROJECTS}
                onValueChange={(v) => changeQuickProject(v === QUICK_ALL_PROJECTS ? "" : v)}
              >
                <SelectTrigger className="h-8 w-[13rem] min-w-0 [&>span]:truncate" data-testid="uib-quick-project">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="max-h-[22rem]">
                  {/* [2026-11-12 15:08 Feng] The parentheses hold the **project count**, not the agent count.
                      "Others read it as 84 projects, too ambiguous" → use projectOptions.length. */}
                  <SelectItem value={QUICK_ALL_PROJECTS}>
                    {t("sendDialog.allProjects", { n: projectOptions.length })}
                  </SelectItem>
                  {projectOptions.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      <span className="inline-flex min-w-0 items-center gap-1.5">
                        <span className="truncate">{projectNameWithPin(p.id, p.name)}</span>
                        <span className="shrink-0 text-[10px] text-muted-foreground">{p.n}</span>
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex min-w-0 items-center gap-1.5">
              <span className="shrink-0 text-xs text-muted-foreground">Agent</span>
              <Select
                value={quickAgentValue || undefined}
                onValueChange={applyQuickAgent}
                disabled={!quickAgentUsable}
              >
                <SelectTrigger
                  className="h-8 w-[22rem] min-w-0 [&>span]:truncate [&>span]:max-w-full"
                  title={quickAgentValue || undefined}
                  data-testid="uib-quick-agent"
                >
                  <SelectValue placeholder={t("sendDialog.pickAgent")} />
                </SelectTrigger>
                <SelectContent className="max-h-[22rem] max-w-[var(--radix-popper-available-width)]">
                  {quickAgentOptions.map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      <AgentOptionLabel agent={a} />
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {quickAgentUsable && (!isLocked("senderAgent") || !isLocked("targetAgent")) && (
              <span className="text-[11px] text-muted-foreground" data-testid="uib-quick-hint">
                {t("sendDialog.quickHint")}
              </span>
            )}
          </div>
          {/* Entering from a session: the receiver is locked, put "who it goes to" right under the title —
              otherwise the user has to scroll to the third screen of the left column to see the landing point, and
              easily thinks they are still choosing. */}
          {isLocked("targetSession") && !!targetSession && (
            <p
              className="flex min-w-0 items-center gap-1.5 text-[11px] text-muted-foreground"
              data-testid="uib-locked-target"
            >
              <Lock className="h-3 w-3 shrink-0" />
              <span className="shrink-0">{t("sendDialog.lockedTarget")}</span>
              <span className="truncate font-mono text-foreground" title={targetSession}>
                {targetSession}
              </span>
              <RunningMark show={rtRunning} runId={rtRunId} testId="uib-locked-target-running" />
            </p>
          )}
        </DialogHeader>

        {/* [2026-11-13 15:40 Feng] Draft restore hint.
            Why this line is needed: "the content came back by itself" and "it never got lost" look exactly the same
            to people — without a hint the user wonders whether he misremembers; with "clear" he can actively drop
            the old content he does not want. */}
        {draftRestored && (
          <div
            className="flex shrink-0 items-center gap-2 border-b bg-muted/30 px-4 py-1 text-[11px] text-muted-foreground"
            data-testid="uib-draft-restored"
          >
            <History className="h-3 w-3 shrink-0" />
            <span className="truncate">
              {draftAt
                ? t("sendDialog.draft.restoredAt", {
                    time: draftHhmm(draftAt),
                    name: agentText(targetAgent),
                  })
                : t("sendDialog.draft.restoredLast")}
            </span>
            {/* [2026-11-13 15:53] Tell "other receivers also keep drafts".
                Without saying it, people do not know the half they wrote for A is still there — they think only the
                current one was kept. */}
            {otherDrafts > 0 && (
              <span
                className="shrink-0 text-muted-foreground/70"
                title={t("sendDialog.draft.othersTitle", { n: otherDrafts })}
                data-testid="uib-draft-others"
              >
                {t("sendDialog.draft.othersCount", { n: otherDrafts })}
              </span>
            )}
            <button
              type="button"
              onClick={discardDraft}
              data-testid="uib-draft-clear"
              className="shrink-0 underline underline-offset-2 transition-colors hover:text-foreground"
            >
              {t("sendDialog.draft.clear")}
            </button>
          </div>
        )}

        {/* [2026-10-20 Feng] Two-column split: left = config (block grid, scroll as little as possible), right = body
             area (the highlight). The old version was one long column: typing the body scrolled the whole block,
             finding things back and forth was exhausting. */}
        <div className="flex min-h-0 flex-1 overflow-hidden" data-testid="uib-body">

        {/* ── Left column: config ── */}
        <div
          className="flex min-h-0 min-w-0 flex-1 gap-2 overflow-y-auto px-4 py-4 [@media(max-height:820px)]:gap-1.5"
          data-testid="uib-config-col"
        >
        <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
{/* ════════ ① Sender: who is sending ════════ */}
        {!isHidden("senderAgent") && (
          <section className="flex min-w-0 flex-col gap-1.5 rounded-md border-2 border-info/40 p-2" data-testid="uib-layer-context">
            {/* [2026-11-13 10:16 Feng] "Just make it the same as above" — consistent with ③ receiver and ⑦ message body:
                no explanation text next to the title. ①③ are a pair, dropping only one side would stand out more. */}
            <LayerTitle n="①" title={t("sendDialog.layer.sender")} accent="info" />
            <div className="grid min-w-0 grid-cols-1 gap-2">
              <URow label={t("sendDialog.field.senderAgent")} className="min-w-0 rounded-lg border bg-card/60 p-2 [&>div:last-child]:min-w-0">
                <div className="flex min-w-0 flex-1 items-center gap-1">
                {isLocked("senderAgent") ? (
                  <div className="min-w-0 flex-1">
                    <LockedValue text={agentText(senderAgent)} />
                  </div>
                ) : (
                  <Select value={senderAgent || undefined} onValueChange={setSenderAgent} disabled={!editable}>
                    <SelectTrigger className="min-w-0 flex-1 [&>span]:truncate" data-testid="uib-sender-agent">
                      <SelectValue placeholder={t("sendDialog.ph.senderAgent")} />
                    </SelectTrigger>
                    <SelectContent className="max-w-[var(--radix-popper-available-width)]">
                      {agentOptions.map((a) => (
                        /* [2026-11-07 Feng] Offline / templates are listed too (must be selectable to query history).
                           Express status with a small badge on the right, keep the text in normal color — greying out
                           the whole row reads as "not clickable", while here it must be clickable (selecting it is
                           exactly to query its sessions in the dropdown below). */
                        <SelectItem key={a.id} value={a.id}>
                          <AgentOptionLabel agent={a} />
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
                {/* [2026-11-15 23:50 Feng] "Add an open after the send subject too" — when this Agent turns out to have a problem, open its detail in place, no need to close the window and make a trip */}
                <AgentOpenButton agentId={senderAgent} testId="uib-open-sender-agent" hint={t("sendDialog.ph.senderFirst")} />
                </div>
              </URow>

              {!isHidden("senderSession") && (
                <URow label={t("sendDialog.field.senderSession")} className="min-w-0 rounded-lg border bg-card/60 p-2 [&>div:last-child]:min-w-0">
                  {isLocked("senderSession") ? (
                    <div className="flex min-w-0 items-center gap-2">
                      <LockedValue text={senderSession} mono />
                      <RunningMark show={senderRtRunning} runId={senderRtRunId} testId="uib-sender-locked-running" />
                    </div>
                  ) : (
                    /* Dropdown + "open session" side by side: left changes "where to send from", right goes to look at it (new tab, does not touch current input) */
                    <div className="flex min-w-0 items-center gap-1">
                    <Select
                      value={senderSession || undefined}
                      onValueChange={setSenderSession}
                      disabled={!editable || !senderAgent}
                    >
                      <SelectTrigger className="min-w-0 [&>span]:truncate" data-testid="uib-sender-session">
                        {senderSel ? (
                          <SelectValue>
                            <span className="inline-flex min-w-0 items-center gap-1.5">
                              <OnlineDot show={sessionRunning(senderSel)} title={t("sendDialog.running.label")} />
                              <span className={cn("truncate", sessionRunning(senderSel) && "text-success")}>
                                {sessionLabel(senderSel, t)}
                              </span>
                            </span>
                          </SelectValue>
                        ) : (
                          <SelectValue placeholder={senderAgent ? t("sendDialog.ph.senderSession") : t("sendDialog.ph.senderFirst")} />
                        )}
                      </SelectTrigger>
                      <SelectContent className="max-w-[var(--radix-popper-available-width)]">
                        {senderSessions.length === 0 ? (
                          <div className="px-3 py-2 text-xs text-muted-foreground">{t("sendDialog.empty.noSenderSessions")}</div>
                        ) : (
                          senderSessions.map((s) => (
                            <SelectItem key={s.id} value={s.id}>
                              <span className="inline-flex items-center gap-1.5">
                                <OnlineDot show={sessionRunning(s)} title={t("sendDialog.running.label")} />
                                {sessionLabel(s, t)}
                              </span>
                            </SelectItem>
                          ))
                        )}
                      </SelectContent>
                    </Select>
                    <SessionOpenButton sessionId={senderSession} testId="uib-open-sender-session" />
                    </div>
                  )}
                </URow>
              )}
            </div>
          </section>
        )}

        {/* ════════ ③ Receiver: varies with execution mode ════════
            · text            → Agent (locked = send subject) + its session
            · Task            → Agent + that Agent's Task
            · quick command   → Agent + that Agent's quick command
            Common downstream of all three: which session to land in (Task / quick command). */}
        {(!isHidden("targetAgent") || !isHidden("targetSession") || !isHidden("task") || !isHidden("quickcmd")) && (
          <section className="flex min-w-0 flex-col gap-1.5 rounded-md border-2 border-success/40 p-2" data-testid="uib-layer-target">
            <LayerTitle
              n="③"
              /* [2026-11-08 21:14 Feng] "Change receive object to receiver, do not have 'sender' above and 'recipient' below,
                 that does not look good." → symmetric with ① "sender": sender / recipient. */
              title={t("sendDialog.layer.receiver")}
              /* [2026-11-13 10:16 Feng] "There is a line below and a line above, actually both are explanations,
                 remove them, remove them all, just make it the same as above (message body)."
                 ★ The difference of execution modes is already expressed by the "② execution mode" row of buttons
                   + the content that appears below; writing "Agent + that Agent's task template" again next to the
                   title is redundant.
                 ★ Consistent with ⑦ message body: no hint at all. */
            accent="success" />

            <div className="grid min-w-0 grid-cols-1 gap-2">
              {/* ── Agent (the first option common to all three modes) ── */}
              {/* [2026-11-13 10:16 Feng] "Change agent to receive subject, same as above (send subject)" */}
              <URow label={t("sendDialog.field.targetAgent")} className="min-w-0 rounded-lg border bg-card/60 p-2 [&>div:last-child]:min-w-0">
                <div className="flex min-w-0 flex-1 items-center gap-1">
                {isLocked("targetAgent") || mode === "text" || targetDefinedByCmd || targetDerivedFromSender ? (
                  /* [2026-11-13 10:16 Feng] There used to be a small line here explaining "locked by the command" /
                     "equals the send subject", removed as well — since the value is already locked and not selectable,
                     explaining why it is locked is a redundant note. ★ data-testid="uib-target-by-cmd" is removed
                     with it; an old acceptance script referencing it must change to judge "is the value read-only"
                     (currently no script depends on it). */
                  <div className="min-w-0 flex-1">
                    <LockedValue text={agentText(targetAgent || senderAgent)} />
                  </div>
                ) : (
                  <Select value={targetAgent || undefined} onValueChange={changeTargetAgent} disabled={!editable}>
                    <SelectTrigger className="min-w-0 flex-1 [&>span]:truncate" data-testid="uib-target-agent">
                      <SelectValue placeholder={t("sendDialog.ph.agent")} />
                    </SelectTrigger>
                    <SelectContent className="max-w-[var(--radix-popper-available-width)]">
                      {agentOptions.map((a) => (
                        /* [2026-11-07 Feng] Offline / templates are listed too (must be selectable to query history).
                           Express status with a small badge on the right, keep the text in normal color — greying out
                           the whole row reads as "not clickable", while here it must be clickable (selecting it is
                           exactly to query its sessions in the dropdown below). */
                        <SelectItem key={a.id} value={a.id}>
                          <AgentOptionLabel agent={a} />
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
                {/* [2026-11-15 23:50 Feng] "Add one after the receive subject too" —
                    must also exist when locked (text mode / command-locked): then it is the send subject himself,
                    and even more often needed to look at */}
                <AgentOpenButton
                  agentId={targetAgent || senderAgent}
                  testId="uib-open-target-agent"
                  hint={t("sendDialog.ph.targetFirst")}
                />
                </div>
              </URow>

              {/* ── Task: corresponding Task ── */}
              {mode === "task" && !isHidden("task") && (
                <URow label="Task" className="min-w-0 rounded-lg border bg-card/60 p-2 [&>div:last-child]:min-w-0">
                  {isLocked("task") ? (
                    <LockedValue text={selectedTask ? `${selectedTask.name} — ${selectedTask.description}` : taskId} />
                  ) : (
                    <Select value={taskId || undefined} onValueChange={setTaskId} disabled={!editable || !targetAgent}>
                      <SelectTrigger className="min-w-0 [&>span]:truncate" data-testid="uib-task-select">
                        <SelectValue placeholder={targetAgent ? t("sendDialog.ph.task") : t("sendDialog.ph.agentFirst")} />
                      </SelectTrigger>
                      <SelectContent className="max-w-[var(--radix-popper-available-width)]">
                        {taskOptions.length === 0 ? (
                          <div className="px-3 py-2 text-xs text-muted-foreground">{t("sendDialog.empty.noTasks")}</div>
                        ) : (
                          taskOptions.map((t) => (
                            <SelectItem key={t.id} value={t.id}>
                              {t.name}{t.description ? ` — ${t.description}` : ""}
                            </SelectItem>
                          ))
                        )}
                      </SelectContent>
                    </Select>
                  )}
                </URow>
              )}

              {/* ── Quick command: corresponding quick command ── */}
              {mode === "quickcmd" && !isHidden("quickcmd") && (
                <URow label={t("sendDialog.field.quickcmd")} className="min-w-0 rounded-lg border bg-card/60 p-2 [&>div:last-child]:min-w-0">
                  {isLocked("quickcmd") ? (
                    <LockedValue text={selectedCmd ? `${selectedCmd.title} (${selectedCmd.cmdId})` : cmdId} />
                  ) : (
                    <Select value={cmdId || undefined} onValueChange={setCmdId} disabled={!editable || cmdsLoading}>
                      <SelectTrigger className="min-w-0 [&>span]:truncate" data-testid="uib-quickcmd-select">
                        <SelectValue placeholder={cmdsLoading ? t("sendDialog.ph.loading") : t("sendDialog.ph.quickcmd")} />
                      </SelectTrigger>
                      <SelectContent className="max-w-[var(--radix-popper-available-width)]">
                        {cmdOptions.length === 0 ? (
                          <div className="px-3 py-2 text-xs text-muted-foreground">
                            {isLocked("targetSession") && cmds.length > 0
                              ? t("sendDialog.empty.cmdsCannotDeliver")
                              : t("sendDialog.empty.noCmds")}
                          </div>
                        ) : (
                          cmdOptions.map((c) => (
                            <SelectItem key={c.cmdId} value={c.cmdId}>
                              {c.title} · {c.cmdId}
                            </SelectItem>
                          ))
                        )}
                      </SelectContent>
                    </Select>
                  )}
                </URow>
              )}
            </div>

            {/* Quick command mapping (carried by the command, not editable) */}
            {mode === "quickcmd" && selectedCmd && (
              <p className="text-[11px] text-muted-foreground" data-testid="uib-quickcmd-mapping">
                <Lock className="mr-1 inline h-3 w-3" />
                {t("sendDialog.cmdMapping.owner")} <span className="font-mono">{selectedCmd.ownerAgent}</span>
                {" · "}
                {t("sendDialog.cmdMapping.target")} <span className="font-mono">{selectedCmd.targetTask}</span>
                {t("sendDialog.cmdMapping.fixed")}
              </p>
            )}

            {/* ── Delivery session (uniform for all three modes: text / Task / quick command) ── */}
            {!isHidden("targetSession") && (
              <div className="flex min-w-0 flex-col gap-2 border-t pt-3" data-testid="uib-delivery-block">
                {/* ★ Session entry (Feng 2026-11-04): the delivery landing point is fixed by the entry — do not show controls
                     like "new session / existing session" that make the user think it can be changed. Locked means
                     read-only display, and write clearly "who decided it". */}
                {isLocked("targetSession") ? (
                  <URow
                    label={t("sendDialog.lockedTarget")}
                    className="min-w-0 rounded-lg border bg-card/60 p-2 [&>div:last-child]:min-w-0"
                  >
                    {/* [2026-11-14 21:45 Feng] "The receiver's receive session originally had an open-session button after it,
                        now I do not know why you removed it. This is wrong, you still have to keep it."
                        ★ Fact: the button component has always been there (`SessionOpenButton`), only **the locked
                          branch did not call it** — entering from the session page / Agent detail / quick entry
                          the receive session is locked, and the locked branch only rendered a `LockedValue` (a
                          value with a lock icon), so "no open button when locked, only when selectable". This
                          was not a deliberate design, it was an omission.
                        ★ Why it is needed even more when locked: locked = the landing point of this send is
                          already fixed, and what the person most wants to do right then is exactly "go look at
                          what that session looks like" — an exit needed more than when freely selectable. */}
                    <div className="flex min-w-0 items-center gap-1">
                      <div className="min-w-0 flex-1">
                        <LockedValue text={lockedSessionText} mono />
                      </div>
                      <SessionOpenButton sessionId={targetSession} testId="uib-open-target-session" />
                    </div>
                  </URow>
                ) : (
                  <>
                    {/* [2026-11-13 10:16 Feng] Remove the "delivery session" label + the whole string of explanation after it:
                       "text direct send, no new session..." "not specified → the system creates one..."
                       "candidates = ...'s existing sessions" all restate what the buttons already say. Text mode
                       has neither of these two buttons anyway, and needs no explanation. */}
                    <div className="flex flex-wrap items-center gap-2">
                      {mode === "text" ? (
                        <></>
                      ) : (
                        <>
                          <Button
                            size="sm"
                            variant={sessionMode === "new" ? "secondary" : "ghost"}
                            disabled={!editable}
                            onClick={() => setSessionMode("new")}
                            data-testid="uib-session-new"
                          >
                            {t("sendDialog.sessionMode.new")}
                          </Button>
                          <Button
                            size="sm"
                            variant={sessionMode === "existing" ? "secondary" : "ghost"}
                            disabled={!editable}
                            onClick={() => setSessionMode("existing")}
                            data-testid="uib-session-existing"
                          >
                            {t("sendDialog.sessionMode.existing")}
                          </Button>
                        </>
                      )}
                    </div>

                    {/* Text: fixed existing session (can only send to the send subject himself) */}
                    {mode === "text" && (
                      <>
                      <URow label={t("sendDialog.lockedTarget")} className="min-w-0 rounded-lg border bg-card/60 p-2 [&>div:last-child]:min-w-0">
                        <div className="flex min-w-0 items-center gap-1">
                        <Select
                          value={targetSession || undefined}
                          onValueChange={setTargetSession}
                          disabled={!editable || !senderAgent}
                        >
                          <SelectTrigger className="min-w-0 [&>span]:truncate" data-testid="uib-text-target-session">
                            {targetSel ? (
                              <SelectValue>
                                <span className="inline-flex min-w-0 items-center gap-1.5">
                                  <OnlineDot show={sessionRunning(targetSel)} title={t("sendDialog.running.label")} />
                                  <span className={cn("truncate", sessionRunning(targetSel) && "text-success")}>
                                    {sessionLabel(targetSel, t)}
                                  </span>
                                </span>
                              </SelectValue>
                            ) : (
                              <SelectValue placeholder={senderAgent ? t("sendDialog.ph.session") : t("sendDialog.ph.senderFirst")} />
                            )}
                          </SelectTrigger>
                          <SelectContent className="max-w-[var(--radix-popper-available-width)]">
                            {targetSessions.length === 0 ? (
                              <div className="px-3 py-2 text-xs text-muted-foreground">{t("sendDialog.empty.noSessions")}</div>
                            ) : (
                              targetSessions.map((s) => (
                                <SelectItem key={s.id} value={s.id}>
                                  <span className="inline-flex items-center gap-1.5">
                                    <OnlineDot show={sessionRunning(s)} title={t("sendDialog.running.label")} />
                                    {sessionLabel(s, t)}
                                  </span>
                                </SelectItem>
                              ))
                            )}
                          </SelectContent>
                        </Select>
                        <SessionOpenButton sessionId={targetSession} testId="uib-open-text-target-session" />
                        </div>
                      </URow>
                      </>
                    )}

                    {/* Task / quick command: show the dropdown only when using an existing session */}
                    {mode !== "text" && sessionMode === "existing" && (
                      <URow label={t("sendDialog.lockedTarget")} className="min-w-0 rounded-lg border bg-card/60 p-2 [&>div:last-child]:min-w-0">
                        <div className="flex min-w-0 items-center gap-1">
                        <Select
                          value={targetSession || undefined}
                          onValueChange={setTargetSession}
                          disabled={!editable || !targetAgent}
                        >
                          <SelectTrigger className="min-w-0 [&>span]:truncate" data-testid="uib-target-session">
                            {targetSel ? (
                              <SelectValue>
                                <span className="inline-flex min-w-0 items-center gap-1.5">
                                  <OnlineDot show={sessionRunning(targetSel)} title={t("sendDialog.running.label")} />
                                  <span className={cn("truncate", sessionRunning(targetSel) && "text-success")}>
                                    {sessionLabel(targetSel, t)}
                                  </span>
                                </span>
                              </SelectValue>
                            ) : (
                              <SelectValue placeholder={targetAgent ? t("sendDialog.ph.targetSession") : t("sendDialog.ph.agentFirst")} />
                            )}
                          </SelectTrigger>
                          <SelectContent className="max-w-[var(--radix-popper-available-width)]">
                            {targetSessions.length === 0 ? (
                              <div className="px-3 py-2 text-xs text-muted-foreground">{t("sendDialog.empty.noSessions")}</div>
                            ) : (
                              targetSessions.map((s) => (
                                <SelectItem key={s.id} value={s.id}>
                                  <span className="inline-flex items-center gap-1.5">
                                    <OnlineDot show={sessionRunning(s)} title={t("sendDialog.running.label")} />
                                    {sessionLabel(s, t)}
                                  </span>
                                </SelectItem>
                              ))
                            )}
                          </SelectContent>
                        </Select>
                        <SessionOpenButton sessionId={targetSession} testId="uib-open-target-session" />
                        </div>
                      </URow>
                    )}
                  </>
                )}

                {/* [2026-11-19] Assistant session + text mode: the kernel `agx_send_text` auto-injects an
                    [assistant identity hint] (about 500 chars) before the body. The user cannot see this action and
                    only finds "longer than what I wrote" after sending — so explain it here.
                    ★ Placed **outside both the locked and non-locked entries**: entering from a session row is
                      locked, entering from a free entry is selectable, both paths must see this line (otherwise
                      the most common session-page entry would miss it, which is as good as not doing it). */}
                {mode === "text" && assistantAutoHint && (
                  <p
                    className="flex items-start gap-1 rounded-md border border-border/60 bg-muted/40 px-2 py-1 text-xs text-muted-foreground"
                    data-testid="uib-assistant-auto-hint"
                  >
                    <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <span>{assistantAutoHint}</span>
                  </p>
                )}
              </div>
            )}
          </section>
        )}

        {/* ════════ ⑤ Skills in use (agx_skill, valid only) ════════ */}
        {!isHidden("skills") && (
          <section className="flex min-h-0 min-w-0 flex-1 flex-col gap-1.5 rounded-md border-2 border-chart-4/40 p-2" data-testid="uib-layer-skills">
            <LayerTitle
              n="⑤"
              title={t("sendDialog.skills.title")}
              hint={t("sendDialog.skills.hint", { n: skillIds.length })}
            accent="pink" />

            {selectedSkills.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5" data-testid="uib-skill-selected">
                {selectedSkills.map((sk) => (
                  <button
                    key={sk.id}
                    type="button"
                    onClick={() => toggleSkill(sk.id)}
                    disabled={!editable}
                    title={t("sendDialog.skills.remove", { name: sk.name })}
                    className="inline-flex max-w-[18rem] items-center gap-1 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-xs text-primary"
                    data-testid={`uib-skill-chip-${sk.id}`}
                  >
                    <span className="truncate">{sk.name}</span>
                    <X className="h-3 w-3 shrink-0" />
                  </button>
                ))}
                <button
                  type="button"
                  onClick={() => setSkillIds([])}
                  disabled={!editable}
                  className="text-[11px] text-muted-foreground underline"
                  data-testid="uib-skill-clear"
                >
                  {t("sendDialog.skills.clear")}
                </button>
              </div>
            )}

            <Input
              className="h-8"
              placeholder={t("sendDialog.skills.search")}
              value={skillQuery}
              onChange={(e) => setSkillQuery(e.target.value)}
              disabled={!editable}
              data-testid="uib-skill-search"
            />

            <div
              className="min-h-0 min-w-0 flex-1 overflow-auto rounded-md border [&>button:not(:last-child)]:border-b"
              data-testid="uib-skill-list"
            >
              {skillOptions.length === 0 ? (
                <div className="px-3 py-3 text-center text-xs text-muted-foreground">
                  {allSkills.length === 0 ? t("sendDialog.skills.empty") : t("sendDialog.skills.noMatch")}
                </div>
              ) : (
                skillOptions.map((sk) => {
                  const on = skillIds.includes(sk.id);
                  return (
                    <button
                      key={sk.id}
                      type="button"
                      onClick={() => toggleSkill(sk.id)}
                      disabled={!editable}
                      aria-pressed={on}
                      className={cn(
                        "flex w-full min-w-0 items-start gap-2 px-3 py-2 text-left text-xs transition-colors hover:bg-accent",
                        on && "bg-primary/5",
                      )}
                      data-testid={`uib-skill-opt-${sk.id}`}
                    >
                      <Check
                        className={cn(
                          "mt-0.5 h-3.5 w-3.5 shrink-0",
                          on ? "text-primary" : "text-muted-foreground/30",
                        )}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium">{sk.name}</span>
                        <span className="block truncate text-muted-foreground">
                          {sk.description || sk.tags || t("sendDialog.skills.noDesc")}
                        </span>
                      </span>
                      <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                        {sk.category}
                      </span>
                    </button>
                  );
                })
              )}
            </div>
            <p className="flex items-start gap-1 text-[10px] leading-tight text-muted-foreground [@media(max-height:820px)]:hidden">
              <Sparkles className="mt-0.5 h-3 w-3 shrink-0" />
              {mode === "text" ? t("sendDialog.skills.textHint") : t("sendDialog.skills.taskHint")}
            </p>
          </section>
        )}

                </div>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
{/* ════════ ② Execution mode: independent options (mutually exclusive single choice) ════════ */}
        {!isHidden("mode") && (
          <section className="flex min-w-0 flex-col gap-1.5 rounded-md border-2 border-chart-1/40 p-2" data-testid="uib-layer-mode">
            <LayerTitle
              n="②"
              title={t("sendDialog.layer.execMode")}
              hint={t("sendDialog.layer.execModeHint")}
              accent="violet"
            />
            <div className="grid grid-cols-2 gap-2" role="radiogroup" data-testid="uib-mode-group">
              {(Object.keys(MODE_META) as UniversalMode[]).map((m) => {
                const Meta = MODE_META[m];
                const Icon = Meta.icon;
                const active = mode === m;
                const lockedOut = isLocked("mode") && !active;
                /* [2026-11-16 12:45 Feng] "After I select this task or this quick command I can no longer select text"
                   — here the text option used to be **disabled** when "send subject ≠ receiver", because
                   agx_send_text can only send into the caller's own session. But disabling is the wrong fix: Task /
                   quick command set the receiver to **someone else**, so once you switch over you can never switch
                   back, and the person is locked in another mode and cannot get out.
                   ★ Correct approach: **allow clicking**, and after clicking the effect below aligns the receiver to
                     the send subject automatically (text mode inherently has only this one legal landing point,
                     there is no case where "the user still has to change it himself").
                   ★ `textBlocked` is kept, but only used for the title explaining "will move the receiver", it does
                     not participate in disabled. */
                const textBlocked = m === "text" && !active && !!senderAgent && !!targetAgent && senderAgent !== targetAgent;
                return (
                  <button
                    key={m}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    disabled={!editable || lockedOut}
                    onClick={() => changeMode(m)}
                    title={
                      textBlocked
                        ? t("sendDialog.textOnly")
                        : undefined
                    }
                    data-testid={`uib-mode-${m}`}
                    data-blocked-reason={textBlocked ? "text-self-only" : undefined}
                    className={cn(
                      "flex min-w-0 items-start gap-1.5 rounded-lg border p-2 text-left transition-colors",
                      active
                        ? "border-primary bg-primary/5 ring-1 ring-primary"
                        : "border-border hover:bg-accent/60",
                      (!editable || lockedOut) && "cursor-not-allowed opacity-50",
                    )}
                  >
                    <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", active ? "text-primary" : "text-muted-foreground")} />
                    <span className="flex min-w-0 flex-col gap-0.5">
                      <span className="text-sm font-medium">{t(Meta.labelKey)}</span>
                      <span className="text-[11px] leading-snug text-muted-foreground">{t(Meta.hintKey)}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </section>
        )}

        {/* ════════ ④ Execution params (optional, shared by all tasks; aligned with RunTaskDialog) ════════ */}
        {!isHidden("execParams") && (
          <section className="flex min-w-0 flex-col gap-1.5 rounded-md border-2 border-chart-2/45 p-2" data-testid="uib-layer-options">
            <LayerTitle
              n="④"
              title={t("sendDialog.layer.params")}
              hint={t("sendDialog.layer.paramsHint")}
              accent="orange"
            />
            <div className="grid min-w-0 grid-cols-1 gap-1.5">
              {/* [2026-11-06 15:45 Feng] "Whether to reply" alone on a whole row is too wide,
                  change it so this row **holds two**: whether to reply + whether human confirmation is needed;
                  the timeout and title below stay as they are, one per row. */}
              <div
                className={cn(
                  "grid min-w-0 gap-1.5",
                  execParamSupported.needsConfirm ? "grid-cols-2" : "grid-cols-1",
                )}
                data-testid="uib-row-reply-confirm"
              >
                {/* [2026-11-06] When side by side switch to "label on top, control below" (tb).
                    The old lr layout's label column was fixed 6em (72px), one cell only ~194px →
                    leaving only ~110px for the dropdown, and "needs reply" was truncated to "needs r..." (clear in
                    the measured screenshot). tb lets the dropdown use the whole half-cell width; the single column
                    (quick command mode) still uses the original lr, look unchanged. */}
                <URow
                  label={t("sendDialog.field.needsReply")}
                  /* [2026-11-06] When side by side, **no note under the label**: the two cards must be structurally identical,
                     otherwise one card has an extra line of small text → the two dropdowns misalign vertically
                     (measured: the right card's dropdown is 18px lower than the left one). The note is moved to a
                     full-width footnote below this row (see the <p> below). */
                  hint={execParamSupported.needsConfirm ? undefined : silentHint}
                  layout={execParamSupported.needsConfirm ? "tb" : "lr"}
                  className="min-w-0 rounded-lg border bg-card/60 p-2 [&>div:last-child]:min-w-0"
                >
                  <Select
                    value={needsReply}
                    onValueChange={(v) => setNeedsReply(v as "0" | "1")}
                    disabled={!editable || !execParamSupported.needsReply}
                  >
                    <SelectTrigger className="min-w-0 [&>span]:truncate" data-testid="uib-needs-reply">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="max-w-[var(--radix-popper-available-width)]">
                      {/* [2026-10-19 P5] No "default (unspecified)" middle state: keep only either/or */}
                      <SelectItem value="1">{t("sendDialog.reply.need")}</SelectItem>
                      <SelectItem value="0">{t("sendDialog.reply.noNeed")}</SelectItem>
                    </SelectContent>
                  </Select>
                </URow>

                {/* Human confirmation: after finishing do not write succeeded, land in awaiting_confirm, counted as done only after
                     a human releases it. Default "not needed" — without this param behavior is exactly as before. */}
                {execParamSupported.needsConfirm && (
                  <URow
                    label={t("sendDialog.field.needsConfirm")}
                    layout="tb"
                    className="min-w-0 rounded-lg border bg-card/60 p-2 [&>div:last-child]:min-w-0"
                    title={t("sendDialog.confirm.title")}
                  >
                    <Select
                      value={needsConfirm}
                      onValueChange={(v) => setNeedsConfirm(v as "0" | "1")}
                      disabled={!editable}
                    >
                      <SelectTrigger className="min-w-0 [&>span]:truncate" data-testid="uib-needs-confirm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent className="max-w-[var(--radix-popper-available-width)]">
                        <SelectItem value="0">{t("sendDialog.confirm.no")}</SelectItem>
                        {/* Write the explanation into the options themselves, not into small text under the label — this way the two cards always have the same height */}
                        <SelectItem value="1">{t("sendDialog.confirm.yes")}</SelectItem>
                      </SelectContent>
                    </Select>
                  </URow>
                )}
              </div>

              {execParamSupported.needsConfirm && (
                <p className="px-0.5 text-[11px] leading-tight text-muted-foreground" data-testid="uib-confirm-note">
                  {t("sendDialog.confirm.note")}
                  {silentHint ? ` ${t("sendDialog.confirm.silent")}` : ""}
                </p>
              )}

              <URow label={t("sendDialog.field.timeout")} className="min-w-0 rounded-lg border bg-card/60 p-2 [&>div:last-child]:min-w-0">
                <Input
                  type="number"
                  inputMode="numeric"
                  min={0}
                  placeholder={t("sendDialog.ph.timeout")}
                  value={timeoutSec}
                  onChange={(e) => setTimeoutSec(e.target.value)}
                  disabled={!editable || !execParamSupported.timeout}
                  data-testid="uib-timeout"
                />
              </URow>

              <URow label={t("sendDialog.field.title")} className="min-w-0 rounded-lg border bg-card/60 p-2 [&>div:last-child]:min-w-0">
                <Input
                  placeholder={t("sendDialog.ph.execName")}
                  value={execName}
                  onChange={(e) => setExecName(e.target.value)}
                  disabled={!editable || !execParamSupported.execName}
                  data-testid="uib-exec-name"
                />
              </URow>
            </div>
            <p className="text-[11px] text-muted-foreground">
              {mode === "text"
                ? t("sendDialog.paramsNote.text")
                : mode === "quickcmd"
                  ? t("sendDialog.paramsNote.quickcmd")
                  : t("sendDialog.paramsNote.task")}
            </p>
          </section>
        )}

        {/* ════════ ⑥ Business Attributes (reserved this round: slots only) ════════ */}
        {!isHidden("business") && (
          <section className="flex min-h-0 flex-1 flex-col gap-1.5 rounded-md border-2 border-dashed border-muted-foreground/25 p-2 [@media(max-height:760px)]:hidden" data-testid="uib-layer-business">
            <LayerTitle
              n="⑥"
              title={t("sendDialog.layer.business")}
              hint={t("sendDialog.layer.businessHint")}
              accent="muted"
            />
            <div className="grid min-h-0 flex-1 grid-cols-1 content-start gap-1.5 opacity-60">
              <URow label={t("sendDialog.field.projectReserved")} className="min-w-0 rounded-lg border bg-card/60 p-2 [&>div:last-child]:min-w-0">
                <Input placeholder={t("sendDialog.ph.reserved")} disabled />
              </URow>
              <URow label={t("sendDialog.field.moduleReserved")} className="min-w-0 rounded-lg border bg-card/60 p-2 [&>div:last-child]:min-w-0">
                <Input placeholder={t("sendDialog.ph.reserved")} disabled />
              </URow>
            </div>
          </section>
        )}

        </div>
        </div>

        {/* ── Right column: body (takes full height independently, can send continuously, does not scroll with the left column) ── */}
        <div
          /* [2026-11-13 00:17 Feng] Widen the body/task instruction column: "Increase the width of this task instruction
             block, make the middle row (execution mode / execution params / business attributes) take a bit less
             space, a bit narrower" — the conversation will go into this column later, so leave enough at once.
             38% → 50%, min 340→420 (key controls fit), max 640→960 (only really wider on big screens).
             ★ The left column is still flex-1; making the right column wider = the left column auto-narrows,
               there is no need to write a separate width for the left column. */
          className="flex w-1/2 min-w-[420px] max-w-[960px] shrink-0 flex-col gap-2.5 border-l bg-muted/20 px-5 py-4"
          data-testid="uib-prompt-col"
        >
        {/* ════════ History conversation (read-only side path, not step ⑧) ════════
            [2026-11-14 11:08 Feng] "You can put the detailed conversation record under the message body of the
              message send window, so history exists, and you never have to temporarily keep it in memory again."
            [2026-11-14 12:33 Feng] **Moved above the message body**, and made it longer than the body:
              "Put the history conversation above the message body, history a bit longer, the message body can be
               shorter now."
            ★ It reads the **receive session**'s history (send to whom, see what they said before).
            ★ A new session has no key → the component provides its own "send the first one" empty state, and does
              not guess a session.
            ★ `sessionMode` is meaningless for text mode (text always lands in an existing session), so text mode
              looks at targetSession directly.
            ★ `fill` = give the height to the right column's remaining space (`flex-1`) — it is the star now, the
              body yields. */}
        <SessionHistory
          ref={histApiRef}
          fill
          sessionKey={mode === "text" ? targetSession : sessionMode === "existing" ? targetSession : ""}
          sessionName={targetSel?.name || ""}
        />

        {/* ════════ ⑦ Prompt Input Bar: what to say ════════ */}
        {/* [2026-11-14 12:33 Feng] "The message body can be shorter now" —
            `flex-1` → `shrink-0`: the height no longer grabs the remaining space, it shrinks to the content's own
            height (title row + input). The input's min-height 160 → 96 (about 4 lines, enough for a paragraph, no
            longer half the screen). */}
        <section className="flex shrink-0 flex-col gap-2.5 rounded-md border-2 border-primary/40 bg-card/40 p-3" data-testid="uib-layer-prompt">
          {/* [2026-11-12 15:19 Feng] The main button moves from the whole bottom bar to the top-right of the body box.
              "The send button row only takes the bottom-left corner, this whole bar is empty, utilization is low".
              → The button is on the same row as the title, right-aligned, the whole bottom bar is removed (the
                border-t goes with it), and the left and right columns each gain a whole bar of height.

              [2026-11-13 09:38 Feng] Then stuff the "sender / receiver" box into the middle of this row:
              "The message body title is at the far left, in the middle is this sender-receiver box, on the right is
                the send button; the send button's width is the same as this box's width. This way space
                utilization is high — right now the top is a blank area."
              ★ Implementation: the title `shrink-0`, the remaining width on the right goes to a `grid-cols-2` —
                the left cell holds the summary box, the right cell the button, two equal cells = the button width
                is naturally equal to the box width, without hardcoding any pixels (hardcoded values break again on
                a screen with another font size).
              ★ Benefit: the title row used to take only a small piece on the left with a big blank on the right;
                now this whole row is filled, and "who it goes to" and "send" are on the same visual row, so
                confirmation and action sit together.
              ★ The summary box becomes two compact lines (it used to expand to three), line height 16px,
                top/bottom padding 4px, and the whole row's height is evened out by the button's `h-auto` — equal
                height by stretching, not by guessing numbers. */}
          <div className="flex shrink-0 items-stretch gap-2">
          <div className="flex min-w-0 shrink-0 items-center">
          <LayerTitle
            n="⑦"
            title={
              mode === "text"
                ? t("sendDialog.prompt.text")
                : mode === "task"
                  ? t("sendDialog.prompt.task")
                  : selectedCmd && selectedCmd.params.length
                    ? t("sendDialog.prompt.quickWithCount", { n: selectedCmd.params.length })
                    : t("sendDialog.prompt.quick")
            }
            /* [2026-11-13 09:35 Feng] "Remove the text after the message body explaining the execution mode —
               you select a different execution mode and the input box already has the relevant explanation, it is
               redundant."
               ★ In the text/task two cases the placeholder itself says "type the message to send..." /
                 "type/edit the task instruction...", explaining it again next to the title is redundant.
               ★ Quick command **keeps** it: it has no input box, the placeholder cannot cover it;
                 "fill in by the param table (* is required)" is an operation hint, not a mode explanation — it
                 must not be deleted along with it. */
            hint={mode === "quickcmd" ? t("sendDialog.prompt.quickHint") : undefined}
          accent="primary" />
          </div>

          {/* [2026-11-13 09:39 Feng] "The send button should not be this long, same width as before;
               make that sender-receiver in the middle longer — the session id after it could be very long."
               ★ So no longer use `grid-cols-2` equal split: the summary box `flex-1` (eats all remaining width),
                 the button `shrink-0` back to its original width (icon + two chars, automatic width, no hardcoded
                 pixels).
               ★ Both still `items-stretch` to equalize height, visually still one row. */}
          <div className="flex min-w-0 flex-1 items-stretch gap-2">
          {/* ── Middle: sender / receiver ──
              ★ Color is only for distinguishing roles, not for state: sender=cyan(info), receiver=green(success).
                When the receiver is not selected, do not use a big red background (too scary), just write
                "unselected" in red text.
              ★ Keep only the last session segment (main / run_xxx / case_xxx) — the name and type are "notes",
                and in this 180px-wide spot they would crowd out the Agent name. */}
          <div
            /* ★ `flex-1`: the summary box eats all width except the title and the button.
               Without it, it only grows to content width (354px), leaving a blank at the right end — "a blank
               area on top" again. `py-0.5` + two leading-4 lines = 36px, same height as the button's h-9, only
               then does the row look aligned. */
            className="flex min-w-0 flex-1 flex-col justify-center gap-0.5 rounded-md border bg-card/60 px-2 py-0.5"
            data-testid="uib-send-summary"
          >
            <div className="flex min-w-0 items-center gap-1" data-testid="uib-sum-from">
              <Badge variant="info" className="shrink-0 rounded px-1 py-0 text-[10px] font-normal leading-4">{t("sendDialog.summary.from")}</Badge>
              <span
                /* [2026-11-16 12:45] Originally only the receiver's "unselected" was red, the sender was grey —
                   yet a missing sender is exactly the most common reason for "the button is grey and I do not know
                   why" (when the top entry opens, the send subject is empty to begin with). The same kind of
                   missing must be the same color. */
                className={`min-w-0 truncate text-[11px] font-medium ${sumFromAgent ? "" : "text-destructive"}`}
                title={sumFromAgent || undefined}
                data-testid="uib-sum-from-agent"
              >{sumFromAgent || t("sendDialog.summary.none")}</span>
              <span className="shrink-0 text-[10px] text-muted-foreground">·</span>
              <span
                className="min-w-0 truncate font-mono text-[10px] text-muted-foreground"
                title={sumFromSession}
                data-testid="uib-sum-from-session"
              >{sumFromSession}</span>
              {/* [2026-11-13 23:29 Feng] The send session, when running, must also be marked (same convention as the receiver) */}
              <RunningMark
                show={senderRtRunning}
                runId={senderRtRunId}
                testId="uib-sender-running"
                className="ml-auto"
              />
            </div>
            <div className="flex min-w-0 items-center gap-1" data-testid="uib-sum-to">
              <Badge variant="success" className="shrink-0 rounded px-1 py-0 text-[10px] font-normal leading-4">{t("sendDialog.summary.to")}</Badge>
              <span
                className={`min-w-0 truncate text-[11px] font-medium ${sumToAgent ? "" : "text-destructive"}`}
                title={sumToAgent || undefined}
                data-testid="uib-sum-to-agent"
              >{sumToAgent || t("sendDialog.summary.none")}</span>
              <span className="shrink-0 text-[10px] text-muted-foreground">·</span>
              <span
                className="min-w-0 truncate font-mono text-[10px] text-muted-foreground"
                title={sumToSession}
                data-testid="uib-sum-to-session"
              >{sumToSession}</span>
              {/* [2026-11-13 21:39 Feng] Receiver session running → give a green mark on this row.
                  Why in the summary bar rather than next to the button: this is the "who it goes to" spot, people
                  look here first to confirm the landing point; putting state and landing point together is the
                  only way to see "it is this session that is busy".
                  ★ `ml-auto` pushes it to the row end: the session id on the left may be long, the mark must not
                    be crowded out. */}
              <RunningMark
                show={rtRunning}
                runId={rtRunId}
                testId="uib-receiver-running"
                className="ml-auto"
              />
            </div>
          </div>

          {/* [2026-11-12 15:19 Feng] The main button has moved here from the bottom bar (top-right of the body box).
              ★ Remaining copy logic untouched: the submitting / problem thresholds are exactly the same as the
                original bottom button, only the position changed — changing position should not change behavior.
              [2026-11-13 09:39] Width back to automatic (icon + two chars),
              height `self-stretch` aligned with the summary box on the left, the three on the same horizontal row. */}
          <Button
            onClick={handleSubmit}
            /* [2026-11-13 21:39] Sending used to be locked by "receiver session running";
               [2026-11-13 23:47 Feng] "The state is wrong... cancel it first, otherwise it gets in the way"
               → cancel the lock: state only displays, does not block sending. */
            disabled={submitting || !!problem}
            /* [2026-11-16 12:45] When greyed out it must be possible to ask why: hover gives the full reason,
               no need to go find it in the small text below (that spot gets pushed out of view when the param
               table is long). */
            title={problem || undefined}
            data-testid="uib-submit"
            /* `self-center` instead of `self-stretch`: the button is fixed height h-9, stretching does not take effect
               (h-9 wins), and it would stick to the top edge, looking half a head misaligned with the left border.
               Centering is simplest and steadiest. */
            className="shrink-0 self-center"
          >
            {submitting ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Send className="mr-1.5 h-4 w-4" />}
            {/* [2026-11-16 12:45 Feng] "First, change 'dispatch' to 'send', stop having two names for this"
                ★ The same action in one window should have only one name. It used to be "send" for text and
                  "dispatch" for Task/quick command — the user sees two buttons, actually the same submit action,
                  only the mode differs. Unify the verb to "send", the mode difference is explained by ② execution
                  mode above, not by the button name. */}
            {submitting ? t("sendDialog.submit.busy") : t("sendDialog.submit.idle")}
          </Button>
          </div>
          </div>

          {mode !== "quickcmd" && !isHidden("text") && (
            <URow
              label={mode === "text" ? t("sendDialog.input.labelContent") : t("sendDialog.input.labelInstruction")}
              layout="tb"
              hideLabel
              className="min-h-0 flex-1"
            >
              {/* The voice button floats at the bottom-right of the input box (the WeChat spot), does not occupy a body line —
                  it is a "use any time" auxiliary action, and should not squeeze the writing area smaller. */}
              <div className="relative flex min-h-0 flex-1 flex-col">
                <Textarea
                  ref={textRef}
                  value={text}
                  onChange={(e) => {
                    setText(e.target.value);
                    /* Typing moves the cursor, record it in passing (onSelect is not continuously triggered in some input methods) */
                    caretRef.current = { start: e.target.selectionStart, end: e.target.selectionEnd };
                  }}
                  /* Mouse click / arrow keys / selection change — persist whenever the caret moves */
                  onSelect={rememberCaret}
                  onClick={rememberCaret}
                  onKeyUp={rememberCaret}
                  onBlur={rememberCaret}
                  disabled={!editable}
                  rows={3}
                  /* `pr-9`: reserve room for the "expand" button in the top-right corner, otherwise the text
   slides under the button. */
                  className="min-h-[88px] max-h-[120px] flex-1 resize-none pb-11 pr-9"
                  placeholder={
                    mode === "text" ? t("sendDialog.input.phContent") : t("sendDialog.input.phInstruction")
                  }
                  data-testid="uib-text"
                />
                {/* [2026-11-17 10:07 Feng] Expanded editing — placed in the **top-right corner of the input** (the spot he named).
                    Why here: the bottom-right corner is already taken by "phrases" and "voice", both of which "fill text in";
                    expanding is "view/write the same content elsewhere", a container-level action, least in the way at a corner.
                    `pointer-events-auto`: the outer layer is a floating layer with pointer-events-none, the button must re-enable itself. */}
                <div className="absolute right-1.5 top-1.5">
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => setTextZoom(true)}
                    title={t("sendDialog.input.zoomTitle")}
                    data-testid="uib-text-zoom"
                    className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
                  >
                    <Maximize2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
                <div className="pointer-events-none absolute inset-x-2 bottom-1.5 flex flex-wrap items-center justify-end gap-2">
                  {/* [2026-11-17 10:19 Feng] "Take all the buttons below along" —
                      the main box and the expanded window share one TextToolsBar (see the component comment). */}
                  <TextToolsBar
                    scope="main"
                    editable={editable}
                    senderAgent={senderAgent}
                    phrases={phrases}
                    phrasesLoading={phrasesLoading}
                    phraseOwn={phraseOwn}
                    phraseShared={phraseShared}
                    open={phraseOpen}
                    onOpenChange={setPhraseOpen}
                    onPick={(c) => insertAtCaret(c.composedInstruction || "")}
                    onAddPhrase={() => setPhraseFormOpen(true)}
                    onVoice={insertAtCaret}
                    onVoicePhase={(p) => setVoicePhase((prev) => ({ ...prev, main: p }))}
                  />
                </div>
              </div>
            </URow>
          )}

          {mode === "quickcmd" && (
            <>
              {cmdsLoading && !selectedCmd ? (
                <div className="rounded-md border border-dashed p-4 text-center text-xs text-muted-foreground">
                  {t("sendDialog.quickPanel.loadingCmds")}
                </div>
              ) : !selectedCmd ? (
                <div className="rounded-md border border-dashed p-4 text-center text-xs text-muted-foreground">
                  {t("sendDialog.quickPanel.pickFirst")}
                </div>
              ) : selectedCmd.params.length === 0 ? (
                <div className="rounded-md border border-dashed p-4 text-center text-xs text-muted-foreground">
                  {t("sendDialog.quickPanel.noParams")}
                </div>
              ) : (
                <div className="flex flex-col gap-3">
                  {selectedCmd.params.map((p) => (
                    <URow key={p.name} label={p.label || p.name} required={p.required}>
                      <ParamField
                        param={p}
                        value={params[p.name]}
                        onChange={(v) => setParams((s) => ({ ...s, [p.name]: v }))}
                        disabled={!editable}
                        allValues={params}
                      />
                    </URow>
                  ))}
                </div>
              )}
              {selectedCmd && (
                <div className="flex flex-col gap-1">
                  <div className="text-xs font-medium text-muted-foreground">
                    {t("sendDialog.quickPanel.previewTitle")}
                  </div>
                  <pre
                    className="max-h-36 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted/40 p-3 font-mono text-xs leading-relaxed"
                    data-testid="uib-preview"
                  >
                    {preview || t("sendDialog.quickPanel.previewEmpty")}
                  </pre>
                </div>
              )}
            </>
          )}

          {/* [2026-11-13 00:23 Feng] Drop the "please enter message body" line —
              "remove the input-message-body line at the bottom, it is obvious at a glance, too much explanation is worse".
              ★ Remove the **display** only, not the **disabled** state: the button stays grey when the text is empty (disabled still reads the problem),
                otherwise people click and nothing happens with no hint, which is the real confusion.
                Filter it out with a separate variable rather than deleting the check — the check is behaviour, the copy is explanation, two different things. */}
          {/* [2026-11-16 12:45 Feng] "The send button stays locked" — grey button plus small grey text = you cannot see the problem.
              Switched to the same colour as errors (destructive): this line explains **why it cannot be sent**, it is not a side note.
              `sticky bottom-0`: with many parameters it sticks to the visible bottom instead of scrolling out of view with the parameter table. */}
          {problemHint && (
            <p
              className="sticky bottom-0 z-10 flex shrink-0 items-center gap-1 rounded-md border border-destructive/30 bg-destructive/5 px-2 py-1 text-xs font-medium text-destructive"
              data-testid="uib-blocked-reason"
            >
              <Info className="h-3.5 w-3.5 shrink-0" />{problemHint}
            </p>
          )}
        </section>

        {/* [2026-11-14 12:33 Feng] The history block has moved above ⑦ (see the same-named comment above) —
            it is no longer rendered here; the comment is kept as a note so nobody "adds it back" later. */}

        {error && (
          <p className="flex items-start gap-1 text-sm text-destructive" data-testid="uib-error">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{error}
          </p>
        )}
        </div>
        </div>

        {/* [2026-11-12 15:19 Feng] The bottom action bar has been removed entirely.
            His words: "the cancel button is useless, it does the same as the X above, remove it"
                 "move the send button to the top-right of the body box" "this line underneath can go".
            Cancel button → same meaning as the top-right ×, removed; primary button → moved into the title row of box ⑦.
            ★ With this whole strip (including border-t) gone, the left and right columns each gain roughly one button's height. */}
        </PortalContainerProvider>
      </DialogContent>

      {/* [2026-11-17 10:07 Feng] Expanded editing window — shares **the same `text`** as the main input (see the textZoom comment).
         ★ Size: `min(96vw,1180px)` plus a body area of `min-h-[58vh]`. Why not full screen:
            when writing a letter or a long instruction you still need the sender/recipient rows on screen to confirm the target,
            full screen squeezes that context out; this size shows about 28 lines at a glance without losing the reference.
         ★ Three ways to close, all fine: the top-right × (the only one on the site) / "Done" / Esc —
            no "cancel" was added because the content syncs live and rolling back is meaningless (exactly the "stop losing content" Feng asked for). */}
      <Dialog open={textZoom} onOpenChange={setTextZoom}>
        <DialogContent
          className="w-[min(96vw,1180px)] max-w-none gap-0 overflow-hidden p-0"
          aria-describedby={undefined}
          data-testid="uib-text-zoom-dialog"
        >
          <DialogHeader className="flex h-12 shrink-0 flex-row items-center gap-2 space-y-0 border-b bg-muted px-4 pr-12">
            <DialogTitle className="text-sm font-medium">
              {t("sendDialog.zoom.title", {
                mode: mode === "text" ? t("sendDialog.prompt.text") : t("sendDialog.prompt.task"),
              })}
            </DialogTitle>
            <span
              className="ml-auto shrink-0 font-mono text-[11px] text-muted-foreground"
              data-testid="uib-text-zoom-count"
            >
              {t("sendDialog.zoom.count", { n: text.length, lines: text.split("\n").length })}
            </span>
          </DialogHeader>
          {/* "Phrases / voice" moved down into the toolbar under the input (same component as the main box),
              so the body no longer needs bottom padding for floating buttons — it gains a full line of height. */}
          <div className="p-4">
            <Textarea
              ref={zoomTextRef}
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                zoomCaretRef.current = { start: e.target.selectionStart, end: e.target.selectionEnd };
              }}
              onSelect={rememberZoomCaret}
              onClick={rememberZoomCaret}
              onKeyUp={rememberZoomCaret}
              onBlur={rememberZoomCaret}
              disabled={!editable}
              className="min-h-[54vh] resize-none font-sans text-sm leading-relaxed"
              placeholder={
                mode === "text" ? t("sendDialog.input.phContent") : t("sendDialog.input.phInstruction")
              }
              data-testid="uib-text-zoom-area"
              autoFocus
            />
          </div>
          {/* [2026-11-17 10:19 Feng] "Take these shortcut buttons along, phrases and voice,
              take all the buttons below" — **the same component** as the main box, not a second copy drawn to look alike.
              Insertion goes through insertAtCaretZoom: it inserts at this window's caret, not the main box's. */}
          <div className="flex items-center gap-1 border-t px-4 py-2">
            <span className="mr-auto text-[11px] text-muted-foreground">
              {t("sendDialog.zoom.sync")}
            </span>
            <TextToolsBar
              scope="zoom"
              editable={editable}
              senderAgent={senderAgent}
              phrases={phrases}
              phrasesLoading={phrasesLoading}
              phraseOwn={phraseOwn}
              phraseShared={phraseShared}
              open={zoomPhraseOpen}
              onOpenChange={setZoomPhraseOpen}
              onPick={(c) => insertAtCaretZoom(c.composedInstruction || "")}
              onAddPhrase={() => setPhraseFormOpen(true)}
              onVoice={insertAtCaretZoom}
              onVoicePhase={(p) => setVoicePhase((prev) => ({ ...prev, zoom: p }))}
            />
            <Button
              type="button"
              size="sm"
              className="ml-2"
              onClick={() => setTextZoom(false)}
              data-testid="uib-text-zoom-done"
            >
              {t("sendDialog.zoom.done")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* [2026-11-14 Feng] Create a phrase in place (without closing the dialog or leaving the page) —
          shares the same form as the management page; prefilled with "text snippet + current sender". */}
      <QuickCmdForm
        open={phraseFormOpen}
        initial={null}
        defaults={{
          sendMode: "text",
          ownerAgent: senderAgent,
          isPublic: 0,
          category: "other",
        }}
        onOpenChange={setPhraseFormOpen}
        onSaved={(saved) => {
          const body = saved?.composedInstruction || "";
          if (body) insertAtCaret(body);
          void loadPhrases();
        }}
      />
    </Dialog>
  );
}

export default MessageSendDialog;
