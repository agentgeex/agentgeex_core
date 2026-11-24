/** Project management · project group templates (/project-groups) */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { DataTable, StatusPill, type ColumnDef } from "@/components/agentgeex/data-table";
import { useNavigate } from "react-router-dom";
import { listProjectGroups, type ProjectGroupRow } from "@/providers/agentgeex";
import { AgentMemberChips } from "@/components/agentgeex/agent-member-chips";

/* [2026-10-29 Feng] Member count / linked projects / updated time got squeezed onto two lines.
   Root cause: the description column is the only long text without a width cap, and table-layout:auto gives it all the remaining width,
   so short columns can only wrap at their narrowest cell.   Fix: cap the description width (it wraps anyway, narrowing costs nothing) and give the width back to the three short fields (nowrap). */
/* Headers are copy → factory (module-level constants have no hook). */
function groupColumns(t: (k: string) => string): ColumnDef<ProjectGroupRow>[] {
  return [
  {
    key: "name", header: t("projectGroupsPage.colName"), cardTitle: true, sortable: true,
    className: "whitespace-nowrap",
    render: (r) => <span className="whitespace-nowrap">{r.name}</span>,
  },
  {
    /* [2026-11-04 Feng] "The description column is too big, narrow it a bit, put the project group members over here"
       — the description is the only long text without a width cap, auto layout eats all the remaining width,
       so the member column can only squeeze onto two lines. Hence narrowing the description (it wraps anyway, no loss). */
    key: "xDesc", header: t("projectGroupsPage.colDesc"), cardSubtitle: true,
    className: "max-w-[180px]",
    /* ★ Besides narrowing, the line count must be capped: measured, with width alone a long description folds into a dozen lines in 139px,
       pushing the whole row to 150px (worse than before). Two lines plus the full text in title is what "narrow it a bit" means,
       instead of "moving the height into the row height". */
    render: (r) => (
      <span
        className="line-clamp-2 max-w-[180px] text-xs leading-snug"
        title={r.xDesc}
      >
        {r.xDesc || "—"}
      </span>
    ),
  },
  {
    /* [2026-11-04 Feng] A project group is itself a template and its members are template agents
       — list them right in the column (clickable through to the agent detail) instead of having to open the group to see who is in it.
       Replaces the old "member count" column: the counted people are right there, the number is redundant. */
    /* The key stays memberCount: DataTable sorts by taking row[column.key],
       changing the key would break sorting by headcount (the column still shows member names, not that number). */
    key: "memberCount", header: t("projectGroupsPage.colMembers"),
    className: "min-w-[280px]",
    sortable: true,
    render: (r) => <AgentMemberChips members={r.members} />,
  },
  { key: "projectCount", header: t("projectGroupsPage.colProjects"), sortable: true, className: "whitespace-nowrap w-[96px]" },
  {
    key: "status", header: t("projectGroupsPage.colStatus"), sortable: true, className: "whitespace-nowrap w-[96px]",
    render: (r) => <StatusPill status={r.status} />,
  },
  { key: "updatedAt", header: t("projectGroupsPage.colUpdated"), sortable: true, className: "whitespace-nowrap w-[150px]" },
  ];
}

export default function ProjectGroupsPage() {
  const { t } = useTranslation();
  const data = listProjectGroups();
  const columns = React.useMemo(() => groupColumns(t), [t]);
  const navigate = useNavigate();
  const open = (id: string) => navigate(`/project-groups/${encodeURIComponent(id)}`);
  return (
    <div className="flex flex-col gap-2" data-testid="page-project-groups">
      {/* [2026-10-28 16:32 Feng] Follow the host page format: drop the page header title/description (the nav already has it), wrap the content in a white card. */}
      <div
        className="flex flex-col gap-4 rounded-xl border border-strong bg-card p-4 shadow-sm"
        data-testid="content-frame"
      >
        <DataTable
          /* [2026-11-02 Feng] Keep the user's conditions across refreshes (search / filters / page / page size / sort / view); without this line a refresh resets everything. */
          stateKey="project-groups"
          data={data}
          columns={columns}
          searchKeys={["name", "xDesc"]}
          searchPlaceholder={t("projectGroupsPage.searchPlaceholder")}
          onRowClick={(r) => open(r.id)}
          filters={[
            {
              key: "status", label: t("projectGroupsPage.fStatus"),
              options: [
                { label: t("status.active"), value: "active" },
                { label: t("status.paused"), value: "paused" },
              ],
            },
          ]}
        />
      </div>
    </div>
  );
}
