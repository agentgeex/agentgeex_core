/**
 * Agent management · Agent list (/agents)
 * Basis: agentgeex_ui.md §2.2:
 *   - Grid card / table dual-view toggle
 *   - Aggregated display grouped by project
 *   - Single card: top basic info + bottom 4 metric cells (session/task/execution/quick command)
 *
 * 2026-10-05 Feng's request (this round):
 *   ① Tagging: different types (template / clone instance / native) and states (deploy status, online)
 *   ② Filtering: template/in-use toggle + role + online status + keyword (no sorting)
 *
 * ⚠️ Quick Commands are not implemented this round — Feng is still designing them (2026-09-30).
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { QuickStar } from "@/components/agentgeex/quick-star";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  LayoutGrid, List, MessageSquare, ListChecks, ReceiptText, ChevronRight,
  Search, Copy, Server, ShieldCheck, AlertTriangle, Layers,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { formatRemaining } from "@/components/agentgeex/dispatch-exempt-panel";
import { Input } from "@/components/ui/input";
import { OnlineDot } from "@/components/ui/online-dot";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select";
import {
  Table, TableHeader, TableBody, TableRow, TableHead, TableCell,
} from "@/components/ui/table";
import { StatusPill } from "@/components/agentgeex/data-table";
import { agentScopedLink } from "@/lib/agent-filter";
import { PlatformLogo } from "@/components/agentgeex/platform-logo";
import { AgentAvatar } from "@/components/agentgeex/agent-avatar";
import { chatDeepLink } from "@/lib/openclaw";
import { MessageSendDialog } from "@/components/agentgeex/message-send-dialog";
import { hostOptions, HOST_FILTER_LABEL_KEY } from "@/lib/host-filter";
import { agentsGroupedByProject, executionSummary, getProjectName, sortByQuickProject, projectNameWithPin, type AgentRow } from "@/providers/agentgeex";
import { sessionsOf, tasksOf, executionsOf } from "@/mock/agentgeex";
import { cn } from "@/lib/utils";
import { cardStateClass, CARD_SELECTED, useCardSelection } from "@/lib/card-selection";
import { useDetailPopup } from "@/components/agentgeex/detail-popup";
import {
  AgentSessionsDialog, AgentTasksDialog, AgentExecutionsDialog,
} from "@/components/agentgeex/page-dialogs";

/* ───────────────────────── Tags: type / status ───────────────────────── */

type AgentKindTag = "template" | "clone" | "native";

/** Canonical kind (fallback-derived from isTemplate/originId when the backend does not provide it) */
function kindOf(a: AgentRow): AgentKindTag {
  if (a.kind) return a.kind as AgentKindTag;
  if (a.isTemplate || a.deployStatus === "template") return "template";
  if (a.originId) return "clone";
  return "native";
}

const KIND_STYLE: Record<AgentKindTag, { labelKey: string; cls: string; icon?: React.ReactNode }> = {
  template: { labelKey: "agentsPage.kind.template", cls: "bg-chart-1/15 text-chart-1 border-chart-1/30" },
  clone: { labelKey: "agentsPage.kind.clone", cls: "bg-info/15 text-info border-info/30", icon: <Copy className="h-3 w-3" /> },
  native: { labelKey: "agentsPage.kind.native", cls: "bg-muted-foreground/15 text-muted-foreground border-muted-foreground/30" },
};

const DEPLOY_LABEL: Record<string, { labelKey: string; cls: string }> = {
  template: { labelKey: "agentsPage.deploy.template", cls: "bg-chart-1/15 text-chart-1 border-chart-1/30" },
  deployed: { labelKey: "agentsPage.deploy.deployed", cls: "bg-success/15 text-success border-success/30" },
  pending: { labelKey: "agentsPage.deploy.pending", cls: "bg-warning/15 text-warning border-warning/30" },
  deploying: { labelKey: "agentsPage.deploy.deploying", cls: "bg-info/15 text-info border-info/30" },
  failed: { labelKey: "agentsPage.deploy.failed", cls: "bg-destructive/15 text-destructive border-destructive/30" },
};

/** Type tag */
function KindTag({ agent }: { agent: AgentRow }) {
  const { t } = useTranslation();
  const k = kindOf(agent);
  const s = KIND_STYLE[k];
  return (
    <Badge variant="outline" className={cn("gap-1 font-normal", s.cls)} data-testid={`agent-kind-${k}`}>
      {s.icon}
      {t(s.labelKey)}
    </Badge>
  );
}

/** Deploy status tag */
function DeployTag({ status }: { status?: string }) {
  const { t } = useTranslation();
  if (!status) return null;
  const m = DEPLOY_LABEL[status];
  const text = m ? t(m.labelKey) : status;
  return (
    <Badge
      variant="outline"
      className={cn("font-normal", m?.cls ?? "bg-muted text-muted-foreground")}
      data-testid={`agent-deploy-${status}`}
    >
      {text}
    </Badge>
  );
}

/** Role colors: distinguish roles by color (shared by card/table) */
const ROLE_STYLE: Record<string, string> = {
  // Management layer uses warm colors (purple/orange); the execution-layer worker uses neutral blue-grey
  project_manager: "border-chart-1/30 bg-chart-1/15 text-chart-1",
  workshop_manager: "border-warning/30 bg-warning/15 text-warning",
  host_manager: "border-chart-3/30 bg-chart-3/15 text-chart-3",
  agent_manager: "border-chart-4/30 bg-chart-4/15 text-chart-4",
  worker: "border-chart-5/30 bg-chart-5/15 text-chart-5",
};
const ROLE_FALLBACK = "border-border bg-muted text-muted-foreground";

/** Role tag: with color, for visually distinguishing roles */
function RoleTag({ role }: { role: string }) {
  const { t } = useTranslation();
  return (
    <Badge variant="outline" className={cn("font-mono text-[11px] font-normal", ROLE_STYLE[role] ?? ROLE_FALLBACK)} data-testid={`agent-role-${role}`}>
      {roleLabel(role, t)}
    </Badge>
  );
}

/* ───────────────────────── Card ───────────────────────── */

/** Three metrics on the card (session / task / execution) — all of them open dialogs now, no page jump */
type MetricKind = "sessions" | "tasks" | "executions";

function AgentCard({
  agent,
  selected,
  onOpen,
  onInput,
  onMetric,
  activeMetric,
}: {
  agent: AgentRow;
  /** [2026-11-05 12:21 Feng] Whether this card is "the one just clicked" */
  selected: boolean;
  onOpen: () => void;
  onInput: () => void;
  /** [2026-11-06 11:22 Feng] "If I click a session inside an agent, the focus actually lands on it,
   *  and after coming back it should be the same as clicking that box — focus stays on it."
   *  = the cell opened last (kept even after the dialog closes), used to draw the "I just clicked here" frame. */
  activeMetric?: MetricKind | null;
  /** [2026-11-06 11:22 Feng] Click a metric → dialog; carry the trigger element out together,
   *  and after closing the dialog return focus to it ("after coming back the focus is still on it"). */
  onMetric: (kind: MetricKind, el: HTMLElement) => void;
}) {
  const { t } = useTranslation();
  const sess = sessionsOf(agent.id);
  const tks = tasksOf(agent.id);
  const execs = executionsOf(agent.id);
  const sum = executionSummary(execs);
  /* [2026-10-29 23:03 Feng] Do not show when the failure count is 0 — a row of red "0 failed" takes space and draws the eye;
     having no failures is good news and should not be shouted on every card. Hoisted into a variable to avoid re-evaluating in JSX. */
  const execOk = agent.execSucceeded ?? sum.succeeded;
  const execBad = agent.execFailed ?? sum.failed;
  // PM card uses a highlighted background to stand out (Feng 2026-10-11)
  const isPm = agent.role === "project_manager";

  /* [2026-11-05 11:34 Feng] "Also fix the frames on the grid page"
     — after unifying all frame colors on the site to #D1D4DC, these blocks still wrote their own fill/border,
       inconsistent with the big frame. Now unified to "white fill + the same 209 line":
       normal card (white fill) → white block + grey line, the line is the only boundary (same set as the small blocks
                                  on the system status page)
       PM card (light purple fill) → white block + purple line, purple keeps it visible on purple (this is a category
                                  marker, not "line darkness")
     Note: the normal card used to be `bg-muted` (grey fill) + `border-strong`; grey fill on a white card steals half
           the contrast. White fill + line now reads the same as the other grid blocks. */
  /* [2026-11-06 11:22 Feng] "For this PM agent, hovering over the session does not change the background,
     but for the others it does… so people doubt whether they are really hovering."
     — Root cause: the PM branch only had `hover:border-chart-1/70`, **no hover background**;
     normal cards have `hover:bg-primary/5`. So three cells in one row, two different reactions.
     Fix: give PM a background too, just in the **purple family** (keep PM's category color, do not switch to blue).
     ★ A background change is the only signal that "this cell is clickable" — without it users assume it is not. */
  const metricBase =
    "flex flex-col items-center justify-center rounded-md border px-1.5 py-2 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring";
  /* The selected cell: deeper line + 2px ring — same language as the card's own selected state (what hover shows is what stays) */
  const metricClsFor = (kind: MetricKind) =>
    cn(
      metricBase,
      isPm
        ? "border-chart-1/45 bg-card hover:border-chart-1/70 hover:bg-chart-1/10"
        : "bg-card hover:border-primary/40 hover:bg-primary/5",
      activeMetric === kind &&
        (isPm
          ? "border-chart-1 ring-2 ring-chart-1/50"
          : "border-primary ring-2 ring-primary/40"),
    );

  return (
    <Card
      data-testid={`agent-card-${agent.id}`}
      data-pm={isPm ? "1" : undefined}
      data-selected={selected ? "true" : undefined}
      className={cn(
        /* [2026-11-05 12:21 Feng] "The hover change exists but is not obvious; you can darken the color and thicken the border;
           and after clicking in and coming back it should keep the hover look, so I know which one I clicked"
           ——
           ① Thicken with ring (box-shadow) rather than border-2:
              thickening border pushes content in by 1px, so sweeping the mouse across a row makes the whole row jitter;
              ring is drawn outside the border and takes no layout space, visually "the frame got one size thicker".
           ② Use the **same** visuals for hover and selected (deeper line + 2px ring + one shadow level) —
              users need not learn two languages: what you see on hover is what stays after clicking. */
        cardStateClass(
          selected,
          // PM card keeps its purple as a category marker, but the intensity and shape match the blue selected state
          isPm ? "border-chart-1 ring-2 ring-chart-1/50 shadow-md" : CARD_SELECTED,
        ),
        isPm && "border-chart-1/40 bg-chart-1/10"
      )}
      onClick={onOpen}
    >
      <CardContent className="flex flex-col gap-3 p-4">
        {/* Top: basic info */}
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              {/* Avatar: prefer agent_avatar, otherwise initials (★ not the platform/framework logo) */}
              <AgentAvatar name={agent.name} id={agent.id} avatarUrl={agent.avatarUrl} className="h-8 w-8" textClassName="text-xs" />
              <div className="min-w-0">
                {/* [2026-11-02 Feng] "Online is a state, right? Move this online indicator next to the title so they are separated"
                    — the top-right used to be "online + platform icon + details", three things of the same height
                    and the same blocky shape, so users could not tell which one was clickable.
                    State goes with the name, the button stays on the right. */}
                <div className="flex items-center gap-1.5">
                  <span className="truncate text-sm font-semibold">{agent.name}</span>
                  <StatusPill
                    status={agent.status}
                    className="h-5 shrink-0 px-1.5 text-[11px] font-medium"
                  />
                </div>
                <div className="truncate font-mono text-[11px] text-muted-foreground">{agent.id}</div>
              </div>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            {/* [2026-10-27 Feng] The platform icon itself is the entry: click it to go straight to that platform's web page.
                Platforms without deep-link support (dsh/codex/custom…) automatically render as non-clickable icons. */}
            <PlatformLogo
              type={agent.agentType}
              href={chatDeepLink(agent.id, agent.agentType, agent.host_id)}
              className="h-9 w-9"
              imgClassName="h-5 w-5"
            />
            {/* [2026-11-02 Feng] "This (details) button is useless, because clicking any blank area jumps to details…
                replace it with the 'send message' button from the details page; I want to talk to this agent right from
                the grid, without going inside and adding another layer of operations."
                —— Details is indeed redundant: the whole card is the entry. What the card **lacks** is a way to speak.
                Reuse the same MessageSendDialog as the detail page (do not write another input box),
                and prefill the same way: sender = target = this Agent. */}
            {/* [2026-11-13 22:03] Add favorite on the card — Agents are also the most common items in quick access. */}
            <QuickStar type="agent" entryKey={agent.id} label={agent.name} testId={`agent-quick-${agent.id}`} />
            <Button
              size="sm"
              className="h-9 gap-1 px-2 text-sm"
              title={t("agentsPage.card.sendAs", { name: agent.name })}
              data-testid={`agent-input-${agent.id}`}
              onClick={(e) => {
                e.stopPropagation();
                onInput();
              }}
            >
              <Layers className="h-4 w-4" />
              <span className="hidden sm:inline">{t("agentsPage.card.sendInfo")}</span>
            </Button>
          </div>
        </div>

        {/* [2026-10-28 Feng] Host: which compute machine this Agent runs on (visible at a glance on the card) */}
        <div className="flex items-center gap-1 text-[11px] text-muted-foreground" data-testid={`agent-host-${agent.id}`}>
          <Server className="h-3 w-3 opacity-70" />
          <span>{t("agentsPage.card.host")}</span>
          <span className="font-mono text-foreground/80">{agent.host_id || "—"}</span>
        </div>

        {/* Tag area: type + deploy status + role */}
        {/* [2026-11-05 Feng] Removed the model field from the card —
            it read agx_agent_registry.base_model, and base_model has long been deprecated
            (decided 2026-10-15: the model follows the **session**, not the agent).
            50 agents are NULL, 35 hardcode "deepseek", 8 "deepseek-chat" —
            it is neither accurate nor related to the model actually running, "showing it only causes confusion".
            ★ Not changed to "—": a placeholder still implies "there used to be a model here"; the right move is to hide the field. */}
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <KindTag agent={agent} />
          <DeployTag status={agent.deployStatus} />
          <RoleTag role={agent.role} />
        </div>

        {/* [2026-10-31 Feng] Temporary dispatch exemption: show it on the card if it was ever granted.
            Why show it in the list too: an exemption is "opening a door for someone"; if it is only visible on the detail page,
            cases of "granted for 8 hours and nobody remembered to close it" will keep happening.
            Only show the two "granted" cases (active / expired) — "none" takes no space. */}
        {agent.dispatchExemptApplicable !== false &&
          (agent.dispatchExemptActive || agent.dispatchExemptUntil) && (
          <div className="flex items-center gap-1" data-testid={`agent-exempt-${agent.id}`}>
            {agent.dispatchExemptActive ? (
              <Badge
                variant="outline"
                className="gap-1 border-success/40 bg-success/10 text-[10px] font-normal text-success"
                data-state="active"
              >
                <ShieldCheck className="h-3 w-3" />
                {t("agentsPage.card.exemptActive", {
                  time: formatRemaining(agent.dispatchExemptRemainingSeconds ?? 0, t),
                })}
              </Badge>
            ) : (
              <Badge
                variant="outline"
                className="gap-1 border-warning/40 bg-warning/10 text-[10px] font-normal text-warning"
                data-state="expired"
              >
                <AlertTriangle className="h-3 w-3" />
                {t("agentsPage.card.exemptExpired")}
              </Badge>
            )}
          </div>
        )}

        {/* Clone source hint */}
        {agent.originId && (
          <div className="truncate text-[10px] text-muted-foreground/80">
            {t("agentsPage.card.clonedFrom")} <span className="font-mono">{agent.originId}</span>
          </div>
        )}

        {/* Bottom: metric area (session / task / execution)
            [2026-10-29 Feng] These three used to be bare at the card bottom with a few lines of fragmentary text under each
            (task had a description, execution had success/failure) — cramped, no clear focus. Changed to three small boxes:
              · drop the description fragment under task ("can't tell what it says")
              · put success/failure as two small rows left of the number, left-aligned with each other, number larger
            Three cells of equal height, each centered; a glance gives three numbers. */}
        <div className="grid grid-cols-3 gap-2 border-t pt-3" data-testid="agent-metrics">
          {/* [2026-11-06 11:22 Feng] "Don't open another page from that nav bar for the popup.
              Why? Once you open a page the focus cannot come back, because a popup can come back."
              —— These three cells used to be <Link> **full page navigation** (/sessions?agent=xx),
                 so browsing several Agents in the grid meant jumping back and forth.
                 Now all three open a dialog (page body + agentId condition); closing returns to the same place.
              ★ Still use <button> rather than div: Tab-focusable, Enter opens it, and the semantics fit. */}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();          // Do not also trigger the card's own "open details"
              onMetric("sessions", e.currentTarget);
            }}
            title={t("agentsPage.card.viewSessions", { name: agent.name })}
            className={metricClsFor("sessions")}
            data-testid="metric-sessions"
          >
            <span className="flex items-center justify-center gap-1 text-[11px] text-muted-foreground">
              <MessageSquare className="h-3 w-3" />
              {t("agentsPage.card.sessions")}
            </span>
            <span className="mt-0.5 text-lg font-semibold leading-tight tabular-nums">
              {sess.length || agent.sessionCount}
            </span>
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onMetric("tasks", e.currentTarget);
            }}
            title={t("agentsPage.card.viewTasks", { name: agent.name })}
            className={metricClsFor("tasks")}
            data-testid="metric-tasks"
          >
            <span className="flex items-center justify-center gap-1 text-[11px] text-muted-foreground">
              <ListChecks className="h-3 w-3" />
              {t("agentsPage.card.tasks")}
            </span>
            <span className="mt-0.5 text-lg font-semibold leading-tight tabular-nums">
              {agent.taskCount ?? tks.length}
            </span>
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onMetric("executions", e.currentTarget);
            }}
            title={t("agentsPage.card.viewExecutions", { name: agent.name })}
            className={metricClsFor("executions")}
            data-testid="metric-executions"
          >
            <span className="flex items-center justify-center gap-1 text-[11px] text-muted-foreground">
              <ReceiptText className="h-3 w-3" />
              {t("agentsPage.card.executions")}
            </span>
            {/* [2026-10-29 23:03 Feng] Big number on the left, success/failure as two small rows on the right.
                Two fixes:
                  · Change the two text lines to [left-aligned] (text-right made right edges flush and left edges ragged,
                    looking like randomly centered)
                  · Do not render the row when the failure count is 0 (no "0 failed") */}
            <div className="mt-0.5 flex items-center justify-start gap-1.5">
              <span className="text-lg font-semibold leading-none tabular-nums">
                {agent.execCount || sum.total}
              </span>
              <div className="flex flex-col items-start text-left text-[10px] leading-tight">
                <span className="text-success tabular-nums">
                  {t("agentsPage.card.execOk", { n: execOk })}
                </span>
                {execBad > 0 && (
                  <span className="text-destructive tabular-nums" data-testid="metric-exec-failed">
                    {t("agentsPage.card.execBad", { n: execBad })}
                  </span>
                )}
              </div>
            </div>
          </button>
        </div>

        {/* [2026-10-29 Feng] Remove "view details": the whole card is already the click target (cursor-pointer),
            one more entry makes people think only that line is clickable. */}
      </CardContent>
    </Card>
  );
}

/* ───────────────────────── Filter state ───────────────────────── */

interface Filters {
  /** Template view: all=everything, in_use=only in use (exclude templates), template=only templates */
  scope: "all" | "in_use" | "template";
  role: string;
  status: string;
  /** Project filter: project id, "__all__" = all */
  project: string;
  /** Host filter: host_id, "__all__" = all (multi-host since 2026-10-26) */
  host: string;
  q: string;
}

/** Default filters: status defaults to "online" (Feng 2026-10-10).
 *  Note: loadFilters merges with localStorage — if the user previously saved "all", that choice still wins;
 *  this default only applies on first visit / when nothing is stored. */
const DEFAULT_FILTERS: Filters = { scope: "in_use", role: "__all__", status: "online", project: "__all__", host: "__all__", q: "" };

/** Persist filters (still effective after closing and reopening) — keys and old-key migration live in lib/storage-keys.ts */

import { readKey, writeKey } from "@/lib/storage-keys";
import { AGENT_ROLES, roleLabel } from "@/lib/role-label";
/** Read the last filters from localStorage (merged with defaults, tolerant of old shapes/dirty data) */
function loadFilters(): Filters {
  try {
    const raw = readKey("agentsFilters");
    if (!raw) return DEFAULT_FILTERS;
    const saved = JSON.parse(raw) as Partial<Filters>;
    return { ...DEFAULT_FILTERS, ...saved };
  } catch {
    return DEFAULT_FILTERS;
  }
}

/** Whether a single agent matches the filters */
function matchFilters(a: AgentRow, f: Filters): boolean {
  const k = kindOf(a);
  if (f.scope === "in_use" && k === "template") return false;
  if (f.scope === "template" && k !== "template") return false;
  if (f.role !== "__all__" && a.role !== f.role) return false;
  if (f.status !== "__all__" && a.status !== f.status) return false;
  if (f.project !== "__all__" && a.project_id !== f.project) return false;
  if (f.host !== "__all__" && a.host_id !== f.host) return false;
  if (f.q.trim()) {
    const kw = f.q.trim().toLowerCase();
    const hay = [a.id, a.name, a.role, a.project_id, a.originId ?? ""].join(" ").toLowerCase();
    if (!hay.includes(kw)) return false;
  }
  return true;
}

/* ───────────────────────── In-group sorting ───────────────────────── */

/**
 * Role weight: inside one project group the PM must come first, then descending by management/execution level.
 * Roles not listed fall into the worker tier (last).
 */
const ROLE_WEIGHT: Record<string, number> = {
  project_manager: 0,   // PM always first
  workshop_manager: 1,
  host_manager: 2,
  agent_manager: 3,
  worker: 4,
};

/** Role order (PM → other management → worker) */
function roleRank(role: string): number {
  return ROLE_WEIGHT[role] ?? 4;
}

/**
 * In-group sorting: PM first → then by role weight → same role, online first → same state, most recently active first.
 * A stable sort keeps unrelated items in their relative order.
 */
function compareAgentsInGroup(a: AgentRow, b: AgentRow): number {
  const ra = roleRank(a.role);
  const rb = roleRank(b.role);
  if (ra !== rb) return ra - rb;
  const oa = a.status === "online" ? 0 : 1;
  const ob = b.status === "online" ? 0 : 1;
  if (oa !== ob) return oa - ob;
  return (b.lastActiveAt || "").localeCompare(a.lastActiveAt || "");
}

/* ───────────────────────── Page ───────────────────────── */

/**
 * [2026-11-05 23:39 Feng] "For the agent page, put it into this popup dialog and
 *   carry the query condition over; that is the most perfect solution."
 *
 * So it is split into two layers here:
 *   · `AgentsBoard` (this function) = the **page body**, shared by the route page and the dialog
 *   · `AgentsPage` (end of file) = thin shell, only reads `?host=` then renders Board
 *
 * Why it must be shared instead of "drawing another list in the dialog":
 *   a self-made second UI ⇒ filters/sorting/grouping/view toggle/columns evolve separately, and sooner or later
 *   there are two different looks. What Feng keeps saying — "don't invent your own" — means in practice:
 *   **write the same thing once, then put it in another container**.
 */
export function AgentsBoard({
  initialHost,
  initialStatus,
  embedded = false,
}: {
  /** Query conditions carried in beforehand (used by the dialog; the page passes them via ?host=) */
  initialHost?: string;
  /** Status condition carried in beforehand (the 4 "agent status" cells on the dashboard: online / offline) */
  initialStatus?: string;
  /** Whether embedded in a dialog: no reading/writing of the local filter archive, no navigation, clicking a card stacks a detail dialog */
  embedded?: boolean;
}) {
  const { t } = useTranslation();
  const [view, setView] = React.useState<"card" | "table">("card");

  /* [2026-11-05 23:25 Feng] The number on the host card of the system status page must be clickable,
     landing on "all agents of this host are here".
     ★ When arriving with host, **release all other filters** (status/scope/role/project → all):
       otherwise, with this machine's last saved "online only / exclude templates", the list count would be lower
       than the number on the card, and the user's first reaction is "you over-counted" or "you gave less". */
  const [filters, setFilters] = React.useState<Filters>(() => {
    const base = loadFilters();
    /* Arriving with conditions (host / status) → release all other filters:
        otherwise this machine's last saved "online only / exclude templates" would make the list count lower than
        the dashboard number, and the two sides would not reconcile. */
    if (!initialHost && !initialStatus) return base;
    return {
      scope: "all",
      role: "__all__",
      status: initialStatus ?? "__all__",
      project: "__all__",
      host: initialHost ?? "__all__",
      q: "",
    };
  });

  // Persist when filters change (restored after closing and reopening). ★ Filters inside the dialog are "temporary viewing",
  // once written to the archive, the next normal open of /agents would carry filters chosen in the dialog.
  React.useEffect(() => {
    if (embedded) return;
    try {
      writeKey("agentsFilters", JSON.stringify(filters));
    } catch {
      /* Silently degrade when localStorage is unavailable */
    }
  }, [filters, embedded]);
  const navigate = useNavigate();
  const popup = useDetailPopup();

  /* The selected card (kept across "list → detail → back").
     The logic has been extracted to @/lib/card-selection, sharing one implementation and one set of visuals with
     the quick command grid — two implementations would drift into two looks, and what this round fixes is exactly
     "several ways of doing the same thing in one product". */
  const cards = useCardSelection({ storageKey: "agentgeex.agents.selected", testIdPrefix: "agent-card-" });
  const selectedId = cards.selectedId;

  const open = (id: string) => {
    cards.select(id);
    /* Clicking a card inside the dialog **must not navigate** — navigating would replace the system status page, and
        closing the dialog could not bring it back, exactly the "jump away and you can never come back" Feng mentioned.
        Instead stack an entity detail dialog on top (same stack). */
    if (embedded && popup?.open) {
      popup.open({ kind: "agent", id });
      return;
    }
    navigate(`/agents/${id}`);
  };
  /* [2026-11-02 Feng] "Send message" on the card (then called "unified input"): the dialog is mounted at page level
     (not one per card), only one can be open at a time — a dozen cards each carrying a 1311-line dialog component
     would be wasted DOM. */
  const [inputAgent, setInputAgent] = React.useState<string | null>(null);

  /* [2026-11-06 11:22 Feng] The three card metrics (session/task/execution) → dialog, no page jump.
     ★ Two things recorded here, do not scatter them into each card:
       ① Only one dialog can be open at a time → a single state holds "which cell of which Agent is open now"
       ② **After closing, return focus to the cell button that triggered it** (Feng: "after coming back focus is
          still on it"). The benefit is not just looks: a keyboard user who Tabs through the card is not thrown back
          to the top of the page. */
  const [metric, setMetric] = React.useState<
    { agentId: string; kind: MetricKind } | null
  >(null);
  /* The cell clicked last — **not cleared** after the dialog closes, used to draw the "I just clicked here" frame.
     ★ Two separate states: metric controls "is the dialog open", metricMark controls "which cell the frame is on".
       Merging them would make the frame disappear as soon as the dialog closes, i.e. "after coming back you cannot
       tell what you just clicked". */
  const [metricMark, setMetricMark] = React.useState<{ agentId: string; kind: MetricKind } | null>(null);
  const metricElRef = React.useRef<HTMLElement | null>(null);
  const openMetric = (agentId: string, kind: MetricKind, el: HTMLElement) => {
    metricElRef.current = el;
    setMetricMark({ agentId, kind });
    setMetric({ agentId, kind });
  };
  const closeMetric = () => {
    setMetric(null);
    /* Wait for the dialog to unmount (Radix close animation) before returning focus, otherwise it takes focus back to
       body itself. Return both DOM focus and the visual frame: the former for the keyboard, the latter for mouse users. */
    window.setTimeout(() => metricElRef.current?.focus({ preventScroll: true }), 60);
  };


  // All agents (grouping is display structure only; flatten first, filter, then regroup by project)
  const allGroups = React.useMemo(() => agentsGroupedByProject(), []);
  const allAgents = React.useMemo(() => allGroups.flatMap((g) => g.agents), [allGroups]);
  /* The dialog title needs the Agent name ("Book Writer · Sessions"), taken from the full set so filters do not affect it */
  const metricAgent = React.useMemo(
    () => (metric ? allAgents.find((a) => a.id === metric.agentId) : undefined),
    [metric, allAgents],
  );
  const groups = React.useMemo(() => {
    const kept = allAgents.filter((a) => matchFilters(a, filters));
    const byProject = new Map<string, AgentRow[]>();
    for (const a of kept) {
      if (!byProject.has(a.project_id)) byProject.set(a.project_id, []);
      byProject.get(a.project_id)!.push(a);
    }
    return [...byProject.entries()].map(([projectId, agents]) => ({
      projectId,
      projectName: allGroups.find((g) => g.projectId === projectId)?.projectName || projectId,
      // In-group sorting: PM first → then role weight → online first → most recently active first (stable sort)
      agents: [...agents].sort(compareAgentsInGroup),
    }));
  }, [allAgents, allGroups, filters]);

  // Project dropdown options: aggregated dynamically from all agents (driven by real data, not the mock project table)
  // Counts react to the other current filters (template view/role/status/keyword) so users can predict before choosing.
  const projectOptions = React.useMemo(() => {
    const base = allAgents.filter((a) => matchFilters(a, { ...filters, project: "__all__" }));
    const m = new Map<string, number>();
    for (const a of base) m.set(a.project_id, (m.get(a.project_id) || 0) + 1);
    /* [2026-11-17 15:07] Pinned projects first, the rest by member count (old rule unchanged). */
    return sortByQuickProject(
      [...m.entries()]
        .map(([id, count]) => ({
          id,
          name: getProjectName(id) || t("agentsPage.unregisteredProject"),
          count,
        }))
        .sort((a, b) => b.count - a.count),
      (p) => p.id,
    );
  }, [allAgents, filters]);

  return (
    <div className="flex flex-col gap-2" data-testid={embedded ? "agents-board-embedded" : "page-agents"}>
      <div
        className={cn(
          "flex flex-col gap-4 bg-card",
          /* The dialog already has an outer frame; adding another border + padding makes a "frame inside a frame" */
          embedded ? "p-3" : "rounded-xl border border-strong p-4 shadow-sm",
        )}
        data-testid={embedded ? undefined : "content-frame"}
      >
        {/* [2026-10-28 16:32 Feng] Drop the page header title/description (navigation already has them); the old
            "showing N/M + grid/table" on the right of the header is merged into the end of the toolbar row, still one row. */}
        {/* Toolbar: keyword + template view + project + host + role + status (one full row, no wrapping) */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={filters.q}
              onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value }))}
              placeholder={t("agentsPage.search.ph")}
              aria-label={t("agentsPage.search.aria")}
              className="h-9 w-48 pl-8"
              data-testid="agents-search"
            />
          </div>

          <Select
            value={filters.scope}
            onValueChange={(v) => setFilters((f) => ({ ...f, scope: v as Filters["scope"] }))}
          >
            <SelectTrigger className="h-9 w-28" aria-label={t("agentsPage.scope.aria")} data-testid="agents-scope">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="in_use">{t("agentsPage.scope.inUse")}</SelectItem>
              <SelectItem value="all">{t("agentsPage.scope.all")}</SelectItem>
              <SelectItem value="template">{t("agentsPage.scope.templateOnly")}</SelectItem>
            </SelectContent>
          </Select>

          <Select
            value={filters.project}
            onValueChange={(v) => setFilters((f) => ({ ...f, project: v }))}
          >
            <SelectTrigger className="h-9 w-36" aria-label={t("agentsPage.project.ph")} data-testid="agents-project">
              <SelectValue placeholder={t("agentsPage.project.ph")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">
                {t("agentsPage.project.all", { n: projectOptions.length })}
              </SelectItem>
              {projectOptions.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  {projectNameWithPin(p.id, p.name)}（{p.count}）
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select
            value={filters.host}
            onValueChange={(v) => setFilters((f) => ({ ...f, host: v }))}
          >
            <SelectTrigger className="h-9 w-36" aria-label={t("agentsPage.card.host")} data-testid="agents-host">
              <SelectValue placeholder={t("agentsPage.card.host")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">{t("agentsPage.host.all")}</SelectItem>
              {hostOptions().map((h) => (
                <SelectItem key={h.value} value={h.value}>
                  <span className="inline-flex items-center gap-1.5">
                    <OnlineDot show={h.online} title={h.online ? t("agentsPage.status.online") : undefined} />
                    {h.label}
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select
            value={filters.role}
            onValueChange={(v) => setFilters((f) => ({ ...f, role: v }))}
          >
            <SelectTrigger className="h-9 w-28" aria-label={t("agentsPage.roleFilter.ph")} data-testid="agents-role">
              <SelectValue placeholder={t("agentsPage.roleFilter.ph")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">{t("agentsPage.roleFilter.all")}</SelectItem>
              {/* Same word list as the card role badge (roles.*); English is a bit longer, the dropdown width already accommodates it */}
              {AGENT_ROLES.map((r) => (
                <SelectItem key={r} value={r}>
                  {roleLabel(r, t)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select
            value={filters.status}
            onValueChange={(v) => setFilters((f) => ({ ...f, status: v }))}
          >
            <SelectTrigger className="h-9 w-[88px]" aria-label={t("agentsPage.status.ph")} data-testid="agents-status">
              <SelectValue placeholder={t("agentsPage.status.ph")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">{t("agentsPage.status.all")}</SelectItem>
              <SelectItem value="online">{t("agentsPage.status.online")}</SelectItem>
              <SelectItem value="offline">{t("agentsPage.status.offline")}</SelectItem>
            </SelectContent>
          </Select>

          {/* [2026-10-29 Feng] There used to be a "showing 74/101" counter here,
              left of the grid/table toggle. It is neither pagination (all data is in frontend memory),
              nor the real total (it is an intermediate "after filtering / before removing templates") → easy to misread, removed.
              The real count is in the pager at the bottom of the page (N rows in total). */}
          <div className="ml-auto flex items-center gap-2">
            <div className="flex items-center rounded-md border p-0.5" role="group" aria-label={t("agentsPage.view.aria")}>
              <Button variant={view === "card" ? "secondary" : "ghost"} size="sm" className="h-7 gap-1 px-2"
                onClick={() => setView("card")} title={t("agentsPage.view.cardTitle")} data-testid="agents-view-card">
                <LayoutGrid className="h-4 w-4" /> {t("agentsPage.view.card")}
              </Button>
              <Button variant={view === "table" ? "secondary" : "ghost"} size="sm" className="h-7 gap-1 px-2"
                onClick={() => setView("table")} title={t("agentsPage.view.tableTitle")} data-testid="agents-view-table">
                <List className="h-4 w-4" /> {t("agentsPage.view.table")}
              </Button>
            </div>
          </div>

        </div>

        {groups.length === 0 ? (
          <div className="rounded-lg border border-dashed py-16 text-center text-sm text-muted-foreground">
            {t("agentsPage.empty")}
          </div>
        ) : (
          groups.map((g) => (
            <ProjectSection
              key={g.projectId}
              group={g}
              view={view}
              selectedId={selectedId}
              open={open}
              onInput={(id) => setInputAgent(id)}
              onMetric={openMetric}
              metricMark={metricMark}
            />
          ))
        )}

        {/* The card and the detail page share the same send-message dialog (full four-layer set), with the same prefill:
            sender = target = this Agent. Deliberately no locked/hidden ——
            if the card entry offered fewer choices than the detail page, users would think "the grid side is a simplified version". */}
        {/* [2026-11-06 11:22] The dialog for the three card metrics (page level, only one open at a time) */}
        <AgentSessionsDialog
          agentName={metricAgent?.name ?? ""}
          agentId={metric?.agentId ?? ""}
          count={metric?.kind === "sessions" ? metricAgent?.sessionCount : undefined}
          open={metric?.kind === "sessions"}
          onOpenChange={(o) => { if (!o) closeMetric(); }}
        />
        <AgentTasksDialog
          agentName={metricAgent?.name ?? ""}
          agentId={metric?.agentId ?? ""}
          count={metric?.kind === "tasks" ? metricAgent?.taskCount : undefined}
          open={metric?.kind === "tasks"}
          onOpenChange={(o) => { if (!o) closeMetric(); }}
        />
        <AgentExecutionsDialog
          agentName={metricAgent?.name ?? ""}
          agentId={metric?.agentId ?? ""}
          count={metric?.kind === "executions" ? metricAgent?.execCount : undefined}
          open={metric?.kind === "executions"}
          onOpenChange={(o) => { if (!o) closeMetric(); }}
        />

        <MessageSendDialog
          open={inputAgent !== null}
          onOpenChange={(o) => {
            if (!o) setInputAgent(null);
          }}
          /* [2026-11-15 15:42 Feng] Entering from the grid, the project must land on **the project this Agent belongs to**:
             "Inside this project, the project of the current agent is not selected."
             Previously only sender/target were passed, so the project dropdown always stayed at "all projects" —
             yet every Agent in the grid is already grouped by project, so people know at a glance which project it is. */
          initialProject={inputAgent ? allAgents.find((a) => a.id === inputAgent)?.project_id : undefined}
          initial={inputAgent ? { senderAgent: inputAgent, targetAgent: inputAgent } : undefined}
        />
      </div>
    </div>
  );
}

/**
 * Route page thin shell: does the one thing only a page can do — consume `?host=` from the URL (the dialog gets it via props).
 * The parameter is stripped after one consumption: leaving it in the URL means a later manual filter change gets pulled
 * back by ?host= on refresh.
 */
export default function AgentsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const host = searchParams.get("host") ?? "";

  React.useEffect(() => {
    if (!searchParams.get("host")) return;
    const next = new URLSearchParams(searchParams);
    next.delete("host");
    setSearchParams(next, { replace: true });
    // Consume only once on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <AgentsBoard initialHost={host} />;
}

/** A single project group (collapsible) */
function ProjectSection({
  group: g, view, selectedId, open, onInput, onMetric, metricMark,
}: {
  group: { projectId: string; projectName: string; agents: AgentRow[] };
  view: "card" | "table";
  /** The card opened last (restored from sessionStorage), null = none */
  selectedId: string | null;
  open: (id: string) => void;
  onInput: (id: string) => void;
  onMetric: (id: string, kind: MetricKind, el: HTMLElement) => void;
  /** The cell opened last (agentId + kind) — used to draw the frame on that cell */
  metricMark: { agentId: string; kind: MetricKind } | null;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = React.useState(true);
  return (
    <div data-testid={`agent-group-${g.projectId}`} className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <button
          className="flex items-center gap-2 text-sm font-semibold"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          title={g.projectId}
        >
          <ChevronRight className={cn("h-4 w-4 transition-transform", expanded && "rotate-90")} />
          {g.projectName}
        </button>
        <Badge variant="secondary" className="text-xs">{g.agents.length}</Badge>
      </div>
      {expanded && (
        view === "card" ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {g.agents.map((a) => (
              <AgentCard
                key={a.id}
                agent={a}
                selected={a.id === selectedId}
                onOpen={() => open(a.id)}
                onInput={() => onInput(a.id)}
                onMetric={(kind, el) => onMetric(a.id, kind, el)}
                activeMetric={metricMark?.agentId === a.id ? metricMark.kind : null}
              />
            ))}
          </div>
        ) : (
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("agentsPage.table.agent")}</TableHead>
                  <TableHead>{t("agentsPage.table.platform")}</TableHead>
                  <TableHead>{t("agentsPage.table.kind")}</TableHead>
                  <TableHead>{t("agentsPage.table.deploy")}</TableHead>
                  <TableHead>{t("agentsPage.table.role")}</TableHead>
                  <TableHead>{t("agentsPage.table.status")}</TableHead>
                  <TableHead className="text-right">{t("agentsPage.table.sessions")}</TableHead>
                  <TableHead className="text-right">{t("agentsPage.table.tasks")}</TableHead>
                  <TableHead className="text-right">{t("agentsPage.table.executions")}</TableHead>
                  <TableHead>{t("agentsPage.table.lastActive")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody striped>
                {g.agents.map((a) => {
                  const sum = executionSummary(executionsOf(a.id));
                  const isPm = a.role === "project_manager";
                  return (
                    <TableRow
                      key={a.id}
                      data-pm={isPm ? "1" : undefined}
                      className={cn(
                        "cursor-pointer",
                        /* The PM row background must use the even: variant, otherwise it sits under the zebra striping (the even variant has higher specificity) */
                        isPm && "bg-chart-1/[0.10] even:bg-chart-1/[0.15] hover:bg-chart-1/20"
                      )}
                      onClick={() => open(a.id)}
                    >
                      <TableCell className="font-medium">
                        <div className="flex min-w-0 items-center gap-2">
                          <AgentAvatar name={a.name} id={a.id} avatarUrl={a.avatarUrl} className="h-7 w-7" textClassName="text-[11px]" />
                          <div className="min-w-0">
                            <div className="truncate">{a.name}</div>
                            <div className="truncate font-mono text-[11px] text-muted-foreground">{a.id}</div>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell>
                        <PlatformLogo
                          type={a.agentType}
                          href={chatDeepLink(a.id, a.agentType, a.host_id)}
                          className="h-8 w-8"
                          imgClassName="h-[18px] w-[18px]"
                        />
                      </TableCell>
                      <TableCell><KindTag agent={a} /></TableCell>
                      <TableCell><DeployTag status={a.deployStatus} /></TableCell>
                      <TableCell><RoleTag role={a.role} /></TableCell>
                      <TableCell><StatusPill status={a.status} /></TableCell>
                      <TableCell className="text-right">{sessionsOf(a.id).length || a.sessionCount}</TableCell>
                      <TableCell className="text-right">{a.taskCount ?? tasksOf(a.id).length}</TableCell>
                      <TableCell className="text-right">{a.execCount || sum.total}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">{a.lastActiveAt}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )
      )}
    </div>
  );
}

export { cn };
