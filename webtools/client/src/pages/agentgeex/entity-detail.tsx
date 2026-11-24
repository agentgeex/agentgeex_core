/**
 * pages/agentgeex/entity-detail.tsx — unified entity detail page
 * ────────────────────────────────────────────────────────────────
 * [2026-10-26 Feng] "you can't see the detail of anything; you need a page where its detail can be seen"
 *
 * Covers 10 entity kinds (routes in App.tsx):
 *   project / task / session / execution / skill / notice / host / plan / project group / quick command
 *
 * Design trade-offs:
 *   - **One page, many shapes**: a shared skeleton (DetailShell + Descriptions), fields and topic blocks vary by kind.
 *     Writing ten sets → font sizes/spacings inevitably drift (historically the section title drifted twice).
 *   - **Two data sources**:
 *       ① already complete in the list snapshot (session/execution/skill/notice/host/plan/project group) → read the
 *          store directly, zero extra requests
 *       ② "heavy fields" deliberately excluded from the list snapshot (project x_desc, task description x_desc) →
 *          fetched from the detail API separately
 *     The goal is not to grow the 24MB snapshot further.
 *   - **Raw data fallback**: each page has a collapsed "raw data" at the bottom, so uncovered fields can still be seen.
 */
import * as React from "react";
import { useParams, Link } from "react-router-dom";
import { Pencil, Play, RefreshCw, Loader2 } from "lucide-react";
import {
  DetailShell, DetailSection, RawBlock, DetailNotFound,
} from "@/components/agentgeex/detail-shell";
import { AgentMemberChips } from "@/components/agentgeex/agent-member-chips";
import { StatusPill } from "@/components/agentgeex/data-table";
import { ContextUsage, SessionStatusDot } from "@/components/agentgeex/session-runtime-badge";
import { EntityLogo } from "@/components/agentgeex/entity-logo";
import { AgentAvatar } from "@/components/agentgeex/agent-avatar";
import { ProjectEditDialog } from "@/components/agentgeex/project-edit-dialog";
import { SourceDirField } from "@/components/agentgeex/source-dir";
import { OpenClawSessionLink } from "@/components/agentgeex/openclaw-session-link";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Descriptions, DescriptionsItem } from "@/components/ui/descriptions";
import { RichTextView } from "@/components/ui/rich-text-view";
import {
  listProjects, listTasks, listSessions, listExecutions, listPlans,
  listProjectGroups, getProjectName, listAgents, listHosts,
} from "@/providers/agentgeex";
import { projectsApi, tasksApi, quickCmdApi, executionsApi, type ProjectDetail, type TaskDetail, type QuickCmd, type ConfirmAction } from "@/providers/api";
import { HumanConfirmDialog, type HumanConfirmTarget } from "@/components/agentgeex/human-confirm-dialog";
import { useToast } from "@/components/ui/toast";
import { refreshPageData } from "@/providers/agentgeex";
import { skills, notices } from "@/mock/agentgeex";
import { platformMeta, PlatformLogo, platformLabel } from "@/components/agentgeex/platform-logo";
import { OpenClawLink } from "@/components/agentgeex/openclaw-link";
import { EntityRef, type DetailKind } from "@/components/agentgeex/detail-popup";
import AgentDetailPage from "@/pages/agentgeex/agent-detail";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";
import { apiErrorText } from "@/lib/api-error";

export type EntityKind =
  | "project" | "task" | "session" | "execution"
  | "skill" | "notice" | "host" | "plan" | "project-group" | "quick-cmd";

/** List page of each entity kind (back button + fallback for a non-existent entity) */
export const ENTITY_META: Record<EntityKind, { listPath: string; labelKey: string }> = {
  project: { listPath: "/projects", labelKey: "entityDetail.notFound.project" },
  task: { listPath: "/tasks", labelKey: "entityDetail.notFound.task" },
  session: { listPath: "/sessions", labelKey: "entityDetail.notFound.session" },
  execution: { listPath: "/executions", labelKey: "entityDetail.notFound.execution" },
  skill: { listPath: "/skills", labelKey: "entityDetail.notFound.skill" },
  notice: { listPath: "/notices", labelKey: "entityDetail.notFound.notice" },
  host: { listPath: "/hosts", labelKey: "entityDetail.notFound.host" },
  plan: { listPath: "/plans", labelKey: "entityDetail.notFound.plan" },
  "project-group": { listPath: "/project-groups", labelKey: "entityDetail.notFound.projectGroup" },
  "quick-cmd": { listPath: "/quick-cmds", labelKey: "entityDetail.notFound.quickCmd" },
};

/* -- Descriptions value renderers ----------------------------------
   The three most common value shapes become small components; pages only write fields, no per-cell tailwind strings:

   · `Mono`  — values where characters must align to read right (id / time / port) (13px mono;
               break-all lets a long id wrap inside a narrow cell instead of widening the column)
   · `Num`   — counts (tabular-nums, digits align vertically)
   · `AgentRef` — an Agent: name (clickable) + a grey id line below

   Empty values always return null, **left to the Descriptions primitive to render as `-` uniformly**.
   Pages do not each write a placeholder — historically "—", "-" and "(empty)" were mixed. */
function Mono({ children, breakAll = true }: { children?: React.ReactNode; breakAll?: boolean }) {
  if (children === null || children === undefined || children === "") return null;
  return <span className={cn("font-mono text-[13px]", breakAll && "break-all")}>{children}</span>;
}

function Num({ children }: { children?: React.ReactNode }) {
  if (children === null || children === undefined || children === "") return null;
  return <span className="tabular-nums">{children}</span>;
}

/** Agent reference: name (link) + grey id.
   "Owning Agent" and "Agent name" used to be two cells describing the same thing,
   merged into one — saves space and avoids "name here, empty id there" inconsistency. */
function AgentRef({ id, name }: { id?: string | null; name?: string | null }) {
  if (!id) return null;
  return (
    <span className="flex min-w-0 flex-col gap-0.5">
      <EntityRef kind="agent" id={id} label={name || id} mono={!name} />
      {name ? <span className="break-all font-mono text-[11px] text-muted-foreground">{id}</span> : null}
    </span>
  );
}

export default function EntityDetailPage({ kind }: { kind: EntityKind }) {
  const { id = "" } = useParams();
  const decoded = React.useMemo(() => {
    try {
      return decodeURIComponent(id);
    } catch {
      return id;
    }
  }, [id]);

  return <EntityDetailBody kind={kind} id={decoded} />;
}

/**
 * Detail content body (without routing): shared by page and dialog —
 * page reads useParams and passes it in; the dialog is passed kind/id directly by DetailPopupProvider.
 * [2026-10-29] added `agent`: the dialog must also show agents (via AgentDetailPage embedded mode).
 */
export function EntityDetailBody({
  kind,
  id,
  from,
}: {
  kind: DetailKind;
  id: string;
  /** [2026-11-16 00:04] Origin marker, passed to the content as-is — e.g. agent detail uses it to hide action buttons */
  from?: string;
}) {
  const { t } = useTranslation();
  switch (kind) {
    case "project":
      return <ProjectDetailView id={id} />;
    case "task":
      return <TaskDetailView id={id} />;
    case "session":
      return <SessionDetailView id={id} />;
    case "execution":
      return <ExecutionDetailView id={id} />;
    case "skill":
      return <SkillDetailView id={id} />;
    case "notice":
      return <NoticeDetailView id={id} />;
    case "host":
      return <HostDetailView id={id} />;
    case "plan":
      return <PlanDetailView id={id} />;
    case "project-group":
      return <ProjectGroupDetailView id={id} />;
    case "quick-cmd":
      return <QuickCmdDetailView id={id} />;
    case "agent":
      return <AgentDetailPage agentId={id} embedded from={from} />;
    default:
      return <DetailNotFound what={t("entityDetail.notFound.any")} backTo="/" />;
  }
}

/* ══════════════════════════ Project ══════════════════════════ */

/** Project detail: the description is the focus (it is what an Agent reads at startup) + editing (via MCP) */
function ProjectDetailView({ id }: { id: string }) {
  const { t } = useTranslation();
  const [detail, setDetail] = React.useState<ProjectDetail | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [editOpen, setEditOpen] = React.useState(false);

  // The row from the list snapshot (render the skeleton first to avoid a blank screen; heavy fields filled in when the detail API returns)
  const row = React.useMemo(() => listProjects().find((p) => p.id === id), [id]);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setDetail(await projectsApi.detail(id));
    } catch (e) {
      setError((e as Error).message || String(e));
    } finally {
      setLoading(false);
    }
  }, [id]);

  React.useEffect(() => {
    void load();
  }, [load]);

  if (!row && !detail && !loading) return <DetailNotFound what={t("entityDetail.notFound.project")} backTo="/projects" />;

  const d = detail;
  const adminId = d?.adminAgentId ?? row?.adminAgentId;
  const projectFields = (
    <Descriptions>
      <DescriptionsItem label={t("entityDetail.field.projectName")}>{d?.name ?? row?.name}</DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.abbr")}>
        <Mono>{d?.abbr ?? row?.abbr}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.platform")}>{platformMeta(d?.platform ?? row?.platform).label}</DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.status")}>
        <StatusPill status={d?.status ?? row?.status ?? ""} />
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.admin")}>
        <AgentRef id={adminId} name={listAgents().find((a) => a.id === adminId)?.name} />
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.createdAt")}>
        <Mono>{d?.createdAt}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.sourceDir")} span={2}>
        {/* Has a path → copyable + open in Finder (via BFF, not a dead file:// link) */}
        {d ? <SourceDirField projectId={id} path={d.sourceDir} /> : loading ? t("entityDetail.common.loading") : null}
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.logo")} span={2}>
        <Mono>{d?.logoUrl ?? row?.logoUrl}</Mono>
      </DescriptionsItem>
    </Descriptions>
  );

  return (
    <DetailShell
      backTo="/projects"
      backLabel={t("entityDetail.back.projects")}
      testId="page-project-detail"
      leading={
        <EntityLogo
          kind="project"
          id={id}
          name={d?.name ?? row?.name ?? id}
          logoUrl={d?.logoUrl ?? row?.logoUrl}
          className="h-10 w-10"
        />
      }
      title={d?.name ?? row?.name ?? id}
      subtitle={id}
      badges={
        <>
          <Badge variant="outline" className="font-mono text-[11px]">
            {platformMeta(d?.platform ?? row?.platform).label}
          </Badge>
          <StatusPill status={d?.status ?? row?.status ?? ""} />
          {loading && (
            <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" /> {t("entityDetail.common.loadingDetail")}
            </span>
          )}
        </>
      }
      actions={
        <>
          <Button variant="outline" size="sm" className="gap-1.5" onClick={() => void load()}>
            <RefreshCw className="h-4 w-4" /> {t("entityDetail.common.refresh")}
          </Button>
          <Button
            size="sm"
            variant="edit"
            className="gap-1.5"
            onClick={() => setEditOpen(true)}
            disabled={!d}
            data-testid="project-detail-edit"
          >
            <Pencil className="h-4 w-4" /> {t("entityDetail.common.edit")}
          </Button>
        </>
      }
    >
      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {t("entityDetail.common.failed", { msg: error })}
        </div>
      )}

      <DetailSection title={t("entityDetail.common.basic")} testId="project-section-basic">
        {projectFields}
      </DetailSection>

      {d && (
        <DetailSection
          title={t("entityDetail.project.scale")}
          hint={t("entityDetail.project.scaleHint")}
          testId="project-section-scale"
        >
          <div className="grid grid-cols-3 gap-3 text-center">
            {[
              { k: t("entityDetail.project.agents"), v: d.counts.agents },
              { k: t("entityDetail.project.tasks"), v: d.counts.tasks },
              { k: t("entityDetail.project.sessions"), v: d.counts.sessions },
            ].map((x) => (
              <div key={x.k} className="rounded-md border bg-muted/20 px-3 py-2">
                <div className="text-lg font-semibold">{x.v}</div>
                <div className="text-[11px] text-muted-foreground">{x.k}</div>
              </div>
            ))}
          </div>
        </DetailSection>
      )}

      <DetailSection
        title={t("entityDetail.project.xdesc")}
        hint={t("entityDetail.project.xdescHint")}
        testId="project-section-xdesc"
      >
        <RichTextView
          value={d?.xDesc}
          testId="project-xdesc"
          emptyText={loading ? t("entityDetail.common.loading") : t("entityDetail.project.xdescEmpty")}
        />
      </DetailSection>

      {d && <RawBlock data={d} label={t("entityDetail.common.rawProject")} />}

      {d && (
        <ProjectEditDialog
          project={d}
          open={editOpen}
          onOpenChange={setEditOpen}
          onSaved={() => void load()}
        />
      )}
    </DetailShell>
  );
}

/* ══════════════════════════ Task ══════════════════════════ */

function TaskDetailView({ id }: { id: string }) {
  const { t } = useTranslation();
  const [detail, setDetail] = React.useState<TaskDetail | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const row = React.useMemo(() => listTasks().find((t) => t.id === id), [id]);

  React.useEffect(() => {
    let alive = true;
    setLoading(true);
    tasksApi
      .detail(id)
      .then((d) => alive && setDetail(d))
      .catch((e) => alive && setError((e as Error).message || String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [id]);

  if (!row && !detail && !loading) return <DetailNotFound what={t("entityDetail.notFound.task")} backTo="/tasks" />;

  const d = detail;
  const taskAgentId = d?.agentId ?? row?.agentId;
  const taskFields = (
    <Descriptions>
      <DescriptionsItem label={t("entityDetail.field.taskName")}>
        <Mono>{d?.name ?? row?.name}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.taskGroup")}>
        <Mono>{d?.labs ?? row?.labs}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.ownerAgent")}>
        <AgentRef id={taskAgentId} name={d?.agentName ?? row?.agentName} />
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.taskType")}>
        <Mono>{d?.taskType}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.priority")}>
        <Num>{d?.priority ? String(d.priority) : null}</Num>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.dedicatedSession")}>
        {d ? (d.dedicatedSession ? t("entityDetail.task.dedicatedYes") : t("entityDetail.task.dedicatedNo")) : null}
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.visibility")}>
        {d ? (d.isPrivate ? t("entityDetail.task.private") : t("entityDetail.task.shared")) : null}
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.activeStatus")}>
        {d ? (d.activeStatus ? t("entityDetail.common.effective") : t("entityDetail.common.disabled")) : null}
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.createdAt")}>
        <Mono>{d?.createdAt}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.updatedAt")}>
        <Mono>{d?.updatedAt ?? row?.updatedAt}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.replyMode")} span={2}>
        {(d?.replyMode ?? row?.replyMode) && (
          // Two lines: the top is this document's value, the bottom maps the three values to their meanings.
          // Side by side would read "confirm result = …", as if the value and the explanation were glued together.
          <span className="flex flex-col gap-1">
            <Mono breakAll={false}>{d?.replyMode ?? row?.replyMode}</Mono>
            <span className="text-[12px] text-muted-foreground">
              {t("entityDetail.task.replyModeHint")}
            </span>
          </span>
        )}
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.taskId")} span={2}>
        <Mono>{id}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.desc")} span={2}>
        {(d?.description ?? row?.description) && (
          <p className="max-w-prose whitespace-pre-line leading-relaxed">{d?.description ?? row?.description}</p>
        )}
      </DescriptionsItem>
    </Descriptions>
  );

  return (
    <DetailShell
      backTo="/tasks"
      backLabel={t("entityDetail.back.tasks")}
      testId="page-task-detail"
      leading={
        <EntityLogo
          kind="task"
          id={id}
          labs={d?.labs ?? row?.labs}
          name={d?.name ?? row?.name ?? id}
          logoUrl={d?.logoUrl ?? row?.logoUrl}
          className="h-10 w-10"
        />
      }
      title={d?.name ?? row?.name ?? id}
      subtitle={id}
      badges={
        <>
          <Badge variant="outline" className="font-mono text-[11px]">
            {d?.labs ?? row?.labs ?? "general"}
          </Badge>
          {loading && (
            <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" /> {t("entityDetail.task.loadingXdesc")}
            </span>
          )}
        </>
      }
      /* [2026-11-03] Same criterion as "session detail": the "owning Agent" in the basic info below
            is already a clickable link (AgentRef → dialog), so the header does not repeat the same entry. */
    >
      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {t("entityDetail.common.failed", { msg: error })}
        </div>
      )}

      <DetailSection title={t("entityDetail.common.basic")} testId="task-section-basic">
        {taskFields}
      </DetailSection>

      <DetailSection
        title={t("entityDetail.task.xdesc")}
        hint={t("entityDetail.task.xdescHint")}
        testId="task-section-xdesc"
      >
        <RichTextView
          value={d?.xDesc}
          testId="task-xdesc"
          emptyText={loading ? t("entityDetail.common.loading") : t("entityDetail.task.xdescEmpty")}
        />
      </DetailSection>

      {d?.payload != null && (
        <DetailSection title={t("entityDetail.task.payload")} hint={t("entityDetail.task.payloadHint")} testId="task-section-payload">
          <RichTextView value={d.payload} mode="text" testId="task-payload" emptyText={t("entityDetail.task.payloadEmpty")} />
        </DetailSection>
      )}

      {d && <RawBlock data={d} label={t("entityDetail.common.rawTask")} />}
    </DetailShell>
  );
}

/* ══════════════════════════ Session ══════════════════════════ */

function SessionDetailView({ id }: { id: string }) {
  const { t } = useTranslation();
  const row = React.useMemo(() => listSessions().find((s) => s.id === id), [id]);
  if (!row) return <DetailNotFound what={t("entityDetail.notFound.session")} backTo="/sessions" />;

  const type = row.sessionType ?? "main";
  const TYPE_LABEL: Record<string, string> = {
    main: t("entityDetail.session.type.main"),
    task: t("entityDetail.session.type.task"),
    case: t("entityDetail.session.type.case"),
    oneshot: t("entityDetail.session.type.oneshot"),
    assistant: t("entityDetail.session.type.assistant"),
  };
  // Session id looks like agent:<agent>:<name> — splitting it is easier to read
  const [, mid, ...rest] = row.id.split(":");
  const sessionName = rest.length ? rest.join(":") : "";

  const sessionFields = (
    <Descriptions>
      <DescriptionsItem label={t("entityDetail.field.sessionName")}>{row.name}</DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.sessionType")}>{TYPE_LABEL[type] ?? type}</DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.ownerAgent")}>
        <AgentRef id={row.agentId} name={row.agentName} />
      </DescriptionsItem>
      {/* [2026-11-05 17:08 Feng] "drop the status in the detail too, but put the running status in.
             Running status and context share must go into this detail, because that is a very important state"
             — the detail page is where "I want to look at this one carefully", so what belongs there is **is it running
               now, how much context is left**, not "is this record still registered" (active/archived registration
               status, also removed from the list).
             ★ Both cells use the same atom as the list (session-runtime-badge), so definition/color/"—" rules do not drift. */}
      {/* "Running host": which machine this session is on right now (a Redis gateway-side fact).
             ★ Also makes the field count even: Descriptions is a 4-column table (label/value/label/value);
               an odd number of half-row items leaves an empty grey cell at the end (th[aria-hidden]) —
               the acceptance script `verify_detail_descriptions` explicitly asserts "no filler cells", that is visual garbage. */}
      <DescriptionsItem label={t("entityDetail.field.host")}>
        {row.runtime?.hostId ? <Mono>{row.runtime.hostId}</Mono> : null}
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.runStatus")}>
        <SessionStatusDot runtime={row.runtime} testId="session-detail-runtime" />
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.ctxRatio")}>
        <ContextUsage runtime={row.runtime} testId="session-detail-ctx" />
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.msgCount")}>
        <Num>{String(row.messageCount ?? 0)}</Num>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.lastActive")}>
        <Mono>{row.lastActiveAt}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.createdAt")}>
        <Mono>{row.createdAt}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.sessionSegment")}>
        <Mono>{sessionName || mid}</Mono>
      </DescriptionsItem>
      {row.isAssistant && (
        <DescriptionsItem label={t("entityDetail.field.assistantSlug")}>
          <Mono>{row.assistantSlug}</Mono>
        </DescriptionsItem>
      )}
      {row.isAssistant && (
        <DescriptionsItem label={t("entityDetail.field.creator")}>
          <AgentRef id={row.creatorAgentId} name={row.creatorName} />
        </DescriptionsItem>
      )}
      <DescriptionsItem label={t("entityDetail.field.sessionId")} span={2}>
        <Mono>{row.id}</Mono>
      </DescriptionsItem>
    </Descriptions>
  );

  return (
    <DetailShell
      backTo="/sessions"
      backLabel={t("entityDetail.back.sessions")}
      testId="page-session-detail"
      leading={<AgentAvatar name={row.agentName || row.agentId} id={row.agentId} className="h-10 w-10" />}
      title={row.name || row.id}
      subtitle={row.id}
      badges={
        <>
          <Badge variant="outline" className="text-[11px]">{TYPE_LABEL[type] ?? type}</Badge>
          {row.isAssistant && <Badge variant="outline" className="text-[11px]">{t("entityDetail.session.badge")}</Badge>}
          <SessionStatusDot runtime={row.runtime} testId="session-detail-runtime-badge" />
        </>
      }
      actions={
        /* [2026-11-03 15:56 Feng] there used to be a "view owning Agent" button — removed:
              the "owning Agent" cell below is itself clickable (AgentRef → dialog); the same action must not appear twice.
              [2026-11-03 20:12 Feng] the entry to the platform web page is unified site-wide into a **small icon**
              (same component, same icon as the session column in "execution records"), no more text buttons —
              text makes it look like "another big deal", while it is just "go look at that session's web page". */
        <OpenClawSessionLink sessionId={row.id} agentId={row.agentId} />
      }
    >
      <DetailSection title={t("entityDetail.session.info")} testId="session-section-basic">
        {sessionFields}
      </DetailSection>

      <DetailSection
        title={t("entityDetail.session.runs")}
        hint={t("entityDetail.session.runsHint")}
        testId="session-section-execs"
      >
        <ExecMiniList rows={listExecutions().filter((e) => e.sessionId === row.id).slice(0, 50)} />
      </DetailSection>

      <RawBlock data={row} label={t("entityDetail.common.rawSession")} />
    </DetailShell>
  );
}

/* ══════════════════════════ Execution ══════════════════════════ */

/** Session type short label (used by the execution detail "session" card) — pure function, t injected by the caller */
const SESSION_TYPE_KEYS = ["main", "task", "case", "assistant", "oneshot"];
function sessionTypeLabel(
  type: string | null | undefined,
  t: (key: string) => string,
): string {
  if (type && SESSION_TYPE_KEYS.includes(type)) return t(`entityDetail.session.typeShort.${type}`);
  return type || "—";
}

/** One cell in the execution detail (grid info block) */
function InfoCard({
  title,
  hint,
  children,
  testId,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
  testId?: string;
}) {
  return (
    <div
      className="flex min-w-0 flex-col gap-1.5 rounded-lg border border-border bg-muted/10 p-3"
      data-testid={testId}
    >
      {/* [2026-10-29 19:14 Feng] the title was 11px, smaller than the Agent name in the card (14px),
             looking like a note rather than a title. Title hierarchy: page title 20px > section title 16px > **card title 15px**. */}
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[15px] font-semibold leading-tight text-foreground">{title}</span>
        {hint && <span className="truncate text-[11px] text-muted-foreground">{hint}</span>}
      </div>
      <div className="min-w-0 text-sm">{children}</div>
    </div>
  );
}

/** "A person" in the grid: avatar + name + clickable ID */
function AgentCell({ agentId, agentName, avatarUrl }: { agentId?: string; agentName?: string; avatarUrl?: string }) {
  const { t } = useTranslation();
  if (!agentId) return <p className="text-[13px] text-muted-foreground">{t("entityDetail.session.noAgent")}</p>;
  return (
    <div className="flex min-w-0 items-center gap-2">
      <AgentAvatar name={agentName || agentId} id={agentId} avatarUrl={avatarUrl} className="h-8 w-8" textClassName="text-[11px]" />
      <div className="flex min-w-0 flex-col gap-0.5 leading-tight">
        {/* Name = link (same as task/session cards), ID demoted to grey meta info */}
        <EntityRef
          kind="agent"
          id={agentId}
          label={agentName || agentId}
          mono={false}
          className="text-[13px] font-medium"
        />
        <span className="truncate font-mono text-[11px] text-muted-foreground" title={agentId}>{agentId}</span>
      </div>
    </div>
  );
}

/**
 * Execution detail
 * ────────────────────────────────────────────────────────────────
 * [2026-10-29 Feng] "the execution detail layout is bad; it should be blocked: sender one, receiver one,
 * runtime data one, in grid form; everything that can be linked inside should be clickable."
 * → top 5-cell info block (sender / receiver / task / session / runtime),
 *   every entity field is an EntityRef (click = dialog detail, no navigation).
 */
function ExecutionDetailView({ id }: { id: string }) {
  const { t } = useTranslation();
  const row = React.useMemo(
    () => listExecutions().find((e) => e.run_uuid === id || e.taskId === id),
    [id],
  );

  /* -- [2026-11-06 21:2x Feng] the detail page must also be able to release --
      The list row has a "release" entry, but when a person opens the detail ("I need to look at the result first"),
      the status says "awaiting human confirm" with no button at all — that makes the only page needing human action read-only.
      ★ hooks must be written before the `if (!row)` below: calling a hook after an early return changes the hook count
        between renders of the same component, and React throws.
   -- */
  const { toast } = useToast();
  const [humanTarget, setHumanTarget] = React.useState<HumanConfirmTarget | null>(null);
  const [humanBusy, setHumanBusy] = React.useState(false);

  if (!row) return <DetailNotFound what={t("entityDetail.notFound.execution")} backTo="/executions" />;

  const needsHuman = row.status === "awaiting_confirm";

  const openHuman = () =>
    setHumanTarget({
      runUuid: row.run_uuid,
      taskName: row.taskName || row.execName,
      agentName: row.agentName || row.agentId,
      summary: row.summary,
    });

  const submitHuman = async (action: ConfirmAction) => {
    if (!humanTarget) return;
    setHumanBusy(true);
    try {
      const r = await executionsApi.confirm(humanTarget.runUuid, action);
      setHumanTarget(null);
      toast({
        title: action === "approve" ? t("entityDetail.execution.toastApprove") : t("entityDetail.execution.toastReject"),
        description:
          action === "approve"
            ? t("entityDetail.execution.toastApproveDesc", { status: r.status || "succeeded" })
            : t("entityDetail.execution.toastRejectDesc", { status: r.status || "failed" }),
        variant: action === "approve" ? "success" : "destructive",
        duration: 6500,
      });
      /* Re-fetch the snapshot and remount this page — the detail page only reads the store; without a refresh, after the
           dialog closes the badge still says "awaiting human confirm" and the user thinks it did not take effect. */
      await refreshPageData();
    } catch (e) {
      const err = e as { code?: string; message?: string; details?: Record<string, unknown> };
      const d = err.details as
        | { handledHow?: string; handledAt?: string; currentStatus?: string }
        | undefined;
      /* Same definition as the list page: 409 has three causes and must be told apart (see realdata._confirm_context).
           Saying "maybe someone released it first" sends people chasing a non-existent problem — in practice it is
           often that they themselves just released it successfully. */
      const title =
        err.code === "STATE_MISMATCH"
          ? d?.handledHow === "approved"
            ? t("entityDetail.execution.reason.alreadyApproved")
            : d?.handledHow === "rejected"
              ? t("entityDetail.execution.reason.alreadyRejected")
              : d?.handledHow === "no_confirm_needed"
                ? t("entityDetail.execution.reason.notNeeded")
                : t("entityDetail.execution.reason.notPending")
          : err.code === "NOT_FOUND"
            ? t("entityDetail.execution.reason.noRun")
            : err.code === "PERMISSION_DENIED"
              ? t("entityDetail.execution.reason.forbidden")
              : t("entityDetail.execution.toastFailed");
      const hint =
        err.code === "STATE_MISMATCH" && d?.handledHow === "approved"
          ? t("entityDetail.execution.done.succeeded", {
              at: d.handledAt ? t("entityDetail.execution.done.atApprove", { time: d.handledAt }) : "",
            })
          : err.code === "STATE_MISMATCH" && d?.handledHow === "rejected"
            ? t("entityDetail.execution.done.failed", {
                at: d.handledAt ? t("entityDetail.execution.done.atReject", { time: d.handledAt }) : "",
              })
            : err.code === "STATE_MISMATCH" && d?.handledHow === "no_confirm_needed"
              ? t("entityDetail.execution.done.notNeeded")
              : err.code === "STATE_MISMATCH"
                ? t("entityDetail.execution.done.refresh")
                : "";
      setHumanTarget(null);
      toast({
        title,
        description: hint || apiErrorText(e, t),
        variant: "destructive",
        duration: 9000,
      });
      /* Self-heal: 409 means this page's data is stale — re-fetch immediately; do not leave the "waiting for you to release" banner in place */
      if (err.code === "STATE_MISMATCH" || err.code === "NOT_FOUND") {
        try {
          await refreshPageData();
        } catch {
          /* A failed refresh must not add a second notice */
        }
      }
    } finally {
      setHumanBusy(false);
    }
  };

  const agents = listAgents();
  const recvAgent = agents.find((a) => a.id === row.agentId);
  const sendAgent = row.sendAgentId ? agents.find((a) => a.id === row.sendAgentId) : undefined;
  const taskRow = listTasks().find((t) => t.id === row.taskId);
  const planRow = row.planId ? listPlans().find((p) => p.id === row.planId) : undefined;
  const sessionRow = listSessions().find((s) => s.id === row.sessionId);
  const hostRow = recvAgent?.host_id ? listHosts().find((h) => h.id === recvAgent.host_id) : undefined;
  const projectId = recvAgent?.project_id || "";
  const isSelf = !!row.sendAgentId && row.sendAgentId === row.agentId;
  const slug = (() => {
    const parts = (row.sessionId || "").split(":");
    return parts.length >= 3 ? parts.slice(2).join(":") : row.sessionId;
  })();

  return (
    <DetailShell
      backTo="/executions"
      backLabel={t("entityDetail.back.executions")}
      testId="page-execution-detail"
      title={row.execName || row.run_uuid}
      subtitle={row.run_uuid}
      badges={<StatusPill status={row.status} />}
      /* [2026-11-03 20:12] the "open session" item in the header was removed:
           the "session" card below already has "session name (click = session detail) + small icon (go to platform)",
           two entries for the same action = back to "are these two the same?".
           ★ The platform entry is not lost — it is that icon inside the "session" card. */
    >
      {/* -- Awaiting human confirm: the release entry on the detail page --
            Placed **before** the grid, because it is the only thing this page should do right now;
            buried in the card pile (or the footer) means making people look for it. */}
      {needsHuman && (
        <div
          className="mb-3 flex flex-wrap items-center gap-3 rounded-lg border border-chart-1/40 bg-chart-1/5 px-3 py-2.5"
          data-testid="exec-human-confirm-bar"
        >
          <div className="min-w-0 flex-1">
            <p className="text-[13px] font-medium">{t("entityDetail.execution.confirmTitle")}</p>
            <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground">
              {t("entityDetail.execution.confirmBody1")}
              <span className="font-medium">{t("entityDetail.execution.confirmBodyStrong")}</span>
              {t("entityDetail.execution.confirmBody2")}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={openHuman}
              data-testid="exec-human-confirm-open"
            >
              {t("entityDetail.execution.confirmAction")}
            </Button>
          </div>
        </div>
      )}

      {/* -- Grid: who dispatched / who received / what task / which session / how it ran -- */}
      <div className="grid gap-3 pb-5 sm:grid-cols-2 xl:grid-cols-3" data-testid="exec-cards">
        <InfoCard title={t("entityDetail.execution.sender")} hint={t("entityDetail.execution.senderHint")} testId="exec-card-sender">
          <AgentCell agentId={row.sendAgentId} agentName={sendAgent?.name} avatarUrl={sendAgent?.avatarUrl} />
          {sendAgent?.role && (
            <p className="mt-2 text-[11px] text-muted-foreground">
              {t("entityDetail.execution.role")} <span className="font-mono text-foreground/90">{sendAgent.role}</span>
            </p>
          )}
          {isSelf && <p className="mt-1 text-[11px] text-muted-foreground">{t("entityDetail.execution.self")}</p>}
          {!row.sendAgentId && <p className="mt-1 text-[11px] text-muted-foreground">{t("entityDetail.execution.legacyNoSender")}</p>}
        </InfoCard>

        <InfoCard title={t("entityDetail.execution.receiver")} hint={t("entityDetail.execution.receiverHint")} testId="exec-card-receiver">
          <AgentCell agentId={row.agentId} agentName={recvAgent?.name || row.agentName} avatarUrl={recvAgent?.avatarUrl} />
          {recvAgent?.role && (
            <p className="mt-2 text-[11px] text-muted-foreground">
              {t("entityDetail.execution.role")} <span className="font-mono text-foreground/90">{recvAgent.role}</span>
            </p>
          )}
        </InfoCard>

        <InfoCard title={t("entityDetail.execution.task")} hint={t("entityDetail.execution.taskHint")} testId="exec-card-task">
          {row.taskId || row.taskName ? (
            <div className="flex flex-col gap-1">
              <EntityRef kind="task" id={row.taskId} label={row.taskName || taskRow?.name || row.taskId} mono={false} className="text-[13px] font-medium" />
              <span className="flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[11px] text-muted-foreground">
                {row.taskId && <span>{row.taskId}</span>}
                {taskRow?.labs && <span>labs={taskRow.labs}</span>}
              </span>
              {planRow && (
                <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                  {t("entityDetail.execution.plan")} <EntityRef kind="plan" id={planRow.id} label={planRow.name || planRow.id} mono={false} />
                </span>
              )}
              {taskRow?.description && (
                <span className="mt-1 line-clamp-2 border-t border-border pt-1.5 text-[11px] leading-snug text-muted-foreground">
                  {taskRow.description}
                </span>
              )}
            </div>
          ) : (
            <p className="text-[13px] text-muted-foreground">{t("entityDetail.execution.noTask")}</p>
          )}
        </InfoCard>

        <InfoCard title={t("entityDetail.execution.session")} hint={t("entityDetail.execution.sessionHint")} testId="exec-card-session">
          <div className="flex min-w-0 flex-col gap-1">
            <span className="flex min-w-0 items-center gap-2">
              <EntityRef
                kind="session"
                id={row.sessionId}
                label={sessionRow?.name || slug || row.sessionId}
                mono={false}
                className="text-[13px] font-medium"
              />
              <OpenClawSessionLink sessionId={row.sessionId} agentId={row.agentId} />
            </span>
            <span className="truncate font-mono text-[11px] text-muted-foreground" title={row.sessionId}>
              {row.sessionId || "—"}
            </span>
            <div className="mt-1 flex flex-col gap-0.5 border-t border-border pt-1.5 text-[11px] text-muted-foreground">
              <span>{t("entityDetail.execution.sessionType")} <span className="text-foreground/90">{sessionTypeLabel(sessionRow?.sessionType, t)}</span></span>
              <span>{t("entityDetail.execution.sessionMsgs")} <span className="tabular-nums text-foreground/90">{sessionRow?.messageCount ?? "—"}</span></span>
              <span>{t("entityDetail.execution.sessionLastActive")} <span className="font-mono text-foreground/90">{sessionRow?.lastActiveAt || "—"}</span></span>
            </div>
          </div>
        </InfoCard>

        <InfoCard title={t("entityDetail.execution.env")} hint={t("entityDetail.execution.envHint")} testId="exec-card-env">
          <div className="flex flex-col gap-1 text-[12px]">
            {hostRow ? (
              <span className="inline-flex min-w-0 items-center gap-1 text-muted-foreground">
                {t("entityDetail.execution.host")} <EntityRef kind="host" id={hostRow.id} label={hostRow.alias || hostRow.id} mono={false} className="text-[13px]" />
              </span>
            ) : (
              <span className="text-[11px] text-muted-foreground">{t("entityDetail.execution.hostMissing")}</span>
            )}
            {projectId ? (
              <span className="inline-flex min-w-0 items-center gap-1 text-muted-foreground">
                {t("entityDetail.execution.project")} <EntityRef kind="project" id={projectId} label={getProjectName(projectId) || projectId} mono={false} className="text-[13px]" />
              </span>
            ) : (
              <span className="text-[11px] text-muted-foreground">{t("entityDetail.execution.projectMissing")}</span>
            )}
            <span className="mt-1 flex flex-wrap items-center gap-x-2 border-t border-border pt-1.5 text-[11px] text-muted-foreground">
              {recvAgent?.agentType && (
                <span>{t("entityDetail.execution.platform")} <span className="font-mono text-foreground/90">{recvAgent.agentType}</span></span>
              )}
              {projectId && <span className="font-mono">{projectId}</span>}
            </span>
          </div>
        </InfoCard>

        <InfoCard title={t("entityDetail.execution.run")} hint={t("entityDetail.execution.runHint")} testId="exec-card-run">
          <div className="flex flex-col gap-1">
            <span className="self-start">
              <StatusPill status={row.status} />
            </span>
            <span className="text-[12px] text-muted-foreground">
              {t("entityDetail.execution.start")} <span className="font-mono text-foreground/90">{row.startedAt || "—"}</span>
            </span>
            <span className="text-[12px] text-muted-foreground">
              {t("entityDetail.execution.end")} <span className="font-mono text-foreground/90">{row.finishedAt || "—"}</span>
            </span>
            <span className="text-[12px] text-muted-foreground">
              {t("entityDetail.execution.duration")}{" "}
              <span className="font-mono text-foreground/90">
                {row.durationSeconds ? `${row.durationSeconds}s` : "—"}
              </span>
            </span>
            <span className="mt-1 break-all border-t border-border pt-1.5 text-[11px] text-muted-foreground">
              {t("entityDetail.execution.runId")} <span className="font-mono text-foreground/90">{row.run_uuid}</span>
            </span>
          </div>
        </InfoCard>

        {(row.prevTask || row.nextTask) && (
          <InfoCard title={t("entityDetail.execution.rel")} hint={t("entityDetail.execution.relHint")} testId="exec-card-rel">
            <div className="flex flex-col gap-1 text-[11px] text-muted-foreground">
              {row.prevTask && (
                <span className="inline-flex items-center gap-1">
                  {t("entityDetail.execution.prev")} <EntityRef kind="execution" id={row.prevTask} label={row.prevTask} />
                </span>
              )}
              {row.nextTask && (
                <span className="inline-flex items-center gap-1">
                  {t("entityDetail.execution.next")} <EntityRef kind="execution" id={row.nextTask} label={row.nextTask} />
                </span>
              )}
            </div>
          </InfoCard>
        )}
      </div>

      <DetailSection
        title={t("entityDetail.execution.instruction")}
        hint={t("entityDetail.execution.instructionHint")}
        testId="exec-section-instruction"
      >
        <RichTextView value={row.instruction} mode="text" testId="exec-instruction" emptyText={t("entityDetail.execution.instructionEmpty")} />
      </DetailSection>

      <DetailSection title={t("entityDetail.execution.summary")} testId="exec-section-summary">
        <RichTextView value={row.summary} testId="exec-summary" emptyText={t("entityDetail.execution.summaryEmpty")} />
      </DetailSection>

      <DetailSection title={t("entityDetail.execution.live")} hint={t("entityDetail.execution.liveHint")} testId="exec-section-log">
        <RichTextView
          value={row.log?.length ? row.log.join("\n") : ""}
          mode="text"
          testId="exec-log"
          emptyText={t("entityDetail.execution.liveEmpty")}
        />
      </DetailSection>

      <HumanConfirmDialog
        target={humanTarget}
        busy={humanBusy}
        onOpenChange={(o) => {
          if (!o) setHumanTarget(null);
        }}
        onConfirm={submitHuman}
      />

      <RawBlock data={row} label={t("entityDetail.common.rawExecution")} />
    </DetailShell>
  );
}

/** Execution records mini list (embedded in session detail) */
function ExecMiniList({ rows }: { rows: ReturnType<typeof listExecutions> }) {
  const { t } = useTranslation();
  if (!rows.length) return <p className="text-sm text-muted-foreground">{t("entityDetail.session.noRuns")}</p>;
  return (
    <ul className="flex min-w-0 flex-col divide-y divide-border rounded-md border">
      {rows.map((e) => (
        <li key={e.run_uuid} className="flex min-w-0 items-center gap-2 px-3 py-2">
          <StatusPill status={e.status} />
          <span className="min-w-0 flex-1">
            <EntityRef
              kind="execution"
              id={e.run_uuid}
              label={e.execName || e.run_uuid}
              mono={false}
              className="text-sm"
            />
          </span>
          <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{e.startedAt}</span>
          <OpenClawSessionLink sessionId={e.sessionId} agentId={e.agentId} className="shrink-0" />
        </li>
      ))}
    </ul>
  );
}

/* ══════════════════════════ Skill ══════════════════════════ */

function SkillDetailView({ id }: { id: string }) {
  const { t } = useTranslation();
  const row = React.useMemo(() => skills.current.find((s) => s.id === id), [id]);
  if (!row) return <DetailNotFound what={t("entityDetail.notFound.skill")} backTo="/skills" />;

  const skillFields = (
    <Descriptions>
      <DescriptionsItem label={t("entityDetail.field.skillName")}>{row.name}</DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.category")}>
        <Mono>{row.category}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.contributor")}>
        <AgentRef id={row.sourceAgentId} name={row.sourceAgentName} />
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.skillId")}>
        <Mono>{row.id}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.useCount")}>
        <Num>{String(row.useCount)}</Num>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.helpfulVotes")}>
        <Num>{String(row.helpfulCount)}</Num>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.createdAt")}>
        <Mono>{row.createdAt}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.updatedAt")}>
        <Mono>{row.updatedAt}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.tags")} span={2}>
        <Mono>{row.tags}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.desc")} span={2}>
        {row.description && (
          <p className="max-w-prose whitespace-pre-line leading-relaxed">{row.description}</p>
        )}
      </DescriptionsItem>
    </Descriptions>
  );

  return (
    <DetailShell
      backTo="/skills"
      backLabel={t("entityDetail.back.skills")}
      testId="page-skill-detail"
      title={row.name}
      subtitle={row.id}
      badges={
        <>
          <Badge variant="outline" className="font-mono text-[11px]">{row.category}</Badge>
          <Badge variant="secondary" className="text-[11px]">{t("entityDetail.skill.useBadge", { n: row.useCount })}</Badge>
          <Badge variant="secondary" className="text-[11px]">{t("entityDetail.skill.helpfulBadge", { n: row.helpfulCount })}</Badge>
        </>
      }
    >
      <DetailSection title={t("entityDetail.common.basic")} testId="skill-section-basic">
        {skillFields}
      </DetailSection>
      <DetailSection title={t("entityDetail.skill.content")} hint={t("entityDetail.skill.contentHint")} testId="skill-section-content">
        <RichTextView value={row.content} testId="skill-content" emptyText={t("entityDetail.skill.contentEmpty")} />
      </DetailSection>
      <RawBlock data={row} label={t("entityDetail.common.rawSkill")} />
    </DetailShell>
  );
}

/* ══════════════════════════ Notice ══════════════════════════ */

function NoticeDetailView({ id }: { id: string }) {
  const { t } = useTranslation();
  const row = React.useMemo(() => notices.current.find((n) => n.id === id), [id]);
  if (!row) return <DetailNotFound what={t("entityDetail.notFound.notice")} backTo="/notices" />;

  const noticeFields = (
    <Descriptions>
      <DescriptionsItem label={t("entityDetail.field.type")}>
        <Mono>{row.type}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.visibility")}>
        <Mono>{row.visibility}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.project")}>
        <EntityRef
          kind="project"
          id={row.projectId}
          label={row.projectName || row.projectId}
          mono={!row.projectName}
        />
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.publisher")}>
        <AgentRef id={row.agentId} name={row.agentName} />
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.taskRef")}>
        <EntityRef kind="task" id={row.taskId} />
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.execRef")}>
        <EntityRef kind="execution" id={row.runUuid} />
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.publishedAt")}>
        <Mono>{row.createdAt}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.noticeId")}>
        <Mono>{row.id}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.title")} span={2}>
        {row.title && <span className="font-medium">{row.title}</span>}
      </DescriptionsItem>
    </Descriptions>
  );

  return (
    <DetailShell
      backTo="/notices"
      backLabel={t("entityDetail.back.notices")}
      testId="page-notice-detail"
      title={row.title || t("entityDetail.notice.untitled")}
      subtitle={row.id}
      badges={
        <>
          <Badge variant="outline" className="text-[11px]">{row.type}</Badge>
          <Badge variant="outline" className="text-[11px]">{row.visibility}</Badge>
        </>
      }
    >
      <DetailSection title={t("entityDetail.common.basic")} testId="notice-section-basic">
        {noticeFields}
      </DetailSection>
      <DetailSection title={t("entityDetail.notice.content")} testId="notice-section-content">
        <RichTextView value={row.content} testId="notice-content" emptyText={t("entityDetail.notice.contentEmpty")} />
      </DetailSection>
      <RawBlock data={row} label={t("entityDetail.common.rawNotice")} />
    </DetailShell>
  );
}

/* ══════════════════════════ Host ══════════════════════════ */

function HostDetailView({ id }: { id: string }) {
  const { t } = useTranslation();
  const row = React.useMemo(() => listHosts().find((h) => h.id === id), [id]);
  if (!row) return <DetailNotFound what={t("entityDetail.notFound.host")} backTo="/hosts" />;

  // Agents on this host — also gives a direct OpenClaw entry for each Agent
  const hostAgents = React.useMemo(() => listAgents().filter((a) => a.host_id === id), [id]);

  /* [2026-10-30 07:55 Feng] "the view-type page is not clearly presented" — replaced the old FieldGrid
       (div grid, right-aligned labels 7em, no grid lines) with the newly delivered shardkit Descriptions
       (real table + border-collapse single-line grid, 150px light-grey bold label column, empty values uniformly '-').
       The difference is not looks but "can label and value be matched at a glance":
         · before, labels were right-aligned and value/label separated only by whitespace; with many fields rows ran together;
         · now every cell has a 1px border on all sides, cells naturally pair up, scanning does not jump rows.
       Full-row items (host ID / description) are explicitly placed at the end of this table — Descriptions also forces
       them last internally; writing it out means the order is readable from the source. */
  /* Field count is deliberately even (8 half-row items + optional full-row "description"):
       Descriptions is table-fixed; with an odd number of half-row items it pads an empty filler cell —
       visually like "this cell is missing content". 8 gives exactly 4 full rows, and the reading order also flows:
       identity → connection → environment → status → (description full row). */
  const hostFields = (
    <Descriptions>
      <DescriptionsItem label={t("entityDetail.field.hostId")}>
        <span className="break-all font-mono text-[13px]">{row.id}</span>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.alias")}>{row.alias}</DescriptionsItem>
      <DescriptionsItem label="IP（endpoint）">
        <span className="break-all font-mono text-[13px]">{row.ip}</span>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.sshPort")}>
        <span className="font-mono text-[13px] tabular-nums">{row.sshPort}</span>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.os")}>{row.os}</DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.slots")}>
        <span className="tabular-nums">
          {row.activeTasks} / {row.slotsLimit}
        </span>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.online")}>
        <Badge
          variant="outline"
          className={row.online ? "border-success/40 bg-success/10 text-success" : "text-muted-foreground"}
        >
          {row.online ? t("entityDetail.host.onlineText") : t("entityDetail.host.offlineText")}
        </Badge>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.active")}>
        <Badge
          variant="outline"
          className={row.active ? "border-info/40 bg-info/10 text-info" : "text-muted-foreground"}
        >
          {row.active ? t("entityDetail.host.enabled") : t("entityDetail.host.disabled")}
        </Badge>
      </DescriptionsItem>
      {row.xDesc ? (
        <DescriptionsItem label={t("entityDetail.field.desc")} span={2}>
          <p className="max-w-prose whitespace-pre-line leading-relaxed">{row.xDesc}</p>
        </DescriptionsItem>
      ) : null}
    </Descriptions>
  );

  return (
    <DetailShell
      backTo="/hosts"
      backLabel={t("entityDetail.back.hosts")}
      testId="page-host-detail"
      title={row.alias || row.id}
      subtitle={row.id}
      badges={
        <>
          <Badge variant="outline" className="text-[11px]">{row.online ? t("entityDetail.host.onlineText") : t("entityDetail.host.offlineText")}</Badge>
          <Badge variant="outline" className="font-mono text-[11px]">{row.ip}</Badge>
        </>
      }
    >
      <DetailSection title={t("entityDetail.host.info")} testId="host-section-basic">
        {hostFields}
      </DetailSection>

      {/* [2026-11-16 09:28 Feng] the detail must also show the platform — the list can only give a "DSH" badge,
            what really matters is "how this machine runs it": instance name / mode / port or address / session path / note.
            One machine can host several platforms, so list them one by one, no uniqueness assumption. */}
      <DetailSection
        title={t("entityDetail.host.platforms", { n: (row.platforms || []).length })}
        hint={t("entityDetail.host.platformsHint")}
        testId="host-section-platforms"
      >
        {(row.platforms || []).length ? (
          <ul className="flex min-w-0 flex-col gap-2">
            {(row.platforms || []).map((p, i) => (
              <li
                key={`${p.platform}-${p.port ?? i}`}
                className="flex min-w-0 flex-wrap items-center gap-2 rounded-md border bg-card/60 px-3 py-2"
                data-testid={`host-platform-${p.platform}`}
              >
                <PlatformLogo type={p.platform} className="h-8 w-8" imgClassName="h-5 w-5" />
                <span className="min-w-0">
                  <span className="block text-sm font-medium">{p.name || platformLabel(p.platform)}</span>
                  <span className="block font-mono text-[11px] text-muted-foreground">
                    {p.platform}
                    {" · "}
                    {p.mode === "container" ? (p.addr || "container") : (p.port ? `:${p.port}` : "—")}
                    {p.base_path ? ` · ${p.base_path}` : ""}
                  </span>
                </span>
                <Badge variant="outline" className="text-[10px]">
                  {p.mode === "container" ? t("entityDetail.host.modeContainer") : t("entityDetail.host.modeProcess")}
                </Badge>
                {p.note ? (
                  <span className="min-w-0 flex-1 text-[11px] text-muted-foreground">{p.note}</span>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">{t("entityDetail.host.noPlatforms")}</p>
        )}
      </DetailSection>

      <DetailSection title={t("entityDetail.host.slots")} testId="host-section-slots">
        <SlotBar used={row.activeTasks} limit={row.slotsLimit} />
      </DetailSection>

      <DetailSection
        title={t("entityDetail.host.agents", { n: hostAgents.length })}
        hint={t("entityDetail.host.agentsHint")}
        testId="host-section-agents"
      >
        {hostAgents.length ? (
          <ul className="flex min-w-0 flex-col divide-y divide-border rounded-md border">
            {hostAgents.map((a) => (
              <li key={a.id} className="flex min-w-0 items-center gap-2 px-3 py-2">
                <AgentAvatar name={a.name} id={a.id} avatarUrl={a.avatarUrl} className="h-7 w-7" textClassName="text-[11px]" />
                <span className="min-w-0 flex-1">
                  <EntityRef
                    kind="agent"
                    id={a.id}
                    label={
                      <>
                        <span className="text-sm">{a.name}</span>
                        <span className="ml-2 font-mono text-[11px] text-muted-foreground">{a.id}</span>
                      </>
                    }
                    mono={false}
                  />
                </span>
                <StatusPill status={a.status} />
                <OpenClawLink agentId={a.id} agentType={a.agentType} hostId={a.host_id} variant="inline" />
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">{t("entityDetail.host.noAgents")}</p>
        )}
      </DetailSection>

      <RawBlock data={row} label={t("entityDetail.common.rawHost")} />
    </DetailShell>
  );
}

export function SlotBar({ used, limit }: { used: number; limit: number }) {
  const { t } = useTranslation();
  const total = Math.max(1, limit || 1);
  const ratio = Math.min(1, used / total);
  const cells = Math.min(total, 32);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span>{t("entityDetail.host.usedOf", { used, limit })}</span>
        <span>{Math.round(ratio * 100)}%</span>
      </div>
      <div className="flex gap-0.5">
        {Array.from({ length: cells }).map((_, i) => {
          const filled = i < Math.round(ratio * cells);
          return (
            <span
              key={i}
              className={cn(
                "h-2.5 flex-1 rounded-sm",
                filled ? "bg-primary" : "bg-muted",
              )}
            />
          );
        })}
      </div>
    </div>
  );
}

/* ══════════════════════════ Plan ══════════════════════════ */

function PlanDetailView({ id }: { id: string }) {
  const { t } = useTranslation();
  const row = React.useMemo(() => listPlans().find((p) => p.id === id), [id]);
  if (!row) return <DetailNotFound what={t("entityDetail.notFound.plan")} backTo="/plans" />;

  const pct = row.totalSteps ? Math.round((row.doneSteps / row.totalSteps) * 100) : 0;
  const planFields = (
    <Descriptions>
      <DescriptionsItem label={t("entityDetail.field.planId")}>
        <Mono>{row.id}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.status")}>
        <StatusPill status={row.status} />
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.project")}>
        <EntityRef
          kind="project"
          id={row.projectId}
          label={row.projectName || row.projectId}
          mono={!row.projectName}
        />
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.owner")}>
        <AgentRef id={row.owner} name={row.ownerName} />
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.trigger")}>
        <Mono>{row.trigger}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.steps")}>
        <Num>
          {row.doneSteps} / {row.totalSteps}
          <span className="text-muted-foreground">（{pct}%）</span>
        </Num>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.nextRun")}>
        <Mono>{row.nextRunAt}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.updatedAt")}>
        <Mono>{row.updatedAt}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.planName")} span={2}>
        <span className="font-medium">{row.name}</span>
      </DescriptionsItem>
    </Descriptions>
  );

  return (
    <DetailShell
      backTo="/plans"
      backLabel={t("entityDetail.back.plans")}
      testId="page-plan-detail"
      title={row.name}
      subtitle={row.id}
      badges={<StatusPill status={row.status} />}
      actions={
        <Button asChild size="sm" variant="view" className="gap-1.5">
          <Link to={`/plans/${encodeURIComponent(row.id)}/flow`}>
            <Play className="h-4 w-4" /> {t("entityDetail.plan.flow")}
          </Link>
        </Button>
      }
    >
      <DetailSection title={t("entityDetail.plan.info")} testId="plan-section-basic">
        {planFields}
      </DetailSection>

      <DetailSection title={t("entityDetail.plan.progress")} testId="plan-section-progress">
        <div className="flex items-center gap-3">
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
            <div className="h-full bg-primary" style={{ width: `${pct}%` }} />
          </div>
          <span className="text-xs text-muted-foreground">{pct}%</span>
        </div>
      </DetailSection>

      <RawBlock data={row} label={t("entityDetail.common.rawPlan")} />
    </DetailShell>
  );
}

/* ══════════════════════════ Project group ══════════════════════════ */

function ProjectGroupDetailView({ id }: { id: string }) {
  const { t } = useTranslation();
  const row = React.useMemo(() => listProjectGroups().find((g) => g.id === id), [id]);
  if (!row) return <DetailNotFound what={t("entityDetail.notFound.projectGroup")} backTo="/project-groups" />;

  const groupFields = (
    <Descriptions>
      <DescriptionsItem label={t("entityDetail.field.groupName")}>{row.name}</DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.groupId")}>
        <Mono>{row.id}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.memberCount")}>
        <Num>{String(row.members?.length ?? row.memberCount)}</Num>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.projectCount")}>
        <Num>{String(row.projectCount)}</Num>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.status")}>
        <StatusPill status={row.status} />
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.updatedAt")}>
        <Mono>{row.updatedAt}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.desc")} span={2}>
        {row.xDesc && <p className="max-w-prose whitespace-pre-line leading-relaxed">{row.xDesc}</p>}
      </DescriptionsItem>
    </Descriptions>
  );

  return (
    <DetailShell
      backTo="/project-groups"
      backLabel={t("entityDetail.back.projectGroups")}
      testId="page-project-group-detail"
      title={row.name}
      subtitle={row.id}
      badges={<StatusPill status={row.status} />}
    >
      <DetailSection title={t("entityDetail.common.basic")} testId="pgroup-section-basic">
        {groupFields}
      </DetailSection>

      {/* [2026-11-04 Feng] members are already visible in the list; the detail block keeps the same presentation,
            so it is not "visible in the list, missing when I click in". */}
      <DetailSection title={t("entityDetail.projectGroup.members", { n: row.members?.length ?? 0 })} testId="pgroup-section-members">
        <AgentMemberChips members={row.members} />
      </DetailSection>

      <RawBlock data={row} label={t("entityDetail.common.rawProjectGroup")} />
    </DetailShell>
  );
}

/* ══════════════════════════ Quick command ══════════════════════════ */

function QuickCmdDetailView({ id }: { id: string }) {
  const { t } = useTranslation();
  const [cmd, setCmd] = React.useState<QuickCmd | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    let alive = true;
    setLoading(true);
    quickCmdApi
      .get(id)
      .then((c) => alive && setCmd(c))
      .catch((e) => alive && setError((e as Error).message || String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [id]);

  if (!loading && !cmd) return <DetailNotFound what={t("entityDetail.notFound.quickCmd")} backTo="/quick-cmds" />;

  const qcmdFields = (
    <Descriptions>
      <DescriptionsItem label={t("entityDetail.field.cmdId")}>
        <Mono>{cmd?.cmdId ?? id}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.category")}>
        <Mono>{cmd?.category}</Mono>
      </DescriptionsItem>
      {/* [2026-11-14 Feng] two kinds of commands: dispatch task / text snippet.
            The detail page must say so — otherwise a text command showing "target task — / params 0" looks misconfigured. */}
      <DescriptionsItem label={t("entityDetail.field.type")}>
        {cmd ? (
          cmd.sendMode === "text"
            ? t("entityDetail.quickCmd.kindText")
            : t("entityDetail.quickCmd.kindTask")
        ) : null}
      </DescriptionsItem>
      {/* [2026-10-26 public/private; 2026-10-27 definition] is_public only says **who executes**:
            public → the clicker executes it themselves (current identity); private → dispatched to target_agent_id. */}
      <DescriptionsItem label={t("entityDetail.field.visibility")}>
        {cmd ? (cmd.isPublic ? t("entityDetail.quickCmd.publicText") : t("entityDetail.quickCmd.privateText")) : null}
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.status")}>
        {cmd ? (cmd.activeStatus ? t("entityDetail.common.effective") : t("entityDetail.quickCmd.archived")) : null}
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.runner")}>
        {cmd?.isPublic ? t("entityDetail.quickCmd.selfRunner") : <AgentRef id={cmd?.ownerAgent} />}
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.targetAgent")}>
        <Mono>{cmd?.delegateTo}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.targetTask")}>
        <Mono>{cmd?.sendMode === "text" ? "—" : cmd?.targetTask}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={cmd?.sendMode === "text" ? t("entityDetail.field.textLength") : t("entityDetail.field.paramCount")}>
        {cmd?.sendMode === "text" ? (
          <Num>{String((cmd?.composedInstruction || "").length)}</Num>
        ) : (
          <Num>{cmd ? String(cmd.params?.length ?? 0) : null}</Num>
        )}
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.createdAt")}>
        <Mono>{cmd?.createdAt}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.updatedAt")}>
        <Mono>{cmd?.updatedAt}</Mono>
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.title")} span={2}>
        {cmd?.title && <span className="font-medium">{cmd.title}</span>}
      </DescriptionsItem>
      <DescriptionsItem label={t("entityDetail.field.desc")} span={2}>
        {cmd?.notes && <p className="max-w-prose whitespace-pre-line leading-relaxed">{cmd.notes}</p>}
      </DescriptionsItem>
    </Descriptions>
  );

  return (
    <DetailShell
      backTo="/quick-cmds"
      backLabel={t("entityDetail.back.quickCmds")}
      testId="page-quickcmd-detail"
      title={cmd?.title ?? id}
      subtitle={id}
      badges={
        <>
          {cmd?.category && <Badge variant="outline" className="font-mono text-[11px]">{cmd.category}</Badge>}
          {loading && (
            <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" /> {t("entityDetail.common.loading")}
            </span>
          )}
        </>
      }
    >
      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {t("entityDetail.common.readFailed", { msg: error })}
        </div>
      )}

      <DetailSection title={t("entityDetail.common.basic")} testId="qcmd-section-basic">
        {qcmdFields}
      </DetailSection>

      <DetailSection title={t("entityDetail.quickCmd.skeleton")} hint={t("entityDetail.quickCmd.skeletonHint")} testId="qcmd-section-skeleton">
        {/* Machine text goes through the text channel: {date} / indentation / leading # in the template would be eaten if rendered as Markdown */}
        <RichTextView
          value={cmd?.semanticSkeleton}
          mode="text"
          testId="qcmd-skeleton"
          emptyText={t("entityDetail.quickCmd.skeletonEmpty")}
        />
      </DetailSection>

      <DetailSection title={t("entityDetail.quickCmd.params")} hint={t("entityDetail.quickCmd.paramsHint", { n: cmd?.params?.length ?? 0 })} testId="qcmd-section-params">
        {cmd?.params?.length ? (
          <ul className="flex flex-col divide-y divide-border rounded-md border">
            {cmd.params.map((p) => (
              <li key={p.name} className="flex flex-wrap items-baseline gap-2 px-3 py-2 text-sm">
                <span className="font-mono text-xs">{p.name}</span>
                <Badge variant="outline" className="text-[10px]">{p.type ?? "string"}</Badge>
                {p.required && <Badge variant="outline" className="text-[10px]">{t("entityDetail.quickCmd.required")}</Badge>}
                <span className="text-xs text-muted-foreground">{p.label}</span>
                {p.placeholder && (
                  <span className="text-[11px] text-muted-foreground/80">（{p.placeholder}）</span>
                )}
                {p.default !== undefined && p.default !== "" && (
                  <span className="font-mono text-[11px] text-muted-foreground">{t("entityDetail.quickCmd.default", { value: String(p.default) })}</span>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">{t("entityDetail.quickCmd.noParams")}</p>
        )}
      </DetailSection>

      {cmd?.composedInstruction && (
        <DetailSection title={t("entityDetail.quickCmd.composed")} hint={t("entityDetail.quickCmd.composedHint")} testId="qcmd-section-composed">
          <RichTextView value={cmd.composedInstruction} mode="text" testId="qcmd-composed" emptyText={t("entityDetail.quickCmd.composedEmpty")} />
        </DetailSection>
      )}

      {cmd && <RawBlock data={cmd} label={t("entityDetail.common.rawQuickCmd")} />}
      {!cmd && !loading && !error && <p className="text-sm text-muted-foreground">{t("entityDetail.quickCmd.noData")}</p>}
    </DetailShell>
  );
}
