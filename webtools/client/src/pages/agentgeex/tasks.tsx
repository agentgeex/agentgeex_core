/** Agent management · Task list (/tasks) -- the list of task "definitions" (as opposed to execution records)
 *
 * [2026-11-06 11:22 Feng] "Do not open another window in that navigation bar anymore... once you open a
 *   window the focus cannot go back" -- "Tasks" on the agent card becomes a **dialog** from now on, so this
 *   page must fit inside a dialog.
 *
 * Structure is exactly the same as sessions / executions (one paradigm, no second one invented):
 *   TasksBoard = the body (shared by the route page and the dialog), TasksPage = thin shell (only reads URL
 *   params).
 *   embedded is absorbed by Board itself in three ways:
 *     ① does not write the local filter archive (stateKey=undefined) -- filters inside a dialog are
 *        "a temporary look" and must not overwrite page memory
 *     ② clicking a row does not navigate, it stacks an **entity detail dialog** (navigating inside a dialog
 *        destroys the outer page)
 *     ③ no box in a box (the dialog already is the box)
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { RowAction, RowActions } from "@/components/agentgeex/row-actions";
import { DataTable, type ColumnDef } from "@/components/agentgeex/data-table";
import { Badge } from "@/components/ui/badge";
import { useNavigate, useSearchParams } from "react-router-dom";
import { listTasks, type TaskRow } from "@/providers/agentgeex";
import { EntityLogo } from "@/components/agentgeex/entity-logo";
import { agentFilterOptions, agentInitialFilter } from "@/lib/agent-filter";
import { useDetailPopup } from "@/components/agentgeex/detail-popup";
import { cn } from "@/lib/utils";

/* Headers are text -> factories (module-level constants cannot use hooks). */
function taskColumns(t: (k: string, o?: Record<string, unknown>) => string): ColumnDef<TaskRow>[] {
  return [
  {
    key: "description", header: t("tasksPage.colTask"), cardSubtitle: true,
    /* [2026-10-30 07:06] truncate was always there but without max-width -> it had no effect, so this column took
        534px. After font +1 the whole table pushed to the edge of the white box. Add the width cap so
        truncate actually works. */
    className: "max-w-[280px]",
    render: (r) => (
      <span className="flex min-w-0 items-center gap-2">
        <EntityLogo kind="task" id={r.id} name={r.name} logoUrl={r.logoUrl} />
        <span className="min-w-0 truncate" title={r.description}>{r.description}</span>
      </span>
    ),
    /* [2026-11-05 19:20 Feng] "You put the description in that blank space, that blank space should hold the name"
       -- the grid cell title now holds task_name (the name), the description becomes the subtitle, and the
       subtitle no longer uses the icon/column-width table styling. */
    cardRender: (r) => r.description || "—",
  },
  { key: "name", header: "task_name", cardTitle: true, className: "font-mono text-xs" },
  { key: "agentName", header: t("tasksPage.colAgent"), sortable: true },
  {
    key: "labs", header: t("tasksPage.colLabs"),
    render: (r) => <Badge variant="outline" className="font-mono text-xs">{r.labs}</Badge>,
  },
  {
    key: "isPrivate", header: t("tasksPage.colVisibility"),
    render: (r) => (r.isPrivate
      ? <Badge variant="secondary">{t("tasksPage.private")}</Badge>
      : <span className="text-muted-foreground">{t("tasksPage.shared")}</span>),
  },
  {
    key: "activeStatus", header: t("tasksPage.colStatus"),
    render: (r) => (r.activeStatus
      ? <Badge variant="outline" className="border-success/30 bg-success/15 text-success font-normal">{t("tasksPage.enabled")}</Badge>
      : <Badge variant="outline" className="text-muted-foreground font-normal">{t("tasksPage.disabled")}</Badge>),
  },
  { key: "updatedAt", header: t("tasksPage.colUpdated"), sortable: true },
  ];
}

export function TasksBoard({
  initialFilters,
  embedded = false,
}: {
  /** Preset filters (for the dialog; e.g. { agentId: "xxx" }) */
  initialFilters?: Record<string, string>;
  embedded?: boolean;
}) {
  const { t } = useTranslation();
  const data = listTasks();
  const columns = React.useMemo(() => taskColumns(t), [t]);
  const navigate = useNavigate();
  const popup = useDetailPopup();
  /* [2026-10-29 Feng] Jumped in from the "tasks" metric on an agent card: ?agent=xxx -> only that agent's tasks */
  const [sp] = useSearchParams();
  const initialFilterVals = {
    ...(agentInitialFilter(sp.get("agent")) ?? {}),
    ...(initialFilters ?? {}),
  };
  const open = (id: string) => {
    /* Clicking a row inside the dialog must not navigate -- navigating destroys the outer layer */
    if (embedded && popup?.open) {
      popup.open({ kind: "task", id });
      return;
    }
    navigate(`/tasks/${encodeURIComponent(id)}`);
  };
  return (
    <div className="flex flex-col gap-2" data-testid={embedded ? "tasks-board-embedded" : "page-tasks"}>
      {/* [2026-10-28 16:32 Feng] Follow the host page format: drop the header title/description (navigation already has them), wrap content in a white box. */}
      <div
        className={cn(
          "flex flex-col gap-4 bg-card",
          embedded ? "p-3" : "rounded-xl border border-strong p-4 shadow-sm",
        )}
        data-testid={embedded ? undefined : "content-frame"}
      >
        <DataTable
          /* [2026-11-02 Feng] Preserve the conditions the user set after a refresh (search/filters/page/per-page/sort/view); without this line = refresh resets to initial. */
          stateKey={embedded ? undefined : "tasks"}
          data={data}
          columns={columns}
          searchKeys={["description", "name", "agentName"]}
          searchPlaceholder={t("tasksPage.searchPlaceholder")}
          initialFilterVals={initialFilterVals}
          onRowClick={(r) => open(r.id)}
          /* [2026-11-01 Feng] Clicking a row is the detail, so the row action becomes "edit" --
              the detail needs no second entry, but edit must have one. */
          rowActions={(r) => (
            <RowActions>
              <RowAction kind="edit" to={`/tasks/${encodeURIComponent(r.id)}/edit`} testId={`task-edit-${r.id}`} />
            </RowActions>
          )}
          filters={[
            {
              key: "labs", label: t("tasksPage.fLabs"),
              options: [
                { label: "ui_lead", value: "ui_lead" },
                { label: "general", value: "general" },
                { label: "universal", value: "universal" },
                { label: "agent_create", value: "agent_create" },
                { label: "agent_deploy", value: "agent_deploy" },
              ],
            },
            {
              key: "activeStatus", label: t("tasksPage.fStatus"),
              options: [
                { label: t("tasksPage.enabled"), value: "1" },
                { label: t("tasksPage.disabled"), value: "0" },
              ],
            },
            /* "Agent" filter: already in place when jumping in from a card (?agent=), and can be switched manually */
            { key: "agentId", label: t("tasksPage.fAgent"), options: agentFilterOptions(data) },
          ]}
        />
      </div>
    </div>
  );
}

/** Route page thin shell (the dialog passes conditions via props, not URL) */
export default function TasksPage() {
  return <TasksBoard />;
}
