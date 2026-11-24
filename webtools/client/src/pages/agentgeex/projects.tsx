/** Project management · project list (/projects) */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { QuickStar } from "@/components/agentgeex/quick-star";
import { useDetailPopup } from "@/components/agentgeex/detail-popup";
import { cn } from "@/lib/utils";
import { DataTable, StatusPill, type ColumnDef } from "@/components/agentgeex/data-table";
import { useNavigate } from "react-router-dom";
import { listProjects, refreshPageData, type ProjectRow } from "@/providers/agentgeex";
import { projectsApi, type ProjectDetail } from "@/providers/api";
import { RowAction, RowActions } from "@/components/agentgeex/row-actions";
import { ProjectEditDialog } from "@/components/agentgeex/project-edit-dialog";
import { useToast } from "@/components/ui/toast";
import { EntityLogo } from "@/components/agentgeex/entity-logo";
import { AgentMemberChips } from "@/components/agentgeex/agent-member-chips";

/**
 * [2026-11-04 Feng] Three merges:
 *   · the whole "platform" column removed — a project's members may span hosts/platforms, the field no longer holds;
 *   · "agent count" + "project manager" merged into "project members" —
 *     the project manager comes first, in its own color, other members follow;
 *   · too many members to fit collapse into "+N", click to see all in a dialog (each is a link to agent details).
 *   Why merge: the project manager is already one of the members, splitting into two columns counts the same
 *   person twice, and "agent count" gives only a number without names — the user must click again to know who.
 */
/* Headers are copy → a factory (module-level constants cannot access the hook). */
function projectColumns(t: (k: string) => string): ColumnDef<ProjectRow>[] {
  return [
  {
    key: "name", header: t("projectsPage.colName"), cardTitle: true, sortable: true,
    render: (r) => (
      <span className="flex min-w-0 items-center gap-2">
        <EntityLogo kind="project" id={r.id} name={r.name} logoUrl={r.logoUrl} />
        <span className="truncate font-medium">{r.name}</span>
      </span>
    ),
  },
  { key: "abbr", header: t("projectsPage.colAbbr"), cardSubtitle: true, className: "font-mono text-xs" },
  {
    key: "agentCount", header: t("projectsPage.colMembers"),
    className: "min-w-[300px]",
    sortable: true,
    /* [2026-11-04 13:53 Feng] "Show everything, wrapping is fine, stop adding plus two plus three"
       —— the old "+N" turned "cannot see it all" into "click once more", while that column exists to be
       scanned at a glance. Ugly wrapping beats "two names missing". */
    render: (r) => <AgentMemberChips members={r.members} />,
  },
  /* the member column now wraps and easily eats the remaining width → headers get squeezed into vertical text
      (one character per line). Both these columns hold fixed-length content, so nowrap keeps them from degrading. */
  {
    key: "status", header: t("projectsPage.colStatus"), sortable: true, className: "whitespace-nowrap",
    render: (r) => <StatusPill status={r.status} />,
  },
  /* [2026-11-04 21:31 Feng] "In the project table drop the update time and use create_at"
     —— that column read `updatedAt`, while BFF always returned an empty string (agx_project has no updated column),
     a whole blank column = a column that **looks present but is empty**. Switched to the real created_at in the DB. */
  { key: "createdAt", header: t("projectsPage.colCreated"), sortable: true, className: "whitespace-nowrap" },
  ];
}

/**
 * [2026-11-06 00:03 Feng "do the same for all the others"] Split into two layers:
 * `ProjectsBoard` = the body (shared by route page and dialog), `ProjectsPage` = thin shell.
 * The dialog takes an initial condition: { status: "active" | "archived" } (dashboard "project overview" 3 cells).
 */
export function ProjectsBoard({
  initialFilters,
  embedded = false,
}: {
  initialFilters?: Record<string, string>;
  embedded?: boolean;
}) {
  const { t } = useTranslation();
  const popup = useDetailPopup();
  const data = listProjects();
  const columns = React.useMemo(() => projectColumns(t), [t]);
  const navigate = useNavigate();
  const { toast } = useToast();
  const open = (id: string) => {
    /* clicking a row in the dialog does not navigate → stack a project detail dialog */
    if (embedded && popup?.open) {
      popup.open({ kind: "project", id });
      return;
    }
    navigate(`/projects/${encodeURIComponent(id)}`);
  };

  /**
   * [2026-11-04 21:31 Feng] "Put editing inside uniformly, see it clearly first then act conveniently"
   * —— editing is unified **inline** (same as host/task/skill/plan/quick command):
   *   look at a row → click edit on that row, no need to open details then use the page header.
   *
   * ★ Why fetch detail once before opening the dialog: the list snapshot only has ProjectRow (list fields),
   *   while the edit dialog needs heavy fields the list does not expose, such as x_desc / source_dir.
   *   Opening without fetching = those form fields are empty, and saving wipes the DB content.
   */
  const [editing, setEditing] = React.useState<ProjectDetail | null>(null);
  const [editOpen, setEditOpen] = React.useState(false);
  const [loadingId, setLoadingId] = React.useState("");

  const openEdit = async (r: ProjectRow) => {
    setLoadingId(r.id);
    try {
      // project_id may contain special characters, encode uniformly (same path-param convention as the detail page)
      const d = await projectsApi.detail(r.id);
      setEditing(d);
      setEditOpen(true);
    } catch (err) {
      const e2 = err as { message?: string };
      toast({ title: t("projectsPage.openEditFailed"), description: e2.message || String(err), variant: "destructive" });
    } finally {
      setLoadingId("");
    }
  };

  return (
    <div className="flex flex-col gap-2" data-testid={embedded ? "projects-board-embedded" : "page-projects"}>
      {/* [2026-10-28 16:32 Feng] Follow the host page format: page header title/description removed (the nav has
   them), content wrapped uniformly in a white frame. */}
      <div
        className={cn(
          "flex flex-col gap-4 bg-card",
          embedded ? "p-3" : "rounded-xl border border-strong p-4 shadow-sm",
        )}
        data-testid={embedded ? undefined : "content-frame"}
      >
        <DataTable
          /* [2026-11-02 Feng] Keep the user's conditions across a refresh (search/filter/page/page size/sort/view);
   without this line, a refresh returns to the initial state. */
          stateKey={embedded ? undefined : "projects"}
          data={data}
          columns={columns}
          pageSize={20}
          searchKeys={["name", "abbr", "adminAgentId"]}
          searchPlaceholder={t("projectsPage.searchPlaceholder")}
          onRowClick={(r) => open(r.id)}
          initialFilterVals={initialFilters}
          filters={[
            /* the "platform" filter goes away with the column: the field no longer holds, keeping the filter would make
   people filter by a value they cannot see. */
            {
              key: "status", label: t("projectsPage.fStatus"),
              /* label reads the global status.* uniformly (the value is a storage value, untouched) */
              options: [
                { label: t("status.active"), value: "active" },
                { label: t("status.paused"), value: "paused" },
                { label: t("status.archived"), value: "archived" },
              ],
            },
          ]}
          /* inline edit: same RowAction look as other lists (one place to change styling later) */
          rowActions={(r) => (
            <RowActions>
              <QuickStar type="project" entryKey={r.id} label={r.name} testId={`project-quick-${r.id}`} />
              <RowAction
                kind="edit"
                onClick={() => void openEdit(r)}
                disabled={loadingId === r.id}
                title={loadingId === r.id ? t("projectsPage.loadingDetail") : t("projectsPage.editTitle")}
                testId={`project-edit-${r.id}`}
              />
            </RowActions>
          )}
        />
      </div>

      {/* mounted only after the detail is fetched: the dialog initializes from project.x_desc and friends,
          mounting an empty object leaves a form that "looks normal but is entirely empty". */}
      {editing && (
        <ProjectEditDialog
          project={editing}
          open={editOpen}
          onOpenChange={(v) => {
            setEditOpen(v);
            if (!v) setEditing(null);
          }}
          onSaved={() => void refreshPageData()}
        />
      )}
    </div>
  );
}

/** thin shell for the route page (the dialog passes conditions as props, not through the URL) */
export default function ProjectsPage() {
  return <ProjectsBoard />;
}
