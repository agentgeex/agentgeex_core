/** Agent management · Task execution records (/executions) -- global execution log and status + manual intervention */
import * as React from "react";
import { DataTable, StatusPill, type ColumnDef } from "@/components/agentgeex/data-table";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { useNavigate, useSearchParams } from "react-router-dom";
import { executionsApi, listAgents, listExecutions, listSessions, type ExecutionRow } from "@/providers/agentgeex";
import { isSessionRunning, SessionRunDot } from "@/components/agentgeex/session-runtime-badge";
import { api } from "@/providers/api";
import { OpenClawSessionLink } from "@/components/agentgeex/openclaw-session-link";
import { RowAction } from "@/components/agentgeex/row-actions";
import { NudgeConfirmDialog, type NudgeConfirmTarget } from "@/components/agentgeex/nudge-confirm-dialog";
import { HumanConfirmDialog, type HumanConfirmTarget } from "@/components/agentgeex/human-confirm-dialog";
import type { ConfirmAction } from "@/providers/api";

/** Details of the 409 (provided by BFF realdata._confirm_context) -- spells out how this row was actually handled */
type ConfirmDetails = {
  currentStatus?: string;
  needsConfirm?: number;
  handledHow?: "approved" | "rejected" | "no_confirm_needed";
  handledAt?: string;
  finishedAt?: string;
};
import { parseSessionId } from "@/lib/openclaw";
import { ArrowRight, BellRing, CheckCircle2, Clock, Stamp } from "lucide-react";
import { hostOptions } from "@/lib/host-filter";
import { agentFilterOptions, agentInitialFilter } from "@/lib/agent-filter";
import { getProjectName, sortByQuickProject, projectNameWithPin } from "@/providers/agentgeex";
import { useDetailPopup } from "@/components/agentgeex/detail-popup";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";
import { apiErrorText } from "@/lib/api-error";

/**
 * Project filter options: list only the projects **that actually appear in the current data** (sorted by name).
 * Why not use the whole project table: most of the 40+ projects have not a single execution,
 * a dropdown option that always yields 0 rows is noise, and users must try them one by one to know which work.
 */
function projectOptions(rows: ExecutionRow[], t: (k: string, v?: Record<string, unknown>) => string) {
  const ids = Array.from(new Set(rows.map((r) => r.projectId).filter(Boolean) as string[]));
  /* [2026-11-17 15:07] Pinned projects first (★ marker), the rest by name. */
  return sortByQuickProject(
    ids
    /* Unregistered projects (e.g. the project was deleted after the execution was stored) must not show a bare id --
       Feng said long ago "a project number means nothing". But we must not invent a name either,
       so explicitly write "unregistered project (proj_xxx)": neither pretending to know nor making people
       guess from gibberish. */
      .map((pid) => ({ label: projectNameWithPin(pid, getProjectName(pid) || t("executions.unregisteredProject", { id: pid })), value: pid }))
      .sort((a, b) => a.label.localeCompare(b.label, "zh-Hans-CN")),
    (x) => x.value,
  );
}

function fmtDur(s: number) {
  if (!s) return "—";
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m${s % 60 ? `${s % 60}s` : ""}`;
}

/**
 * "Sender / receiver" names in execution records -- **click it to open that agent's detail dialog** (no navigation).
 *
 * [2026-11-06 15:11 Feng] "Make a link, put the link right on it, so I click the sender or the receiver and
 *  it just pops open that agent's detail page in a dialog, then I can see what is going on directly."
 *  Previously there was only a name; seeing "agx_host" still meant searching the agents page to know who it was.
 *
 * The three points below are all lessons already learned:
 *   ① Must stopPropagation -- the whole row also has an onClick (open execution detail), otherwise two layers open.
 *   ② Use the **display name** rather than the bare id (the sender used to show `send_agent_id` directly, so
 *      one side was Chinese and the other English).
 *      Fall back to the id only when the name cannot be found, and mark it mono so it is obvious
 *      "this row has no registered name".
 *   ③ Append the id tail on duplicates -- host_manager on multiple hosts is all named `agx_host`, writing
 *      only the name makes them all look identical.
 */
function ExecAgentLink({ agentId, label, mono }: { agentId?: string; label?: string; mono?: boolean }) {
  const { t } = useTranslation();
  const popup = useDetailPopup();
  const text = label || agentId || "";
  if (!agentId) return <span className="text-muted-foreground">—</span>;
  return (
    <button
      type="button"
      /* Title has three lines: name / full id / this is clickable -- so you can confirm who you clicked even when the name wraps */
      title={t("executions.agentLinkTitle", { name: text, id: agentId })}
      onClick={(e) => {
        e.stopPropagation();
        popup?.open({ kind: "agent", id: agentId });
      }}
      data-testid={`exec-agent-link-${agentId}`}
      className={cn(
        /* [2026-11-06] It used to be 13ch + truncate. Column width is allocated by the table and is much wider than
           the content, so truncating left a big blank to the right of this column (Feng: "there is a lot of
           blank after it, that is not great").
           Now: cap raised to 22ch, **wrap instead of ellipsis when longer** (Feng: "wrapping at that point is
           fine too").
           `shrink-0` is required: flex children shrink by default, and once the name shrinks it "breaks words
           inside the box" (measured: `XHOSTTestWorker-2(test_srv_a2)` split into `Worker-` / `2(test_srv_a2)` on two
           lines, looking like a typo). With shrink-0 it stays one block -> when space runs out, the whole
           "-> name" moves to the next line.
           Also do **not** use overflow-wrap:anywhere: it shrinks min-content to one character wide, so flex
           prefers "squeeze the name" over wrapping, exactly the opposite of what we want. */
        "max-w-[24ch] shrink-0 break-words text-left leading-tight text-primary hover:underline",
        mono && "font-mono text-xs",
      )}
    >
      {text}
    </button>
  );
}

/** 'YYYY-MM-DD HH:MM' -> 'HH:MM' (inline shows only hour/minute, full value goes in title) */
function hhmm(s?: string) {
  if (!s) return "";
  const t = s.replace("T", " ");
  return t.length >= 16 ? t.slice(11, 16) : t;
}

/**
 * Scheduled time: same day shows only "HH:MM", other days add "MM-DD HH:MM".
 * Why hhmm alone is not enough: scheduled tasks often land in the small hours of tomorrow (measured a batch
 * from 01:00-08:00), so showing only "01:00" makes people think it was that moment today, already past.
 */
function schedLabel(s?: string) {
  if (!s) return "";
  const t = s.replace("T", " ");
  if (t.length < 16) return t;
  const today = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  const ymd = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;
  return t.slice(0, 10) === ymd ? t.slice(11, 16) : `${t.slice(5, 10)} ${t.slice(11, 16)}`;
}

/* ── Manual intervention: error code -> what the human should do ──────────────────────────────
   [2026-10-31 Feng] Rate limiting and status mismatch are two completely different things:
     rate limited = just urged a moment ago, wait;
     status mismatch = the task already ended, no need to urge at all.
   Merging them into "operation failed" makes people check whether the feature is broken. */
function nudgeErrorHint(code: string, t: (k: string) => string): string {
  switch (code) {
    case "RATE_LIMITED":
      return t("executions.nudge.rateLimited");
    case "STATE_MISMATCH":
      return t("executions.nudge.stateMismatch");
    case "NOT_FOUND":
      return t("executions.nudge.notFound");
    case "KERNEL_NOT_READY":
      return t("executions.nudge.kernelNotReady");
    case "PERMISSION_DENIED":
      return t("executions.nudge.permissionDenied");
    default:
      return "";
  }
}

/**
 * [2026-11-06 00:03 Feng "do the rest the same way"] Split the page into two layers:
 * `ExecutionsBoard` = the body (shared by the route page and the dialog), `ExecutionsPage` = thin shell.
 * The dialog comes in with initial conditions: { status: "succeeded" } (board 7 cells), { __host: "test_host_mac" } (host card).
 */
export function ExecutionsBoard({
  initialFilters,
  embedded = false,
}: {
  initialFilters?: Record<string, string>;
  embedded?: boolean;
}) {
  const { t } = useTranslation();
  const popup = useDetailPopup();
  const data = listExecutions();
  /* [2026-10-29 Feng] Jumped in from the "executions" metric on an agent card: ?agent=xxx -> only that agent's executions */
  const [sp] = useSearchParams();
  const initialFilterVals = {
    ...(agentInitialFilter(sp.get("agent")) ?? {}),
    ...(initialFilters ?? {}),
  };
  const navigate = useNavigate();
  const open = (id: string) => {
    /* Clicking a row inside the dialog does not navigate (navigating destroys the outer page) -> stack an execution detail dialog */
    if (embedded && popup?.open) {
      popup.open({ kind: "execution", id });
      return;
    }
    navigate(`/executions/${encodeURIComponent(id)}`);
  };
  const { toast } = useToast();

  /* [2026-10-31] After urging, **only this row is replaced**, the page is not remounted.
     Going through refreshPageData() would clear the status filter / search term / page the user just set --
     one urge and you are bounced back to page 1, easily mistaken for a misclick. */
  const [rows, setRows] = React.useState<ExecutionRow[]>(data);
  React.useEffect(() => setRows(data), [data]);

  /* agentId -> display name (shared by sender/receiver). Append the id tail on duplicates:
     host_manager on multiple hosts is stored as `agx_host` everywhere, writing only the name makes them all
     look identical.
     (Same convention as the duplicate handling in components/agentgeex/agent-member-chips) */
  const agentLabels = React.useMemo(() => {
    const list = listAgents();
    const dup = new Map<string, number>();
    for (const a of list) dup.set(a.name, (dup.get(a.name) ?? 0) + 1);
    const m = new Map<string, string>();
    for (const a of list) {
      const tail = a.id.split("_").filter(Boolean).slice(-1)[0] || a.id;
      m.set(a.id, (dup.get(a.name) ?? 0) > 1 ? `${a.name}·${tail}` : a.name);
    }
    return m;
  }, [data]);

  const [target, setTarget] = React.useState<NudgeConfirmTarget | null>(null);
  const [busy, setBusy] = React.useState(false);

  const askNudge = (r: ExecutionRow, kind: "urge" | "ack_remind") => {
    // Direction must be fixed right here: urge -> the executor; acknowledge reminder -> the initiator
    const toAgent =
      kind === "urge" ? r.agentId || r.agentName : r.sendAgentId || r.agentId || r.agentName;
    setTarget({
      kind,
      runUuid: r.run_uuid,
      taskName: r.taskName || r.execName,
      toAgent: toAgent || t("executions.unknownAgent"),
      nudgeCount: r.nudgeCount ?? 0,
    });
  };

  const submitNudge = async () => {
    if (!target) return;
    setBusy(true);
    try {
      const r = await executionsApi.nudge(target.runUuid, {
        kind: target.kind,
        note: target.kind === "urge" ? t("executions.nudge.noteUrge") : t("executions.nudge.noteAck"),
      });
      // Server decides: swap this row back to its latest state (buttons disappear when they should)
      const fresh = await api.get<ExecutionRow>(`/executions/${encodeURIComponent(target.runUuid)}`);
      setRows((list) => list.map((x) => (x.run_uuid === fresh.run_uuid ? fresh : x)));
      toast({
        title: target.kind === "urge" ? t("executions.nudge.sentUrge") : t("executions.nudge.sentAck"),
        description: t("executions.nudge.sentDesc", {
          agent: r.toAgent || target.toAgent,
          via: r.deliveredTo ? t("executions.nudge.sentVia", { target: r.deliveredTo }) : "",
          n: fresh.nudgeCount ?? 1,
        }),
        variant: "success",
      });
      setTarget(null);
    } catch (e) {
      const err = e as { status?: number; code?: string; message?: string };
      const hint = nudgeErrorHint(err.code || "", t);
      toast({
        title: err.code === "RATE_LIMITED" ? t("executions.nudge.rateTitle") : t("executions.nudge.failTitle"),
        description: `${apiErrorText(e, t)}${hint ? `　${hint}` : ""}`,
        variant: "destructive",
        // The failure message must be readable before it disappears (rate limit / status mismatch must be read to know the next step)
        duration: 9000,
      });
      setTarget(null);
    } finally {
      setBusy(false);
    }
  };

  /* ── Human approval / rejection (tasks with needs_confirm=1 land here after they finish) ──
     ★ The frontend trusts state only: buttons are shown only when status === "awaiting_confirm".
       No separate "can it be confirmed" API field -- that states the same thing twice, and once the two
       drift you get a dead end where "status says awaiting confirm but there is no button". */
  const [humanTarget, setHumanTarget] = React.useState<HumanConfirmTarget | null>(null);
  const [humanBusy, setHumanBusy] = React.useState(false);

  const askHumanConfirm = (r: ExecutionRow) => {
    setHumanTarget({
      runUuid: r.run_uuid,
      taskName: r.taskName || r.execName,
      agentName: agentLabels.get(r.agentId) || r.agentName || r.agentId,
      summary: r.summary,
    });
  };

  const submitHumanConfirm = async (action: ConfirmAction) => {
    if (!humanTarget) return;
    setHumanBusy(true);
    try {
      const r = await executionsApi.confirm(humanTarget.runUuid, action);
      // Server decides: swap this row back to its latest state (the button then disappears)
      const fresh = await api.get<ExecutionRow>(`/executions/${encodeURIComponent(humanTarget.runUuid)}`);
      setRows((list) => list.map((x) => (x.run_uuid === fresh.run_uuid ? fresh : x)));
      toast({
        title: action === "approve" ? t("entityDetail.execution.toastApprove") : t("entityDetail.execution.toastReject"),
        description:
          action === "approve"
            ? t("entityDetail.execution.toastApproveDesc", { status: r.status || "succeeded" })
            : t("entityDetail.execution.toastRejectDesc", { status: r.status || "failed" }),
        variant: action === "approve" ? "success" : "destructive",
        /* 3 seconds default is too short: the dialog has just closed and the eye is still lower in the list, the message is gone and people think it failed */
        duration: 6500,
      });
      setHumanTarget(null);
    } catch (e) {
      const err = e as { status?: number; code?: string; message?: string; details?: ConfirmDetails };
      /* ★ 2026-11-06 21:5x Feng measured: clicking approve reports "is 'succeeded', not 'awaiting_confirm'",
         the hint says "someone may have approved/rejected first", and a refresh shows **it was his own
         approval that succeeded**.
         Two problems fixed together here:

         ① The 409 has three causes, each meaning something completely different to a human
            (see realdata._confirm_context):
              already approved -> it is done, ignore
              already rejected -> it failed, ignore
              never needed confirmation -> your expectation was wrong, the feature is not broken
            Saying "maybe someone approved it first" for all of them = making people chase a
            nonexistent problem.
         ② Receiving a 409 is itself the signal "your local copy of this row is stale".
            Reporting the error without fixing local data -> the "approve" button stays in place -> the
            user clicks again and gets the error again.
            So we also refresh this row to the true value here (drop it entirely on NOT_FOUND). */
      const d = err.details as ConfirmDetails | undefined;
      const title =
        err.code === "STATE_MISMATCH"
          ? d?.handledHow === "approved"
            ? t("entityDetail.execution.reason.alreadyApproved")
            : d?.handledHow === "rejected"
              ? t("entityDetail.execution.reason.alreadyRejected")
              : d?.handledHow === "no_confirm_needed"
                ? t("entityDetail.execution.reason.notNeeded")
                : t("entityDetail.execution.reason.notPending")
          : err.code === "NOT_FOUND"
            ? t("entityDetail.execution.reason.noRun")
            : err.code === "PERMISSION_DENIED"
              ? t("entityDetail.execution.reason.forbidden")
              : t("entityDetail.execution.toastFailed");
      const hint =
        err.code === "STATE_MISMATCH" && d?.handledHow === "approved"
          ? t("entityDetail.execution.done.succeeded", {
              at: d.handledAt ? t("entityDetail.execution.done.atApprove", { time: d.handledAt }) : "",
            })
          : err.code === "STATE_MISMATCH" && d?.handledHow === "rejected"
            ? t("entityDetail.execution.done.failed", {
                at: d.handledAt ? t("entityDetail.execution.done.atReject", { time: d.handledAt }) : "",
              })
            : err.code === "STATE_MISMATCH" && d?.handledHow === "no_confirm_needed"
              ? t("entityDetail.execution.done.notNeeded")
              : err.code === "STATE_MISMATCH"
                ? t("executions.actions.refreshed")
                : err.code === "PERMISSION_DENIED"
                  ? t("executions.actions.selfApproveForbidden")
                  : "";
      toast({
        title,
        description: `${hint || err.message || String(e)}`,
        variant: "destructive",
        duration: 9000,
      });
      /* Self-heal: replace this row with the server's truth, the button disappears when it should */
      const stale = humanTarget.runUuid;
      setHumanTarget(null);
      try {
        if (err.code === "NOT_FOUND") {
          setRows((list) => list.filter((x) => x.run_uuid !== stale));
        } else {
          const fresh = await api.get<ExecutionRow>(`/executions/${encodeURIComponent(stale)}`);
          setRows((list) => list.map((x) => (x.run_uuid === fresh.run_uuid ? fresh : x)));
        }
      } catch {
        /* If even the refresh fails, let it go -- the main error already told the user, do not stack a second piece of noise */
      }
    } finally {
      setHumanBusy(false);
    }
  };

  /* Column definitions live inside the component: the action column needs callbacks (open confirm dialog).
     [2026-10-31] Duration merged into the "start time" column -- the intervention button needs more width,
     and in a 1440 viewport the white box is only ~1146px, without freeing space the whole table overflows. */
  /* Session status lookup: the execution row only carries sessionId, runtime state must come from the session store */
  const sessionsById = React.useMemo(
    () => new Map(listSessions().map((x) => [x.id, x])),
    [],
  );

  const columns: ColumnDef<ExecutionRow>[] = React.useMemo(
    () => [
      /* [2026-10-29 15:49 Feng] First column "execution" (execName) removed --
         it is a whole natural-language sentence (e.g. "please log all of today's project logs..."),
         and after width-capping it pushes right and squeezes later columns, looking at a glance like content
         leaked into other cells.
         This field is of little use in the table (to know what it did, click into the detail);
         the card view title is now taken by the "task" column, a short identifier, better as a title than a sentence. */
      {
        key: "run_uuid", header: "run_uuid", cardSubtitle: true,
        className: "w-[120px] font-mono text-xs",
        render: (r) => (
          <span className="block max-w-[120px] truncate font-mono text-xs" title={r.run_uuid}>{r.run_uuid}</span>
        ),
      },
      /* [2026-10-30 07:06 after Feng's "table font +1"] this column grew to 361px -> the whole table overflowed the
         white box by 69px.
         This column was whitespace-nowrap and untruncated, so a long task name stretched the table as wide as
         it wanted; now width-capped + truncated (full value in title), so the larger font no longer pushes
         other columns off screen. */
      {
        key: "taskName", header: t("executions.columns.task"), cardTitle: true, sortable: true, className: "max-w-[200px]",
        render: (r) => (
          <span className="block max-w-[200px] truncate" title={r.taskName}>{r.taskName}</span>
        ),
      },
      /* [2026-10-29 Feng] Task executions must show "who dispatched / who received".
         It used to be a single "agent" column = the receiver, you could not tell who dispatched the work to it.
         Now split into "sender -> receiver":
           · send == recv (2488/5792 rows in the DB are self-dispatched) -> shown once + gray "self"
           · different -> A ->(arrow) B
         Splitting into two columns would blow the table width (only ~1150px inside the white box at 1440),
         so one column + arrow.

         [2026-11-06 15:11 Feng, both spots changed]
         ① "You truncated it, that truncation can be relaxed a bit, because this column is now rather wide,
             and after truncation there is a lot of blank after the entire column"
            -> was `whitespace-nowrap` + 13ch truncation. Column width is distributed proportionally by the
               table (wider than content), so truncated content leaves the rest blank. Now removing nowrap,
               cap 22ch, **wrap instead of ellipsis when too long**, using the allocated width fully.
         ② "For these two, sender and receiver, you can make it a bit bigger... wrapping is fine too"
            -> give each cell a min-w, whole column min-w-[210px], prefer a taller row over hard squeezing.
         ③ "Make a link... click a sender or click a receiver, just pop open that agent's detail"
            -> names become buttons, opening the agent detail dialog (ExecAgentLink). */
      {
        key: "agentName", header: t("executions.columns.agentPair"), sortable: true,
        className: "align-top min-w-[210px]",
        render: (r) => {
          const recvId = r.agentId || "";
          const sendId = r.sendAgentId || "";
          const recvLabel = agentLabels.get(recvId) || r.agentName || recvId;
          const sendLabel = sendId ? agentLabels.get(sendId) || "" : "";
          /* Old data has no send_agent_id (early executions did not record the sender) -> show only the receiver, do not invent a "sender" */
          if (!sendId) return <ExecAgentLink agentId={recvId} label={recvLabel} mono={!agentLabels.has(recvId)} />;
          if (sendId === recvId) {
            return (
              <span className="inline-flex flex-wrap items-baseline gap-1">
                <ExecAgentLink agentId={recvId} label={recvLabel} mono={!agentLabels.has(recvId)} />
                <span className="text-[10px] text-muted-foreground/70" title={t("executions.selfSent")}>{t("executions.selfSent")}</span>
              </span>
            );
          }
          return (
            /* On wrap the **arrow must follow the receiver**, it cannot stay at the end of the previous line --
               measured (2026-11-06): when both names are long, the arrow dangles alone at the end of line 1
               while the object it points at starts line 2, reading like a stray symbol at the line end.
               So arrow + receiver form one nowrap unit: if it wraps, the whole thing moves to line 2, and
               line 2 begins with "-> receiver", making the pointing relation clearer. */
            <span className="inline-flex flex-wrap items-center gap-x-1 gap-y-0.5">
              <ExecAgentLink
                agentId={sendId}
                label={sendLabel}
                mono={!agentLabels.has(sendId)}
              />
              {/* No whitespace-nowrap: it would be inherited by the button, and then the receiver name could never wrap
                  (measured: `XHOSTTestWorker-2(test_srv_a2)` overflows 2px horizontally instead of wrapping).
                  A flex container is enough -- a flex item under the default flex-nowrap is not split across
                  lines, while the name can still wrap inside the button's own box. */}
              <span className="inline-flex items-center gap-1">
                <ArrowRight className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
                <ExecAgentLink agentId={recvId} label={recvLabel} mono={!agentLabels.has(recvId)} />
              </span>
            </span>
          );
        },
      },
      /* [2026-10-28 Feng] Which session a task ran in must be one click away to see the scene.
         Show the session short name (the slug of `agent:xxx:slug`), click the icon to jump straight to that
         OpenClaw session. */
      {
        key: "sessionId",
        header: t("executions.columns.session"),
        className: "whitespace-nowrap",
        render: (r) => {
          const slug = parseSessionId(r.sessionId)?.slug || r.sessionId || "—";
          /* [2026-11-13 23:42 Feng] Wherever the session appears, the status is marked there --
             this column shows "which session it ran in", so if the session is running (still has an active
             run) mark a green dot. */
          const sess = sessionsById.get(r.sessionId);
          return (
            <span className="inline-flex items-center gap-1.5">
              <SessionRunDot
                running={isSessionRunning(sess?.runtime)}
                testId={`exec-run-${r.run_uuid}`}
              />
              <span className="max-w-[12ch] truncate font-mono text-xs text-muted-foreground" title={r.sessionId}>
                {slug}
              </span>
              <OpenClawSessionLink sessionId={r.sessionId} agentId={r.agentId} />
            </span>
          );
        },
      },
      /* For running rows, attach "how long it has run / how long expected" to the status (title) --
         that is exactly what you look at to judge "is it stuck", without opening the detail first. */
      {
        key: "status", header: t("executions.columns.status"), sortable: true, className: "whitespace-nowrap w-[96px]",
        render: (r) => (
          <span className="flex flex-col items-start gap-1" title={r.nudgeReason || undefined}>
            <span data-testid={`exec-status-${r.run_uuid}`}>
              <StatusPill status={r.status} />
            </span>
            {/* [2026-11-03 Feng] "Let me see what scheduled tasks there are" --
                no need to filter first to know: scanning the status column already shows this one was scheduled. */}
            {r.scheduledAt ? (
              <span
                className="inline-flex items-center gap-0.5 rounded border border-strong px-1 text-[10px] text-muted-foreground"
                data-testid={`exec-scheduled-${r.run_uuid}`}
                title={t("executions.scheduled.title", { at: r.scheduledAt })}
              >
                <Clock className="h-2.5 w-2.5" aria-hidden />
                {t("executions.scheduled.chip")}
              </span>
            ) : null}
          </span>
        ),
      },
      /* [2026-10-31] Duration merged into "start time": the intervention button needs width,
         and the two columns answer the same question (when it started, how long it took). */
      {
        key: "startedAt", header: t("executions.columns.started"), sortable: true, className: "whitespace-nowrap w-[124px]",
        render: (r) => {
          /* Not started yet but scheduled: this cell should answer "when will it start",
             not a "—" that looks the same as "data unavailable".

             ★ 2026-11-03 fixing a lie: previously it only checked `!startedAt && scheduledAt`, ignoring status.
               But started_at is written lazily by the kernel (only filled when the agent queries its own
               execution), measured today: of 6 inspection runs, 5 had an empty started_at while run_status
               was already succeeded and summary complete -- so the UI labelled finished tasks
               "scheduled · not started".
               "—" means "no such data" (honest); "not started" means "it has not run" (false statement).
               The latter is worse. The only authority on status is run_status, startedAt is descriptive only. */
          if (r.status === "pending" && r.scheduledAt) {
            return (
              <span className="flex flex-col leading-tight" title={t("executions.scheduled.title", { at: r.scheduledAt })}>
                <span>{schedLabel(r.scheduledAt)}</span>
                <span className="text-[11px] text-muted-foreground">{t("executions.scheduled.notStarted")}</span>
              </span>
            );
          }
          return (
            <span className="flex flex-col leading-tight">
              <span>{r.startedAt || "—"}</span>
              <span className="text-[11px] text-muted-foreground">{fmtDur(r.durationSeconds)}</span>
            </span>
          );
        },
      },
    ],
    [],
  );

  /* Action column: available actions are computed by the server (availableActions), the frontend only renders.
     With no action show "—" -- clearly meaning "nothing should be urged now", rather than leaving it blank for
     people to guess the load failed. */
  const rowActions = (r: ExecutionRow) => {
    const acts = r.availableActions ?? [];
    /* Awaiting human confirmation: put "approve / reject" first -- the only thing this column should do right now.
       It is not an "urge", so it does not go through availableActions (that is the watchdog intervention
       semantics, computed by the kernel from state, a different matter from "should a human approve"). */
    const needsHuman = r.status === "awaiting_confirm";
    return (
      <div className="flex flex-col items-end gap-0.5">
        <div className="flex items-center justify-end gap-1">
          {needsHuman ? (
            <Button
              variant="default"
              size="sm"
              className="h-7 gap-1 px-2 text-xs"
              title={t("executions.actions.confirmTitle")}
              data-testid={`confirm-open-${r.run_uuid}`}
              onClick={() => askHumanConfirm(r)}
            >
              <CheckCircle2 className="h-3.5 w-3.5" />
              {t("executions.actions.approve")}
            </Button>
          ) : null}
          {acts.length === 0 ? (
            /* [2026-11-01] This column now has "edit" in addition to urge, so "no action" no longer draws a visible
               "—": a dash makes people think the entire column is empty, while an edit button is actually
               next to it.
               But accessibility/test anchors must remain: make it sr-only, screen readers say "no action to
               urge", and it takes no visual space. */
            <span className="sr-only" data-testid={`nudge-none-${r.run_uuid}`}>
              {t("executions.actions.none")}
            </span>
          ) : (
            acts.map((a) => (
              <Button
                key={a.kind}
                variant="outline"
                size="sm"
                className="h-7 gap-1 px-2 text-xs"
                title={r.nudgeReason || a.label}
                data-testid={`nudge-${a.kind}-${r.run_uuid}`}
                onClick={() => askNudge(r, a.kind)}
              >
                {a.kind === "urge" ? <BellRing className="h-3.5 w-3.5" /> : <Stamp className="h-3.5 w-3.5" />}
                {a.label}
              </Button>
            ))
          )}
          {/* [2026-11-01 Feng] "You can add an edit for execution records" -- detail is via clicking the row, edit gets its own entry */}
          <RowAction
            kind="edit"
            to={`/executions/${encodeURIComponent(r.run_uuid)}/edit`}
            testId={`execution-edit-${r.run_uuid}`}
          />
        </div>
        {/* How many times a human has urged it -- urged N times with no progress means it is time to change the
            approach (terminate/redispatch is phase two); pressing the same button again is just repeated nagging. */}
        {(r.nudgeCount ?? 0) > 0 ? (
          <span
            className="text-[11px] whitespace-nowrap text-muted-foreground"
            data-testid={`nudge-count-${r.run_uuid}`}
            title={r.nudgeLastAt ? t("executions.actions.lastNudge", { at: r.nudgeLastAt }) : undefined}
          >
            {t("executions.actions.nudged", { n: r.nudgeCount })}
            {r.nudgeLastAt ? ` · ${hhmm(r.nudgeLastAt)}` : ""}
          </span>
        ) : null}
      </div>
    );
  };

  return (
    <div className="flex flex-col gap-2" data-testid={embedded ? "executions-board-embedded" : "page-executions"}>
      {/* [2026-10-28 16:32 Feng] Follow the host page format: drop the header title/description (navigation already has them), wrap the content in a white box. */}
      <div
        className={cn(
          "flex flex-col gap-4 bg-card",
          embedded ? "p-3" : "rounded-xl border border-strong p-4 shadow-sm",
        )}
        data-testid={embedded ? undefined : "content-frame"}
      >
        <DataTable
          data={rows}
          columns={columns}
          rowKey={(r) => r.run_uuid}
          /* [2026-11-02 Feng] "The refresh button does not bind the conditions of the current window, click refresh
             and all data comes in... having to re-set this query condition after a refresh is really unfriendly."
             The refresh button itself is fine (re-pulls the snapshot), the problem is it takes effect via
             "remount the whole page" -- which clears the search term/filters/page number too.
             Here we hand those to lib/page-state to remember. */
          stateKey={embedded ? undefined : "executions"}
          searchKeys={["run_uuid", "taskName", "agentName"]}
          searchPlaceholder={t("executions.filters.searchPlaceholder")}
          initialFilterVals={initialFilterVals}
          onRowClick={(r) => open(r.run_uuid)}
          rowActions={rowActions}
          filters={[
            /* [2026-11-03 21:43 Feng] "The status query condition should correspond to the 7 statuses in system status".
               It used to have only 4 (success/running/failed/awaiting confirm), picked from "the common ones",
               so the four kinds cancelled / archived / draft / pending **could not be filtered at all** --
               visible in the list, yet impossible to pick out.
               Now the same vocabulary, the same source of truth, the same order (by lifecycle) as the board
               "task overview".
               ★ Wording must match the board character for character: here it says "success" while the board
                 says "succeeded", users would think they are not the same thing. */
            {
              key: "status", label: t("executions.filters.status"),
              options: [
                { label: t("status.pending"), value: "pending" },
                { label: t("status.running"), value: "running" },
                /* [2026-11-06 Feng] New status "awaiting human confirmation": the work is done, waiting for approval.
                   Positioned by lifecycle between "running" and "succeeded". */
                /* ★ Only awaiting_confirm is offered, the old name pending_confirm is not listed separately --
                   not a single row in the DB has that value, a dropdown option that is always 0 rows is just
                   noise; should one ever appear, the badge shows the same word (StatusPill already maps it)
                   and search/board still find it. */
                { label: t("status.awaiting_confirm"), value: "awaiting_confirm" },
                { label: t("status.succeeded"), value: "succeeded" },
                { label: t("status.failed"), value: "failed" },
                { label: t("status.cancelled"), value: "cancelled" },
                { label: t("status.archived"), value: "archived" },
                { label: t("status.draft"), value: "draft" },
              ],
            },
            {
              key: "__host", label: t("common.host"),
              /* [2026-11-06 Feng chose option A] Filter by `agx_task_execution.host_id` --
                 i.e. "which machine this work actually ran on". Previously hostOfAgent(agentId)
                 (which machine the agent belongs to) -> host card 5731 vs list 4980 did not match;
                 the gap was exactly "work that agents which moved hosts ran on the old machine historically".
                 ★ No fallback to agent ownership: falling back for the 54 old rows with empty host_id would turn
                   card 5733 into list 5757 (+24), numbers mismatch again. The card is pure SQL
                   GROUP BY host_id: no host means none, keep that consistent here.
                   Those 54 rows remain visible when no host filter is applied (they just belong to no host
                   when filtering by host). */
              getValue: (r) => r.hostId || "",
              options: hostOptions(),
            },
            /* [2026-11-03 Feng] "There is a kind of scheduled task... add a filter".
               The criterion is scheduled_at (only agx_schedule_task writes it), not task_type --
               task_type has only normal/plan, plan means "this is a step inside a plan", unrelated to scheduling.
               Keeping an "immediate" option is for the reverse view "which ones are not scheduled". */
            {
              key: "__trigger", label: t("executions.filters.trigger"),
              getValue: (r) => (r.scheduledAt ? "scheduled" : "immediate"),
              options: [
                { label: t("executions.filters.triggerScheduled"), value: "scheduled" },
                { label: t("executions.filters.triggerImmediate"), value: "immediate" },
              ],
            },
            /* [2026-11-03 21:38 Feng] "Add project to the task execution record search conditions".
               Options come from projectIds **actually present in the current data**, not the whole project table --
               there are 40+ projects there, most with zero executions, so the dropdown is full of dead options
               that yield 0 rows when selected.
               Value uses projectId (stable), display uses the project name (human readable),
               unregistered projects fall back to showing the id, not swallowed (swallowing it would mean
               "this record does not exist").
               ★ Ownership comes from the project_id stored on the execution itself, not resolved via the agent --
                 agents change projects; historical executions must not drift with them. */
            {
              key: "__project",
              label: t("executions.filters.project"),
              getValue: (r) => r.projectId || "",
              options: projectOptions(data, t),
            },
            /* [2026-11-06 11:10 Feng] "Task execution ranking, this agent's task inside it must be clickable
               through" -- the ranking card's numbers are **last 7 days** (SQL: created_at >= NOW()-7d),
               while this list is all-time. Without a time condition, clicking through gives far more rows than
               the card (5733 vs 177), which is exactly the "two numbers do not match" Feng kept pointing out.

               Three tiers are **mutually exclusive** (not "last 30 days cumulative"), labels state it plainly,
               because filtering is equality matching: a row can belong to only one tier.
               "All" = no time limit (default when none of the three is selected). */
            {
              key: "__range", label: t("executions.filters.range"),
              getValue: (r) => {
                /* Use createdAt (creation time), not startedAt -- queued/draft rows have no startedAt, filtering by start
                   time would miss them, while the board SQL counts creation time. */
                const t = Date.parse(r.createdAt || r.startedAt || "");
                if (!t) return "unknown";
                const days = (Date.now() - t) / 86400000;
                if (days < 7) return "7d";
                if (days < 30) return "30d";
                return "older";
              },
              options: [
                { label: t("executions.filters.range7d"), value: "7d" },
                { label: t("executions.filters.range30d"), value: "30d" },
                { label: t("executions.filters.rangeOlder"), value: "older" },
                { label: t("executions.filters.rangeUnknown"), value: "unknown" },
              ],
            },
            /* "Agent" filter: already in place when jumping in from a card (?agent=), and can be switched manually */
            { key: "agentId", label: t("executions.filters.agent"), options: agentFilterOptions(data) },
          ]}
        />
      </div>

      <HumanConfirmDialog
        target={humanTarget}
        busy={humanBusy}
        onOpenChange={(o) => {
          if (!o) setHumanTarget(null);
        }}
        onConfirm={submitHumanConfirm}
      />

      <NudgeConfirmDialog
        target={target}
        busy={busy}
        onOpenChange={(o) => {
          if (!o) setTarget(null);
        }}
        onConfirm={submitNudge}
      />
    </div>
  );
}

/** Route page thin shell (the dialog passes conditions via props, not URL) */
export default function ExecutionsPage() {
  return <ExecutionsBoard />;
}
