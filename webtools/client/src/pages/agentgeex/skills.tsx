/**
 * Agent management · skill library (/skills)
 *
 * Feature: reusable skills shared between agents (agx_skill).
 *   - list: skill name / category / contributor / usage count / helpful votes / updated at
 *   - filter: category
 *   - search: skill name / description / tags / contributor
 *   - detail: click a row → standalone detail page (/skills/:id, full body Markdown)
 *             [2026-10-26 Feng] details moved to a standalone page (a dialog cannot show all, links unshareable)
 *
 * UI invents nothing: the list strictly reuses components/agentgeex/data-table (pagination + filter + dual view),
 * the detail dialog reuses ui/dialog. Structurally identical to plans.tsx.
 */
import { RowAction, RowActions } from "@/components/agentgeex/row-actions";
import * as React from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { ThumbsUp, Repeat2, Tag, UserRound } from "lucide-react";
import { DataTable, type ColumnDef } from "@/components/agentgeex/data-table";
import { Badge } from "@/components/ui/badge";
import { listSkills, type SkillRow } from "@/providers/agentgeex";

/** category → i18n key (unknown categories shown as-is) */
const CATEGORY_KEY: Record<string, string> = {
  github: "skillsPage.category.github",
  search: "skillsPage.category.search",
  huggingface: "skillsPage.category.huggingface",
  browser: "skillsPage.category.browser",
  mobile: "skillsPage.category.mobile",
  build: "skillsPage.category.build",
  cli: "skillsPage.category.cli",
  mcp: "skillsPage.category.mcp",
  other: "skillsPage.category.other",
};

function catLabel(c: string, t: (k: string) => string): string {
  const key = CATEGORY_KEY[c];
  return key ? t(key) : c;
}

/** category badge colors (same category, same color, easy to scan) */
const CATEGORY_STYLE: Record<string, string> = {
  github: "bg-muted-foreground/15 text-muted-foreground border-muted-foreground/30",
  search: "bg-info/15 text-info border-info/30",
  huggingface: "bg-warning/15 text-warning border-warning/30",
  browser: "bg-chart-1/15 text-chart-1 border-chart-1/30",
  mobile: "bg-success/15 text-success border-success/30",
  build: "bg-destructive/15 text-destructive border-destructive/30",
};

function CategoryBadge({ category }: { category: string }) {
  const { t } = useTranslation();
  return (
    <Badge
      variant="outline"
      className={`font-normal ${CATEGORY_STYLE[category] ?? "bg-muted text-muted-foreground"}`}
    >
      {catLabel(category, t)}
    </Badge>
  );
}

/** tags: render a comma-separated string as several small tag chips */
function TagList({ tags }: { tags: string }) {
  const list = tags
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean)
    .slice(0, 4);
  if (list.length === 0) return <span className="text-muted-foreground">—</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {list.map((t) => (
        <span key={t} className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
          {t}
        </span>
      ))}
    </div>
  );
}

export default function SkillsPage() {
  const { t } = useTranslation();
  const data = listSkills();
  const navigate = useNavigate();
  const open = (id: string) => navigate(`/skills/${encodeURIComponent(id)}`);

  const columns: ColumnDef<SkillRow>[] = [
    /* [2026-10-29 Feng] Skill name/description already wrap, so compress them and give the width to category/contributor:
       a category is a short enum and must not wrap, and a contributor is an agent name that must not break in two. */
    {
      key: "name", header: t("skillsPage.colName"), cardTitle: true,
      className: "max-w-[180px]",
      render: (r) => <span className="block max-w-[180px] truncate" title={r.name}>{r.name}</span>,
    },
    {
      key: "category", header: t("skillsPage.colCategory"), sortable: true, className: "whitespace-nowrap w-[120px]",
      render: (r) => <CategoryBadge category={r.category} />,
    },
    {
      key: "description",
      header: t("skillsPage.colDesc"),
      className: "max-w-[260px]",
      render: (r) => (
        <span className="block max-w-[260px] text-xs text-muted-foreground">{r.description || "—"}</span>
      ),
    },
    {
      key: "sourceAgentName",
      header: t("skillsPage.colContributor"),
      sortable: true,
      className: "whitespace-nowrap w-[170px]",
      render: (r) => (
        <span className="inline-flex items-center gap-1 whitespace-nowrap text-xs">
          <UserRound className="h-3 w-3 opacity-60" />
          {r.sourceAgentName || r.sourceAgentId || "—"}
        </span>
      ),
    },
    {
      key: "useCount",
      header: t("skillsPage.colUses"),
      sortable: true,
      className: "w-[64px] whitespace-nowrap text-right",
      render: (r) => <span className="tabular-nums">{r.useCount}</span>,
    },
    {
      key: "helpfulCount",
      header: t("skillsPage.colHelpful"),
      sortable: true,
      className: "w-[64px] whitespace-nowrap text-right",
      render: (r) => (
        <span className="inline-flex items-center gap-1 tabular-nums text-success">
          <ThumbsUp className="h-3 w-3" />
          {r.helpfulCount}
        </span>
      ),
    },
    { key: "tags", header: t("skillsPage.colTags"), hideInCard: true, render: (r) => <TagList tags={r.tags} /> },
    { key: "updatedAt", header: t("skillsPage.colUpdated"), sortable: true, className: "whitespace-nowrap" },
  ];

  return (
    <div className="flex flex-col gap-2" data-testid="page-skills">
      {/* [2026-10-28 16:32 Feng] Follow the host page format: page header title/description removed (the nav has
   them), content wrapped uniformly in a white frame. */}
      <div
        className="flex flex-col gap-4 rounded-xl border border-strong bg-card p-4 shadow-sm"
        data-testid="content-frame"
      >
        <DataTable
          /* [2026-11-02 Feng] Keep the user's conditions across a refresh (search/filter/page/page size/sort/view);
   without this line, a refresh returns to the initial state. */
          stateKey="skills"
          data={data}
          columns={columns}
          searchKeys={["name", "description", "tags", "sourceAgentId", "sourceAgentName", "category"]}
          searchPlaceholder={t("skillsPage.searchPlaceholder")}
          filters={[
            {
              key: "category",
              label: t("skillsPage.fCategory"),
              options: Array.from(new Set(data.map((s) => s.category))).map((c) => ({
                label: catLabel(c, t),
                value: c,
              })),
            },
          ]}
          defaultView="table"
          emptyText={t("skillsPage.empty")}
          onRowClick={(row) => open(row.id)}
          /* [2026-11-01 Feng] Skill details changed to edit (details come from clicking a row) */
          rowActions={(r) => (
            <RowActions>
              <RowAction kind="edit" to={`/skills/${encodeURIComponent(r.id)}/edit`} testId={`skill-edit-${r.id}`} />
            </RowActions>
          )}
        />

      </div>
    </div>
  );
}
