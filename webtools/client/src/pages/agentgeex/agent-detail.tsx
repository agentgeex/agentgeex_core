/**
 * Agent management · agent detail (/agents/:id)
 * Per agentgeex_ui.md §2.3:
 *   - Master-Detail: 5 streaming cards at the top (tasks / executions / assistants / sessions / quick commands)
 *   - The workspace below switches dynamically:
 *       view 1 task/execution workspace (left: instruction config; right: status + description + live output; toolbar at bottom)
 *       view 2 session workspace (left: session list; middle: that session's executions (newest first); right: selected detail)
 *       view 3 assistant workspace (left: assistant tiles (icon + name + creator); middle: executions; right: detail)
 *       view 4 quick command workspace (filtered by the current agent)
 *
 * [2026-10-16] Assistant = a case session with is_assistant=1 (same agx_session table) —
 *   so 'assistant detail' and 'session detail' are isomorphic; both reuse ExecPane (middle: execution list / right: detail).
 *
 * ⚠️ Quick command cards are shown on this page — filtered by the current agent (Feng, 2026-10-14).
 */
import * as React from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft, ListChecks, ReceiptText, MessageSquare, Zap, Play,
  Bot, Loader2, CheckCircle2, XCircle, MessageCircle, Plus, RefreshCw, Sparkles, Send, Layers,
  Globe, Lock, Info, Pencil,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { StatusPill } from "@/components/agentgeex/data-table";
import { SessionStatusDot } from "@/components/agentgeex/session-runtime-badge";
import { PlatformTag } from "@/components/agentgeex/platform-logo";
import { AgentAvatar } from "@/components/agentgeex/agent-avatar";
import { chatDeepLink } from "@/lib/openclaw";
import { OpenClawSessionLink } from "@/components/agentgeex/openclaw-session-link";
import { DispatchExemptPanel } from "@/components/agentgeex/dispatch-exempt-panel";
import { refreshStoreOnce } from "@/lib/fresh-data";
import { getAgentBundle, refreshPageData, type AgentBundle, fetchQuickCmdByTask, fetchQuickCommands, listAgents, listAgentOptions, agentStateLabel } from "@/providers/agentgeex";
import { agentsApi, type AgentProfile, type RunTaskResult } from "@/providers/api";
import type { QuickCmd } from "@/providers/api";
import { QuickCmdSendDialog } from "@/components/agentgeex/quick-cmd-send-dialog";
import { SessionSendDialog } from "@/components/agentgeex/session-send-dialog";
import { MessageSendDialog } from "@/components/agentgeex/message-send-dialog";
import { AgentEditDialog } from "@/components/agentgeex/agent-edit-dialog";
import { RichTextView } from "@/components/ui/rich-text-view";
import type { ExecutionRow, SessionRow, TaskRow } from "@/mock/agentgeex";
import { sessionsOf } from "@/mock/agentgeex";
import { cn } from "@/lib/utils";
import { useDetailCrumb } from "@/lib/breadcrumb";
import { useDetailEmbed } from "@/components/agentgeex/detail-popup";
import { apiErrorText } from "@/lib/api-error";

type TopKey = "tasks" | "executions" | "assistants" | "sessions" | "commands";

function fmtDur(s: number) {
  if (!s) return "—";
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m${s % 60 ? `${s % 60}s` : ""}`;
}

/* ---------------- Top metric cards ---------------- */
/** The five top cards are color-coded by category (Feng 2026-10-19 13:15): each has its own border + background set.
 *  Design tokens only; unselected = light background, selected = deeper plus an inner stroke. */
type TopAccent = "info" | "violet" | "pink" | "success" | "orange";

const TOP_ACCENT: Record<TopAccent, { on: string; off: string; title: string; ws: string; panel: string; item: string; itemActive: string }> = {
  info:    { on: "border-info/60 bg-info/10 ring-1 ring-inset ring-info/30",         off: "border-info/25 bg-info/5 hover:border-info/50",             title: "text-info",    ws: "border-info/60", item: "border-info/35 hover:ring-2 hover:ring-inset hover:ring-info/50",             itemActive: "border-info/60 ring-2 ring-inset ring-info/70", panel: "border-info/35" },
  violet:  { on: "border-chart-1/60 bg-chart-1/10 ring-1 ring-inset ring-chart-1/30", off: "border-chart-1/25 bg-chart-1/5 hover:border-chart-1/50",   title: "text-chart-1", ws: "border-chart-1/60", item: "border-chart-1/35 hover:ring-2 hover:ring-inset hover:ring-chart-1/50",       itemActive: "border-chart-1/60 ring-2 ring-inset ring-chart-1/70", panel: "border-chart-1/35" },
  pink:    { on: "border-chart-4/60 bg-chart-4/10 ring-1 ring-inset ring-chart-4/30", off: "border-chart-4/25 bg-chart-4/5 hover:border-chart-4/50",   title: "text-chart-4", ws: "border-chart-4/60", item: "border-chart-4/35 hover:ring-2 hover:ring-inset hover:ring-chart-4/50",       itemActive: "border-chart-4/60 ring-2 ring-inset ring-chart-4/70", panel: "border-chart-4/35" },
  success: { on: "border-success/60 bg-success/10 ring-1 ring-inset ring-success/30", off: "border-success/25 bg-success/5 hover:border-success/50",   title: "text-success", ws: "border-success/60", item: "border-success/35 hover:ring-2 hover:ring-inset hover:ring-success/50",       itemActive: "border-success/60 ring-2 ring-inset ring-success/70", panel: "border-success/35" },
  orange:  { on: "border-chart-2/60 bg-chart-2/10 ring-1 ring-inset ring-chart-2/30", off: "border-chart-2/25 bg-chart-2/5 hover:border-chart-2/50",   title: "text-chart-2", ws: "border-chart-2/60", item: "border-chart-2/35 hover:ring-2 hover:ring-inset hover:ring-chart-2/50",       itemActive: "border-chart-2/60 ring-2 ring-inset ring-chart-2/70", panel: "border-chart-2/40" },
};

/** In-block 'child item' color palette (Feng 2026-10-19 16:39):
 *  If a block holds several cards, cycle the hue card by card — so it is obvious at a glance that
 *  'these are two different things', instead of one same-color lump. Colors cycle in list order
 *  (stable order → stable colors).
 *  Note: the task area keeps the 'single layer color' rule (confirmed by Feng); this palette is only for
 *  executions/assistants/quick commands. */
const ITEM_TONE = [
  { border: "border-chart-1/45",    bg: "bg-chart-1/5",    ring: "ring-chart-1/60" },    // purple
  { border: "border-chart-2/50",    bg: "bg-chart-2/5",    ring: "ring-chart-2/60" },    // orange
  { border: "border-chart-3/45",    bg: "bg-chart-3/5",    ring: "ring-chart-3/60" },    // cyan
  { border: "border-chart-4/45",    bg: "bg-chart-4/5",    ring: "ring-chart-4/60" },    // pink
  { border: "border-chart-5/45",    bg: "bg-chart-5/5",    ring: "ring-chart-5/60" },    // blue
  { border: "border-chart-6/45",    bg: "bg-chart-6/5",    ring: "ring-chart-6/60" },    // teal
  { border: "border-success/45",    bg: "bg-success/5",    ring: "ring-success/60" },    // green
  { border: "border-destructive/40", bg: "bg-destructive/5", ring: "ring-destructive/55" }, // red
];
const toneOf = (i: number) => ITEM_TONE[((i % ITEM_TONE.length) + ITEM_TONE.length) % ITEM_TONE.length];

const TOP_ACCENT_OF: Record<TopKey, TopAccent> = {
  tasks: "info",
  executions: "violet",
  assistants: "pink",
  sessions: "success",
  commands: "orange",
};

function TopCard({
  k, active, onClick, icon, title, count, hint,
}: {
  k: TopKey; active: boolean; onClick: () => void;
  icon: React.ReactNode; title: string; count: React.ReactNode; hint: string;
}) {
  const a = TOP_ACCENT[TOP_ACCENT_OF[k]];
  return (
    <button
      onClick={onClick}
      data-testid={`top-card-${k}`}
      className={cn(
        "flex-1 rounded-lg border p-4 text-left transition-all",
        active ? a.on : a.off,
      )}
    >
      <div className={cn("flex items-center gap-2 text-xs font-medium", a.title)}>
        {icon}{title}
      </div>
      <div className="mt-1 text-2xl font-semibold">{count}</div>
      <div className="mt-0.5 text-[11px] text-muted-foreground/80">{hint}</div>
    </button>
  );
}

/* ---------------- View 1: task / execution workspace (Master-Detail) ----------------
 * 2026-10-07 Feng's request:
 *   left/top = this agent's execution list (startedAt desc), each row shows exec name + status + time + duration;
 *   right/bottom = detail of the selected one (instruction / status / session / duration / summary / run_uuid),
 *   keeping the existing 'task instruction / Prompt config' editor + live output area + bottom Toolbar.
 * ------------------------------------------------------------------------ */

/** Execution name fallback: execName → taskName → run_uuid → unnamed */
function execTitle(e: ExecutionRow, t: (key: string) => string): string {
  return e.execName || e.taskName || e.run_uuid || t("agentDetail.exec.unnamed");
}

function TaskExecutionWorkspace({ executions, selected, onSelect, frame = "" }: {
  executions: ExecutionRow[];
  selected?: ExecutionRow;
  onSelect: (e: ExecutionRow) => void;
  /** Outer frame color (follows the theme color of the containing block) */
  frame?: string;
}) {
  const { t } = useTranslation();
  // [2026-10-11] After removing the live output log, the log state/timer is no longer needed.

  if (!executions.length) {
    return <div className="p-8 text-sm text-muted-foreground" data-testid="workspace-exec-empty">{t("agentDetail.exec.empty")}</div>;
  }

  return (
    <div className="flex flex-col gap-3" data-testid="workspace-exec">
      <div className="grid gap-3 lg:grid-cols-[300px_1fr]">
        {/* Left: execution list (startedAt desc)
            [2026-10-19 16:39 Feng] The big frame follows the block color; each record cycles its own color, the selected one is bold. */}
        <div className={cn("flex flex-col gap-2 rounded-lg border p-3", frame)}>
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-muted-foreground">{t("agentDetail.exec.list")}</span>
            <span className="text-[11px] text-muted-foreground">{t("agentDetail.exec.count", { n: executions.length })}</span>
          </div>
          <div className="max-h-[520px] flex flex-col gap-1 overflow-auto" data-testid="exec-list">
            {executions.map((e, i) => {
              const tone = toneOf(i);
              const active = selected?.run_uuid === e.run_uuid;
              return (
              <button
                key={e.run_uuid}
                onClick={() => onSelect(e)}
                data-testid={`exec-item-${e.run_uuid}`}
                data-active={active ? "1" : "0"}
                className={cn(
                  "block w-full rounded-md border px-3 py-2 text-left text-xs transition-all",
                  tone.border,
                  active ? cn("ring-2 ring-inset", tone.ring, tone.bg) : cn(tone.bg, "hover:ring-1 hover:ring-inset"),
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium">{execTitle(e, t)}</span>
                  <StatusPill status={e.status} />
                </div>
                <div className="mt-0.5 flex items-center gap-2 text-muted-foreground">
                  <span>{e.startedAt || "—"}</span>
                  <span>·</span>
                  <span>{fmtDur(e.durationSeconds)}</span>
                </div>
              </button>
              );
            })}
          </div>
        </div>

        {/* Right: selected execution detail (instruction / status / description / summary)
            [2026-10-11] Removed the 'update instruction' button (an execution that already ran cannot change its instruction);
            removed the 'live execution output/log' black box; kept 'execution result/Summary'.
            [2026-10-11 second pass] Unified into RichTextView: bordered + inner scrolling + Markdown rendering +
            an 'expand' button left of the title that opens a dialog. Fixed the 'one has a border, one does not' inconsistency. */}
        {selected ? (
          <div className="flex flex-col gap-3">
            <div className="grid flex-1 gap-3 lg:grid-cols-2">
              {/* Top left: task instruction / Prompt config */}
              <RichTextView
                title={t("agentDetail.exec.instruction")}
                value={selected.instruction}
                bodyClassName="h-60"
                testId="exec-instruction"
              />

              {/* Top right: task detailed description (same source as the instruction; includes status/metadata) */}
              <div className={cn("flex flex-col gap-2 rounded-lg border p-3", frame)}>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold" data-testid="exec-title">{execTitle(selected, t)}</span>
                  <StatusPill status={selected.status} />
                </div>
                <RichTextView
                  title={t("agentDetail.exec.desc")}
                  value={selected.instruction}
                  bodyClassName="h-40"
                  testId="exec-desc"
                />
                <Separator />
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                  <span>{t("agentDetail.exec.operator")}{selected.agentName}</span>
                  <span className="inline-flex items-center gap-1">
                    {t("agentDetail.exec.session")}{selected.sessionId ? selected.sessionId.split(":").pop() : "—"}
                    <OpenClawSessionLink
                      sessionId={selected.sessionId}
                      agentId={selected.agentId}
                    />
                  </span>
                  <span>{t("agentDetail.exec.duration")}{fmtDur(selected.durationSeconds)}</span>
                </div>
              </div>
            </div>

            {/* Bottom: execution result summary (kept) */}
            <RichTextView
              title={t("agentDetail.exec.summary")}
              value={selected.summary}
              bodyClassName="h-48"
              testId="exec-summary"
              emptyText={t("agentDetail.exec.summaryEmpty")}
            />
          </div>
        ) : (
          <div className={cn("grid place-items-center rounded-lg border p-8 text-sm text-muted-foreground", frame)}>
            {t("agentDetail.exec.pickOne")}
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------- View 2: session workspace ---------------- */
/** Executions sorted by startedAt desc (newest first) */
function byStartedDesc(a: ExecutionRow, b: ExecutionRow) {
  return (b.startedAt || "").localeCompare(a.startedAt || "");
}
/** Take the most recent execution (null when the list is empty) */
function latestExec(rows: ExecutionRow[]): ExecutionRow | null {
  if (!rows.length) return null;
  return [...rows].sort(byStartedDesc)[0];
}

/* ---------------- View 2: shared panel — executions (middle) + detail (right) ----------------
 * [2026-10-16 Feng's request] Session/assistant detail unified as 'left list → middle executions (time-sorted) → right detail'.
 *   This component = the middle + right columns, reused by both the 'session' and 'assistant' workspaces.
 * ---------------------------------------------------------------------- */
function ExecPane({
  executions, selected, onSelect, emptyText,
  onSend, sendTestId = "exec-send-btn", frame = "",
}: {
  executions: ExecutionRow[]; selected: ExecutionRow | null;
  onSelect: (e: ExecutionRow) => void; emptyText?: string;
  /** Outer frame color (follows the theme color of the containing block) */
  frame?: string;
  /** When passed, renders a 'send message' button at the right end of the 'executions' title row (send a message to the current session/assistant) */
  onSend?: () => void; sendTestId?: string;
}) {
  const { t } = useTranslation();
  const list = React.useMemo(() => [...executions].sort(byStartedDesc), [executions]);
  return (
    <div className="grid min-h-0 gap-3 lg:h-full lg:grid-cols-[minmax(0,280px)_1fr]" data-testid="workspace-exec-pane">
      {/* Middle: that session's executions (time desc, inner scroll)
          [2026-10-19 16:39 Feng] The big frame follows the block color; each record cycles its own color, the selected one is bold. */}
      <div className={cn("flex min-h-0 flex-col gap-2 rounded-lg border p-3", frame)}>
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-semibold text-muted-foreground">{t("agentDetail.exec.paneTitle")}</span>
          <div className="flex shrink-0 items-center gap-2">
            <span className="text-[11px] text-muted-foreground/70">{t("agentDetail.exec.paneMeta", { n: list.length })}</span>
            {onSend && (
              <Button
                size="sm"
                variant="outline"
                className="h-7 px-2 text-xs"
                onClick={onSend}
                data-testid={sendTestId}
              >
                <Send className="mr-1 h-3.5 w-3.5" />{t("agentDetail.exec.send")}
              </Button>
            )}
          </div>
        </div>
        <div className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto">
          {list.map((e, i) => {
            const tone = toneOf(i);
            const active = selected?.run_uuid === e.run_uuid;
            return (
            <button
              key={e.run_uuid}
              onClick={() => onSelect(e)}
              data-testid={`exec-item-${e.run_uuid}`}
              data-active={active ? "1" : "0"}
              className={cn(
                "block w-full rounded-md border px-3 py-2 text-left text-xs transition-all",
                tone.border,
                active ? cn("ring-2 ring-inset", tone.ring, tone.bg) : cn(tone.bg, "hover:ring-1 hover:ring-inset"),
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium">{execTitle(e, t)}</span>
                <StatusPill status={e.status} />
              </div>
              <div className="mt-0.5 text-muted-foreground">{e.startedAt} · {fmtDur(e.durationSeconds)}</div>
            </button>
            );
          })}
          {list.length === 0 && <div className="text-xs text-muted-foreground">{emptyText}</div>}
        </div>
      </div>

      {/* Right: detail of the selected execution ([2026-10-11] unified as RichTextView; inner scroll) */}
      <div className={cn("flex min-h-0 flex-col gap-2 overflow-y-auto rounded-lg border p-3", frame)} data-testid="exec-detail">
        {selected ? (
          <>
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-sm font-semibold" data-testid="exec-detail-title">{execTitle(selected, t)}</span>
              <StatusPill status={selected.status} />
            </div>
            <div className="font-mono text-[11px] text-muted-foreground" data-testid="exec-detail-meta">
              {selected.run_uuid} · {selected.startedAt} · {fmtDur(selected.durationSeconds)}
            </div>
            <RichTextView
              title={t("agentDetail.exec.descTitle")}
              value={selected.instruction}
              bodyClassName="h-52"
              testId="exec-instruction-view"
            />
            <RichTextView
              title={t("agentDetail.exec.resultTitle")}
              value={selected.summary}
              bodyClassName="h-52"
              testId="exec-result"
              emptyText={t("agentDetail.exec.resultEmpty")}
            />
          </>
        ) : (
          <div className="p-6 text-sm text-muted-foreground">{t("agentDetail.exec.pickFromList")}</div>
        )}
      </div>
    </div>
  );
}

/* ---------------- View 2: session workspace (left: session list → middle: executions → right: detail) ---------------- */
function SessionWorkspace({ bundle, frame = "" }: { bundle: AgentBundle; frame?: string }) {
  const { t } = useTranslation();
  const [selected, setSelected] = React.useState<SessionRow | null>(bundle.sessions[0] ?? null);
  const [sendOpen, setSendOpen] = React.useState(false);
  const execsOfSession = (sid: string) => bundle.executions.filter((e) => e.sessionId === sid);
  const [selectedExec, setSelectedExec] = React.useState<ExecutionRow | null>(
    bundle.sessions[0] ? latestExec(execsOfSession(bundle.sessions[0].id)) : null
  );

  const pickSession = (s: SessionRow) => {
    setSelected(s);
    setSelectedExec(latestExec(execsOfSession(s.id)));
  };

  if (!bundle.sessions.length) return <div className="p-8 text-sm text-muted-foreground">{t("agentDetail.session.empty")}</div>;

  return (
    <div className="grid min-h-0 gap-3 lg:h-full lg:grid-cols-[minmax(0,300px)_1fr]" data-testid="workspace-session">
      {/* Left: session list */}
      <div className="flex min-h-0 flex-col gap-2 rounded-lg border p-3">
        <div className="text-xs font-semibold text-muted-foreground">{t("agentDetail.session.label")}</div>
        <div className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto">
          {bundle.sessions.map((s) => (
            <button
              key={s.id}
              onClick={() => pickSession(s)}
              data-testid={`session-item-${s.id}`}
              className={cn(
                "flex w-full items-center justify-between gap-2 rounded-md border px-3 py-2 text-left text-sm",
                selected?.id === s.id ? "border-primary/40 bg-primary/5" : "hover:bg-accent"
              )}
            >
              <span className="truncate">{s.name}</span>
              {/* 2026-11-05 17:08: the session 'registration status' was removed site-wide, replaced by runtime status (Redis fact).
                  The session list / session detail were already switched; this is the session panel inside agent detail — the same thing must not have two conventions. */}
              <SessionStatusDot runtime={s.runtime} />
            </button>
          ))}
        </div>
      </div>

      {/* Middle + right: that session's executions + selected detail (the title row has the 'send message' entry) */}
      <ExecPane
        frame={frame}
        executions={selected ? execsOfSession(selected.id) : []}
        selected={selectedExec}
        onSelect={setSelectedExec}
        onSend={selected ? () => setSendOpen(true) : undefined}
        sendTestId="session-send-btn"
      />

      {/* [2026-11-04 Feng] Sending to a session goes through the 'message send' window:
          receiver (agent + session) locked, sender subject selectable, text / Task / quick command all enabled. */}
      <SessionSendDialog session={selected} open={sendOpen && !!selected} onOpenChange={setSendOpen} />
    </div>
  );
}

/* ---------------- View 3: assistant workspace (left: assistant tiles → middle: executions → right: detail) ----------------
 * [2026-10-16 Feng's request] Agent detail gains an 'assistants' category.
 *   · Assistant = a case session with is_assistant=1 (same agx_session table, same fields) —
 *     so 'assistant detail' and 'session detail' are the same thing → reuse ExecPane.
 *   · Left column: all assistant names in **tile style**, with creator + icon (no icon → first letter of the name).
 * -------------------------------------------------------------------------------- */
function AssistantWorkspace({ bundle, frame = "" }: { bundle: AgentBundle; frame?: string }) {
  const { t } = useTranslation();
  const assistants = React.useMemo(() => bundle.sessions.filter((s) => s.isAssistant), [bundle.sessions]);
  const [selected, setSelected] = React.useState<SessionRow | null>(assistants[0] ?? null);
  const [sendOpen, setSendOpen] = React.useState(false);
  const execsOfSession = (sid: string) => bundle.executions.filter((e) => e.sessionId === sid);
  const [selectedExec, setSelectedExec] = React.useState<ExecutionRow | null>(
    assistants[0] ? latestExec(execsOfSession(assistants[0].id)) : null
  );

  const pickAssistant = (a: SessionRow) => {
    setSelected(a);
    setSelectedExec(latestExec(execsOfSession(a.id)));
  };

  if (!assistants.length) {
    return (
      <div className="p-8 text-sm text-muted-foreground" data-testid="workspace-assistants-empty">
        {t("agentDetail.assistant.empty")}
      </div>
    );
  }

  return (
    <div className="grid min-h-0 gap-3 lg:h-full lg:grid-cols-[minmax(0,320px)_1fr]" data-testid="workspace-assistants">
      {/* Left: assistant tile list (icon + name + creator) */}
      <div className={cn("flex min-h-0 flex-col gap-2 rounded-lg border p-3", frame)}>
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-xs font-semibold text-muted-foreground">{t("agentDetail.assistant.label")}</span>
          <span className="shrink-0 text-[11px] text-muted-foreground/70">{t("agentDetail.assistant.count", { n: assistants.length })}</span>
        </div>
        <div className="grid min-h-0 flex-1 grid-cols-2 content-start gap-2 overflow-y-auto">
          {assistants.map((a, i) => {
            const tone = toneOf(i);
            const active = selected?.id === a.id;
            return (
            <button
              key={a.id}
              onClick={() => pickAssistant(a)}
              data-testid={`assistant-tile-${a.assistantSlug || a.id}`}
              data-active={active ? "1" : "0"}
              className={cn(
                "flex flex-col gap-2 rounded-lg border p-2.5 text-left transition-all",
                tone.border,
                active ? cn("ring-2 ring-inset", tone.ring, tone.bg) : cn(tone.bg, "hover:ring-1 hover:ring-inset"),
              )}
            >
              <div className="flex items-center gap-2">
                {/* Icon: use the image if avatar exists, otherwise the first letter of the name + a deterministic color */}
                <AgentAvatar
                  name={a.name}
                  id={a.assistantSlug || a.id}
                  avatarUrl={a.avatar}
                  className="h-9 w-9 shrink-0"
                  textClassName="text-sm"
                />
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium" title={a.name} data-testid="assistant-name">{a.name}</div>
                  <div className="truncate font-mono text-[10px] text-muted-foreground">{a.assistantSlug || "—"}</div>
                </div>
              </div>
              <div className="flex items-center gap-1 text-[11px] text-muted-foreground" data-testid="assistant-creator">
                <span className="shrink-0">{t("agentDetail.assistant.creator")}</span>
                <span className="truncate" title={a.creatorName || a.creatorAgentId}>{a.creatorName || a.creatorAgentId || "—"}</span>
              </div>
            </button>
            );
          })}
        </div>
      </div>

      {/* Middle + right: executions + detail of that assistant (= session) */}
      <div className="flex min-h-0 flex-col gap-3">
        {selected && (
          <div className={cn("flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border px-3 py-2 text-xs", frame)} data-testid="assistant-summary">
            <span className="font-semibold">{selected.name}</span>
            <SessionTypeBadge type={selected.sessionType} />
            <SessionStatusDot runtime={selected.runtime} />
            <span className="text-muted-foreground">{t("agentDetail.assistant.creator")} {selected.creatorName || selected.creatorAgentId || "—"}</span>
            <span className="text-muted-foreground">{t("agentDetail.assistant.created", { at: selected.createdAt || "—" })}</span>
            <span className="text-muted-foreground">{t("agentDetail.assistant.runs", { n: selected.messageCount })}</span>
            <span className="font-mono text-[10px] text-muted-foreground">{selected.id}</span>
          </div>
        )}
        <ExecPane
          frame={frame}
          executions={selected ? execsOfSession(selected.id) : []}
          selected={selectedExec}
          onSelect={setSelectedExec}
          onSend={selected ? () => setSendOpen(true) : undefined}
          sendTestId="assistant-send-btn"
        />
      </div>

      {/* [2026-11-04 Feng] Sending to a session goes through the 'message send' window:
           receiver (agent + session) locked, sender subject selectable, text / Task / quick command all enabled. */}
      <SessionSendDialog session={selected} open={sendOpen && !!selected} onOpenChange={setSendOpen} />
    </div>
  );
}

/* ---------------- Dialog: execute task ---------------- */
interface RunFeedback {
  phase: "idle" | "sending" | "sent" | "error";
  result?: RunTaskResult;
  error?: string;
}

/** Session type → language pack key (main/task/case/assistant/oneshot) */
const SESSION_TYPE_KEY: Record<string, string> = {
  main: "agentDetail.session.type.main",
  task: "agentDetail.session.type.task",
  case: "agentDetail.session.type.case",
  assistant: "agentDetail.session.type.assistant",
  oneshot: "agentDetail.session.type.oneshot",
};

/** Session type badge (text label + distinguishing color) */
function SessionTypeBadge({ type }: { type?: string }) {
  const { t } = useTranslation();
  if (!type) return null;
  const cls: Record<string, string> = {
    main: "border-chart-1/30 bg-chart-1/15 text-chart-1",
    task: "border-info/30 bg-info/15 text-info",
    case: "border-warning/30 bg-warning/15 text-warning",
    oneshot: "border-border bg-muted text-muted-foreground",
  };
  const tone = cls[type] ?? "bg-muted text-muted-foreground";
  return (
    <Badge variant="outline" className={cn("shrink-0 text-[10px] font-normal", tone)} data-testid={`session-type-${type}`}>
      {SESSION_TYPE_KEY[type] ? t(SESSION_TYPE_KEY[type]) : type}
    </Badge>
  );
}

/**
 * [2026-10-11] Task category (labs) color mapping:
 *   A few common labs get dedicated colors; the rest get a stable color from a string hash (same labs = constant color).
 */
const LABS_TONE: Record<string, string> = {
  general: "text-info",
  watchdog_alert: "text-destructive",
  universal: "text-chart-1",
  log_organize: "text-chart-6",
  project_create: "text-success",
  project_audit: "text-warning",
};
const LABS_FALLBACK = [
  "text-success",
  "text-chart-3",
  "text-chart-5",
  "text-chart-4",
  "text-warning",
  "text-chart-6",
];
function labsTone(labs: string): string {
  if (LABS_TONE[labs]) return LABS_TONE[labs];
  let h = 0;
  for (let i = 0; i < labs.length; i++) h = (h * 31 + labs.charCodeAt(i)) >>> 0;
  return LABS_FALLBACK[h % LABS_FALLBACK.length];
}

function RunTaskDialog({
  agentId, agentName, task, sessions, onClose,
}: {
  agentId: string; agentName: string; task: TaskRow;
  sessions: SessionRow[]; onClose: () => void;
}) {
  const { t } = useTranslation();
  /* [2026-11-06 22:30 Feng] 'the Agent dialog forgot a refresh button — it does need a refresh'
     — this dialog is hand-rolled (`fixed inset-0`, not using the Dialog system), so it was missed
     when the previous round uniformly added buttons to `PageDialog` / `detail-popup`.

     ★ Refresh here **must not remount the whole block**: a remount would wipe the instruction the user
       already typed and the sender they already picked. So only let the 'data source' recompute
       (tick goes into the deps), keeping the form state as is. */
  const [tick, setTick] = React.useState(0);
  const [refreshing, setRefreshing] = React.useState(false);
  const [staleMsg, setStaleMsg] = React.useState("");
  const doRefresh = async () => {
    setRefreshing(true);
    setStaleMsg("");
    const r = await refreshStoreOnce(true);
    if (!r.ok) setStaleMsg(r.message || t("agentDetail.run.refreshFailed"));
    setRefreshing(false);
    setTick((t) => t + 1);
  };

  // (1) Sender agent identity (default = current target agent; Feng 2026-10-10 Super Terminal)
  const allAgents = React.useMemo(() => listAgentOptions(), [tick]);
  const [callerAgentId, setCallerAgentId] = React.useState(agentId);
  const [callerQuery, setCallerQuery] = React.useState("");
  const [callerOpen, setCallerOpen] = React.useState(false);
  const callerAgent = allAgents.find((a) => a.id === callerAgentId);
  const callerName = callerAgent?.name || callerAgentId;
  const callerAgents = React.useMemo(() => {
    const kw = callerQuery.trim().toLowerCase();
    const base = kw
      ? allAgents.filter((a) => a.id.toLowerCase().includes(kw) || a.name.toLowerCase().includes(kw))
      : allAgents;
    // Put the current target agent at the top
    return [...base].sort((a, b) => (a.id === agentId ? -1 : b.id === agentId ? 1 : 0));
  }, [allAgents, callerQuery, agentId]);

  // (2) Sender session: candidates = the [sender agent]'s own sessions (change with the sender)
  const sessionsOfCaller = React.useMemo(() => {
    if (callerAgentId === agentId) return sessions; // sender == receiver: use the passed-in one
    return sessionsOf(callerAgentId);
  }, [callerAgentId, agentId, sessions]);
  const callerMainKey = `agent:${callerAgentId}:main`;
  const sessionRank: Record<string, number> = { main: 0, case: 1, task: 2, oneshot: 3 };
  const orderSessions = (arr: SessionRow[]) =>
    [...arr].sort((a, b) => {
      const ra = sessionRank[a.sessionType || ""] ?? 4;
      const rb = sessionRank[b.sessionType || ""] ?? 4;
      if (ra !== rb) return ra - rb;
      return (b.lastActiveAt || "").localeCompare(a.lastActiveAt || "");
    });
  const orderedSendSessions = React.useMemo(() => orderSessions(sessionsOfCaller), [sessionsOfCaller]);
  const [sendSessionKey, setSendSessionKey] = React.useState(callerMainKey);
  const [sendSessionQuery, setSendSessionQuery] = React.useState("");
  // Sender switched → sender session resets to that sender's main session
  React.useEffect(() => {
    setSendSessionKey(`agent:${callerAgentId}:main`);
    setSendSessionQuery("");
  }, [callerAgentId]);

  // (3) Receiver session: the receiver = current agent (fixed), candidates = current agent's own sessions; **not linked to the sender**
  const recvMainKey = `agent:${agentId}:main`;
  const orderedRecvSessions = React.useMemo(() => orderSessions(sessions), [sessions]);
  // [2026-10-11] Added the 'new session' option: means the current task needs a new session → no receiver session id passed.
  // Sentinel value (will not collide with a real session id).
  const NEW_SESSION_KEY = "__new__";
  const [recvSessionKey, setRecvSessionKey] = React.useState(
    sessions.find((s) => s.id === recvMainKey)?.id || orderedRecvSessions[0]?.id || recvMainKey
  );
  const isNewSession = recvSessionKey === NEW_SESSION_KEY;
  const [recvSessionQuery, setRecvSessionQuery] = React.useState("");

  // (4) Task description (editable override; prefilled with the task's own description)
  const [instruction, setInstruction] = React.useState(task.description || "");
  // (5) Common optional params (Feng 2026-10-10): shared by all tasks (needs_subagent removed — legacy attribute)
  const [needsReply, setNeedsReply] = React.useState<"" | "0" | "1">(""); // ""=not passed (default)
  const [timeoutSec, setTimeoutSec] = React.useState<string>("");            // ""=not passed
  const [execName, setExecName] = React.useState("");
  // (6) Execution feedback
  const [fb, setFb] = React.useState<RunFeedback>({ phase: "idle" });

  const submit = async () => {
    setFb({ phase: "sending" });
    try {
      const result = await agentsApi.runTask(agentId, {
        session_key: isNewSession ? "" : recvSessionKey,  // Receiver session (new session → empty, created by the backend)
        instruction,
        task_id: task.id,
        task_name: task.name,
        caller_agent_id: callerAgentId,     // Sender identity
        send_session_key: sendSessionKey,   // Sender session
        ...(needsReply !== "" ? { needs_reply: Number(needsReply) } : {}),
        // [2026-10-10] The user picked 'no reply needed' → must explicitly force_no_reply=true,
        // otherwise the task.reply_mode (result/confirm) template derivation flips it back to 1 (the convention after the MCP kernel gap was fixed).
        ...(needsReply === "0" ? { force_no_reply: true } : {}),
        ...(timeoutSec.trim() && !Number.isNaN(Number(timeoutSec)) ? { timeout_seconds: Number(timeoutSec) } : {}),
        ...(execName.trim() ? { exec_name: execName.trim() } : {}),
      });
      setFb({ phase: "sent", result });
    } catch (e) {
      const err = e as { message?: string; code?: string };
      setFb({ phase: "error", error: `${err?.code ? `[${err.code}] ` : ""}${err?.message || t("agentDetail.run.deliverFailed")}` });
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
      data-testid="run-task-dialog"
    >
      <Card className="max-h-[94vh] w-full max-w-5xl overflow-auto" onClick={(e) => e.stopPropagation()}>
        <CardHeader className="flex flex-row items-center justify-between pb-3">
          <CardTitle className="text-base">{t("agentDetail.run.title", { name: task.description || task.name })}</CardTitle>
          <div className="flex items-center gap-1">
            {staleMsg ? (
              <span className="mr-1 text-[11px] text-destructive" title={staleMsg}>
                {t("agentDetail.run.refreshFailed")}
              </span>
            ) : null}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void doRefresh()}
              disabled={refreshing}
              aria-label={t("agentDetail.run.refreshAria")}
              title={t("agentDetail.run.refreshTitle")}
              data-testid="run-task-dialog-refresh"
            >
              <RefreshCw className={refreshing ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            </Button>
            <Button variant="ghost" size="sm" onClick={onClose} aria-label={t("agentDetail.run.close")}>✕</Button>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          {/* (1) Sender agent identity (default = current agent) */}
          <div className="flex flex-col gap-1.5">
            <div className="text-xs font-semibold text-muted-foreground">
              {t("agentDetail.run.step1")}
            </div>
            <div className="relative">
              <button
                type="button"
                onClick={() => setCallerOpen((v) => !v)}
                data-testid="run-caller-select"
                className="flex h-9 w-full items-center justify-between rounded-md border bg-background px-3 text-sm"
              >
                <span className="truncate">
                  {/* Collapsed state and dropdown items use the same convention: both carry [host_id], unchanged before and after selection */}
                  {callerAgent?.host_id ? `[${callerAgent.host_id}] ` : ""}
                  {callerName}
                  <span className="ml-2 font-mono text-[11px] text-muted-foreground">{callerAgentId}</span>
                </span>
                <span className="text-muted-foreground">▾</span>
              </button>
              {callerOpen && (
                <div className="absolute z-10 mt-1 w-full rounded-md border bg-popover shadow-md">
                  <input
                    autoFocus
                    className="h-8 w-full border-b bg-transparent px-3 text-xs outline-none"
                    placeholder={t("agentDetail.run.callerSearch")}
                    value={callerQuery}
                    onChange={(e) => setCallerQuery(e.target.value)}
                    data-testid="run-caller-search"
                  />
                  <div className="max-h-56 overflow-auto" data-testid="run-caller-list">
                    {callerAgents.length === 0 && (
                      <div className="px-3 py-2 text-xs text-muted-foreground">{t("agentDetail.run.noMatchAgent")}</div>
                    )}
                    {callerAgents.map((a) => (
                      <button
                        key={a.id}
                        type="button"
                        onClick={() => { setCallerAgentId(a.id); setCallerOpen(false); setCallerQuery(""); }}
                        data-testid={`run-caller-opt-${a.id}`}
                        className={cn(
                          "flex w-full items-center justify-between gap-3 border-b px-3 py-2 text-left text-sm last:border-b-0",
                          callerAgentId === a.id ? "bg-primary/10" : "hover:bg-accent"
                        )}
                      >
                        {/* [2026-11-04 Feng] All agent-picking dropdowns carry a [host_id] prefix —
                            this one is a custom combobox (not Select), the id sits alone on the right, so the host goes before the name. */}
                        <span className="flex min-w-0 items-center gap-1.5">
                          <span className="truncate">
                            {a.host_id ? `[${a.host_id}] ` : ""}
                            {a.name}
                          </span>
                          {/* [2026-11-07] Offline / template agents are listed too (so their history can be looked up); status shown as its own block */}
                          {agentStateLabel(a) && (
                            <span className="shrink-0 rounded border border-border px-1 py-px text-[10px] font-normal leading-4 text-muted-foreground">
                              {agentStateLabel(a)}
                            </span>
                          )}
                        </span>
                        <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{a.id}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
            <div className="text-[11px] text-muted-foreground">
              {t("agentDetail.run.superTerminal", { name: agentName })}
            </div>
          </div>

          {/* (2) Sender session (candidates = the selected sender's own sessions) */}
          <div className="flex flex-col gap-1.5">
            <div className="text-xs font-semibold text-muted-foreground">
              {t("agentDetail.run.step2")}
            </div>
            <input
              className="h-9 w-full rounded-md border bg-background px-3 text-sm"
              placeholder={t("agentDetail.run.sendSessionSearch")}
              value={sendSessionQuery}
              onChange={(e) => setSendSessionQuery(e.target.value)}
              data-testid="run-send-session-search"
            />
            <div className="max-h-48 overflow-auto rounded-md border" data-testid="run-send-session-list">
              {(() => {
                const kw = sendSessionQuery.trim().toLowerCase();
                const list = orderedSendSessions.filter(
                  (s) => !kw || s.id.toLowerCase().includes(kw) || (s.name || "").toLowerCase().includes(kw)
                );
                const rows = list.length
                  ? list
                  : [{ id: callerMainKey, name: callerMainKey, sessionType: "main" as const, agentId: callerAgentId, agentName: "", status: "active" as const, messageCount: 0, lastActiveAt: "" }];
                return rows.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => setSendSessionKey(s.id)}
                    data-testid={`run-send-session-opt-${s.id}`}
                    className={cn(
                      "flex w-full items-center justify-between gap-3 border-b px-3 py-2 text-left text-sm last:border-b-0",
                      sendSessionKey === s.id ? "bg-primary/10" : "hover:bg-accent"
                    )}
                  >
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate font-medium">{s.name || s.id}</span>
                      <span className="truncate font-mono text-[10px] text-muted-foreground">{s.id}</span>
                    </span>
                    <span className="flex shrink-0 items-center gap-3 text-[11px] text-muted-foreground">
                      <SessionTypeBadge type={s.sessionType} />
                    </span>
                  </button>
                ));
              })()}
            </div>
            <div className="text-[11px] text-muted-foreground">
              {t("agentDetail.run.chosenSenderSession")}
              <span className="font-medium">{sessionsOfCaller.find((s) => s.id === sendSessionKey)?.name || t("agentDetail.run.mainSession")}</span>
              <span className="ml-2 font-mono text-muted-foreground/70">{sendSessionKey}</span>
              <input type="hidden" value={sendSessionKey} data-testid="run-send-session-select" readOnly />
            </div>
          </div>

          {/* (3) Receiver session (receiver = current agent, fixed; not linked to the sender) */}
          <div className="flex flex-col gap-1.5">
            <div className="text-xs font-semibold text-muted-foreground">
              {t("agentDetail.run.step3", { name: agentName })}
            </div>
            <input
              className="h-9 w-full rounded-md border bg-background px-3 text-sm"
              placeholder={t("agentDetail.run.recvSessionSearch")}
              value={recvSessionQuery}
              onChange={(e) => setRecvSessionQuery(e.target.value)}
              data-testid="run-session-search"
            />
            <div className="max-h-48 overflow-auto rounded-md border" data-testid="run-session-list">
              {/* [2026-10-11] New session option (pinned at top): the current task needs a new session → no receiver session id passed */}
              {(() => {
                const kw = recvSessionQuery.trim().toLowerCase();
                // Hide this option when the search keyword does not match 'new session'
                const showNew = !kw || t("agentDetail.run.newSession").toLowerCase().includes(kw) || "new".includes(kw) || "__new__".includes(kw);
                return showNew ? (
                  <button
                    type="button"
                    onClick={() => setRecvSessionKey(NEW_SESSION_KEY)}
                    data-testid="run-session-opt-new"
                    className={cn(
                      "flex w-full items-center justify-between gap-3 border-b px-3 py-2 text-left text-sm last:border-b-0",
                      isNewSession ? "bg-primary/10" : "hover:bg-accent"
                    )}
                  >
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="flex items-center gap-1.5 truncate font-medium">
                        <Plus className="h-3.5 w-3.5 text-primary" />{t("agentDetail.run.newSession")}
                      </span>
                      <span className="truncate font-mono text-[10px] text-muted-foreground">{t("agentDetail.run.newSessionHint")}</span>
                    </span>
                    <SessionTypeBadge type="oneshot" />
                  </button>
                ) : null;
              })()}
              {(() => {
                const kw = recvSessionQuery.trim().toLowerCase();
                const list = orderedRecvSessions.filter(
                  (s) => !kw || s.id.toLowerCase().includes(kw) || (s.name || "").toLowerCase().includes(kw)
                );
                const rows = list.length
                  ? list
                  : [{ id: recvMainKey, name: recvMainKey, sessionType: "main" as const, agentId, agentName: "", status: "active" as const, messageCount: 0, lastActiveAt: "" }];
                return rows.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => setRecvSessionKey(s.id)}
                    data-testid={`run-session-opt-${s.id}`}
                    className={cn(
                      "flex w-full items-center justify-between gap-3 border-b px-3 py-2 text-left text-sm last:border-b-0",
                      recvSessionKey === s.id ? "bg-primary/10" : "hover:bg-accent"
                    )}
                  >
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate font-medium" data-testid={`session-name-${s.id}`}>{s.name || s.id}</span>
                      <span className="truncate font-mono text-[10px] text-muted-foreground" data-testid={`session-id-${s.id}`}>{s.id}</span>
                    </span>
                    <span className="flex shrink-0 items-center gap-3 text-[11px] text-muted-foreground">
                      <SessionTypeBadge type={s.sessionType} />
                      {s.messageCount > 0 && <span>{t("agentDetail.exec.count", { n: s.messageCount })}</span>}
                      {s.lastActiveAt && <span>{s.lastActiveAt}</span>}
                    </span>
                  </button>
                ));
              })()}
            </div>
            <div className="text-[11px] text-muted-foreground">
              {t("agentDetail.run.chosenRecvSession")}
              {isNewSession ? (
                <>
                  <span className="font-medium text-primary">{t("agentDetail.run.newSession")}</span>
                  <span className="ml-2 text-muted-foreground/70">{t("agentDetail.run.newSessionHint")}</span>
                </>
              ) : (
                <>
                  <span className="font-medium">{sessions.find((s) => s.id === recvSessionKey)?.name || t("agentDetail.run.mainSession")}</span>
                  <span className="ml-2 font-mono text-muted-foreground/70">{recvSessionKey}</span>
                </>
              )}
              <input type="hidden" value={recvSessionKey} data-testid="run-session-select" readOnly />
            </div>
            <div className="text-[11px] text-muted-foreground">
              {t("agentDetail.run.recvSessionRule")}
            </div>
          </div>

          {/* (4) Task description + attributes */}
          <div className="flex flex-col gap-1.5">
            <div className="text-xs font-semibold text-muted-foreground">{t("agentDetail.run.step4")}</div>
            <textarea
              className="h-28 w-full resize-none rounded-md border bg-background p-3 font-mono text-xs"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              aria-label={t("agentDetail.run.descAria")}
              data-testid="run-instruction"
            />
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
              <span>{t("agentDetail.run.taskName")}<span className="font-mono">{task.name}</span></span>
              <span>{t("agentDetail.run.taskLabs")}{task.labs}</span>
              <span>{task.isPrivate ? t("agentDetail.run.taskPrivate") : t("agentDetail.run.taskShared")}</span>
              <span>{t("agentDetail.run.taskUpdated")}{task.updatedAt || "—"}</span>
            </div>
            <div className="text-[11px] text-muted-foreground">
              {t("agentDetail.run.taskTarget")}<span className="font-mono">{agentId}</span>（{agentName}）
            </div>
          </div>

          {/* (5) Common optional params (shared by all tasks; see agx_dispatch_task) */}
          <div className="flex flex-col gap-2 rounded-md border bg-muted/20 p-3" data-testid="run-options">
            <div className="text-xs font-semibold text-muted-foreground">{t("agentDetail.run.step5")}</div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {/* Whether the message needs a reply */}
              <label className="flex flex-col gap-1">
                <span className="text-[11px] text-muted-foreground">{t("agentDetail.run.needReply")}</span>
                <select
                  className="h-9 rounded-md border bg-background px-2 text-sm"
                  value={needsReply}
                  onChange={(e) => setNeedsReply(e.target.value as "" | "0" | "1")}
                  data-testid="run-needs-reply"
                >
                  <option value="">{t("agentDetail.run.replyDefault")}</option>
                  <option value="1">{t("agentDetail.run.replyYes")}</option>
                  <option value="0">{t("agentDetail.run.replyNo")}</option>
                </select>
              </label>
              {/* Watchdog timeout (seconds) */}
              <label className="flex flex-col gap-1">
                <span className="text-[11px] text-muted-foreground">{t("agentDetail.run.timeout")}</span>
                <Input
                  type="number"
                  inputMode="numeric"
                  min={0}
                  placeholder={t("agentDetail.run.timeoutPh")}
                  value={timeoutSec}
                  onChange={(e) => setTimeoutSec(e.target.value)}
                  data-testid="run-timeout"
                />
              </label>
              {/* Execution instance short title */}
              <label className="flex flex-col gap-1">
                <span className="text-[11px] text-muted-foreground">{t("agentDetail.run.execName")}</span>
                <Input
                  placeholder={t("agentDetail.run.execNamePh")}
                  value={execName}
                  onChange={(e) => setExecName(e.target.value)}
                  data-testid="run-exec-name"
                />
              </label>
            </div>
          </div>

          {/* (6) Execution feedback */}
          <div className="rounded-md border bg-muted/30 p-3" data-testid="run-feedback">
            <div className="mb-1 text-xs font-semibold text-muted-foreground">{t("agentDetail.run.feedback")}</div>
            {fb.phase === "idle" && (
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <MessageCircle className="h-3.5 w-3.5" /> {t("agentDetail.run.notSent")}
              </div>
            )}
            {fb.phase === "sending" && (
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> {t("agentDetail.run.sending")}
              </div>
            )}
            {fb.phase === "sent" && fb.result && (
              <div className="flex flex-col gap-1 text-xs">
                <div className="flex items-center gap-2 text-success">
                  <CheckCircle2 className="h-3.5 w-3.5" /> {t("agentDetail.run.sent")}
                </div>
                <div className="text-muted-foreground">
                  {t("agentDetail.run.sender")}<span className="font-mono">{fb.result.caller_agent_id || callerAgentId}</span>
                  <span className="font-mono"> @ {fb.result.send_session_key || sendSessionKey}</span>
                </div>
                <div className="text-muted-foreground">
                  {t("agentDetail.run.receiver")}<span className="font-mono">{fb.result.target_agent || agentId}</span>
                  <span className="font-mono"> @ {fb.result.session_key || recvSessionKey}</span>
                </div>
                <div className="text-muted-foreground">
                  {t("agentDetail.run.runId")}<span className="font-mono">{fb.result.run_uuid || "—"}</span>
                  {fb.result.msg_id && <> · msg_id：<span className="font-mono">{fb.result.msg_id}</span></>}
                  {fb.result.queue_length != null && <> · {t("agentDetail.run.queue")}{fb.result.queue_length}</>}
                </div>
                <div className="text-muted-foreground">
                  {t("agentDetail.run.echoHint")}
                </div>
              </div>
            )}
            {fb.phase === "error" && (
              <div className="flex items-start gap-2 text-xs text-destructive">
                <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> <span>{fb.error}</span>
              </div>
            )}
          </div>

          {/* Action buttons */}
          <div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={onClose}>{t("agentDetail.run.close")}</Button>
            <Button
              size="sm"
              onClick={submit}
              disabled={fb.phase === "sending" || !instruction.trim() || !recvSessionKey}
              data-testid="run-confirm"
            >
              {fb.phase === "sending" ? (
                <><Loader2 className="h-3.5 w-3.5 animate-spin" /> {t("agentDetail.run.delivering")}</>
              ) : (
                <><Play className="h-3.5 w-3.5" /> {t("agentDetail.run.confirmExec")}</>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

/* ---------------- Quick command workspace (filtered by agent) ---------------- */
function QuickCommandsWorkspace({
  agentId,
  commands,
  loading,
  error,
  onReload,
  onRun,
}: {
  agentId: string;
  commands: QuickCmd[];
  loading: boolean;
  error: string;
  onReload: () => void;
  onRun: (c: QuickCmd) => void;
}) {
  const { t } = useTranslation();
  /* [2026-10-19 16:39 Feng] A command that was clicked 'run' stays bold until another card is clicked */
  const [activeCmdId, setActiveCmdId] = React.useState<string>("");
  /* [2026-10-27] Public items (is_public=1, not bound to an agent, target is the caller) must be shown too */
  const publicCount = commands.filter((c) => c.isPublic).length;
  const privateCount = commands.length - publicCount;
  if (loading) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground" data-testid="workspace-commands">
        <Loader2 className="h-4 w-4 animate-spin" /> {t("agentDetail.commands.loading")}
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex flex-col gap-2 p-6" data-testid="workspace-commands">
        <div className="flex items-center gap-2 text-sm text-destructive">
          <XCircle className="h-4 w-4" /> {error}
        </div>
        <Button size="sm" variant="run" onClick={onReload}>
          <RefreshCw className="h-3.5 w-3.5" /> {t("agentDetail.commands.retry")}
        </Button>
      </div>
    );
  }
  if (commands.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 p-10 text-sm text-muted-foreground" data-testid="workspace-commands">
        <Zap className="h-6 w-6 opacity-50" />
        <span>{t("agentDetail.commands.empty")}</span>
        <span className="text-xs opacity-70">
          {t("agentDetail.commands.emptyHintPrefix")}<span className="font-mono">{agentId}</span>{t("agentDetail.commands.emptyHintSuffix")}
        </span>
      </div>
    );
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" data-testid="workspace-commands">
      {/* [2026-10-16 Feng] The same-semantics 'quick commands' also exist here (agent detail → quick commands tab,
          a self-built list filtered by agent) — the previous two rounds only changed the /quick-cmds list page and missed this.
          Now aligned item by item with the 'tasks' page card pattern: title badge → description line-clamp-3 → bottom block mt-auto pinned down. */}
      <div className="mb-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground sm:col-span-2 lg:col-span-3">
        <Sparkles className="h-3.5 w-3.5" />
        <span>
          {t("agentDetail.commands.title", { id: agentId, n: commands.length })}
        </span>
        {/* ★ 2026-10-27: available = private ownership ∪ public (an active task with the same task_labs exists under that agent) */}
        <Badge variant="outline" className="gap-1 font-normal" data-testid="cmds-private-count">
          <Lock className="h-3 w-3" /> {t("agentDetail.commands.privateCount", { n: privateCount })}
        </Badge>
        <Badge variant="outline" className="gap-1 font-normal" data-testid="cmds-public-count">
          <Globe className="h-3 w-3" /> {t("agentDetail.commands.publicCount", { n: publicCount })}
        </Badge>
      </div>
      {commands.map((c, i) => {
        const tone = toneOf(i);
        const active = activeCmdId === c.cmdId;
        return (
        <div
          key={c.cmdId}
          onClick={() => setActiveCmdId(c.cmdId)}
          data-active={active ? "1" : "0"}
          className={cn(
            "flex flex-col gap-2 rounded-lg border bg-card p-3 transition-all",
            tone.border,
            active ? cn("ring-2 ring-inset", tone.ring, tone.bg) : cn(tone.bg, "hover:ring-1 hover:ring-inset"),
          )}
          data-testid={`cmd-row-${c.cmdId}`}
        >
          {/* Row 1: command name (large text, colored background, primary identity, same as task-name) + run button at top right
              [2026-11-05 21:49 Feng] 'put the action buttons ... in the top right, unify it across the whole system'
              — same pattern as the data-table generic grid: title row with two columns, the button no longer takes a row at the card bottom. */}
          <div className="flex items-start justify-between gap-2">
            <span
              className="min-w-0 truncate rounded-md bg-primary px-2 py-0.5 text-sm font-semibold text-primary-foreground shadow-sm"
              data-testid={`cmd-name-${c.cmdId}`}
            >
              {c.title}
            </span>
            <div className="flex shrink-0 items-center gap-1" onClick={(e) => e.stopPropagation()}>
              <Button
                size="sm"
                variant="outline"
                onClick={() => { setActiveCmdId(c.cmdId); onRun(c); }}
                data-testid={`cmd-run-${c.cmdId}`}
              >
                <Play className="h-3.5 w-3.5" /> {t("agentDetail.commands.run")}
              </Button>
            </div>
          </div>
          {/* cmdId + category: small meta text right after the title */}
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-mono text-xs text-muted-foreground" data-testid={`cmd-id-${c.cmdId}`}>{c.cmdId}</span>
            {/* ★ 2026-10-27: public items are marked 'public' (no owning agent shown); private items show the owning agent */}
            {c.isPublic ? (
              <Badge variant="secondary" className="gap-1 font-normal" data-testid={`cmd-public-${c.cmdId}`}>
                <Globe className="h-3 w-3" /> {t("agentDetail.commands.public")}
              </Badge>
            ) : (
              <Badge variant="outline" className="gap-1 font-normal" data-testid={`cmd-owner-${c.cmdId}`}>
                <Lock className="h-3 w-3" /> {c.ownerAgent || agentId}
              </Badge>
            )}
            {c.category && (
              <Badge variant="outline" className="font-normal">{c.category}</Badge>
            )}
          </div>
          {/* Description: limited to 3 lines (same as task-desc), so uneven lengths do not break the grid */}
          <p className="line-clamp-3 text-sm leading-relaxed text-foreground/90" data-testid={`cmd-desc-${c.cmdId}`}>
            {c.semanticSkeleton || c.notes || t("agentDetail.commands.noNotes")}
          </p>
          {/* Bottom block (mt-auto pinned): metadata only now (the run button moved up to the title row's top right) */}
          <div className="mt-auto flex items-center gap-2 pt-1">
            <div className="inline-flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1 rounded-md border border-border px-2 py-1 text-xs" data-testid={`cmd-meta-${c.cmdId}`}>
              <span className="text-muted-foreground">{t("agentDetail.commands.runner")}</span>
              <span className={cn("font-mono", c.isPublic && "text-info")} data-testid={`cmd-executor-${c.cmdId}`}>
                {c.isPublic ? t("agentDetail.commands.self") : (c.ownerAgent || "—")}
              </span>
              <span className="text-border">|</span>
              <span className="text-muted-foreground">{t("agentDetail.commands.targetTask")}</span>
              <span className="font-mono">{c.targetTask || "—"}</span>
              <span className="text-border">|</span>
              <span className="text-muted-foreground">{t("agentDetail.commands.params", { n: c.params?.length ?? 0 })}</span>
            </div>
          </div>
        </div>
        );
      })}
    </div>
  );
}

/* ---------------- Page ---------------- */
export default function AgentDetailPage({
  agentId,
  embedded = false,
  from,
}: {
  /** Passed by the caller when inline in a dialog; not passed by the page route (reads useParams) */
  agentId?: string;
  /** [2026-10-29] Embedded-in-dialog mode: no breadcrumb, no 'back', self-contained height */
  embedded?: boolean;
  /** [2026-11-16 00:04 Feng] 'add a param — this param says where this window came from;
   *   with that param, I think we should hide it (message send)'
   *
   *  Why narrow it by source instead of 'hide whenever embedded':
   *    The dialog state has other origins too (clicking an agent in executions, in a session, ...); in those places
   *    sending a quick message while viewing details is legitimate. Only in the case of **being opened from the send window itself**
   *    would showing 'message send' stack a second send window on top of itself.
   *  ★ The value is set by the caller; this is the only consuming point: `from === "message-send"` → hide. */
  from?: string;
} = {}) {
  const { t } = useTranslation();
  const { id: paramId = "" } = useParams();
  const id = agentId ?? paramId;
  const navigate = useNavigate();
  /* [2026-11-06 22:5x hit in real testing] Originally the page was refreshed with `refreshPageData()` after sending —
     that calls `notifyPageRefreshed()` → App's `RefreshableRoutes` key changes → **the whole page remounts** →
     the 'message send' dialog being shown gets unmounted → the user cannot see the 'sent' result, nor 'send again'.
     Sending one message wiped out the feedback UI — putting the cart before the horse.
     Switched to a local tick: only this page recomputes data, the dialog and its own result state stay. */
  const [dataTick, setDataTick] = React.useState(0);
  const bundle = React.useMemo(() => {
    void dataTick; // Dep: local refresh counter
    return getAgentBundle(id);
  }, [id, dataTick]);
  const [top, setTop] = React.useState<TopKey>("tasks");
  /* [2026-10-19 13:32 Feng] Task card 'last run' highlight: remembered once run/card is clicked,
     and stays bold after the run dialog closes, until focus moves to another task. */
  const [activeTaskId, setActiveTaskId] = React.useState<string>("");
  const [dialogOpen, setDialogOpen] = React.useState<null | "executions" | "sessions">(null);
  const [runTask, setRunTask] = React.useState<TaskRow | null>(null);
  // [ownership rework 2026-10-08] Quick command routing when clicking 'run':
  //   configured → open the custom params dialog (QuickCmdDialog); not configured → default path (RunTaskDialog)
  const [quickTask, setQuickTask] = React.useState<TaskRow | null>(null);
  const [quickCmd, setQuickCmd] = React.useState<QuickCmd | null>(null);
  const [quickOpen, setQuickOpen] = React.useState(false);
  const [selectedExec, setSelectedExec] = React.useState<ExecutionRow | null>(null);
  // [2026-10-17 notice note_03cb822ee346] Message-send dialog (formerly Universal Input Bar) —
  //   activated by the top button; the full set has four layers, the other three entries apply it as 'prefill + lock + hide'.
  const [universalOpen, setUniversalOpen] = React.useState(false);

  /* [2026-11-07 Feng] 'the Agent seems to be uneditable ... add an edit button in the detail, edit via one MCP method.'
     Fetch the profile once before opening: the list snapshot lacks the long fields x_desc / system_prompt / skills / mcp
     (they are kept out of the snapshot precisely to avoid pulling several extra MB every time). So the dialog's initial values
     **must come from the single-item API**, never from the incomplete list rows — otherwise saving writes empty values into fields that were not loaded. */
  const [editProfile, setEditProfile] = React.useState<AgentProfile | null>(null);
  const [editLoading, setEditLoading] = React.useState(false);
  const [editErr, setEditErr] = React.useState("");
  const openEdit = React.useCallback(async () => {
    setEditLoading(true);
    setEditErr("");
    try {
      setEditProfile(await agentsApi.profile(id));
    } catch (e) {
      setEditErr(apiErrorText(e, t));
    } finally {
      setEditLoading(false);
    }
  }, [id]);

  // [2026-10-14] Quick command tab: filtered by the current agent (backend GET /quick-cmd?agent_id=)
  const [cmds, setCmds] = React.useState<QuickCmd[]>([]);
  const [cmdsLoading, setCmdsLoading] = React.useState(true);
  const [cmdsError, setCmdsError] = React.useState("");
  const [runCmd, setRunCmd] = React.useState<QuickCmd | null>(null);

  const loadCmds = React.useCallback(async () => {
    if (!id) return;
    setCmdsLoading(true);
    setCmdsError("");
    try {
      const r = await fetchQuickCommands(id);
      setCmds(r.commands || []);
    } catch (e) {
      const x = e as { message?: string };
      setCmdsError(x.message || t("agentDetail.commands.loadFailed"));
    } finally {
      setCmdsLoading(false);
    }
  }, [id]);

  React.useEffect(() => { void loadCmds(); }, [loadCmds]);

  /**
   * [ownership rework 2026-10-08] Clicking 'run' on a task:
   *   look up the quick command by task identifier (agentId + labs + name) → if found open the custom dialog, else use the default.
   *   A failed lookup also falls back to the default (never blocks running).
   */
  const handleRunTask = React.useCallback(async (t: TaskRow) => {
    setActiveTaskId(t.id);
    setQuickTask(t);
    setQuickCmd(null);
    setQuickOpen(false);
    let cmd: QuickCmd | null = null;
    try {
      const r = await fetchQuickCmdByTask({
        agent_id: t.agentId,
        task_labs: String(t.labs || ""),
        task_name: t.name,
      });
      cmd = r.cmd;
    } catch {
      cmd = null; // lookup failed → fall back to the default run
    }
    if (cmd) {
      setQuickCmd(cmd);
      setQuickOpen(true);
    } else {
      setRunTask(t); // default: the original RunTaskDialog
    }
  }, []);

  /* [2026-10-29] Inside a dialog: got onClose ('back' becomes 'close') */
  const embed = useDetailEmbed();
  /* [2026-10-29] Hand the entity name to the top-bar breadcrumb (not inside a dialog: a dialog is not navigation) */
  useDetailCrumb(embedded ? null : (bundle?.agent?.name ?? id));

  if (!bundle) {
    return (
      <div className="flex flex-col gap-4" data-testid="page-agent-detail">
        {!embedded && (
          <Button variant="back" size="sm" onClick={() => navigate("/agents")}>
            <ArrowLeft className="h-4 w-4" /> {t("agentDetail.page.backToList")}
          </Button>
        )}
        <Card><CardContent className="p-8 text-sm text-muted-foreground">{t("agentDetail.page.notFound", { id })}</CardContent></Card>
      </div>
    );
  }

  const { agent, sessions, tasks, executions, projectName } = bundle;
  // Top stats: prefer the backend's direct ground truth (agent.*, unaffected by MCP visibility filtering);
  // when there is no ground truth, fall back to local collection lengths (so a fake 0 is never shown).
  const taskTotal = agent.taskCount ?? tasks.length;
  const sessionTotal = agent.sessionCount ?? sessions.length;
  // Assistant = a case session with is_assistant=1 (owned by this agent)
  const assistants = React.useMemo(() => sessions.filter((s) => s.isAssistant), [sessions]);
  const execTotal = agent.execCount ?? executions.length;
  const execOk = agent.execSucceeded ?? executions.filter((e) => e.status === "succeeded").length;
  const execBad = agent.execFailed ?? executions.filter((e) => e.status === "failed").length;
  // Success rate: computed only over [finished] (succeeded+failed), so pending/draft/running do not drag it down.
  const execFinished = execOk + execBad;
  const successRate = execFinished > 0 ? Math.round((execOk / execFinished) * 100) : 0;
  // Execution list: startedAt desc (newest first)
  const sortedExecutions = React.useMemo(
    () => [...executions].sort((a, b) => (b.startedAt || "").localeCompare(a.startedAt || "")),
    [executions]
  );
  const currentExec = selectedExec ?? sortedExecutions.find((e) => e.status === "running") ?? sortedExecutions[0];

  return (
    <div
      className={embedded ? "flex h-[74vh] min-h-0 flex-col" : "flex h-full min-h-0 flex-col"}
      data-testid="page-agent-detail"
    >
      {/* [2026-10-28 16:37 Feng] The detail page content also goes into a white frame (same spec as the list page);
          the page header stays (entity name / back / status badges are identity info, not a duplicate navigation title). */}
      <div
        className={
          embedded
            ? "flex min-h-0 flex-1 flex-col gap-3"
            : "flex min-h-0 flex-1 flex-col gap-3 rounded-xl border border-strong bg-card p-4 shadow-sm"
        }
        data-testid="content-frame"
      >
        {/* Header (first row inside the frame, fixed, does not scroll with the list)
          [2026-10-29 11:39 Feng] It used to hang OUTSIDE the white frame, clashing with 'everything lives inside the white frame'.
          Now moved inside, plus a divider (border-b) separating it from the content below —
          same style as the line between 'session info | session executions' in 'session detail'.
          [2026-10-28 14:48 Feng] Unified with the entity detail page:
            left = avatar + name + ID/project + role/platform/status badges (one row, horizontal)
            right = action buttons (message send) + 'back' (buttons all at the far right, same style and height as the right-side buttons)
          The back used to be a transparent arrow floating in the top-left corner — invisible and not grouped with the buttons. */}
        <div
          className="flex shrink-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-border pb-3"
          data-testid="detail-head"
        >
        <div className="flex min-w-0 items-center gap-2.5">
          {/* Avatar: prefer agent_avatar, otherwise the first letter (★ do not use a platform/framework logo) */}
          <AgentAvatar name={agent.name} id={agent.id} avatarUrl={agent.avatarUrl} className="h-9 w-9" textClassName="text-sm" />
          <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
            <span className="truncate text-lg font-semibold" data-testid="agent-name">{agent.name}</span>
            <span className="truncate font-mono text-[11px] text-muted-foreground">{agent.id} · {projectName}</span>
            <Badge variant="outline" className="font-mono text-[11px]">{agent.role}</Badge>
            {/* [2026-10-27 Feng] The platform marker is the entry: clicking it goes straight to that platform's web page, no separate button */}
            <PlatformTag
              type={agent.agentType}
              href={chatDeepLink(agent.id, agent.agentType, agent.host_id)}
              data-testid="agent-platform"
            />
            <StatusPill status={agent.status} />
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {/* [2026-11-02] Profile page entry.
              Originally only the 'detail' button on the grid card could reach `/agents/:id/profile`;
              Feng said that button was redundant (clicking the card's empty area also opens the detail), and replaced it
              with 'message send' (then called 'unified input') — so nobody could reach this page anymore.
              Added back here: you are already inside this agent, switching to its profile is the same level of action,
              one step less than going back to the list and clicking through. */}
          <Button
            variant="outline"
            size="sm"
            className="gap-1"
            onClick={() => void openEdit()}
            disabled={editLoading}
            title={editErr || t("agentDetail.page.editTitle")}
            data-testid="agent-edit-open"
          >
            {editLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Pencil className="h-4 w-4" />}
            {t("agentDetail.page.edit")}
          </Button>
          <Button
            variant="view"
            size="sm"
            className="gap-1"
            onClick={() => navigate(`/agents/${agent.id}/profile`)}
            title={t("agentDetail.page.profileTitle")}
            data-testid="agent-profile-open"
          >
            <Info className="h-4 w-4" />
            {t("agentDetail.page.profile")}
          </Button>
          {/* [2026-10-17] Message-send entry (formerly 'unified input bar', renamed 2026-11-04): activated by the top button (notice note_03cb822ee346)
              [2026-11-16 00:04 Feng] The dialog opened by clicking 'open' inside the send window does not show this button —
              otherwise clicking it would stack another send window on itself. The criterion is the source param, not 'is it embedded':
              when an agent is opened elsewhere (executions / sessions), sending a quick message is a legitimate need. */}
          {from === "message-send" ? null : (
            <Button
              size="sm"
              onClick={() => setUniversalOpen(true)}
              data-testid="universal-input-open"
            >
              <Layers className="mr-1.5 h-4 w-4" />{t("agentDetail.page.sendInfo")}
            </Button>
          )}
          {/* [2026-11-05 23:54 Feng 'let us unify this'] The close entry is unified site-wide as the X at the dialog's top right.
              The dialog **no longer renders** a '✕ close' text button (previously hideClose + this button;
              now the shell provides the X, the content does not duplicate it). Full-page mode still keeps 'back to list'. */}
          {embedded ? null : (
            <Button
              variant="back"
              size="sm"
              className="gap-1"
              onClick={() => navigate("/agents")}
              aria-label={t("agentDetail.page.backAria")}
              title={t("agentDetail.page.backToList")}
              data-testid="agent-detail-back"
            >
              <ArrowLeft className="h-4 w-4" />
              {t("agentDetail.page.back")}
            </Button>
          )}
        </div>
      </div>

      {/* [2026-10-31 Feng] Dispatch exemption bar — placed under the header, above the card flow:
          'can this agent dispatch right now' is identity info that must be visible at a glance;
          hidden inside a tab, people would never notice they had been granted the opening (or not).
          After submit it goes through refreshPageData() (re-pull the snapshot + remount the page), not just local state. */}
      <DispatchExemptPanel
        agent={agent}
        onChanged={async () => {
          await refreshPageData();
        }}
      />

      {/* Top 5-card flow (fixed, does not scroll with the list) */}
      <div className="flex shrink-0 flex-wrap gap-3" data-testid="top-cards">
        <TopCard k="tasks" active={top === "tasks"} onClick={() => setTop("tasks")}
          icon={<ListChecks className="h-3.5 w-3.5" />} title={t("agentDetail.top.tasks")} count={taskTotal} hint={t("agentDetail.top.tasksHint")} />
        <TopCard k="executions" active={top === "executions"} onClick={() => setTop("executions")}
          icon={<ReceiptText className="h-3.5 w-3.5" />} title={t("agentDetail.top.executions")}
          count={execTotal} hint={t("agentDetail.top.executionsHint", { ok: execOk, bad: execBad, rate: successRate })} />
        {/* [2026-10-16] Added the 'assistants' category (after executions) — an assistant is essentially a session */}
        <TopCard k="assistants" active={top === "assistants"} onClick={() => setTop("assistants")}
          icon={<Sparkles className="h-3.5 w-3.5" />} title={t("agentDetail.top.assistants")} count={assistants.length}
          hint={t("agentDetail.top.assistantsHint")} />
        <TopCard k="sessions" active={top === "sessions"} onClick={() => setTop("sessions")}
          icon={<MessageSquare className="h-3.5 w-3.5" />} title={t("agentDetail.top.sessions")} count={sessionTotal} hint={t("agentDetail.top.sessionsHint")} />
        <TopCard k="commands" active={top === "commands"} onClick={() => setTop("commands")}
          icon={<Zap className="h-3.5 w-3.5" />} title={t("agentDetail.top.commands")} count={cmdsLoading ? "…" : cmds.length} hint={t("agentDetail.top.commandsHint")} />
      </div>

      {/* Bottom workspace (fills the remaining height, inner scroll)
          [2026-10-19 13:20 Feng] The border color follows the selected card above (same hue, same intensity).
          A key forces a rebuild on switch, avoiding mixed colors in the transition state. */}
      <Card
        key={top}
        className={cn("flex min-h-0 flex-1 flex-col transition-colors", TOP_ACCENT[TOP_ACCENT_OF[top]].ws)}
        data-testid="bottom-workspace"
        data-accent={TOP_ACCENT_OF[top]}
      >
        <CardContent className="min-h-0 flex-1 overflow-y-auto p-4">
          {top === "executions" && (
            <TaskExecutionWorkspace
              executions={sortedExecutions}
              selected={currentExec}
              onSelect={setSelectedExec}
              frame={TOP_ACCENT[TOP_ACCENT_OF[top]].panel}
            />
          )}

          {top === "tasks" && (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" data-testid="workspace-tasks">
              {/* [2026-10-16 Feng] Task list: long rows → large grid (card grid).
                  In the old row layout, 'description' and 'run' were pushed apart by justify-between → a large blank gap;
                  with cards, content stacks vertically and the action sits at the bottom (mt-auto), removing the blank. */}
              {tasks.map((task: TaskRow) => (
                <div
                  key={task.id}
                  onClick={() => setActiveTaskId(task.id)}
                  onFocus={() => setActiveTaskId(task.id)}
                  tabIndex={0}
                  className={cn(
                    "flex flex-col gap-2 rounded-lg border bg-card p-3 outline-none transition-all",
                    activeTaskId === task.id
                      ? TOP_ACCENT[TOP_ACCENT_OF[top]].itemActive
                      : TOP_ACCENT[TOP_ACCENT_OF[top]].item,
                  )}
                  data-testid={`task-card-${task.id}`}
                  data-active={activeTaskId === task.id ? "1" : "0"}
                >
                  {/* Row 1: task_name (large text, colored background, primary identity) + run button at top right
                      [2026-11-05 21:49 Feng] Action buttons unified at the top right, no longer taking a row at the card bottom. */}
                  <div className="flex items-start justify-between gap-2">
                    <span
                      className="min-w-0 truncate rounded-md bg-primary px-2 py-0.5 text-sm font-semibold text-primary-foreground shadow-sm"
                      data-testid={`task-name-${task.id}`}
                    >
                      {task.name || t("agentDetail.tasks.unnamed")}
                    </span>
                    <div className="flex shrink-0 items-center gap-1" onClick={(e) => e.stopPropagation()}>
                      {/* [2026-10-11] watchdog_alert is a watchdog-only task and has no manual run entry (the task is still shown). */}
                      {String(task.labs) !== "watchdog_alert" && (
                        <Button size="sm" variant="run" onClick={() => handleRunTask(task)} data-testid={`task-run-${task.id}`}>
                          <Play className="h-3.5 w-3.5" /> {t("agentDetail.tasks.run")}
                        </Button>
                      )}
                      {String(task.labs) === "watchdog_alert" && (
                        <span className="text-[11px] text-muted-foreground" data-testid={`task-norun-${task.id}`}>{t("agentDetail.tasks.watchdogOnly")}</span>
                      )}
                    </div>
                  </div>
                  {/* Description: limited to 3 lines, so uneven lengths do not break the grid */}
                  <p className="line-clamp-3 text-sm leading-relaxed text-foreground/90" data-testid={`task-desc-${task.id}`}>
                    {task.description}
                  </p>
                  {/* Bottom block (mt-auto pinned): metadata only now (the run button moved up to the title row's top right) */}
                  <div className="mt-auto flex items-center gap-2 pt-1">
                    <div className="inline-flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1 rounded-md border border-border px-2 py-1 text-xs" data-testid={`task-meta-${task.id}`}>
                      <span className={cn("font-medium", labsTone(String(task.labs)))} data-testid={`task-labs-${task.id}`}>{task.labs}</span>
                      <span className="text-border">|</span>
                      <span
                        className={cn("font-medium", task.isPrivate ? "text-warning" : "text-success")}
                        data-testid={`task-vis-${task.id}`}
                      >
                        {task.isPrivate ? t("agentDetail.tasks.private") : t("agentDetail.tasks.shared")}
                      </span>
                      <span className="text-border">|</span>
                      <span className="text-muted-foreground">{task.updatedAt || "—"}</span>
                    </div>
                  </div>
                </div>
              ))}
              {tasks.length === 0 && (
                <div className="p-6 text-sm text-muted-foreground sm:col-span-2 lg:col-span-3">
                  {taskTotal > 0
                    ? t("agentDetail.tasks.allHint", { n: taskTotal })
                    : t("agentDetail.tasks.none")}
                </div>
              )}
            </div>
          )}

          {top === "sessions" && <SessionWorkspace bundle={bundle} frame={TOP_ACCENT[TOP_ACCENT_OF.sessions].panel} />}

          {top === "assistants" && <AssistantWorkspace bundle={bundle} frame={TOP_ACCENT[TOP_ACCENT_OF.assistants].panel} />}

          {top === "commands" && (
            <QuickCommandsWorkspace
              agentId={id}
              commands={cmds}
              loading={cmdsLoading}
              error={cmdsError}
              onReload={loadCmds}
              onRun={setRunCmd}
            />
          )}
        </CardContent>
      </Card>

      {/* Dialog: execute task (pick session → description/attributes → execution feedback) */}
      {runTask && (
        <RunTaskDialog
          agentId={agent.id}
          agentName={agent.name}
          task={runTask}
          sessions={sessions}
          onClose={() => setRunTask(null)}
        />
      )}

      {/* Dialog: [ownership rework] this task has a quick command → the generic 'message send' window (command/executor auto-locked) */}
      <QuickCmdSendDialog
        cmd={quickOpen ? quickCmd : null}
        open={quickOpen && !!quickTask && !!quickCmd}
        onOpenChange={(o) => {
          setQuickOpen(o);
          if (!o) { setQuickTask(null); setQuickCmd(null); }
        }}
      />

      {/* Dialog: [2026-10-14] quick commands tab → run (same window, command locked) */}
      <QuickCmdSendDialog
        cmd={runCmd}
        open={!!runCmd}
        onOpenChange={(o) => { if (!o) setRunCmd(null); }}
      />

      {/* Dialog: [2026-10-17] message-send dialog (formerly Universal Input Bar, the full four-layer set)
          — this page is the 'full set' entry: prefills the current agent, everything else selectable. */}
      <MessageSendDialog
        open={universalOpen}
        onOpenChange={setUniversalOpen}
        initial={{ senderAgent: agent.id, targetAgent: agent.id }}
        /* [2026-11-15 15:42 Feng] Same as above: enter this agent's send window; the project defaults to the one it belongs to */
        initialProject={agent.project_id}
        /* ★ 2026-11-06 22:0x Feng's real test path: send one 'needs human confirmation' task → go to system status →
           the board number (realtime) says one, but the opened list (store snapshot) has none.
           Re-pull right after sending, so 'what was just sent is not invisible to its own sender'.
           Use refreshPageData rather than hydrateFromApi: this page's list is a useMemo over the store,
           only a remount re-reads it; the dialog is already closed by then, so a remount interrupts nothing. */
        onSent={() => {
          /* Only swap the store + recompute this page, no page remount (rationale in the dataTick comment above) */
          void refreshStoreOnce(true).then(() => setDataTick((t) => t + 1));
        }}
      />

      {/* Dialog: edit agent (PATCH → MCP agx_update_agent).
          After saving, re-pull the store once and recompute this page: name/status/host all change in the header and list,
          without a refresh people assume the save failed. Swap the store only, no page remount (same as the dataTick comment). */}
      {editProfile && (
        <AgentEditDialog
          profile={editProfile}
          open={!!editProfile}
          onOpenChange={(v) => { if (!v) setEditProfile(null); }}
          onSaved={() => {
            void refreshStoreOnce(true).then(() => setDataTick((t) => t + 1));
          }}
        />
      )}

      {/* Dialog: view all executions */}
      {dialogOpen === "executions" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={() => setDialogOpen(null)}>
          <Card className="max-h-[80vh] w-full max-w-2xl overflow-auto" onClick={(e) => e.stopPropagation()}>
            <CardHeader><CardTitle className="text-sm">{t("agentDetail.tasks.allExecs")}</CardTitle></CardHeader>
            <CardContent className="flex flex-col gap-2">
              {sortedExecutions.map((e) => (
                <button key={e.run_uuid} className="flex w-full items-center justify-between rounded-md border p-2 text-left text-sm hover:bg-accent"
                  onClick={() => { setSelectedExec(e); setTop("executions"); setDialogOpen(null); }}>
                  <span className="truncate">{e.execName}</span>
                  <span className="flex items-center gap-2 text-xs text-muted-foreground">{e.startedAt}<StatusPill status={e.status} /></span>
                </button>
              ))}
            </CardContent>
          </Card>
        </div>
      )}
      </div>
    </div>
  );
}
