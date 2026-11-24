/**
 * Agent management · notices / work logs (/notices)
 *
 * What: work logs / decisions / pitfalls / handoffs / config changes published by agents (agx_agent_notice).
 *   - list: type / title / agent / project / visibility / time
 *   - filters: type (log/decision/error/handoff/config/other)
 *   - search: title / body / agent / project
 *   - detail: click a row → dedicated detail page (/notices/:id, full Markdown body)
 *           [2026-10-26 Feng] detail became a standalone page (a dialog cannot show it fully and cannot be shared as a link)
 *
 * No invented UI: the list reuses components/agentgeex/data-table, the detail reuses ui/dialog + ui/markdown-view.
 * Same shape as skills.tsx.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { Newspaper, Bot, FolderKanban, Eye } from "lucide-react";
import { DataTable, type ColumnDef } from "@/components/agentgeex/data-table";
import { Badge } from "@/components/ui/badge";
import { listNotices, sortByQuickProject, projectNameWithPin, type NoticeRow } from "@/providers/agentgeex";

/** Notice type → i18n key (module-level constants have no hook, so store keys only) */
const TYPE_KEY: Record<string, string> = {
  log: "noticesPage.type.log",
  decision: "noticesPage.type.decision",
  error: "noticesPage.type.error",
  handoff: "noticesPage.type.handoff",
  config: "noticesPage.type.config",
  other: "noticesPage.type.other",
};

/** Type badge colours (same type, same colour, for quick scanning) */
const TYPE_STYLE: Record<string, string> = {
  log: "bg-info/15 text-info border-info/30",
  decision: "bg-chart-1/15 text-chart-1 border-chart-1/30",
  error: "bg-destructive/15 text-destructive border-destructive/30",
  handoff: "bg-warning/15 text-warning border-warning/30",
  config: "bg-success/15 text-success border-success/30",
  other: "bg-muted text-muted-foreground",
};

/* Unknown types are shown as-is (a new value in the data must not be swallowed) */
function typeLabel(type: string, t: (k: string) => string): string {
  const key = TYPE_KEY[type];
  return key ? t(key) : type;
}

function TypeBadge({ type }: { type: string }) {
  const { t } = useTranslation();
  return (
    <Badge variant="outline" className={`font-normal ${TYPE_STYLE[type] ?? TYPE_STYLE.other}`}>
      {typeLabel(type, t)}
    </Badge>
  );
}

/** Visibility → i18n key */
const VIS_KEY: Record<string, string> = {
  private: "noticesPage.visibility.private",
  project: "noticesPage.visibility.project",
  network: "noticesPage.visibility.network",
};

function VisibilityBadge({ v }: { v: string }) {
  const { t } = useTranslation();
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
      <Eye className="h-3 w-3 opacity-60" />
      {VIS_KEY[v] ? t(VIS_KEY[v]) : v}
    </span>
  );
}

export default function NoticesPage() {
  const { t } = useTranslation();
  const data = listNotices();
  const navigate = useNavigate();
  const open = (id: string) => navigate(`/notices/${encodeURIComponent(id)}`);

  // Agent filter options: distinct (agentId → agentName) extracted from the data
  const agentOptions = React.useMemo(() => {
    const map = new Map<string, string>();
    for (const n of data) {
      if (!n.agentId) continue;
      if (!map.has(n.agentId)) map.set(n.agentId, n.agentName || n.agentId);
    }
    return [...map.entries()]
      .map(([value, label]) => ({ label, value }))
      .sort((a, b) => a.label.localeCompare(b.label, "zh-CN"));
  }, [data]);

  // Project filter options: distinct (projectId → projectName) extracted from the data
  const projectOptions = React.useMemo(() => {
    const map = new Map<string, string>();
    for (const n of data) {
      if (!n.projectId) continue;
      if (!map.has(n.projectId)) map.set(n.projectId, n.projectName || n.projectId);
    }
    /* [2026-11-17 15:07] Pinned projects come first (★ marker), the rest by name. */
    return sortByQuickProject(
      [...map.entries()]
        .map(([value, label]) => ({ label: projectNameWithPin(value, label), value }))
        .sort((a, b) => a.label.localeCompare(b.label, "zh-CN")),
      (x) => x.value,
    );
  }, [data]);

  const columns: ColumnDef<NoticeRow>[] = [
    {
      key: "type",
      header: t("noticesPage.colType"),
      sortable: true,
      className: "w-[80px] whitespace-nowrap",
      render: (r) => <TypeBadge type={r.type} />,
    },
    {
      key: "title",
      header: t("noticesPage.colTitle"),
      cardTitle: true,
      render: (r) => (
        <span className="line-clamp-1 font-medium">{r.title || t("noticesPage.untitled")}</span>
      ),
    },
    {
      key: "agentName",
      header: "Agent",
      sortable: true,
      className: "whitespace-nowrap",
      render: (r) => (
        <span className="inline-flex items-center gap-1 text-xs">
          <Bot className="h-3 w-3 opacity-60" />
          {r.agentName || r.agentId || "—"}
        </span>
      ),
    },
    {
      key: "projectName",
      header: t("noticesPage.colProject"),
      sortable: true,
      className: "whitespace-nowrap",
      render: (r) => (
        <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
          <FolderKanban className="h-3 w-3 opacity-60" />
          {r.projectName || r.projectId || "—"}
        </span>
      ),
    },
    { key: "visibility", header: t("noticesPage.colVisibility"), hideInCard: true, className: "whitespace-nowrap", render: (r) => <VisibilityBadge v={r.visibility} /> },
    { key: "createdAt", header: t("noticesPage.colTime"), sortable: true, className: "whitespace-nowrap" },
  ];

  return (
    <div className="flex flex-col gap-2" data-testid="page-notices">
      {/* [2026-10-28 16:32 Feng] Follow the host page format: drop the page header title/description (the nav already has it), wrap the content in a white card. */}
      <div
        className="flex flex-col gap-4 rounded-xl border border-strong bg-card p-4 shadow-sm"
        data-testid="content-frame"
      >
        <DataTable
          /* [2026-11-02 Feng] Keep the user's conditions across refreshes (search / filters / page / page size / sort / view); without this line a refresh resets everything. */
          stateKey="notices"
          data={data}
          columns={columns}
          searchKeys={["title", "content", "agentName", "agentId", "projectName"]}
          searchPlaceholder={t("noticesPage.searchPlaceholder")}
          filters={[
            {
              key: "type",
              label: t("noticesPage.fType"),
              /* ★ The map parameter here must not be called t — it would shadow the translation function (third time this round) */
              options: Object.keys(TYPE_KEY).map((type) => ({ label: typeLabel(type, t), value: type })),
            },
            {
              key: "agentId",
              label: "Agent",
              getValue: (r) => r.agentId,
              options: agentOptions,
            },
            {
              key: "projectId",
              label: t("noticesPage.fProject"),
              getValue: (r) => r.projectId,
              options: projectOptions,
            },
          ]}
          defaultView="table"
          emptyText={t("noticesPage.empty")}
          onRowClick={(row) => open(row.id)}
        />

      </div>
    </div>
  );
}
