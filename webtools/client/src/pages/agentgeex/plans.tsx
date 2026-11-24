/** Plans (/plans) — cron / scheduled task list and execution status */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { QuickStar } from "@/components/agentgeex/quick-star";
import { useNavigate } from "react-router-dom";
import { User, Network } from "lucide-react";
import { DataTable, StatusPill, type ColumnDef } from "@/components/agentgeex/data-table";
import { Progress } from "@/components/ui/progress";
import { RowAction, RowActions } from "@/components/agentgeex/row-actions";
import { listPlans, sortByQuickProject, projectNameWithPin, type PlanRow } from "@/providers/agentgeex";
import { hostOptions, hostOfAgent, HOST_FILTER_LABEL_KEY } from "@/lib/host-filter";
import { useDetailPopup } from "@/components/agentgeex/detail-popup";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { PlanFlow } from "@/pages/agentgeex/plan-flow";
import { cn } from "@/lib/utils";

/* columns = a factory taking t (headers are copy; a module-level constant cannot reach the hook). */
function planColumns(t: (k: string, o?: Record<string, unknown>) => string): ColumnDef<PlanRow>[] {
  return [
  {
    key: "name", header: t("plansPage.colName"), cardTitle: true, sortable: true,
    /* [2026-10-29 Feng] The plan name column used to eat most of the width, squeezing 'trigger' into two lines.
       Cap the width + single-line truncation (hover title for the full name), so the columns on the right get normal widths. */
    className: "max-w-[320px]",
    render: (r) => (
      <span className="block max-w-[320px] truncate" title={r.name}>{r.name}</span>
    ),
  },
  { key: "projectName", header: t("plansPage.colProject"), cardSubtitle: true, sortable: true },
  {
    key: "ownerName", header: t("plansPage.colOwner"), sortable: true, className: "whitespace-nowrap",
    render: (r) => (
      <span className="inline-flex items-center gap-1 text-xs">
        <User className="h-3 w-3 opacity-60" />
        {r.ownerName || r.owner || "—"}
      </span>
    ),
  },
  {
    key: "trigger", header: t("plansPage.colTrigger"), className: "whitespace-nowrap",
    render: (r) => <span className="whitespace-nowrap font-mono text-xs">{r.trigger}</span>,
  },
  {
    key: "status", header: t("plansPage.colStatus"), sortable: true, className: "whitespace-nowrap",
    render: (r) => <StatusPill status={r.status} />,
  },
  {
    key: "doneSteps", header: t("plansPage.colDone"), className: "whitespace-nowrap",
    render: (r) => {
      const pct = r.totalSteps ? Math.round((r.doneSteps / r.totalSteps) * 100) : 0;
      return (
        <div className="flex items-center gap-2">
          <Progress value={pct} className="h-1.5 w-16" />
          <span className="text-xs text-muted-foreground">{r.doneSteps}/{r.totalSteps}</span>
        </div>
      );
    },
  },
  { key: "nextRunAt", header: t("plansPage.colNextRun"), sortable: true, className: "whitespace-nowrap" },
  ];
}

/**
 * [2026-11-06 00:03 Feng 'do the rest the same way'] Split into two layers:
 * `PlansBoard` = the body (shared by the route page and the dialog), `PlansPage` = a thin shell.
 * The dialog carries initial conditions: { status: "completed" } (the board's 6 'plan execution' cells).
 */
export function PlansBoard({
  initialFilters,
  embedded = false,
}: {
  initialFilters?: Record<string, string>;
  embedded?: boolean;
}) {
  const { t } = useTranslation();
  const popup = useDetailPopup();
  const data = listPlans();
  const columns = React.useMemo(() => planColumns(t), [t]);
  const navigate = useNavigate();
  /* [2026-11-06 00:09 Feng] 'when coming from a popped window, the flow chart should pop too; otherwise keep it as before'
     — only when embedded in a dialog is an extra flow chart dialog stacked; the normal /plans page still navigates full page. */
  const [flowId, setFlowId] = React.useState<string | null>(null);
  const openFlow = (id: string) => {
    if (embedded) {
      setFlowId(id);
      return;
    }
    navigate(`/plans/${encodeURIComponent(id)}/flow`);
  };
  const open = (id: string) => {
    /* Clicking a row in the dialog does not navigate → stack a plan detail dialog */
    if (embedded && popup?.open) {
      popup.open({ kind: "plan", id });
      return;
    }
    navigate(`/plans/${encodeURIComponent(id)}`);
  };

  // Project filter options: dedupe from the data (projectId → projectName)
  const projectOptions = React.useMemo(() => {
    const map = new Map<string, string>();
    for (const p of data) {
      if (!p.projectId) continue;
      if (!map.has(p.projectId)) map.set(p.projectId, p.projectName || p.projectId);
    }
    /* [2026-11-17 15:07] Pinned project goes first (★ marker), the rest sorted by name. */
    return sortByQuickProject(
      [...map.entries()]
        .map(([value, label]) => ({ label: projectNameWithPin(value, label), value }))
        .sort((a, b) => a.label.localeCompare(b.label, "zh-CN")),
      (x) => x.value,
    );
  }, [data]);

  // Owner filter options: dedupe from the data (owner → ownerName)
  const ownerOptions = React.useMemo(() => {
    const map = new Map<string, string>();
    for (const p of data) {
      if (!p.owner) continue;
      if (!map.has(p.owner)) map.set(p.owner, p.ownerName || p.owner);
    }
    return [...map.entries()]
      .map(([value, label]) => ({ label, value }))
      .sort((a, b) => a.label.localeCompare(b.label, "zh-CN"));
  }, [data]);

  return (
    <div className="flex flex-col gap-2" data-testid={embedded ? "plans-board-embedded" : "page-plans"}>
      {/* [2026-10-28 16:32 Feng] Follow the compute host page format: drop the page title/description (the nav already has them), wrap the content in a white frame. */}
      <div
        className={cn(
          "flex flex-col gap-4 bg-card",
          embedded ? "p-3" : "rounded-xl border border-strong p-4 shadow-sm",
        )}
        data-testid={embedded ? undefined : "content-frame"}
      >
        <DataTable
          /* [2026-11-02 Feng] Keep the user's conditions after a refresh (search/filter/page/page size/sort/view); without this line a refresh resets to initial. */
          stateKey={embedded ? undefined : "plans"}
          data={data}
          columns={columns}
          searchKeys={["name", "projectName", "ownerName", "trigger"]}
          initialFilterVals={initialFilters}
          searchPlaceholder={t("plansPage.searchPlaceholder")}
          filters={[
            {
              key: "projectId", label: t("plansPage.fProject"),
              getValue: (r) => r.projectId,
              options: projectOptions,
            },
            {
              key: "owner", label: t("plansPage.fOwner"),
              getValue: (r) => r.owner,
              options: ownerOptions,
            },
            {
              key: "status", label: t("plansPage.fStatus"),
              /* ★ 2026-11-03: options follow the 7 values of the agx_plan.plan_status source of truth
                 (failed is a reserved value with no write path today, always 0 rows, but Feng asked to include it —
                  it is part of the truth source, so no need to change this again when writes appear). */
              /* label comes from the global status.* — one status vocabulary site-wide (value is the stored value, untouched). */
              options: [
                { label: t("status.activated"), value: "activated" },
                { label: t("status.draft"), value: "draft" },
                { label: t("status.cancelled"), value: "cancelled" },
                { label: t("status.cancelling"), value: "cancelling" },
                { label: t("status.completed"), value: "completed" },
                { label: t("status.archived"), value: "archived" },
                /* ★ 2026-11-03 Feng: failed can be added. There is no write path today (always 0 rows),
                    but it is one of the 7 truth-source values — when writes appear, no change is needed here. */
                { label: t("status.failed"), value: "failed" },
              ],
            },
            {
              key: "__host", label: t(HOST_FILTER_LABEL_KEY),
              getValue: (r) => hostOfAgent(r.owner),
              options: hostOptions(),
            },
          ]}
          onRowClick={(r) => open(r.id)}
          /* [2026-11-01 Feng] Unified into a button: detail via row click, keeping only 'flow chart' as the real action */
          rowActions={(r) => (
            <RowActions>
              <QuickStar type="plan" entryKey={r.id} label={r.name} testId={`plan-quick-${r.id}`} />
              <RowAction
                kind="flow"
                onClick={() => openFlow(r.id)}
                testId={`agx-plan-flow-${r.id}`}
              />
            </RowActions>
          )}
        />
      </div>

      {/* Flow chart dialog (only used when 'the plan list itself is also in a dialog') */}
      {embedded && (
        <Dialog open={!!flowId} onOpenChange={(o) => { if (!o) setFlowId(null); }}>
          <DialogContent
            className="flex h-[94vh] w-[min(98vw,1600px)] max-w-none flex-col gap-0 overflow-hidden p-0"
            data-testid="plan-flow-dialog"
            aria-describedby={undefined}
          >
            <DialogTitle className="sr-only">{t("plansPage.flowDialogTitle")}</DialogTitle>
            <div className="min-h-0 flex-1 overflow-hidden p-3" data-testid="plan-flow-dialog-body">
              {flowId && (
                <PlanFlow planId={flowId} embedded onClose={() => setFlowId(null)} />
              )}
            </div>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}

/** Thin shell for the route page (the dialog passes conditions via props, not the URL) */
export default function PlansPage() {
  return <PlansBoard />;
}
