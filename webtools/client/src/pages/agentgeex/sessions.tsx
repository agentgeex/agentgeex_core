/** Agent management · session list (/sessions) — standard Data Table + "send message" entry
 *
 * Sending goes through the generic "message send" window (components/agentgeex/message-send-dialog.tsx),
 * wrapped by session-send-dialog.tsx → receiver locked, sender selectable:
 *   ① text       → agx_send_text (direct, no execution; only the session owner may send)
 *   ② Task       → agx_dispatch_task (wrapped as a task, sender may be specified)
 *   ③ quick command → agx_quick_cmd_send
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { QuickStar } from "@/components/agentgeex/quick-star";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Hand, Send } from "lucide-react";
import { DataTable, type ColumnDef } from "@/components/agentgeex/data-table";
import { SessionSendDialog } from "@/components/agentgeex/session-send-dialog";
import { RowAction, RowActions } from "@/components/agentgeex/row-actions";
import { listSessions, type SessionRow } from "@/providers/agentgeex";
import { ContextUsage, SessionStatusDot } from "@/components/agentgeex/session-runtime-badge";
import { sessionDeepLink } from "@/lib/openclaw";
import { hostOptions, HOST_FILTER_LABEL_KEY } from "@/lib/host-filter";
import {
  contextBucketOptions, runtimeStatusOptions, contextBucketOf, runtimeStatusOf,
} from "@/lib/session-context";
import { agentFilterOptions, agentInitialFilter } from "@/lib/agent-filter";
import { useDetailPopup } from "@/components/agentgeex/detail-popup";
import { cn } from "@/lib/utils";

/** elide long strings in the middle: keep head and tail, use … (a session id's colons stay recognizable) */
function midEllipsis(s: string, head = 12, tail = 10): string {
  if (!s) return "—";
  if (s.length <= head + tail + 1) return s;
  return `${s.slice(0, head)}…${s.slice(-tail)}`;
}

/* columns became "a factory taking t": headers are copy, and module-level constants cannot access the hook. */
function sessionColumns(t: (k: string, o?: Record<string, unknown>) => string): ColumnDef<SessionRow>[] {
  return [
  /* [2026-10-29 Feng] Assistants have no separate list page, and mixed into sessions you cannot tell which is one.
     Approach: prepend a hand icon (= "a hand this side extends to execute", see the assistant mechanism) + purple + medium.
     Why not color the whole row: one color per row turns the table into zebra stripes, making content harder to read;
     distinguish only on the name anchor, so a column scan is enough. */
  {
    key: "name", header: t("sessionsPage.colName"), cardTitle: true, sortable: true,
    render: (r) =>
      r.isAssistant ? (
        <span className="flex items-center gap-1.5" data-testid="session-assistant-badge">
          <Hand className="h-3.5 w-3.5 shrink-0 text-chart-1" aria-hidden />
          <span
            className="font-medium text-chart-1"
            title={t("sessionsPage.assistantTitle", { slug: r.assistantSlug ?? "" }).trim()}
          >
            {r.name}
          </span>
        </span>
      ) : (
        r.name
      ),
  },
  { key: "agentName", header: t("sessionsPage.colAgent"), cardSubtitle: true, sortable: true },
  /* [2026-10-29 Feng] status/message count/last activity are short fields that must be seen at a glance → no wrapping;
     the session id is a long string (agent:{agent}:{slug}) → fixed width + middle elision, full value in title. */
  /* [2026-11-05 17:08 Feng] "There is a status column in the session list, drop it, because the active status is hard
     to judge. Just use the running status, since it can be read from redis... that one status is enough"
     —— the old "status" column was the MySQL registration state (active / archived), which answers "is this record
        still registered", while the customer asks "is this session running right now". Two questions look alike,
        the answers differ, one is enough: keep the **running status** (a Redis gateway-side fact), drop the
        registration status.
        Removed the "status" filter (active/idle/archived) along with it — keeping a filter for a deleted column
        is even more misleading. */
  /* gateway-side live status + context watermark (from Redis agx:gw:*) */
  {
    key: "runtime", header: t("sessionsPage.colRuntime"), sortable: true, className: "whitespace-nowrap w-[104px]",
    render: (r) => <SessionStatusDot runtime={r.runtime} />,
  },
  {
    key: "__ctx", header: t("sessionsPage.colContext"), sortable: true, className: "whitespace-nowrap w-[168px]",
    // sort by CONTEXT_RATIO (null last), not as a string
    sortValue: (r) => (r.runtime?.contextRatio ?? -1),
    render: (r) => (
      <span className="flex items-center gap-1.5">
        <ContextUsage runtime={r.runtime} />
        {/* same name across hosts (agent:main:main etc.): which machine's data this row came from must be shown explicitly */}
        {r.runtimeHosts && r.runtimeHosts.length > 1 ? (
          <span
            className="rounded border px-1 text-[10px] text-muted-foreground"
            title={t("sessionsPage.multiHost", {
              hosts: r.runtimeHosts.join(" / "),
              host: r.runtime?.hostId ?? "—",
            })}
            data-testid={`session-multi-host-${r.id}`}
          >
            {t("sessionsPage.hostCount", { count: r.runtimeHosts.length })}
          </span>
        ) : null}
      </span>
    ),
  },
  { key: "messageCount", header: t("sessionsPage.colMessages"), sortable: true, className: "whitespace-nowrap w-[88px]" },
  { key: "lastActiveAt", header: t("sessionsPage.colLastActive"), sortable: true, className: "whitespace-nowrap w-[150px]" },
  {
    key: "id", header: t("sessionsPage.colId"),
    className: "text-xs text-muted-foreground w-[200px]",
    render: (r) => (
      <span className="block max-w-[200px] font-mono text-xs text-muted-foreground" title={r.id}>
        {midEllipsis(r.id)}
      </span>
    ),
  },
  ];
}

/**
 * [2026-11-05 23:39 / 11-06 00:03 Feng]
 *   "That agent page — put it inside this popup, carry the query conditions over"
 *   "Do the same for all the others"
 * → two layers: `SessionsBoard` = the body (shared by route page and dialog), `SessionsPage` = thin shell.
 *   `embedded` is handled inside Board for three things: no local filter persistence / row click does not navigate
 *   (stack the detail dialog) / remove the frame inside a frame.
 */
export function SessionsBoard({
  initialFilters,
  embedded = false,
}: {
  /** preset filters (for the dialog; e.g. { __host: "test_host_mac" }) */
  initialFilters?: Record<string, string>;
  embedded?: boolean;
}) {
  const { t } = useTranslation();
  const popup = useDetailPopup();
  const data = listSessions();
  const columns = React.useMemo(() => sessionColumns(t), [t]);

  /* [2026-11-05] Live summary at the top of the page.
     ★ Definition: the numbers come from the **gateway side** (Redis agx:gw:*), not this page's MySQL row count —
       the two are inherently unequal: MySQL contains historically archived sessions (long gone gateway-side),
       so this says explicitly "M of the N rows on this page have live data", never forcing two numbers into one. */
  const rt = React.useMemo(() => {
    const c = { running: 0, idle: 0, failed: 0, stalled: 0, unknown: 0 };
    let known = 0;
    for (const r of data) {
      if (!r.runtime) continue;
      known += 1;
      const k = r.runtime.status;
      if (k in c) c[k as keyof typeof c] += 1;
      else c.unknown += 1;
    }
    return { ...c, known };
  }, [data]);
  const [active, setActive] = React.useState<SessionRow | null>(null);
  const navigate = useNavigate();
  /* [2026-10-29 Feng] Entered from the agent card's "sessions" metric: ?agent=xxx → show only that agent */
  const [sp] = useSearchParams();
  const initialFilterVals = {
    ...(agentInitialFilter(sp.get("agent")) ?? {}),
    ...(initialFilters ?? {}),
  };
  const open = (id: string) => {
    /* clicking a row in the dialog must not navigate — navigating tears down the outer system-status page
   (Feng: "once it pops over, coming back does not work") */
    if (embedded && popup?.open) {
      popup.open({ kind: "session", id });
      return;
    }
    navigate(`/sessions/${encodeURIComponent(id)}`);
  };

  return (
    <div className="flex flex-col gap-2" data-testid={embedded ? "sessions-board-embedded" : "page-sessions"}>
      {/* [2026-10-28 16:32 Feng] Follow the host page format: page header title/description removed (the nav has
   them), content wrapped uniformly in a white frame. */}
      <div
        className={cn(
          "flex flex-col gap-4 bg-card",
          embedded ? "p-3" : "rounded-xl border border-strong p-4 shadow-sm",
        )}
        data-testid={embedded ? undefined : "content-frame"}
      >
        {/* live summary bar: see at a glance "how many are running now", no paging to count */}
        <div
          className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border bg-muted/30 px-3 py-2 text-xs"
          data-testid="sessions-runtime-summary"
        >
          <span className="font-medium text-muted-foreground">{t("sessionsPage.gatewayLive")}</span>
          <span className="inline-flex items-center gap-1.5" data-testid="sum-running">
            <span className="h-2 w-2 rounded-full bg-success" />
            {t("sessionsPage.sumRunning")} <b className="text-success">{rt.running}</b>
          </span>
          <span className="inline-flex items-center gap-1.5" data-testid="sum-idle">
            <span className="h-2 w-2 rounded-full bg-muted-foreground/50" />
            {t("sessionsPage.sumIdle")} <b>{rt.idle}</b>
          </span>
          <span className="inline-flex items-center gap-1.5" data-testid="sum-failed">
            <span className="h-2 w-2 rounded-full bg-destructive" />
            {t("sessionsPage.sumFailed")}{" "}
            <b className={rt.failed ? "text-destructive" : ""}>{rt.failed}</b>
          </span>
          <span className="inline-flex items-center gap-1.5" data-testid="sum-stalled">
            <span className="h-2 w-2 rounded-full bg-warning" />
            {t("sessionsPage.sumStalled")}{" "}
            <b className={rt.stalled ? "text-warning" : ""}>{rt.stalled}</b>
          </span>
          <span className="inline-flex items-center gap-1.5" data-testid="sum-unknown">
            <span className="h-2 w-2 rounded-full bg-muted-foreground/30" />
            {t("sessionsPage.sumUnknown")} <b>{rt.unknown}</b>
          </span>
          <span className="ml-auto text-muted-foreground" data-testid="sum-scope">
            {t("sessionsPage.sumScope", { total: data.length, known: rt.known })}
          </span>
        </div>
        <DataTable
          /* [2026-11-02 Feng] Keep the user's conditions across a refresh (search/filter/page/page size/sort/view);
   without this line, a refresh returns to the initial state. */
          stateKey={embedded ? undefined : "sessions"}
          data={data}
          columns={columns}
          searchKeys={["name", "agentName", "id"]}
          searchPlaceholder={t("sessionsPage.searchPlaceholder")}
          initialFilterVals={initialFilterVals}
          filters={[
            /* [2026-10-29 Feng] "Is assistant" filter. An assistant = a session with is_assistant=1,
               so a derived column is used (no new stored field), stacking automatically with the host filter. */
            {
              /* the values of getValue / options are **storage values**, kept as-is; only the label is for humans and gets
                  translated. Changing a value = every saved filter dies. */
              key: "__assistant", label: t("sessionsPage.fAssistant"),
              getValue: (r) => (r.isAssistant ? "是" : "否"),
              options: [
                { label: t("sessionsPage.fAssistantYes"), value: "是" },
                { label: t("sessionsPage.fAssistantNo"), value: "否" },
              ],
            },
            {
              key: "__host", label: t(HOST_FILTER_LABEL_KEY),
              /* [2026-11-06] Filter by `agx_session.host_id` (the host the session itself belongs to).
                 It used to be hostOfAgent(agentId) — when an agent changes host, historical sessions were
                 counted on the new machine. Same definition as the executions page: an empty host does not
                 fall back, and card stats do not include it either. */
              getValue: (r) => r.hostId || "",
              options: hostOptions(),
            },
            /* [2026-11-06 11:10 Feng] "Run — that is, the live drawing status — make these links work too;
               you already did the earlier ones, is this not just passing a condition? Including the context below"
               —— these come from clicking the status cell / watermark cell in the dashboard "live session status"
               card, carrying exactly these two conditions.

               ★ The status value comes from the **gateway side** (Redis), not MySQL's active/archived;
                 a session without gateway data goes to "no gateway data", not into "unknown" —
                 the two mean different things (one is the gateway saying its state is unclear, the other is
                 the gateway not knowing it at all). */
            {
              key: "__runtime", label: t("sessionsPage.fRuntime"),
              getValue: (r) => runtimeStatusOf(r.runtime),
              options: runtimeStatusOptions(t),
            },
            /* context watermark: bucket definitions live in `lib/session-context.ts`, same boundaries as the backend `_BUCKETS`. */
            {
              key: "__ctxBucket", label: t("sessionsPage.fContext"),
              getValue: (r) => contextBucketOf(r.runtime?.contextRatio),
              options: contextBucketOptions(t),
            },
            /* "Agent" filter: already set when jumping in from a card (?agent=), can also be switched manually */
            { key: "agentId", label: t("sessionsPage.fAgent"), options: agentFilterOptions(data) },
          ]}
          onRowClick={(r) => open(r.id)}
          /* [2026-11-01 Feng] "The session list has a column called openclaw, that is wrong... because this session
             is not necessarily openclaw, other platforms will be supported later. Put the 'open' action
             into the actions column, next to the others."
             —— the column name lies: "OpenClaw" describes an implementation, not this thing; when other
             platforms arrive, the column either gets renamed or becomes invalid. In the actions column it is
             just **one thing this session can do**, like "send message", independent of the platform name.
             ★ The link itself is still dispatched by platform: `sessionDeepLink` picks a template by the
               agent's platform type, returns null for unregistered platforms (no path guessing), and then
               no button is rendered here. */
          rowActions={(r) => {
            const href = sessionDeepLink(r.id);
            return (
              <RowActions>
                <QuickStar type="session" entryKey={r.id} label={r.name} testId={`session-quick-${r.id}`} />
                {href ? (
                  <RowAction
                    kind="open"
                    href={href}
                    title={t("sessionsPage.openTitle", { href })}
                    testId={`openclaw-session-${r.id}`}
                  />
                ) : null}
                <RowAction
                  kind="send"
                  onClick={() => setActive(r)}
                  title={t("sessionsPage.sendTitle")}
                  testId={`agx-session-send-${r.id}`}
                />
              </RowActions>
            );
          }}
        />
        {/* [2026-11-04 Feng] "Send message" uses the generic "message send" window:
            the receiver (agent + this session) is locked, the sender is selectable, all three modes available. */}
        <SessionSendDialog
          session={active}
          open={!!active}
          onOpenChange={(o) => { if (!o) setActive(null); }}
        />
      </div>
    </div>
  );
}

/**
 * Thin shell for the route page: does only "things only a page does" (the dialog passes props instead of using the URL).
 */
export default function SessionsPage() {
  return <SessionsBoard />;
}
