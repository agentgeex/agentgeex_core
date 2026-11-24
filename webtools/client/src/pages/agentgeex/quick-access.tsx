/**
 * pages/agentgeex/quick-access.tsx — quick access
 *
 * [2026-11-13 22:03 Feng]
 *   "I do a lot of operations frequently now, but every time I still have to find them in a list (e.g. message send,
 *     once closed it's gone). Can we add a module under system status called "quick access": a certain session /
 *     a certain Agent / a certain plan / a certain project — I mark it as quick, and when I open this window they are there."
 *
 * Design trade-offs:
 *   · **Sections, not one big table**: the four object kinds have different information structures (project has status,
 *     Agent has online state, session has runtime, plan has progress); mixed in one table only the most generic fields
 *     can show.
 *   · **Invalid items are not hidden**: after the target is deleted/renamed it still shows, marked "invalid" —
 *     silently disappearing is more confusing than showing one invalid item ("but I did favorite it").
 *   · [2026-11-13 22:25/22:31 Feng changed the rule] the marker lives directly in the `is_quick` column of the 4 core
 *     tables (project / Agent / session / plan), **one global copy**, no longer bucketed per person;
 *     the name is fetched from the target table by the backend (no longer a snapshot taken at favorite time).
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import {
  FolderKanban, Bot, MessageSquare, CalendarClock, Star, ExternalLink, Trash2,
  RefreshCw, Loader2, AlertTriangle, Plus, Send,
} from "lucide-react";
import {
  listQuickEntries, refreshQuickEntries, removeQuickEntry,
  listProjects, listAgents, listSessions, listPlans, projectName,
} from "@/providers/agentgeex";
import type { QuickEntryRow, QuickEntryType } from "@/mock/agentgeex";
import { MessageSendDialog } from "@/components/agentgeex/message-send-dialog";
/* [2026-11-20 Feng] "opening should still use a popup window, don't navigate straight to the page... if you don't open the dialog you can't get back to this page" */
import { useDetailPopup, type DetailKind } from "@/components/agentgeex/detail-popup";
import { SessionSendDialog } from "@/components/agentgeex/session-send-dialog";
import type { SessionRow } from "@/providers/agentgeex";
import { sessionDeepLink } from "@/lib/openclaw";
import { isSessionRunning, SessionRunDot } from "@/components/agentgeex/session-runtime-badge";
import { cn } from "@/lib/utils";

type Resolved = {
  entry: QuickEntryRow;
  /** Whether the entity still exists (false = deleted/archived → shown as invalid, still removable) */
  alive: boolean;
  title: string;
  subtitle: string;
  /** Internal route (link when present); empty means external link or not navigable */
  href: string;
  external?: boolean;
  /** Default target of "send message"; null when there is no default recipient (button not shown) */
  send: SendTarget | null;
};

/** [2026-11-13 22:39 Feng] add "send message" to every quick access item:
 *   session → recipient = that session, sender = that Agent's that session (reuse SessionSendDialog's locking rules)
 *   Agent → sender / sending session / receiving session all default to that Agent's main session
 *   project → default to the main session of that project's PM (admin_agent_id)
 *   plans not included (Feng only named these three).
 */
type SendTarget =
  | { kind: "session"; session: SessionRow; projectId: string }
  | { kind: "agent"; agentId: string; projectId: string }
  | { kind: "project"; pmId: string; projectId: string };

/* Store only the key (module-level constants cannot get the hook). */
const TYPE_META: Record<QuickEntryType, { labelKey: string; icon: typeof Bot; hintKey: string; to: string }> = {
  project: { labelKey: "quickAccessPage.type.project", icon: FolderKanban, hintKey: "quickAccessPage.hint.project", to: "/projects" },
  agent: { labelKey: "quickAccessPage.type.agent", icon: Bot, hintKey: "quickAccessPage.hint.agent", to: "/agents" },
  session: { labelKey: "quickAccessPage.type.session", icon: MessageSquare, hintKey: "quickAccessPage.hint.session", to: "/sessions" },
  plan: { labelKey: "quickAccessPage.type.plan", icon: CalendarClock, hintKey: "quickAccessPage.hint.plan", to: "/plans" },
};

const ORDER: QuickEntryType[] = ["project", "agent", "session", "plan"];

/* Which detail dialog "open" lands on (session does not go here — it navigates to the native OpenClaw page, handled separately) */
const DETAIL_KIND: Record<QuickEntryType, DetailKind> = {
  project: "project",
  agent: "agent",
  session: "session",
  plan: "plan",
};

/* [2026-11-20 Feng] "the grid colors are too plain, at a glance it took me a while to find it"
   → give each of the four kinds an accent color (chart-N is the existing category color, no semantics;
     used like status colors: border-chart-N/30 bg-chart-N/15 text-chart-N).
   ★ Must be written as **static class literals**: Tailwind only scans complete strings in the source;
     a concatenation like `bg-chart-${n}/10` is invisible at build time and becomes "no color". */
const ACCENT: Record<QuickEntryType, {
  section: string;    // Section container: light background + colored border
  head: string;       // Section header: one level darker background
  iconChip: string;   // Small icon block
  count: string;      // Item count badge
  card: string;       // Item card
}> = {
  project: {
    section: "border-chart-5/35 bg-chart-5/5",
    head: "border-b border-chart-5/25 bg-chart-5/10",
    iconChip: "bg-chart-5/20 text-chart-5",
    count: "bg-chart-5/20 text-chart-5",
    card: "border-chart-5/40 bg-chart-5/15 hover:border-chart-5/70 hover:bg-chart-5/25",
  },
  agent: {
    section: "border-chart-1/35 bg-chart-1/5",
    head: "border-b border-chart-1/25 bg-chart-1/10",
    iconChip: "bg-chart-1/20 text-chart-1",
    count: "bg-chart-1/20 text-chart-1",
    card: "border-chart-1/40 bg-chart-1/15 hover:border-chart-1/70 hover:bg-chart-1/25",
  },
  session: {
    section: "border-chart-6/35 bg-chart-6/5",
    head: "border-b border-chart-6/25 bg-chart-6/10",
    iconChip: "bg-chart-6/20 text-chart-6",
    count: "bg-chart-6/20 text-chart-6",
    card: "border-chart-6/40 bg-chart-6/15 hover:border-chart-6/70 hover:bg-chart-6/25",
  },
  plan: {
    section: "border-chart-2/35 bg-chart-2/5",
    head: "border-b border-chart-2/25 bg-chart-2/10",
    iconChip: "bg-chart-2/20 text-chart-2",
    count: "bg-chart-2/20 text-chart-2",
    card: "border-chart-2/40 bg-chart-2/15 hover:border-chart-2/70 hover:bg-chart-2/25",
  },
};

type TFn = (k: string, o?: Record<string, unknown>) => string;

/* [2026-11-20 Feng] "change the buttons too, too plain"
   Three weights: send (primary, what should be seen first) > open (info, secondary) > delete (grey, red only on hover).
   ★ Extracted into constants instead of inline: the three buttons are scattered in the card; changing color = one place. */
const OPEN_BTN =
  "inline-flex shrink-0 items-center gap-1 rounded-md border border-info/40 bg-info/10 px-2 py-1 text-[11px] font-medium text-info transition-colors hover:bg-info/20";
const SEND_BTN =
  "inline-flex shrink-0 items-center gap-1 rounded-md border border-primary/50 bg-primary/20 px-2 py-1 text-[11px] font-semibold text-primary transition-colors hover:bg-primary/30";
const REMOVE_BTN =
  "shrink-0 rounded-md border border-transparent px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:border-destructive/40 hover:bg-destructive/10 hover:text-destructive disabled:opacity-40";

/* Pure function → pass t in (module level cannot get the hook). */
function resolveAll(entries: QuickEntryRow[], t: TFn): Record<QuickEntryType, Resolved[]> {
  const projects = listProjects();
  const agents = listAgents();
  const sessions = listSessions();
  const plans = listPlans();

  const out: Record<QuickEntryType, Resolved[]> = { project: [], agent: [], session: [], plan: [] };

  for (const e of entries) {
    const type = e.entryType;
    if (!TYPE_META[type]) continue;
    let alive = false;
    let title = e.label || e.entryKey;
    let subtitle = e.entryKey;
    let href = "";
    let send: SendTarget | null = null;

    if (type === "project") {
      const p = projects.find((x) => x.id === e.entryKey);
      alive = !!p;
      title = p?.name || projectName(e.entryKey) || e.label || e.entryKey;
      subtitle = p?.status === "archived"
        ? t("quickAccessPage.archived", { id: e.entryKey })
        : [p?.abbr, p?.platform, t("quickAccessPage.agentCount", { count: p?.agentCount ?? 0 })]
            .filter(Boolean).join(" · ") || e.entryKey;
      href = `/projects/${encodeURIComponent(e.entryKey)}`;
      /* A project has no "session" of its own: default to sending to that project PM's main session (PM = admin_agent_id) */
      if (p?.adminAgentId) send = { kind: "project", pmId: p.adminAgentId, projectId: e.entryKey };
    } else if (type === "agent") {
      const a = agents.find((x) => x.id === e.entryKey);
      alive = !!a;
      title = a?.name || e.label || e.entryKey;
      subtitle = a
        ? `${a.status === "online" ? t("quickAccessPage.online") : t("quickAccessPage.offline")} · ${a.id}`
        : e.entryKey;
      href = `/agents/${encodeURIComponent(e.entryKey)}`;
      /* The top project dropdown must follow this Agent's ownership (Feng 23:16) */
      send = { kind: "agent", agentId: e.entryKey, projectId: a?.project_id || "" };
    } else if (type === "session") {
      const s = sessions.find((x) => x.id === e.entryKey);
      alive = !!s;
      title = s?.name || e.label || e.entryKey;
      subtitle = e.entryKey;
      /* Session prefers navigating to the native OpenClaw page (that is where you can actually talk);
            when no deep link is available, fall back to the console session list. */
      const deep = s ? sessionDeepLink(s.id) : "";
      href = deep || `/sessions`;
      if (s) {
        /* A session has no project field of its own → take its owning Agent's project */
        const owner = agents.find((x) => x.id === s.agentId);
        send = { kind: "session", session: s, projectId: owner?.project_id || "" };
      }
      if (deep) out[type].push({ entry: e, alive, title, subtitle, href, external: true, send });
      else out[type].push({ entry: e, alive, title, subtitle, href, send });
      continue;
    } else {
      const p = plans.find((x) => x.id === e.entryKey);
      alive = !!p;
      title = p?.name || e.label || e.entryKey;
      subtitle = p ? `${p.status || "—"} · ${e.entryKey}` : e.entryKey;
      href = `/plans/${encodeURIComponent(e.entryKey)}/flow`;
    }

    out[type].push({ entry: e, alive, title, subtitle, href, send });
  }
  return out;
}

export default function QuickAccessPage() {
  const { t } = useTranslation();
  /* Detail dialog (null when there is no Provider → degrades to the original navigate Link, so it is never unclickable) */
  const popup = useDetailPopup();
  const [entries, setEntries] = React.useState<QuickEntryRow[]>(() => listQuickEntries());
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState<string>("");
  const [tick, setTick] = React.useState(0);
  /* [2026-11-13 22:39 Feng] Target of the "send message" button (null = do not open) */
  const [sendTarget, setSendTarget] = React.useState<SendTarget | null>(null);

  React.useEffect(() => {
    let alive = true;
    setLoading(true);
    refreshQuickEntries()
      .then((rows) => { if (alive) { setEntries(rows); setError(""); } })
      .catch((e: { message?: string }) => {
        if (alive) setError(e?.message || t("quickAccessPage.readFailed"));
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [tick]);

  const groups = React.useMemo(() => resolveAll(entries, t), [entries, tick, t]);

  const drop = async (e: QuickEntryRow) => {
    const key = `${e.entryType}:${e.entryKey}`;
    setBusy(key);
    try {
      const rows = await removeQuickEntry(e.entryType, e.entryKey);
      setEntries(rows);
    } catch (err) {
      setError((err as { message?: string })?.message || t("quickAccessPage.removeFailed"));
    } finally {
      setBusy("");
    }
  };

  const total = entries.length;

  return (
    <div className="flex flex-col" data-testid="page-quick-access">
      <div className="flex flex-col gap-4 rounded-xl border border-strong bg-card p-4 shadow-sm" data-testid="content-frame">
        <div className="flex flex-wrap items-center gap-2">
          <Star className="h-4 w-4 text-warning" />
          <h1 className="text-base font-semibold">{t("quickAccessPage.title")}</h1>
          <span className="text-xs text-muted-foreground">
            {loading
              ? t("quickAccessPage.loading")
              : total
                ? t("quickAccessPage.counted", { count: total })
                : t("quickAccessPage.empty")}
          </span>
          <button
            type="button"
            onClick={() => setTick((n) => n + 1)}
            className="ml-auto inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs text-muted-foreground hover:text-foreground"
            data-testid="qa-refresh"
          >
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            {t("quickAccessPage.refresh")}
          </button>
        </div>

        {error ? (
          <p className="flex items-center gap-1.5 text-xs text-destructive" data-testid="qa-error">
            <AlertTriangle className="h-3.5 w-3.5" />{error}
          </p>
        ) : null}

        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {ORDER.map((type) => {
            const meta = TYPE_META[type];
            const label = t(meta.labelKey);
            const Icon = meta.icon;
            const rows = groups[type];
            return (
              <section
                key={type}
                className={cn("flex min-w-0 flex-col overflow-hidden rounded-lg border", ACCENT[type].section)}
                data-testid={`qa-group-${type}`}
              >
                <header className={cn("flex items-center gap-2 px-3 py-2", ACCENT[type].head)}>
                  <span className={cn("inline-flex h-6 w-6 items-center justify-center rounded-md", ACCENT[type].iconChip)}>
                    <Icon className="h-3.5 w-3.5" />
                  </span>
                  <span className="text-sm font-semibold">{label}</span>
                  <span className={cn("rounded px-1.5 text-[11px] font-medium tabular-nums", ACCENT[type].count)}>
                    {rows.length}
                  </span>
                  <Link
                    to={meta.to}
                    className="ml-auto inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
                  >
                    <Plus className="h-3 w-3" />{t("quickAccessPage.toList", { label })}
                  </Link>
                </header>

                {rows.length === 0 ? (
                  <p className="px-3 py-4 text-xs text-muted-foreground">
                    {t("quickAccessPage.addHint", { hint: t(meta.hintKey) })}
                  </p>
                ) : (
                  <div className="grid min-w-0 grid-cols-1 gap-2 p-2 sm:grid-cols-2 2xl:grid-cols-3">
                    {rows.map((r) => {
                      const key = `${r.entry.entryType}:${r.entry.entryKey}`;
                      return (
                        /* [2026-11-19 Feng] "change each category to a grid... right now yours is one column, row by row,
                              wasting a big blank in the middle" → inside each category switch to grid cards, 2~3 per row.
                              ★ Content and buttons **unchanged, one by one**: title / subtitle / runtime dot / open /
                                send / delete, only changing "title left + buttons right, blank in between" into a card
                                with "title on top, buttons below". */
                        <div
                          key={r.entry.entryId || key}
                          className={cn("flex min-w-0 flex-col gap-1 rounded-md border p-2 transition-colors", ACCENT[type].card)}
                          data-testid={`qa-item-${key}`}
                        >
                          <span className={cn("truncate text-sm", !r.alive && "text-muted-foreground line-through")} title={r.title}>
                            {r.title}
                          </span>
                          <span className="flex min-w-0 items-center gap-1">
                            {/* [2026-11-13 23:42 Feng] mark the status where the session is — the same dot as the send dialog */}
                            {r.send?.kind === "session" && (
                              <SessionRunDot
                                running={isSessionRunning(r.send.session.runtime)}
                                testId={`qa-run-${r.entry.entryKey}`}
                              />
                            )}
                            <span className="truncate font-mono text-[10px] text-muted-foreground" title={r.subtitle}>
                              {r.alive
                                ? r.subtitle
                                : `${r.subtitle}${t("quickAccessPage.expiredSuffix")}`}
                            </span>
                          </span>

                          <div className="mt-0.5 flex flex-wrap items-center gap-1">
                            {r.href && r.alive ? (
                              r.external ? (
                                /* Session: native OpenClaw page (opened in a new tab, this page stays) */
                                <a
                                  href={r.href}
                                  target="_blank"
                                  rel="noreferrer noopener"
                                  className={OPEN_BTN}
                                  data-testid={`qa-open-${key}`}
                                >
                                  <ExternalLink className="h-3 w-3" />{t("quickAccessPage.open")}
                                </a>
                              ) : popup ? (
                                /* Project / Agent / plan: open the detail in place — closing it returns to this page,
                                      no more whole-page jump (jumping means pressing back and losing this page's scroll position) */
                                <button
                                  type="button"
                                  onClick={() => popup.open({ kind: DETAIL_KIND[type], id: r.entry.entryKey })}
                                  className={OPEN_BTN}
                                  data-testid={`qa-open-${key}`}
                                >
                                  <ExternalLink className="h-3 w-3" />{t("quickAccessPage.open")}
                                </button>
                              ) : (
                                <Link
                                  to={r.href}
                                  className={OPEN_BTN}
                                  data-testid={`qa-open-${key}`}
                                >
                                  <ExternalLink className="h-3 w-3" />{t("quickAccessPage.open")}
                                </Link>
                              )
                            ) : null}

                            {r.alive && r.send ? (
                              <button
                                type="button"
                                onClick={() => setSendTarget(r.send)}
                                title={t("quickAccessPage.sendTitle")}
                                className={SEND_BTN}
                                data-testid={`qa-send-${key}`}
                              >
                                <Send className="h-3 w-3" />{t("quickAccessPage.send")}
                              </button>
                            ) : null}

                            <button
                              type="button"
                              onClick={() => void drop(r.entry)}
                              disabled={busy === key}
                              title={t("quickAccessPage.removeTitle")}
                              className={REMOVE_BTN}
                              data-testid={`qa-remove-${key}`}
                            >
                              {busy === key ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </section>
            );
          })}
        </div>
      </div>

      {/* [2026-11-13 22:39 Feng] quick access sends messages directly:
            · session uses SessionSendDialog (both receive/send locked to "this session + its owning Agent")
            · Agent / project PM use the generic dialog, three places pre-filled, still editable
            · key is the target identifier → switch one item and it remounts, never carrying the previous defaults */}
      {sendTarget?.kind === "session" ? (
        <SessionSendDialog
          session={sendTarget.session}
          initialProject={sendTarget.projectId}
          open
          onOpenChange={(o) => { if (!o) setSendTarget(null); }}
        />
      ) : sendTarget ? (
        <MessageSendDialog
          key={sendTarget.kind === "agent" ? `agent:${sendTarget.agentId}` : `pm:${sendTarget.pmId}`}
          open
          initialProject={sendTarget.projectId}
          onOpenChange={(o) => { if (!o) setSendTarget(null); }}
          initial={{
            senderAgent: sendTarget.kind === "agent" ? sendTarget.agentId : sendTarget.pmId,
            senderSession: `agent:${sendTarget.kind === "agent" ? sendTarget.agentId : sendTarget.pmId}:main`,
            targetAgent: sendTarget.kind === "agent" ? sendTarget.agentId : sendTarget.pmId,
            mode: "text",
            sessionMode: "existing",
            targetSession: `agent:${sendTarget.kind === "agent" ? sendTarget.agentId : sendTarget.pmId}:main`,
          }}
        />
      ) : null}
    </div>
  );
}
