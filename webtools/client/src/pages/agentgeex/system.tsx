/**
 * System status · Dashboard (/system)
 * Per agentgeex_ui.md §2.1: grid/tile layout, 5 sections ordered by display priority.
 */
import * as React from "react";
import {
  FolderKanban, CalendarClock, ListChecks, Bot, MessageSquare, TrendingUp, Activity,
  Radio, RadioTower, Server,
} from "lucide-react";
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip as RTooltip,
  PieChart, Pie, Cell, BarChart, Bar, CartesianGrid,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Button } from "@/components/ui/button";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getDashboardStats, fetchDashboardStats } from "@/providers/agentgeex";
import { formatRatio, STATUS_META } from "@/components/agentgeex/session-runtime-badge";
import { AgentsDialog } from "@/components/agentgeex/agents-dialog";

/** Host online in three tiers — [2026-11-06 10:19 Feng, option A]
 *
 * ★ The criterion is last_heartbeat, not presence_status:
 *   the daemon runs `SET presence_status=1, last_heartbeat=NOW()` every 30s,
 *   but **no code writes it back to 0** → that column is always 1, so the green dot is always lit (fake).
 *   The watchdog also uses last_heartbeat to detect a lost host (threshold 90s = 3 missed heartbeats).
 *
 * Three tiers: <=90s green "online" / <=3600s yellow "lost" / else grey "offline" / null grey "no heartbeat".
 * Why the middle tier (yellow): it reported just now and has now missed 3 heartbeats — worth a look,
 * but not yet "definitely stopped", so it does not share the grey of "stopped a day ago".
 */
function hostPresence(h: { heartbeatAgeSeconds?: number | null }, t: (k: string, v?: Record<string, unknown>) => string) {
  const age = h.heartbeatAgeSeconds;
  if (age == null) {
    return {
      tone: "unknown",
      dot: "bg-muted-foreground/40",
      label: t("system.presence.unknown"),
      hint: t("system.presence.unknownHint"),
    };
  }
  const rel =
    age < 60 ? t("system.presence.justNow")
      : age < 3600 ? t("system.presence.minutesAgo", { n: Math.floor(age / 60) })
        : age < 86400 ? t("system.presence.hoursAgo", { n: (age / 3600).toFixed(1) })
          : t("system.presence.daysAgo", { n: Math.floor(age / 86400) });
  if (age <= 90) {
    return { tone: "online", dot: "bg-success", label: t("system.presence.online"), hint: t("system.presence.lastHeartbeat", { rel }) };
  }
  if (age <= 3600) {
    return {
      tone: "lost",
      dot: "bg-warning",
      label: t("system.presence.lost"),
      hint: t("system.presence.lostHint", { rel }),
    };
  }
  return {
    tone: "offline",
    dot: "bg-muted-foreground/40",
    label: t("system.presence.offline"),
    hint: t("system.presence.lastHeartbeat", { rel }),
  };
}
import {
  HostSessionsDialog, HostExecutionsDialog,
  StatusExecutionsDialog, StatusPlansDialog, StatusAgentsDialog, StatusProjectsDialog,
  ActiveSessionsDialog, RankExecutionsDialog, RuntimeSessionsDialog, ContextSessionsDialog,
} from "@/components/agentgeex/page-dialogs";
import { useDetailPopup } from "@/components/agentgeex/detail-popup";

/* 2026-11-05 21:09 Feng: "the colors of the charts cannot be removed; whatever color they used to be, keep it"
   — the 18:58 pass extended "make text black" to charts and turned pie/bar/area charts all grey; overreach.
     Corrected rule: **text and numbers stay black/white/grey, statistical charts keep semantic colors**.
     Pie chart back to five colors (blue/purple/pink/cyan/green): sectors differ by hue, faster to read than lightness. */
const PIE_COLORS = ["hsl(var(--info))", "hsl(var(--chart-1))", "hsl(var(--chart-4))", "hsl(var(--chart-2))", "hsl(var(--success))"];

/* -- Status color semantics (2026-11-13 09:08 Feng) ----------------------------------
   "Online, running, queued, especially running — in short anything meaning alive — switch to green;
     it can't all be blue either. And failed, switch all to red."

   Four tiers, fixed meaning, not redefined per location:
     green TONE_OK   alive / moving (online · busy · running · queued · active · in progress)
     red TONE_BAD    error (failed · abnormal)
     grey TONE_IDLE  none / ended (offline · cancelled · archived · draft)
     blue (omitted)  the other clickable neutral numbers (succeeded · completed · awaiting confirm · total · cancelling)

   ★ Blue is not a "fourth state"; it is the default when there is no status semantics —
     do not color things just to add color; too many colors become decoration and stop conveying information. */
const TONE_OK = "text-success";
const TONE_BAD = "text-destructive";
const TONE_IDLE = "text-muted-foreground";

function Metric({
  label, value, sub, tone, className, size = "md", onClick, testId,
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
  /**
 * Number color (status semantics). ★ 2026-11-13 09:08 Feng:
 * "online, queued, running — the ones that mean alive — must be green, not all blue either;
 *   failed, switch all to red."
 * So **clickable tiles also accept tone**: they used to hardcode `text-primary`, so online/failed/archived were all blue.
 * Omitted = still the primary-action blue (hint of clickability).
 */
  tone?: string;
  /** [2026-11-06 00:03 Feng] Click a number → a dialog shows this batch of data (it embeds the corresponding page itself).
 * ★ When the value is 0 the caller must **not** pass onClick — clicking through to nothing is more confusing than not clickable.
 */
  onClick?: () => void;
  testId?: string;
  /** Placeholder (e.g. the last cell of a 7-cell layout spans the full row) */
  className?: string;
  /**
 * [2026-11-05 13:08 Feng] "make this status bigger, the text bigger too, put it in the middle, must fill it"
 * md = the original compact cell (label top-left, number left-aligned); lg = fills the whole cell and centers.
 * Both kept: in the same row some cards have more content, some less; cannot cut them all into large cells.
 */
  size?: "md" | "lg";
}) {
  if (size === "lg") {
    return (
      <div className={`flex h-full flex-col items-center justify-center gap-1.5 rounded-lg border bg-card/50 p-3 text-center ${className ?? ""}`}>
        <div className="text-sm text-muted-foreground">{label}</div>
        {/* Number scaled to text-4xl: once the cell is stretched taller, a number that does not grow only looks like "big frame, empty inside" */}
        {onClick ? (
          /* Clickable: color as the hint (Feng: no underline), hover lightens one level */
          <button
            type="button"
            onClick={onClick}
            data-testid={testId}
            className={cn(
              "text-4xl font-semibold leading-none tabular-nums transition-colors focus-visible:underline focus-visible:outline-none",
              tone ? cn(tone, "hover:opacity-80") : "text-primary hover:text-primary/70",
            )}
          >
            {value}
          </button>
        ) : (
          <div data-testid={testId} className={`text-4xl font-semibold leading-none tabular-nums ${tone ?? ""}`}>{value}</div>
        )}
        {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
      </div>
    );
  }
  return (
    <div className={`rounded-lg border bg-card/50 p-3 ${className ?? ""}`}>
      <div className="text-xs text-muted-foreground">{label}</div>
      {onClick ? (
        <button
          type="button"
          onClick={onClick}
          data-testid={testId}
          className={cn(
            "mt-1 block text-2xl font-semibold tabular-nums transition-colors focus-visible:underline focus-visible:outline-none",
            tone ? cn(tone, "hover:opacity-80") : "text-primary hover:text-primary/70",
          )}
        >
          {value}
        </button>
      ) : (
        <div data-testid={testId} className={`mt-1 text-2xl font-semibold ${tone ?? ""}`}>{value}</div>
      )}
      {sub && <div className="mt-0.5 text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
}

/* -- One tile of the task execution ranking (2026-11-06) ----------------------------
   Feng: "task execution ranking — clicking a task of an agent in here should drill in"
   The whole tile is clickable (not only the number) — there is no other clickable element inside,
   so making the whole block clickable saves one aim;
   clickable only when agentId exists (old data without id would force guessing by name — better not clickable). */
function RankCell({
  i, name, calls, onOpen,
}: { i: number; name: string; calls: number; onOpen?: () => void }) {
  const { t } = useTranslation();
  const body = (
    <>
      <span className="absolute left-2 top-2 font-mono text-sm font-semibold leading-none tabular-nums text-muted-foreground">
        #{i + 1}
      </span>
      <div className="w-full min-w-0 text-center">
        {/* The tile is only ~109px wide; single-line truncate would cut the name in half → wrap onto two lines;
   after the underscore in the English id insert <wbr>, so it prefers breaking at segment boundaries
   instead of splitting a word in the middle. */}
        <span
          className="line-clamp-2 min-h-[30px] break-words text-xs leading-tight text-muted-foreground"
          title={name}
        >
          {name.split("_").map((part, idx, arr) => (
            <React.Fragment key={idx}>
              {part}
              {idx < arr.length - 1 ? <>_<wbr /></> : null}
            </React.Fragment>
          ))}
        </span>
        <span
          className={cn(
            "mt-1 block font-mono text-2xl font-semibold leading-none tabular-nums",
            onOpen ? "text-primary" : "text-foreground",
          )}
        >
          {calls}
        </span>
      </div>
    </>
  );
  const cls =
    "relative flex flex-col items-center justify-center rounded-md border bg-muted/40 px-2 py-2";
  if (!onOpen) {
    return (
      <div data-testid={`rank-${i + 1}`} className={cls}>
        {body}
      </div>
    );
  }
  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid={`rank-${i + 1}`}
      title={t("system.sessions.execHistory", { name, n: calls })}
      className={cn(
        cls,
        "text-left transition-colors hover:border-primary/50 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      )}
    >
      {body}
    </button>
  );
}


/**
 * Chart shell that fills its parent (2026-11-05 13:39)
 *
 * Why `<ResponsiveContainer height="100%">` is no longer used:
 * it measures at the moment of mount, while the card height is stretched by other cards in the same row —
 * at that instant it is still 0; measured svg was only 16px, the chart collapsed to a line with a big blank below.
 * Here a ResizeObserver measures actively; once the real height is known it is handed to Recharts,
 * and recomputed when the window or content changes.
 */
function FillChart({ children, minHeight = 110 }: { children: (h: number) => React.ReactNode; minHeight?: number }) {
  const ref = React.useRef<HTMLDivElement>(null);
  const [h, setH] = React.useState(0);
  React.useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setH(Math.round(el.clientHeight));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return (
    <div ref={ref} className="flex-1" style={{ minHeight }}>
      {h > 0 ? children(h) : null}
    </div>
  );
}

/** Dashboard polling interval (same as the plan flow chart: 3–5s is enough to sense "moving" without hammering the BFF) */
const LIVE_MS = 5000;

export default function SystemDashboard() {
  const { t } = useTranslation();
  /* [2026-10-28 Feng] Data must be dynamic: fetch once on mount, then every 5s.
     - Skip while the page is hidden (document.hidden); fetch immediately on return;
     - Failures are not silent: explicitly tell at the top "stalled + reason"; numbers keep the last real value. */
  const [s, setS] = React.useState(() => getDashboardStats());
  const [liveOn, setLiveOn] = React.useState(true);
  /* [2026-11-05 23:32 Feng] Clicking a number must open a **dialog**, not navigate away — once you leave you cannot come back.
     The dialog records only "which host is being viewed"; the list content is queried by the dialog itself via host_id. */
  const [hostAgentsFor, setHostAgentsFor] = React.useState<string | null>(null);
  /* [2026-11-06 00:03 Feng: "do all the others the same way"] Every number on the dashboard opens a dialog
     that embeds **the corresponding page itself** and carries the query condition in. One big state holds
     "which one is open" instead of 8 useStates — only one can be open at a time; with several booleans
     two of them will eventually be true at once. */
  const [pg, setPg] = React.useState<
    | null
    | { k: "hostSessions"; host: string; count: number }
    | { k: "hostExecutions"; host: string; count: number }
    | { k: "execStatus"; label: string; status: string; count: number }
    | { k: "planStatus"; label: string; status: string; count: number }
    | { k: "agentStatus"; label: string; status: string; count: number }
    | { k: "projectStatus"; label: string; status: string; count: number }
    | { k: "sessions"; count: number }
    /* [2026-11-06 11:10 Feng] The remaining links, added as well:
      ranking tile (agent + last 7 days) / session live status tiles / context watermark tile. */
    | { k: "rankExec"; name: string; agentId: string; count: number }
    | { k: "rtStatus"; label: string; status: string; count: number }
    | { k: "rtBucket"; label: string; bucket: string; count: number }
  >(null);
  const closePg = () => setPg(null);
  /* Tiles with 0 get no onClick: clicking through to nothing is more confusing than "not clickable".
     (Feng 2026-11-06 00:03: "if it's 0 forget it; clicking into a 0 is useless") */
  const only = (n: number, make: () => NonNullable<typeof pg>) => (n > 0 ? () => setPg(make()) : undefined);
  /* [2026-11-06 11:10] Top 5 context usage: click one → open the detail of **that session** directly.
     No PageDialog here (it is not "a batch of data", it is "this one"), using the site-wide detail dialog. */
  const popup = useDetailPopup();
  const [err, setErr] = React.useState("");
  const [at, setAt] = React.useState("");

  const pull = React.useCallback(async () => {
    try {
      const d = await fetchDashboardStats();
      setS({ ...d });
      setAt(d.generatedAt || new Date().toTimeString().slice(0, 8));
      setErr("");
    } catch (e) {
      const x = e as { message?: string };
      setErr(x.message || t("system.refreshFailed"));
    }
  }, []);

  React.useEffect(() => {
    if (!liveOn) return;
    void pull();
    const id = window.setInterval(() => {
      if (!document.hidden) void pull();
    }, LIVE_MS);
    const onVis = () => {
      if (!document.hidden) void pull();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [liveOn, pull]);

  const totalAgents = s.agentMetrics.total ?? s.agentMetrics.online + s.agentMetrics.offline;
  /* Session live status block (new BFF block). Old snapshots lack this key → undefined; the card explicitly says
     "no data" instead of rendering a row of 0s (0 would suggest "the gateway has no sessions at all"). */
  const srt = s.sessions.runtime;
  /** The three distribution blocks all use the "share" column, denominator = total sessions on the gateway; */
  const pctOf = (n: number) => (srt?.total ? `${Math.round((n / srt.total) * 100)}%` : "—");
  /* Host scale stats (BFF hostStats block). Missing block → show explanation, do not render an empty table.
     ★ 2026-11-05 09:09 Feng correction: "it's not organization, it's host". The organization block is still in the API
       (the "4 organizations missing their master record" surfaced by org_stats is a real problem), but it no longer
       takes dashboard space. */
  const hs = s.hostStats;

  /* [2026-11-13 09:23 Feng] Inside the host distribution, "the dot after the host name" must stay in sync with the host card above:
     green dot online / red dot offline. The host card's criterion is heartbeat (`online` boolean from BFF);
     do not recompute it here, use the same data — computing twice in two places will diverge eventually. */
  const hostOnline = React.useMemo(() => {
    const m = new Map<string, boolean>();
    for (const h of hs?.byHost ?? []) m.set(h.hostId, !!h.online);
    return m;
  }, [hs]);
  /** Heartbeat age (seconds) — the dot in the host distribution must use the same hostPresence() criterion as the host card */
  const hostAge = React.useMemo(() => {
    const m = new Map<string, number | null>();
    for (const h of hs?.byHost ?? []) m.set(h.hostId, h.heartbeatAgeSeconds ?? null);
    return m;
  }, [hs]);

  /* [2026-11-13 09:08 Feng] The "send message" button and dialog on this page were removed:
     "since the top already has send message, drop the send message button inside system status; it's redundant and useless".
     ★ The dialog itself is not disabled — it moved up to the Shell layer (shared by the top-bar button);
       this page simply no longer builds its own entry and its own instance. */

  return (
    /* 2026-11-05 11:25: border levels are unified to the global --border (84%).
     This page used to override --border / --input for its own effect; that is reverted — one thing in one place,
     otherwise a later change to one copy produces "this page differs from the others". */
    <div className="flex flex-col gap-2" data-testid="page-system">
      {/* [2026-10-28 16:32 Feng] Follow the host page format: drop the page header title/description (nav already has it), */}
      <div
        className="flex flex-col gap-4 rounded-xl border border-strong bg-card p-4 shadow-sm"
        data-testid="content-frame"
      >

        {/* Live bar: live status + switch (Feng 2026-10-29: refresh button / page refresh time removed — nav bar has global refresh)
              ★ 2026-11-05 21:35 Feng: "live means this state is on — can't it turn green? icon green, text green,
                the time after it green, the button green too"
                — on = green (icon/text/time/button all green), off = grey.
                Green appears only when "on", which is itself the strongest status signal; no extra color block needed.
                Interruption uses warning (same color as live interruption in the plan flow chart, one color language). */}
        <div className="flex flex-wrap items-center gap-3 text-xs" data-testid="dash-live-bar">
          <span className="inline-flex items-center gap-1.5">
            {liveOn ? (
              <Radio className={cn("h-3.5 w-3.5", err ? "text-warning" : "text-success")} data-testid="dash-live-icon" />
            ) : (
              <RadioTower className="h-3.5 w-3.5 text-muted-foreground" data-testid="dash-live-icon" />
            )}
            <span
              className={cn("text-[11px]", liveOn ? (err ? "text-warning" : "text-success") : "text-muted-foreground")}
              data-testid="dash-live-text"
            >
              {liveOn
                ? err
                  ? t("system.live.interrupted", { err })
                  : t("system.live.on", { at: at || t("system.live.connecting") })
                : t("system.live.offline")}
            </span>
          </span>
          <Button
            size="sm"
            variant={liveOn ? "outline" : "ghost"}
            className={cn(
              "h-7 gap-1 px-2 text-[11px]",
              liveOn && "border-success/40 bg-success/10 text-success hover:bg-success/20 hover:text-success",
            )}
            onClick={() => setLiveOn((v) => !v)}
            title={t("system.live.every", { seconds: LIVE_MS / 1000 })}
            data-testid="dash-live-toggle"
          >
            {liveOn ? <Radio className="h-3 w-3" /> : <RadioTower className="h-3 w-3" />}
            {t("system.live.label")}
          </Button>
          {/* Off state uses a grey button + grey icon (RadioTower antenna tower); the on/off icons already differ, adding color separates them further */}
        </div>

        {/* Row 0 — host scale (2026-11-05 Feng: "every host has agents, sessions, executed tasks on it")
              ★ The three columns come from three different tables, with different definitions:
                Agent = owning host (agx_agent_registry.host_id)
                Session = host of the owning Agent (agx_session **has no** host_id column, only derivable via agent)
                Execution = executing host (agx_task_execution.host_id, not via agent — an execution can be dispatched by A to run on B)
                       [2026-11-05 23:09 Feng] this column used to be called "task" → the summary row then read
                       "task 7549", which looks like seven thousand tasks in the system, but they are execution records.
                       Unified as "execution".
              ★ Session column has two numbers: registered (MySQL, incl. archived) and online (really present in the Redis gateway now).
                Measured 4145 vs 2406 — not a contradiction, two questions: "how many ever registered" vs "how many right now". */}
        <Card data-testid="card-hosts">
          <CardHeader className="pb-3">
            <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
              <Server className="h-4 w-4 text-muted-foreground" /> {t("system.host.title")}
              {hs ? (
                <span className="text-[11px] font-normal text-muted-foreground" data-testid="hosts-scope">
                  {t("system.host.summary", {
                    total: hs.total,
                    online: hs.onlineByHeartbeat ?? 0,
                    agents: hs.totals.agentCount,
                    sessions: hs.totals.sessionCount,
                    live: hs.totals.liveSessions,
                    executions: hs.totals.taskCount,
                    slots: hs.totals.slots ? t("system.host.slots", { n: hs.totals.slots }) : "",
                  })}
                </span>
              ) : null}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {!hs ? (
              <div className="text-xs text-muted-foreground" data-testid="hosts-empty">
                {t("system.host.empty")}
              </div>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" data-testid="hosts-grid">
                {hs.byHost.map((h) => (
                  <div
                    key={h.hostId}
                    className="rounded-md border border-border bg-muted/20 px-4 py-4"
                    data-testid={`host-row-${h.hostId}`}
                  >
                    {/* Header: host name + online dot + disabled badge | platform
                          ★ 2026-11-06 10:17 Feng: "move that circle, that green marker, after the name and left-align the name,
                            then it's uniform; because look, below you also have one, and below it is shown to the right of the host name."
                            — previously `● test_host_mac` (dot first) pushed the name right and out of alignment with the content below;
                              moving it after the name → **every row of the card aligns to the name's left edge**. */}
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex min-w-0 items-center gap-1.5">
                        <span className="truncate font-mono text-sm font-medium">{h.hostId}</span>
                        {(() => {
                          const p = hostPresence(h, t);
                          return (
                            <span
                              className={cn("h-2 w-2 shrink-0 rounded-full", p.dot)}
                              title={`${p.label} · ${p.hint}`}
                              data-testid={`host-online-${h.hostId}`}
                              /* data-online now reflects the **visual truth** (heartbeat), no longer equals presence_status —
                                   attribute and picture must agree, otherwise future debugging gets misled.
                                   The raw value is still available as online from /v1/snapshot. */
                              data-online={p.tone === "online"}
                              data-presence={p.tone}
                            />
                          );
                        })()}
                        {!h.active && (
                          <Badge variant="outline" className="px-1 py-0 text-[10px] text-muted-foreground">{t("system.host.disabled")}</Badge>
                        )}
                      </div>
                      <span
                        className="shrink-0 text-[11px] text-muted-foreground"
                        data-testid={`host-platform-${h.hostId}`}
                        title={h.platform}
                      >
                        {h.platform ? h.platform.replace(/\s*x86_64$/, "") : "—"}
                      </span>
                    </div>

                    {/* Three metrics: larger numbers
                          ★ 2026-11-05 15:36 Feng: "each box can be a bit taller, enlarge all the fonts, enlarge that 70 Agent number"
                            — was text-base (16px) and looked weak next to the 4xl cards; changed to text-2xl (24px) bold.
                          ★ 2026-11-06 09:49 Feng: "these three are not centered, center them, they are left-aligned now"
                            — the numbers were left-aligned (aligned to each column start, three numbers not centered, uneven padding).
                              Add text-center so the "number + label" block is centered in its own column:
                              the child div is a block without its own text-align → it inherits from the container. */}
                    <div className="mt-3 grid grid-cols-4 gap-2 text-center">
                      <div>
                        <div className="font-mono text-2xl font-semibold leading-none">
                          {/* [2026-11-05 23:25 → 23:32 Feng, two corrections]
                                1) 23:25: "click this 70, go to agents and auto-filter this host's agents"
                                   — color implies clickability, no underline.
                                2) 23:32: "you have a fatal problem, you did not use a popup window... otherwise you open a window
                                   and it navigates away, and when you come back you cannot get back. You must use a popup window,
                                   and feed the query condition into it."
                                   — the navigate approach was rejected, changed to an **in-place dialog**. The number is still a colored
                                     button (no underline); clicking opens "this host's Agent list" with host_id passed in directly. */}
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              setHostAgentsFor(h.hostId);
                            }}
                            title={t("system.host.viewAgents", { host: h.hostId, n: h.agentCount })}
                            data-testid={`host-agents-${h.hostId}`}
                            className="text-primary transition-colors hover:text-primary/70 focus-visible:underline focus-visible:outline-none"
                          >
                            {h.agentCount}
                          </button>
                          {h.pendingDeploy > 0 && (
                            <span className="ml-1 align-top text-[11px] text-muted-foreground" title={t("system.host.pendingDeploy", { n: h.pendingDeploy })}>
                              +{h.pendingDeploy}
                            </span>
                          )}
                        </div>
                        <div className="mt-1.5 text-[11px] text-muted-foreground">Agent</div>
                      </div>
                      <div>
                        {/* Session number clickable → the dialog embeds the session page itself, condition = this host (not clickable when 0) */}
                        {h.sessionCount > 0 ? (
                          <button
                            type="button"
                            onClick={() => setPg({ k: "hostSessions", host: h.hostId, count: h.sessionCount })}
                            title={t("system.host.viewSessions", { host: h.hostId, n: h.sessionCount })}
                            data-testid={`host-sessions-${h.hostId}`}
                            className="font-mono text-2xl font-semibold leading-none text-primary transition-colors hover:text-primary/70 focus-visible:underline focus-visible:outline-none"
                          >
                            {h.sessionCount}
                          </button>
                        ) : (
                          <div className="font-mono text-2xl font-semibold leading-none" data-testid={`host-sessions-${h.hostId}`}>
                            {h.sessionCount}
                          </div>
                        )}
                        <div className="mt-1.5 text-[11px] text-muted-foreground">{t("system.host.sessions")}</div>
                      </div>
                      <div
                        title={t("system.host.executionsHint", { n: h.taskCount })}
                      >
                        {h.taskCount > 0 ? (
                          <button
                            type="button"
                            onClick={() => setPg({ k: "hostExecutions", host: h.hostId, count: h.taskCount })}
                            title={t("system.host.viewExecutions", { host: h.hostId, n: h.taskCount })}
                            data-testid={`host-tasks-${h.hostId}`}
                            className="font-mono text-2xl font-semibold leading-none text-primary transition-colors hover:text-primary/70 focus-visible:underline focus-visible:outline-none"
                          >
                            {h.taskCount}
                          </button>
                        ) : (
                          <div className="font-mono text-2xl font-semibold leading-none" data-testid={`host-tasks-${h.hostId}`}>
                            {h.taskCount}
                          </div>
                        )}
                        {/* [2026-11-05 23:00 Feng] "there is a task inside the host, that's wrong, it should be task execution records"
                              — this number is `agx_task_execution` on the executing host (the backend definition itself),
                                not `agx_task_base`. test_host_mac alone has 5731, so calling it "task" necessarily misleads.
                              ★ Label "execution" rather than "execution record": site-wide "execution" already means an execution
                                instance (agent card "task 7 / execution 79", agent table "execution" column), so changing only
                                here to a long label would add a second vocabulary. Full semantics via title tooltip:
                                "N task execution records". */}
                        <div className="mt-1.5 text-[11px] text-muted-foreground">{t("system.host.executions")}</div>
                      </div>
                      {/* [2026-11-06 10:32 Feng] "raise slots to this same row as agent, because this row currently has three, make it four, add slots;
                            don't show so many, you can't read it when you show so many"
                            — it used to be a small line in the card's bottom sub-row, unrelated to the three big numbers above;
                              moved up as the fourth tile in the same row. Numerator = running executions (agx_task_execution
                              running), denominator = concurrency limit (task_slots_limit). Not clickable: no "slots page". */}
                      <div
                        data-testid={`host-slots-${h.hostId}`}
                        title={t("system.host.slotsHint", { running: h.runningTasks, slots: h.slots })}
                      >
                        {/* [2026-11-06 10:37 Feng] "change this font to blue too; the former one should be what you're using,
                              make it green, and the latter blue. The total becomes blue, the one in use is green.
                              The slash can stay as is, otherwise it looks out of place."
                              — the first three numbers are all text-primary blue; slots were black + grey, looking dull in the row.
                                Numerator (in use) = green (success, same semantic palette as the host online dot);
                                denominator (limit/total) = blue (primary, same family as the other three);
                                slash stays light grey, a separator, not content. */}
                        <div className="font-mono text-2xl font-semibold leading-none">
                          <span className="text-success">{h.runningTasks}</span>
                          <span className="mx-0.5 text-muted-foreground/50">/</span>
                          <span className="text-primary">{h.slots}</span>
                        </div>
                        <div className="mt-1.5 text-[11px] text-muted-foreground">{t("system.host.slotsLabel")}</div>
                      </div>
                    </div>

                    {/* [2026-11-06 10:32 Feng] The whole bottom sub-row deleted:
                          "the bottom row — slots, gateway sessions, 24h failures — remove it, don't want it;
                            don't show so many, you can't read it when you show so many, four is enough"
                          — slots moved up into one of the four tiles; the other two (gateway sessions = Redis definition,
                            24h failures) come from a different source than the three numbers above and sat right next to them,
                            which only made reading messier. Data is still in BFF (liveSessions / failedTasks24h), and can be
                            shown separately elsewhere when needed. */}
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Grid: row 1 — project overview + plan execution */}
        <div className="grid gap-4 lg:grid-cols-3">
          {/* 1. Project overview */}
          <Card data-testid="card-projects" className="flex flex-col">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-sm">
                <FolderKanban className="h-4 w-4 text-muted-foreground" /> {t("system.projects.title")}
              </CardTitle>
            </CardHeader>
            {/* [2026-11-05 13:08 Feng] "make active/archived/total bigger, larger font, put it in the middle, must fill it;
                   put the active share at the very bottom, full width, don't leave blank"
                   Three cards in the same grid row are equal height, this card has the least content → a blank block at the bottom is
                   inevitable. Solution: three cells flex-1 eat the remaining height (content centered), the share bar mt-auto sticks
                   to the bottom, full width. So the "blank" is spread across the three cells instead of piling up at the card bottom. */}
            <CardContent className="flex flex-1 flex-col gap-3">
              <div className="grid flex-1 grid-cols-3 gap-2">
                {/* All three numbers clickable → project page itself, condition by status. 0 gets no onClick */}
                <Metric
                  size="lg" label={t("system.projects.active")} value={s.projects.active}
                  tone={TONE_OK}
                  testId="dash-projects-active"
                  onClick={only(s.projects.active, () => ({ k: "projectStatus", label: t("system.projects.active"), status: "active", count: s.projects.active }))}
                />
                <Metric
                  size="lg" label={t("system.projects.archived")} value={s.projects.archived}
                  testId="dash-projects-archived"
                  onClick={only(s.projects.archived, () => ({ k: "projectStatus", label: t("system.projects.archivedLong"), status: "archived", count: s.projects.archived }))}
                />
                <Metric
                  size="lg" label={t("system.projects.total")} value={s.projects.total ?? s.projects.active + s.projects.archived}
                  testId="dash-projects-total"
                  onClick={only(s.projects.total ?? s.projects.active + s.projects.archived, () => ({
                    k: "projectStatus", label: t("system.projects.all"), status: "__all__",
                    count: s.projects.total ?? s.projects.active + s.projects.archived,
                  }))}
                />
              </div>
              <div className="mt-auto">
                <div className="mb-1 flex items-center justify-between text-xs text-muted-foreground">
                  <span>{t("system.projects.healthRatio")}</span><span className="font-medium text-foreground">{s.projects.healthScore}%</span>
                </div>
                {/* [2026-11-05 22:59 Feng] "the progress bar under the project overview still needs a color;
                       making it black and white is not quite right."
                       — the 18:58 pass turned the bar neutral dark grey (rationale then: "keep only black/white/grey").
                       But a progress bar is not a typographic element like text/title; it is a **data graphic**:
                       in the same product the plans / plan-flow progress bars have always been brand blue (Progress default
                       bg-primary), only this one turned grey → which instead hinted "these two are not the same kind of thing".
                       Restore the default brand color, consistent site-wide. */}
                <Progress value={s.projects.healthScore} className="h-2.5" />
              </div>
            </CardContent>
          </Card>

          {/* 2. Plan execution */}
          <Card data-testid="card-plans" className="flex flex-col">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-sm">
                <CalendarClock className="h-4 w-4 text-muted-foreground" /> {t("system.plans.title")}
              </CardTitle>
            </CardHeader>
            {/* [2026-11-05 13:23 Feng] Unify the three cards to "fill + center + large number":
                   in the same row there must not be one big tile and one small tile; it reads like two different component sets. */}
            <CardContent className="flex flex-1 flex-col gap-3">
              {/* ★ 2026-11-03 Feng: "running does not exist, you can check the database".
                     Changed to the real source agx_plan.plan_status with 6 values (failed is currently always 0, no placeholder).
                     Previously "running" counted "plans with running executions", which is a different thing from plan status;
                     and archived (5 rows) had no field at all → effectively nonexistent on the dashboard. */}
              <div className="grid flex-1 grid-cols-3 gap-2" data-testid="dash-plan-status">
                {([
                  ["system.plans.status.activated", "activated", s.plans.activated, TONE_OK],
                  ["system.plans.status.completed", "completed", s.plans.completed, undefined],
                  ["system.plans.status.cancelled", "cancelled", s.plans.cancelled, TONE_IDLE],
                  ["system.plans.status.draft", "draft", s.plans.draft, TONE_IDLE],
                  ["system.plans.status.cancelling", "cancelling", s.plans.cancelling, undefined],
                  ["system.plans.status.archived", "archived", s.plans.archived, TONE_IDLE],
                ] as const).map(([label, status, n, tone]) => (
                  <Metric
                    key={status}
                    size="lg" label={t(label)} value={n}
                    tone={tone}
                    testId={`dash-plans-${status}`}
                    onClick={only(n, () => ({ k: "planStatus", label: t(label), status, count: n }))}
                  />
                ))}
              </div>
              <div className="mt-auto flex items-center justify-between rounded-md border bg-muted/30 px-3 py-2 text-xs">
                <span className="text-muted-foreground">{t("system.plans.nextRun")}</span>
                <span className="font-medium" data-testid="dash-next-run">
                  {s.plans.nextRunAt || "—"}
                </span>
              </div>
            </CardContent>
          </Card>

          {/* 4. Agent status */}
          <Card data-testid="card-agents" className="flex flex-col">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-sm">
                <Bot className="h-4 w-4 text-muted-foreground" /> {t("system.agents.title")}
                {/* [2026-11-05 13:08 Feng] "86 agents in total" used to be its own line at the bottom →
                       moved into parentheses after the title; with the bottom line gone, the four cells naturally fill the card. */}
                <span className="text-xs font-normal text-muted-foreground">
                  {t("system.agents.totalCount", { n: totalAgents })}
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col gap-3">
              {/* [2026-11-05 13:20] Four statuses squeezed into 4 columns = 80px per cell,
                     a slightly larger number overflows → the number must stay small, and a 210px box holding small text looks
                     even emptier. Changed to 2×2: ~190px per cell, a 4xl number still fits comfortably, four blocks fill the card. */}
              <div className="grid flex-1 grid-cols-2 gap-2" data-testid="agent-status-grid">
                {([
                  ["system.agents.status.online", "online", s.agentMetrics.online, TONE_OK],
                  ["system.agents.status.busy", "busy", s.agentMetrics.busy, TONE_OK],
                  ["system.agents.status.offline", "offline", s.agentMetrics.offline, TONE_IDLE],
                  ["system.agents.status.error", "error", s.agentMetrics.error, TONE_BAD],
                ] as const).map(([label, status, n, tone]) => (
                  <Metric
                    key={status}
                    size="lg" label={t(label)} value={n}
                    tone={tone}
                    testId={`dash-agents-${status}`}
                    onClick={only(n, () => ({ k: "agentStatus", label: t(label), status, count: n }))}
                  />
                ))}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Row 2 — task execution records + active sessions */}
        <div className="grid gap-4 lg:grid-cols-3">
          {/* 3. Task execution records */}
          <Card data-testid="card-tasks" className="flex flex-col">
            <CardHeader className="pb-3">
              {/* [2026-11-05 13:39 Feng] "the line under the task overview, move it into parentheses after the title, fill the rest"
                     — "last 24h: success x · failure y" used to take a whole line at the card bottom, the seven cells crammed in the
                       upper half with a blank strip below. After moving into the title the bottom line disappears and the seven cells
                       fill the card.
                     [2026-11-05 23:00 Feng] "this title is wrong... there can't be 5000+ tasks,
                       it should be task execution records that have 5000+"
                     — the seven cells count `agx_task_execution` (executions) by run_status, not `agx_task_base` (task templates).
                       The whole database has only a few hundred task templates; 6000+ can only come from here → the title "task"
                       is semantically wrong. Changed to the same word as the left menu "task execution records", no guessing needed. */}
              <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
                <ListChecks className="h-4 w-4 text-muted-foreground" /> {t("system.tasks.title")}
                <span className="text-[11px] font-normal text-muted-foreground" data-testid="task-24h">
                  {t("system.tasks.last24h", { ok: s.tasks.succeeded24h, fail: s.tasks.failed24h })}
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col gap-3">
              {/* [2026-11-03 21:33 Feng] All 7 statuses shown, 3-column layout (3/3/1).
                     Three things to be clear about:
                     1) numbers are always **totals** (not last 24h) — same definition as the paginated total of the
                        "execution record" list; the last-24h pair moved to a small bottom line, taking no cell.
                     2) `pending` renamed "queued" and split into plan-queued (waiting for prerequisites) + scheduled
                        (waiting for its time). The old name "pending" suggested 129 items waiting for someone, while none needs action.
                     3) order follows the lifecycle (queued→running→succeeded/failed/cancelled/archived→draft), not the number
                        size — ordering by size makes positions change whenever data moves, which looks like flicker. */}
              <div className="grid flex-1 grid-cols-3 gap-2" data-testid="task-status-grid">
                <Metric
                  size="lg"
                  label={t("system.tasks.pending")}
                  value={s.tasks.byStatus?.pending ?? s.tasks.pending}
                  tone={TONE_OK}
                  sub={t("system.tasks.pendingSub", {
                    queued: s.tasks.pendingSplit?.queued ?? 0,
                    scheduled: s.tasks.pendingSplit?.scheduled ?? 0,
                  })}
                  testId="dash-tasks-pending"
                  onClick={only(s.tasks.byStatus?.pending ?? s.tasks.pending,
                    () => ({ k: "execStatus", label: t("system.tasks.pending"), status: "pending", count: s.tasks.byStatus?.pending ?? s.tasks.pending }))}
                />
                {([
                  ["system.tasks.status.running", "running", s.tasks.byStatus?.running ?? s.tasks.running, TONE_OK],
                  /* [2026-11-06] Awaiting human confirm = work is done, waiting for a human to release.
                        It must hold its own cell: merging into "running" suggests it is still running,
                        merging into "succeeded" suggests it is done — both lead to nobody clicking release. */
                  ["system.tasks.status.awaiting_confirm", "awaiting_confirm", s.tasks.byStatus?.awaiting_confirm ?? 0, undefined] as const,
                  ["system.tasks.status.succeeded", "succeeded", s.tasks.byStatus?.succeeded ?? 0, undefined],
                  ["system.tasks.status.failed", "failed", s.tasks.byStatus?.failed ?? 0, TONE_BAD],
                  ["system.tasks.status.cancelled", "cancelled", s.tasks.byStatus?.cancelled ?? 0, TONE_IDLE],
                  ["system.tasks.status.archived", "archived", s.tasks.byStatus?.archived ?? 0, TONE_IDLE],
                ] as const).map(([label, status, n, tone]) => (
                  <Metric
                    key={status}
                    size="lg" label={t(label)} value={n}
                    tone={tone}
                    testId={`dash-tasks-${status}`}
                    onClick={only(n, () => ({ k: "execStatus", label: t(label), status, count: n }))}
                  />
                ))}
                <Metric
                  size="lg"
                  label={t("system.tasks.status.draft")}
                  /* The 8 cells are now arranged 3+3+2: "archived" takes 1 cell in the last row, draft eats the remaining 2,
                        leaving no empty cell (an empty cell looks like a missing block). */
                  value={s.tasks.byStatus?.draft ?? 0}
                  tone={TONE_IDLE}
                  className="col-span-2"
                  testId="dash-tasks-draft"
                  onClick={only(s.tasks.byStatus?.draft ?? 0,
                    () => ({ k: "execStatus", label: t("system.tasks.status.draft"), status: "draft", count: s.tasks.byStatus?.draft ?? 0 }))}
                />
              </div>
            </CardContent>
          </Card>

          {/* 5. Active sessions */}
          <Card data-testid="card-sessions" className="flex flex-col">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-sm">
                <MessageSquare className="h-4 w-4 text-chart-4" /> {t("system.sessions.active")}
              </CardTitle>
            </CardHeader>
            {/* [2026-11-05 13:39 Feng] "this line chart is fine, fill it up, there's a blank below"
                   — the chart used to be a fixed 120px tall, so after the card was stretched by its row a blank strip appeared.
                     Changed to flex-1 + min-h: the chart grows with the card, X axis at the bottom. */}
            <CardContent className="flex flex-1 flex-col">
              {/* Active session number: click → the dialog embeds the session page itself (not clickable when 0) */}
              {s.sessions.active > 0 ? (
                <button
                  type="button"
                  onClick={() => setPg({ k: "sessions", count: s.sessions.active })}
                  data-testid="dash-sessions-active"
                  className="mb-2 self-start text-2xl font-semibold text-success transition-colors hover:opacity-80 focus-visible:underline focus-visible:outline-none"
                >
                  {s.sessions.active}
                </button>
              ) : (
                <div className="mb-2 text-2xl font-semibold" data-testid="dash-sessions-active">{s.sessions.active}</div>
              )}
              <FillChart minHeight={110}>
                {(chartH) => (
                <ResponsiveContainer width="100%" height={chartH}>
                  <AreaChart data={s.sessions.trend} margin={{ top: 4, right: 4, left: -24, bottom: 0 }}>
                    <defs>
                      <linearGradient id="sessFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="hsl(var(--chart-4))" stopOpacity={0.5} />
                        <stop offset="100%" stopColor="hsl(var(--chart-4))" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="date" tick={{ fontSize: 10 }} stroke="currentColor" opacity={0.4} />
                    <YAxis tick={{ fontSize: 10 }} stroke="currentColor" opacity={0.4} />
                    <RTooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid hsl(var(--border))" }} />
                    <Area type="monotone" dataKey="count" stroke="hsl(var(--chart-4))" fill="url(#sessFill)" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
                )}
              </FillChart>
            </CardContent>
          </Card>

          {/* Agent invocation ranking */}
          <Card data-testid="card-rank" className="flex flex-col">
            <CardHeader className="pb-3">
              {/* [2026-11-05 13:39 Feng] "the invocation ranking isn't filled either, an empty spot in the middle looks bad.
                     just six, top six, 1 2 3 on top, 4 5 6 below, lay it out in a grid"
                     — it was a 5-row list, the whitespace between rows stretched the card empty; changed to a 3×2 grid, fills it exactly. */}
              {/* 2026-11-05 15:36 Feng "invocation frequency — I don't know what you mean by invocation frequency, frequency of invoking what?"
                     — the data is agx_task_execution grouped and counted by agent_id, i.e. "how many jobs this Agent was
                       dispatched in the last 7 days". "Invocation frequency" drops the subject; nobody can tell what is counted.
                       The title now states the definition. */}
              <CardTitle className="flex flex-wrap items-baseline gap-2 text-sm" data-testid="rank-title">
                <TrendingUp className="h-4 w-4 text-muted-foreground" /> {t("system.sessions.rank")}
                <span className="text-[11px] font-normal text-muted-foreground">{t("system.sessions.rankHint")}</span>
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col">
              <div className="grid flex-1 grid-cols-3 gap-2" data-testid="rank-grid">
                {s.agentMetrics.top.slice(0, 6).map((a, i) => (
                  <RankCell
                    /* ★ 2026-11-11: the key used to be t.name, but there are 6 host-management Agents
                         **all named "agx_host"** (different ids: agx_host / _test_srv_a /
                         _test_srv_b / _test_srv_a2 / _test_srv_b2 / _test_srv_b3),
                         so React reported "two children with the same key".
                         A key only needs to be unique among siblings; agentId is the stable unique identifier. */
                    key={a.agentId || a.name}
                    i={i}
                    name={a.name}
                    calls={a.calls}
                    onOpen={
                      a.agentId && a.calls > 0
                        ? () => setPg({ k: "rankExec", name: a.name, agentId: a.agentId as string, count: a.calls })
                        : undefined
                    }
                  />
                ))}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Row 2.5 — session live status (2026-11-05)
              ★ This and the "active sessions" card above are two different definitions, deliberately side by side, not merged:
                that one is agx_session (MySQL, incl. archived, 4145 rows);
                this one is Redis agx:gw:* (really present on the gateway now, 2406 entities).
                Merging into one number forces picking one definition; the other becomes a lie. */}
        <Card data-testid="card-session-runtime">
          <CardHeader className="pb-3">
            <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
              <Radio className="h-4 w-4 text-muted-foreground" /> {t("system.sessions.liveTitle")}
              {srt ? (
                <span className="text-[11px] font-normal text-muted-foreground" data-testid="srt-scope">
                  {t("system.sessions.liveSummary", {
                    total: srt.total,
                    unique: srt.uniqueKeys,
                    conflicts: srt.conflicts ? t("system.sessions.conflicts", { n: srt.conflicts }) : "",
                    backend: srt.backend === "unavailable" ? t("system.sessions.redisFail") : "",
                  })}
                </span>
              ) : null}
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {!srt ? (
              <div className="text-xs text-muted-foreground" data-testid="srt-empty">
                {t("system.sessions.noLiveData")}
              </div>
            ) : (
              <>
                {/* [2026-11-05 13:23 Feng] "different types need to be boxed; right now this is a mess"
                       the problem before: three columns each with a pile of rows, each row carrying a thin progress bar — uneven
                       lengths, the lines more prominent than the text, the whole block read like three different tables.
                       Fix:
                         1) give each of the three sections its own box (status / host distribution / context watermark);
                         2) inside each box use a uniform "name | sessions | share" three-column layout, **drop the progress
                            bars**, compare by table alignment (column alignment + right-aligned numbers + tabular font is more
                            accurate than a room full of uneven lines);
                         3) the Top5 at the bottom also gets a box, changed to 5 horizontal cells (was one record per row). */}
                <div className="grid gap-3 lg:grid-cols-3">
                  {/* 1) Session status */}
                  <div className="flex flex-col rounded-lg border p-3" data-testid="srt-by-status">
                    <div className="mb-2 flex items-baseline justify-between">
                      <span className="text-xs font-medium">{t("system.sessions.states")}</span>
                      <span className="text-[11px] text-muted-foreground">{t("system.sessions.count", { n: srt.total })}</span>
                    </div>
                    <div className="grid flex-1 grid-cols-2 gap-2 content-stretch">
                      {([
                        { k: "running", v: srt.byStatus.running },
                        { k: "idle", v: srt.byStatus.idle },
                        { k: "failed", v: srt.byStatus.failed },
                        { k: "stalled", v: srt.byStatus.stalled },
                        { k: "unknown", v: srt.byStatus.unknown },
                      ] as const).map((r0, i) => {
                        /* [2026-11-13 09:23 Feng] "in the session live status there is also this running one,
                              that should turn green."
                              ★ Colors are no longer redefined here — use `STATUS_META` directly (the same one as the session list
                                and execution records). Previously these cells had an empty `tone`, so running/failed looked the same
                                as neutral numbers, two color languages on one page. */
                        const meta = STATUS_META[r0.k];
                        const r = { k: r0.k, label: t(meta.labelKey), v: r0.v, tone: meta.text };
                        /* [2026-11-06 11:10 Feng] "running, that is the chart live status, make all these links too;
                               you've done the previous ones, isn't this just passing a condition?"
                              → every tier is clickable: the dialog embeds **the session page itself** + the running status condition.
                              Tiers with 0 are not clickable (an empty list is more confusing than not clickable). */
                        const open = only(r.v, () => ({ k: "rtStatus", label: r.label, status: r.k, count: r.v }));
                        const cls = "flex items-center justify-between gap-1.5 rounded-md border bg-muted/40 px-2.5 py-2";
                        const inner = (
                          <>
                            <span className="text-[13px] text-muted-foreground">{r.label}</span>
                            <span className={cn(
                              "font-mono text-xl font-semibold leading-none tabular-nums",
                              /* tone first: status color > "clickable blue" > default foreground.
                                   ★ Do not paint everything blue just because it is clickable — that is exactly why the previous
                                     version could not show running/failed status. */
                              r.tone || (open ? "text-primary" : "text-foreground"),
                            )}>{r.v}</span>
                            <span className="w-9 shrink-0 text-right font-mono text-[13px] tabular-nums text-muted-foreground">{pctOf(r.v)}</span>
                          </>
                        );
                        return open ? (
                          <button
                            type="button"
                            key={r.k}
                            onClick={open}
                            data-testid={`srt-status-${r.k}`}
                            title={t("system.sessions.viewLabel", { label: r.label, n: r.v })}
                            className={cn(cls, "text-left transition-colors hover:border-primary/50 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", i === 4 && "col-span-2")}
                          >
                            {inner}
                          </button>
                        ) : (
                          <div key={r.k} data-testid={`srt-status-${r.k}`} className={cn(cls, i === 4 && "col-span-2")}>
                            {inner}
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* 2) Session host distribution */}
                  <div className="flex flex-col rounded-lg border p-3" data-testid="srt-by-host">
                    <div className="mb-2 flex items-baseline justify-between">
                      <span className="text-xs font-medium">{t("system.sessions.hostDist")}</span>
                      <span className="text-[11px] text-muted-foreground">{t("system.sessions.hostCount", { n: srt.byHost.length })}</span>
                    </div>
                    <div className="grid flex-1 grid-cols-2 gap-2 content-stretch">
                      {[...srt.byHost].sort((a, b) => b.total - a.total).map((h) => {
                        /* [2026-11-06 11:10] Host cells also become links: session page + host condition.
                             ★ Reuse HostSessionsDialog (hostSessions), do not build a new one —
                               it filters `agx_session.host_id`, the same definition as the host card. */
                        const open = only(h.total, () => ({ k: "hostSessions", host: h.hostId, count: h.total }));
                        const cls = "flex flex-col justify-center rounded-md border bg-muted/40 px-2.5 py-1.5";
                        const inner = (
                          <>
                          {/* 2026-11-05 16:29 Feng: host name top-left, font a bit larger; "put the red dot after the name";
                                numbers below left-aligned, share right-aligned, **both the same size**.
                                [2026-11-13 09:23] the dot after the name becomes an in-sync liveness dot:
                                green online / red offline (same criterion as the host card and host list = heartbeat).
                                ★ This dot used to mean "has failed sessions", colliding with "offline" (both red).
                                  Fix: liveness dot becomes **solid**, failed sessions use a **hollow red ring** —
                                  two signals in one position, told apart by shape, not by two kinds of red. */}
                          <span className="flex w-full min-w-0 items-center gap-1">
                            <span className="truncate font-mono text-sm font-medium text-foreground">{h.hostId}</span>
                            {(() => {
                              /* [2026-11-13 09:23 Feng] "the marker after the host must sync with the one above:
                                    online gets a green dot, offline a red dot."
                                    ★ Criterion = **the same function as the host card above**, hostPresence() (three heartbeat
                                      tiers), not recomputed; the tooltip gives the exact tier (online/lost/offline/no heartbeat),
                                      so a red dot does not mean "it is dead", it means "it is not reporting now".
                                    ★ Why not the grey/yellow above: Feng wanted "see at a glance which host is abnormal";
                                      a grey dot is nearly invisible in small text on a dark background. Green/red is enough. */
                              const p = hostPresence({ heartbeatAgeSeconds: hostAge.get(h.hostId) ?? null }, t);
                              const on = p.tone === "online";
                              return (
                                <span
                                  className={cn(
                                    "h-1.5 w-1.5 shrink-0 rounded-full",
                                    on ? "bg-success" : "bg-destructive",
                                  )}
                                  /* [2026-11-13 13:47 Feng] here, besides the liveness dot, it also drew a
                                       "hollow red circle" meaning this host has failed sessions — as a result two hosts in the
                                       first row showed "green dot + red circle", looking like duplication / a render bug.
                                       ★ One position carries one signal: the liveness dot only means online status;
                                         the failed-session count is folded into the tooltip, not drawn separately.
                                         (A "visible" failure hint should go through text/badges, not grab this dot) */
                                  title={t("system.sessions.pointHint", {
                                    label: p.label,
                                    hint: p.hint,
                                    failed: h.failed > 0 ? ` · ${t("system.sessions.hostFailed", { n: h.failed })}` : "",
                                  })}
                                  data-testid={`srt-hostdot-${h.hostId}`}
                                  data-online={on ? "1" : "0"}
                                  data-presence={p.tone}
                                />
                              );
                            })()}
                          </span>
                          <span className="mt-1 flex w-full items-baseline justify-between gap-1">
                            <span className={cn(
                              "font-mono text-lg font-semibold leading-none tabular-nums",
                              open ? "text-primary" : "text-foreground",
                            )}>{h.total}</span>
                            <span className="font-mono text-lg leading-none tabular-nums text-muted-foreground">{pctOf(h.total)}</span>
                          </span>
                          </>
                        );
                        return open ? (
                          <button
                            type="button"
                            key={h.hostId}
                            onClick={open}
                            data-testid={`srt-host-${h.hostId}`}
                            title={t("system.sessions.hostTip", {
                              host: h.hostId,
                              total: h.total,
                              running: h.running ? t("system.sessions.hostRunning", { n: h.running }) : "",
                              failed: h.failed ? t("system.sessions.hostFailed", { n: h.failed }) : "",
                            })}
                            className={cn(cls, "text-left transition-colors hover:border-primary/50 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring")}
                          >
                            {inner}
                          </button>
                        ) : (
                          <div
                            key={h.hostId}
                            data-testid={`srt-host-${h.hostId}`}
                            className={cls}
                            title={t("system.sessions.hostTip", { host: h.hostId, total: h.total, running: "", failed: "" })}
                          >
                            {inner}
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* 3) Context watermark */}
                  <div className="flex flex-col rounded-lg border p-3" data-testid="srt-context">
                    <div className="mb-2 flex items-baseline justify-between">
                      <span className="text-xs font-medium">{t("system.sessions.ctxTitle")}</span>
                      <span className="text-[11px] text-muted-foreground">{t("system.sessions.ctxHint")}</span>
                    </div>
                    <div className="grid flex-1 grid-cols-2 gap-2 content-stretch">
                      {srt.contextBuckets.map((b) => {
                        /* 2026-11-05 18:58 Feng: "change all the text to the black version, don't make it all colorful"
                              — watermark tiers used to be colored red/orange at 100%/80%, now unified neutral;
                                danger is read from the number itself (>100% is obvious), not shouted by color. */
                        /* [2026-11-06 11:10] Similarly clickable: session page + context watermark condition.
                              Tiers defined in `lib/session-context.ts`, same boundaries as backend `_BUCKETS`. */
                        const open = only(b.count, () => ({ k: "rtBucket", label: b.label, bucket: b.key, count: b.count }));
                        const cls = "flex items-center justify-between gap-1.5 rounded-md border bg-muted/40 px-2.5 py-2";
                        const inner = (
                          <>
                            <span className="text-[13px] text-muted-foreground">
                              {b.label}
                            </span>
                            <span className={cn(
                              "font-mono text-xl font-semibold leading-none tabular-nums",
                              open ? "text-primary" : "text-foreground",
                            )}>
                              {b.count}
                            </span>
                            <span className="w-9 shrink-0 text-right font-mono text-[13px] tabular-nums text-muted-foreground">{pctOf(b.count)}</span>
                          </>
                        );
                        return open ? (
                          <button
                            type="button"
                            key={b.key}
                            onClick={open}
                            data-testid={`srt-bucket-${b.key}`}
                            title={t("system.sessions.ctxView", { label: b.label, n: b.count })}
                            className={cn(cls, "text-left transition-colors hover:border-primary/50 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring")}
                          >
                            {inner}
                          </button>
                        ) : (
                          <div key={b.key} data-testid={`srt-bucket-${b.key}`} className={cls}>
                            {inner}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>

                {/* 4) Top 5 context usage — one long box + 5 horizontal cells */}
                <div className="rounded-lg border p-3" data-testid="srt-top-risk">
                  <div className="mb-2 flex items-baseline justify-between">
                    <span className="text-xs font-medium">{t("system.sessions.ctxTop5")}</span>
                    <span className="text-[11px] text-muted-foreground">{t("system.sessions.ctxTopHint")}</span>
                  </div>
                  {srt.topRisk.length === 0 ? (
                    <div className="text-xs text-muted-foreground">{t("system.sessions.none")}</div>
                  ) : (
                    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
                      {srt.topRisk.map((x, i) => {
                        return (
                          /* [2026-11-06 11:10 Feng] "including the context below, the highest ones"
                                — these 5 are not "a batch of data", they are **this one session**:
                                  open its detail directly (the site-wide detail dialog), not the list page. */
                          <button
                            type="button"
                            key={`${x.hostId}:${x.sessionKey}`}
                            data-testid={`srt-risk-${x.sessionKey}`}
                            onClick={() => popup?.open({ kind: "session", id: x.sessionKey })}
                            title={t("system.sessions.openSession", {
                              name: x.sessionName || x.sessionKey,
                              agent: x.agentId,
                              host: x.hostId,
                            })}
                            className="flex flex-col gap-1 rounded-md border bg-muted/40 p-2 text-left transition-colors hover:border-primary/50 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          >
                            <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                              <span className="font-mono">#{i + 1}</span>
                              <span className="font-mono font-semibold tabular-nums text-foreground">
                                {formatRatio(x.contextRatio)}
                              </span>
                            </div>
                            <div className="truncate text-xs font-medium" title={`${x.sessionName}\n${x.sessionKey}`}>
                              {x.sessionName || x.sessionKey}
                            </div>
                            <div className="truncate font-mono text-[10px] text-muted-foreground" title={`${x.agentId} · ${x.hostId}`}>
                              {x.agentId} · {x.hostId}
                            </div>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              </>
            )}
          </CardContent>
        </Card>

        {/* Row 3 — execution trend + role distribution */}
        <div className="grid gap-4 lg:grid-cols-3">
          {/* 2026-11-05 15:36 Feng: "what task is the 7-day execution trend? task invocation or what? that's unclear"
                 — the data is agx_task_execution counted per day, i.e. "how many tasks were dispatched each day". The title says it;
                   also replaced the fixed 220px with FillChart: the role distribution card in the same row is taller, and a fixed
                   height would leave a white strip below. */}
          <Card className="flex flex-col lg:col-span-2" data-testid="card-trend">
            <CardHeader className="pb-3">
              <CardTitle className="flex flex-wrap items-baseline gap-2 text-sm" data-testid="trend-title">
                <Activity className="h-4 w-4 text-info" /> {t("system.trend.title")}
                <span className="text-[11px] font-normal text-muted-foreground">{t("system.trend.hint")}</span>
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col">
              <FillChart minHeight={180}>
                {(chartH) => (
                <ResponsiveContainer width="100%" height={chartH}>
                  <BarChart data={s.runsTrend} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.15} vertical={false} />
                    <XAxis dataKey="date" tick={{ fontSize: 11 }} stroke="currentColor" opacity={0.4} />
                    <YAxis tick={{ fontSize: 11 }} stroke="currentColor" opacity={0.4} />
                    <RTooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid hsl(var(--border))" }} />
                    <Bar dataKey="runs" fill="hsl(var(--info))" radius={[4, 4, 0, 0]} name={t("system.trend.runs")} />
                  </BarChart>
                </ResponsiveContainer>
                )}
              </FillChart>
            </CardContent>
          </Card>

          <Card data-testid="card-roles">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">{t("system.trend.roleDist")}</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="h-[220px]">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={s.roleDistribution.filter((r) => r.value > 0)} dataKey="value" nameKey="name"
                      innerRadius={45} outerRadius={75} paddingAngle={3}>
                      {s.roleDistribution.filter((r) => r.value > 0).map((_, i) => (
                        <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                      ))}
                    </Pie>
                    <RTooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid hsl(var(--border))" }} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="flex flex-wrap gap-2">
                {s.roleDistribution.filter((r) => r.value > 0).map((r, i) => (
                  <span key={r.name} className="flex items-center gap-1 text-xs text-muted-foreground">
                    <span className="h-2 w-2 rounded-full" style={{ background: PIE_COLORS[i % PIE_COLORS.length] }} />
                    {r.name} ({r.value})
                  </span>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* [2026-11-13 09:08] This page's own send-message dialog was removed — the top full-width button is the only site-wide entry.
             Keeping one instance would create "two dialog components each holding form state", prone to double-opening. */}

      <AgentsDialog
        hostId={hostAgentsFor ?? ""}
        count={hs?.byHost?.find((x) => x.hostId === hostAgentsFor)?.agentCount}
        open={!!hostAgentsFor}
        onOpenChange={(o) => { if (!o) setHostAgentsFor(null); }}
      />

      {/* [2026-11-06 00:03] The remaining numbers on the dashboard: likewise "dialog embeds the page itself + condition passed in" */}
      <HostSessionsDialog
        hostId={pg?.k === "hostSessions" ? pg.host : ""}
        count={pg?.k === "hostSessions" ? pg.count : undefined}
        open={pg?.k === "hostSessions"}
        onOpenChange={(o) => { if (!o) closePg(); }}
      />
      <HostExecutionsDialog
        hostId={pg?.k === "hostExecutions" ? pg.host : ""}
        count={pg?.k === "hostExecutions" ? pg.count : undefined}
        open={pg?.k === "hostExecutions"}
        onOpenChange={(o) => { if (!o) closePg(); }}
      />
      <StatusExecutionsDialog
        label={pg?.k === "execStatus" ? pg.label : ""}
        status={pg?.k === "execStatus" ? pg.status : ""}
        count={pg?.k === "execStatus" ? pg.count : undefined}
        open={pg?.k === "execStatus"}
        onOpenChange={(o) => { if (!o) closePg(); }}
      />
      <StatusPlansDialog
        label={pg?.k === "planStatus" ? pg.label : ""}
        status={pg?.k === "planStatus" ? pg.status : ""}
        count={pg?.k === "planStatus" ? pg.count : undefined}
        open={pg?.k === "planStatus"}
        onOpenChange={(o) => { if (!o) closePg(); }}
      />
      <StatusAgentsDialog
        label={pg?.k === "agentStatus" ? pg.label : ""}
        status={pg?.k === "agentStatus" ? pg.status : ""}
        count={pg?.k === "agentStatus" ? pg.count : undefined}
        open={pg?.k === "agentStatus"}
        onOpenChange={(o) => { if (!o) closePg(); }}
      />
      <StatusProjectsDialog
        label={pg?.k === "projectStatus" ? pg.label : ""}
        status={pg?.k === "projectStatus" ? pg.status : ""}
        count={pg?.k === "projectStatus" ? pg.count : undefined}
        open={pg?.k === "projectStatus"}
        onOpenChange={(o) => { if (!o) closePg(); }}
      />
      <ActiveSessionsDialog
        count={pg?.k === "sessions" ? pg.count : undefined}
        open={pg?.k === "sessions"}
        onOpenChange={(o) => { if (!o) closePg(); }}
      />
      {/* Ranking tile → that Agent's execution records for the last 7 days */}
      <RankExecutionsDialog
        agentName={pg?.k === "rankExec" ? pg.name : ""}
        agentId={pg?.k === "rankExec" ? pg.agentId : ""}
        count={pg?.k === "rankExec" ? pg.count : undefined}
        open={pg?.k === "rankExec"}
        onOpenChange={(o) => { if (!o) closePg(); }}
      />
      {/* Session live status: status tile / watermark tile → session page + condition */}
      <RuntimeSessionsDialog
        label={pg?.k === "rtStatus" ? pg.label : ""}
        status={pg?.k === "rtStatus" ? pg.status : ""}
        count={pg?.k === "rtStatus" ? pg.count : undefined}
        open={pg?.k === "rtStatus"}
        onOpenChange={(o) => { if (!o) closePg(); }}
      />
      <ContextSessionsDialog
        label={pg?.k === "rtBucket" ? pg.label : ""}
        bucket={pg?.k === "rtBucket" ? pg.bucket : ""}
        count={pg?.k === "rtBucket" ? pg.count : undefined}
        open={pg?.k === "rtBucket"}
        onOpenChange={(o) => { if (!o) closePg(); }}
      />
    </div>
  );
}
