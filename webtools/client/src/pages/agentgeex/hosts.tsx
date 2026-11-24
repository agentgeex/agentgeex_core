/**
 * System · Compute hosts (/hosts)
 *
 * Function: AgentGeeX compute host asset ledger (agx_host_inventory).
 *   - List: host / alias / IP / OS / task slots (used/limit) / status
 *   - Detail: click a row → standalone detail page (/hosts/:id, with the Agents on that host and a direct OpenClaw entry)
 *           [2026-10-26 Feng] detail became a standalone page (a dialog cannot show everything, and cannot be shared as a link)
 *
 * UI invents nothing: the list reuses components/agentgeex/data-table (pagination + filter + dual view),
 * dialogs reuse ui/dialog. Same shape as skills.tsx.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { CircleDot, Server, Network, Cpu, Bot, Pencil } from "lucide-react";
import { RowAction, RowActions } from "@/components/agentgeex/row-actions";
import { DataTable, type ColumnDef } from "@/components/agentgeex/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { listHosts, type HostRow } from "@/providers/agentgeex";
import { PlatformTag, platformLabel } from "@/components/agentgeex/platform-logo";

/** Status badge: online / offline */
function StatusBadge({ online }: { online: boolean }) {
  const { t } = useTranslation();
  return online ? (
    <Badge variant="outline" className="gap-1 border-success/30 bg-success/15 font-normal text-success">
      <CircleDot className="h-3 w-3" />{t("hostsPage.online")}
    </Badge>
  ) : (
    <Badge variant="outline" className="gap-1 border-muted-foreground/30 bg-muted font-normal text-muted-foreground">
      <CircleDot className="h-3 w-3" />{t("hostsPage.offline")}
    </Badge>
  );
}

/** [2026-11-16 09:28 Feng] There are hosts with the DSH platform now — "if the platform data is not exposed, this side knows nothing".
 *
 *  One host can have **several platform instances** (test_host_mac one openclaw; test_srv_a2 is the second instance;
 *  test_host_srv is dsh), so here they are listed vertically one by one per array, not assuming "one platform per machine".
 *  Icons use the site-wide PlatformTag (`platform-logo.tsx`, already has dsh.svg / openclaw.svg);
 *  do not hardcode an `<img>` path here — otherwise adding a platform later means searching the whole repo.
 *  Hover shows "how to connect to it": instance name + port / address + note (the dsh note states it does not listen
 *  on a port).
 */
function HostPlatformCell({ platforms }: { platforms?: HostRow["platforms"] }) {
  const list = platforms || [];
  if (!list.length) {
    return <span className="text-xs text-muted-foreground" data-testid="host-platforms-empty">—</span>;
  }
  return (
    <span className="flex flex-col gap-0.5" data-testid="host-platforms">
      {list.map((p, i) => {
        const where = p.mode === "container" ? (p.addr || "container") : (p.port ? `:${p.port}` : "—");
        const tip = [p.name || platformLabel(p.platform), where, p.note].filter(Boolean).join(" · ");
        return (
          <PlatformTag
            key={`${p.platform}-${p.port ?? i}`}
            type={p.platform}
            title={tip}
            data-name={p.name || ""}
            data-mode={p.mode}
          />
        );
      })}
    </span>
  );
}

/** Task slots: used/limit (with an occupancy bar) */
function SlotBar({ used, limit }: { used: number; limit: number }) {
  const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  const tone = pct >= 90 ? "bg-destructive" : pct >= 60 ? "bg-warning" : "bg-success";
  return (
    <div className="flex items-center gap-2">
      <span className="tabular-nums text-xs">
        <span className="font-medium">{used}</span>
        <span className="text-muted-foreground">/{limit || "—"}</span>
      </span>
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
        <div className={`h-full ${tone}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default function HostsPage() {
  const { t, i18n } = useTranslation();
  const data = listHosts();
  const navigate = useNavigate();

  /* Platform filter candidates come from **real data**, not hardcoded openclaw/dsh —
     hardcoding would mean code changes when a new platform ships, while this is exactly where "a host with a new
     platform" gets discovered. */
  const platformOptions = React.useMemo(() => {
    const set = new Set<string>();
    data.forEach((h) => (h.platforms || []).forEach((p) => p.platform && set.add(p.platform)));
    return [...set].sort().map((v) => ({ label: platformLabel(v), value: v }));
  }, [data]);
  const open = (id: string) => navigate(`/hosts/${encodeURIComponent(id)}`);

  /* [2026-10-30 Feng] Row action changed from "details (clicking the row already opens it, the button is redundant)" to "Edit".
     [Second instruction the same day] No dialog — open edit as a page window: `/hosts/:id/edit`,
     attached to this menu's tab (tab-meta tabKey=/hosts), no extra tab.
     Edit goes through BFF → MCP agx_update_host (as agx_core). */

  const columns: ColumnDef<HostRow>[] = [
    {
      key: "alias",
      header: t("hostsPage.colHost"),
      cardTitle: true,
      render: (r) => (
        <span className="inline-flex items-center gap-1.5">
          <Server className="h-3.5 w-3.5 text-primary" />
          <span className="font-medium">{r.alias}</span>
        </span>
      ),
    },
    {
      key: "ip",
      header: t("hostsPage.colIp"),
      className: "whitespace-nowrap",
      render: (r) => (
        <span className="inline-flex items-center gap-1 font-mono text-xs">
          <Network className="h-3 w-3 opacity-60" />
          {r.ip}{r.sshPort ? `:${r.sshPort}` : ""}
        </span>
      ),
    },
    {
      key: "os",
      header: t("hostsPage.colOs"),
      className: "whitespace-nowrap",
      render: (r) => (
        <span className="inline-flex items-center gap-1 text-xs">
          <Cpu className="h-3 w-3 opacity-60" />
          {r.os || "—"}
        </span>
      ),
    },
    {
      /* [2026-11-16 09:28] The platform column goes after "operating system":
         the OS is "what this machine is", the platform is "what this machine can run" — read together they form a full judgment. */
      key: "platforms",
      header: t("hostsPage.colPlatform"),
      className: "whitespace-nowrap",
      sortValue: (r) => (r.platforms?.[0]?.platform || ""),
      render: (r) => <HostPlatformCell platforms={r.platforms} />,
    },
    {
      key: "slotsLimit",
      header: t("hostsPage.colSlots"),
      sortable: true,
      className: "whitespace-nowrap",
      render: (r) => <SlotBar used={r.activeTasks} limit={r.slotsLimit} />,
    },
    {
      // [2026-10-28 Feng] The host ID duplicates "host" → removed; this cell now shows the number of Agents on that host
      key: "agentCount",
      header: t("hostsPage.colAgents"),
      sortable: true,
      className: "whitespace-nowrap",
      render: (r) => (
        <span className="inline-flex items-center gap-1.5" data-testid={`host-agents-${r.id}`}>
          <Bot className="h-3 w-3 opacity-60" />
          <span className="tabular-nums font-medium">{r.agentCount ?? 0}</span>
          {/* The Chinese measure word has no English counterpart — decide by language, do not leave an empty-string key (the checker reports it as an empty value) */}
          {i18n.language.startsWith("zh") ? (
            <span className="text-muted-foreground text-xs">个</span>
          ) : null}
        </span>
      ),
    },
    {
      key: "online",
      header: t("hostsPage.colStatus"),
      sortable: true,
      className: "whitespace-nowrap",
      render: (r) => <StatusBadge online={r.online} />,
    },
    { key: "xDesc", header: t("hostsPage.colDesc"), hideInCard: true, render: (r) => <span className="text-xs text-muted-foreground">{r.xDesc || "—"}</span> },
  ];

  return (
    /* [2026-10-28 16:27 Feng] Two trial changes (this page only):
       ① Remove the page header title + description — "redundant, the nav already explains it".
       ② Wrap the main display area in a white frame (white card + padding), content inside;
          leave a gap between the white frame and the page background so it is obvious "this whole block is the display area".
          Cards use border-strong + shadow-sm: one step darker than a normal border, visible on white. */
    <div className="flex flex-col" data-testid="page-hosts">
      <div
        className="flex flex-col gap-4 rounded-xl border border-strong bg-card p-4 shadow-sm"
        data-testid="content-frame"
      >
        <DataTable
          /* [2026-11-02 Feng] Keep user-set conditions after refresh (search/filter/page/page size/sort/view); without this line, refresh resets everything. */
          stateKey="hosts"
          data={data}
          columns={columns}
          searchKeys={["alias", "id", "ip", "os", "xDesc"]}
          searchPlaceholder={t("hostsPage.searchPlaceholder")}
          filters={[
            /* [2026-11-16 09:28] Filter by platform: a machine can now host several platforms (openclaw + dsh),
                so use `multi` matching (getValue gives a comma set; keep rows matching any). */
            ...(platformOptions.length > 1
              ? [{
                  key: "platform",
                  label: t("hostsPage.fPlatform"),
                  multi: true,
                  getValue: (r: HostRow) => (r.platforms || []).map((p) => p.platform).join(","),
                  options: platformOptions,
                }]
              : []),
            {
              key: "online",
              label: t("hostsPage.fStatus"),
              getValue: (r) => (r.online ? "online" : "offline"),
              options: [
                { label: t("hostsPage.online"), value: "online" },
                { label: t("hostsPage.offline"), value: "offline" },
              ],
            },
          ]}
          defaultView="table"
          emptyText={t("hostsPage.empty")}
          onRowClick={(row) => open(row.id)}
          /* [2026-11-01] Switched to the unified row action component: the look shares one implementation with other pages,
              so future style changes touch one place (before, each page wrote its own — the root cause of inconsistency). */
          rowActions={(r) => (
            <RowActions>
              <RowAction
                kind="edit"
                to={`/hosts/${encodeURIComponent(r.id)}/edit`}
                testId={`host-edit-${r.id}`}
              />
            </RowActions>
          )}
        />
      </div>
    </div>
  );
}
