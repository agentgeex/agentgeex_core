/**
 * components/agentgeex/page-dialogs.tsx — dialogs opened from each number on the system status page
 * (one per spot, all using the same structure)
 * ────────────────────────────────────────────────────────────────
 * [2026-11-06 00:03 Feng]
 *   "This agent one works... do the same for all the others. This feature is very powerful, very useful."
 *   "Add a check: if it is 0, skip it, clicking into a 0 is useless."
 *
 * Every dialog says the same thing: `<PageDialog>` wrapping **that page's body** (XBoard embedded),
 * with the query conditions passed as props. **Content is never redrawn** — this rule dates from the
 * 23:39 rejection.
 *
 * Handling of 0 is not here: whether a cell is clickable is decided by the caller (system.tsx) — at 0 there is
 * no onClick at all, because "clickable but empty" is more confusing than "not clickable".
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import {
  Bot, CalendarClock, FolderKanban, MessageSquare, ListChecks, Server, Radio,
} from "lucide-react";
import { PageDialog } from "@/components/agentgeex/page-dialog";
import { AgentsBoard } from "@/pages/agentgeex/agents";
import { SessionsBoard } from "@/pages/agentgeex/sessions";
import { ExecutionsBoard } from "@/pages/agentgeex/executions";
import { PlansBoard } from "@/pages/agentgeex/plans";
import { TasksBoard } from "@/pages/agentgeex/tasks";
import { ProjectsBoard } from "@/pages/agentgeex/projects";

/* ── host card: sessions / executions ─────────────────────────────────────── */

export function HostSessionsDialog({
  hostId, count, open, onOpenChange,
}: { hostId: string; count?: number; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation();
  return (
    <PageDialog
      open={open}
      onOpenChange={onOpenChange}
      testId="sessions-dialog"
      icon={<Server className="h-4 w-4 text-muted-foreground" />}
      title={<span className="font-mono">{hostId}</span>}
      subtitle={
        typeof count === "number"
          ? t("pageDialogs.hostSessionsCount", { count })
          : t("pageDialogs.hostSessions")
      }
    >
      <SessionsBoard initialFilters={{ __host: hostId }} embedded />
    </PageDialog>
  );
}

export function HostExecutionsDialog({
  hostId, count, open, onOpenChange,
}: { hostId: string; count?: number; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation();
  return (
    <PageDialog
      open={open}
      onOpenChange={onOpenChange}
      testId="executions-dialog"
      icon={<Server className="h-4 w-4 text-muted-foreground" />}
      title={<span className="font-mono">{hostId}</span>}
      subtitle={
        typeof count === "number"
          ? t("pageDialogs.hostExecutionsCount", { count })
          : t("pageDialogs.hostExecutions")
      }
    >
      <ExecutionsBoard initialFilters={{ __host: hostId }} embedded />
    </PageDialog>
  );
}

/* ── task execution ranking: one agent's cell ─────────────────────────────── */

/**
 * [2026-11-06 11:10 Feng] "Task execution ranking, one of these agent tasks in here must be clickable"
 *
 * ★ There are **two** conditions: agentId + last 7 days.
 *   The card SQL is `WHERE created_at >= NOW() - INTERVAL 7 DAY GROUP BY agent_id`;
 *   missing the time condition shows the full set on click (5000+ instead of 177) — that is exactly
 *   "the numbers do not match".
 */
export function RankExecutionsDialog({
  agentName, agentId, count, open, onOpenChange,
}: { agentName: string; agentId: string; count?: number; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation();
  return (
    <PageDialog
      open={open}
      onOpenChange={onOpenChange}
      testId="executions-dialog"
      icon={<ListChecks className="h-4 w-4 text-muted-foreground" />}
      title={t("pageDialogs.agentExec", { name: agentName })}
      subtitle={
        typeof count === "number"
          ? t("pageDialogs.agentExecSubCount", { count })
          : t("pageDialogs.agentExecSub")
      }
    >
      <ExecutionsBoard initialFilters={{ agentId, __range: "7d" }} embedded />
    </PageDialog>
  );
}

/* ── live session status: status cell / watermark cell / host cell ─────────────────── */

/**
 * [2026-11-06 11:10 Feng] "Run — that is, the live drawing status — make all these links work too...
 *   including the context below, the top few"
 *
 * ★ The definition must be stated: the numbers on the card come from **Redis agx:gw:*** (entities the gateway
 *   really has right now), the list comes from **MySQL agx_session** (kept forever once registered).
 *   Of the 2415 gateway entities, 347 were never registered in MySQL → **the two numbers are inherently unequal**.
 *   So the subtitle writes both out and never pretends they are equal (what Feng hates most is
 *   "numbers do not match and nobody explains why").
 */
export function RuntimeSessionsDialog({
  label, status, count, open, onOpenChange,
}: { label: string; status: string; count?: number; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation();
  return (
    <PageDialog
      open={open}
      onOpenChange={onOpenChange}
      testId="sessions-dialog"
      icon={<Radio className="h-4 w-4 text-muted-foreground" />}
      title={t("pageDialogs.runtimeSessions", { label })}
      subtitle={t("pageDialogs.runtimeSessionsSub", { count: count ?? 0 })}
    >
      <SessionsBoard initialFilters={{ __runtime: status }} embedded />
    </PageDialog>
  );
}

export function ContextSessionsDialog({
  label, bucket, count, open, onOpenChange,
}: { label: string; bucket: string; count?: number; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation();
  return (
    <PageDialog
      open={open}
      onOpenChange={onOpenChange}
      testId="sessions-dialog"
      icon={<Radio className="h-4 w-4 text-muted-foreground" />}
      title={t("pageDialogs.contextSessions", { label })}
      subtitle={t("pageDialogs.contextSessionsSub", { count: count ?? 0 })}
    >
      <SessionsBoard initialFilters={{ __ctxBucket: bucket }} embedded />
    </PageDialog>
  );
}

/* ── the three metrics on the agent card (sessions / tasks / executions) ─────────────── */

/**
 * [2026-11-06 11:22 Feng] "Do not open another window from that navbar for the popup. Why?
 *   Once you open a window the focus cannot get back, since a popup can return, right."
 *
 * The three metrics used to be `<Link to="/sessions?agent=xx">` —— **full page navigation**,
 * so viewing several agents in a row from the agent grid meant jumping back and forth.
 * Now all three are dialogs: still the same three page bodies, with agentId passed as the condition.
 * Close and you are back where you were, with the previously selected card still selected
 * (focus also returns to it, see agents.tsx).
 */
type AgentMetricProps = {
  agentName: string;
  agentId: string;
  count?: number;
  open: boolean;
  onOpenChange: (o: boolean) => void;
};

export function AgentSessionsDialog({ agentName, agentId, count, open, onOpenChange }: AgentMetricProps) {
  const { t } = useTranslation();
  return (
    <PageDialog
      open={open}
      onOpenChange={onOpenChange}
      testId="sessions-dialog"
      icon={<MessageSquare className="h-4 w-4 text-muted-foreground" />}
      title={t("pageDialogs.agentSessions", { name: agentName })}
      subtitle={t("pageDialogs.agentSessionsSub", { count: count ?? 0 })}
    >
      <SessionsBoard initialFilters={{ agentId }} embedded />
    </PageDialog>
  );
}

export function AgentTasksDialog({ agentName, agentId, count, open, onOpenChange }: AgentMetricProps) {
  const { t } = useTranslation();
  return (
    <PageDialog
      open={open}
      onOpenChange={onOpenChange}
      testId="tasks-dialog"
      icon={<ListChecks className="h-4 w-4 text-muted-foreground" />}
      title={t("pageDialogs.agentTasks", { name: agentName })}
      subtitle={t("pageDialogs.agentTasksSub", { count: count ?? 0 })}
    >
      <TasksBoard initialFilters={{ agentId }} embedded />
    </PageDialog>
  );
}

export function AgentExecutionsDialog({ agentName, agentId, count, open, onOpenChange }: AgentMetricProps) {
  const { t } = useTranslation();
  return (
    <PageDialog
      open={open}
      onOpenChange={onOpenChange}
      testId="executions-dialog"
      icon={<ListChecks className="h-4 w-4 text-muted-foreground" />}
      title={t("pageDialogs.agentRuns", { name: agentName })}
      subtitle={t("pageDialogs.agentRunsSub", { count: count ?? 0 })}
    >
      <ExecutionsBoard initialFilters={{ agentId }} embedded />
    </PageDialog>
  );
}

/* ── dashboard cards: status cells ───────────────────────────────────────── */

export function StatusExecutionsDialog({
  label, status, count, open, onOpenChange,
}: { label: string; status: string; count?: number; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation();
  return (
    <PageDialog
      open={open}
      onOpenChange={onOpenChange}
      testId="executions-dialog"
      icon={<ListChecks className="h-4 w-4 text-muted-foreground" />}
      title={t("pageDialogs.execStatus", { label })}
      subtitle={
        typeof count === "number"
          ? t("pageDialogs.execStatusSubCount", { status, count })
          : t("pageDialogs.execStatusSub", { status })
      }
    >
      <ExecutionsBoard initialFilters={{ status }} embedded />
    </PageDialog>
  );
}

export function StatusPlansDialog({
  label, status, count, open, onOpenChange,
}: { label: string; status: string; count?: number; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation();
  return (
    <PageDialog
      open={open}
      onOpenChange={onOpenChange}
      testId="plans-dialog"
      icon={<CalendarClock className="h-4 w-4 text-muted-foreground" />}
      title={t("pageDialogs.planStatus", { label })}
      subtitle={
        typeof count === "number"
          ? t("pageDialogs.planStatusSubCount", { status, count })
          : t("pageDialogs.planStatusSub", { status })
      }
    >
      <PlansBoard initialFilters={{ status }} embedded />
    </PageDialog>
  );
}

export function StatusAgentsDialog({
  label, status, count, open, onOpenChange,
}: { label: string; status: string; count?: number; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation();
  return (
    <PageDialog
      open={open}
      onOpenChange={onOpenChange}
      testId="agents-status-dialog"
      icon={<Bot className="h-4 w-4 text-muted-foreground" />}
      title={t("pageDialogs.agentStatus", { label })}
      subtitle={
        typeof count === "number"
          ? t("pageDialogs.planStatusSubCount", { status, count })
          : t("pageDialogs.planStatusSub", { status })
      }
    >
      <AgentsBoard initialStatus={status} embedded />
    </PageDialog>
  );
}

export function StatusProjectsDialog({
  label, status, count, open, onOpenChange,
}: { label: string; status: string; count?: number; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation();
  return (
    <PageDialog
      open={open}
      onOpenChange={onOpenChange}
      testId="projects-dialog"
      icon={<FolderKanban className="h-4 w-4 text-muted-foreground" />}
      title={t("pageDialogs.projectStatus", { label })}
      subtitle={
        typeof count === "number"
          ? t("pageDialogs.planStatusSubCount", { status, count })
          : t("pageDialogs.planStatusSub", { status })
      }
    >
      <ProjectsBoard initialFilters={{ status }} embedded />
    </PageDialog>
  );
}

/* ── active sessions ───────────────────────────────────────────────── */

export function ActiveSessionsDialog({
  count, open, onOpenChange,
}: { count?: number; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation();
  return (
    <PageDialog
      open={open}
      onOpenChange={onOpenChange}
      testId="sessions-dialog"
      icon={<MessageSquare className="h-4 w-4 text-muted-foreground" />}
      title={t("pageDialogs.activeSessions")}
      /* count defaults to 0 (not "—"): a count placeholder can only be a number, so the copy matches both sides */
      subtitle={t("pageDialogs.activeSessionsSub", { count: count ?? 0 })}
    >
      <SessionsBoard embedded />
    </PageDialog>
  );
}
