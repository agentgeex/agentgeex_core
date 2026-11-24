/**
 * The two atoms of live session status (2026-11-05)
 *
 * Both the session list and the system status page must show "is it running" and "how much context is used",
 * and the two must share one convention:
 * the status word, the color thresholds, and most importantly -- **null shows "—" rather than 0%**.
 *
 * ★ Why extract a component instead of writing one per page:
 *   both of these have "looks similar, actually different" traps (see the comments below),
 *   and a copy means one place gets fixed while the other does not.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import type { SessionRuntime } from "@/mock/agentgeex";

type RtStatus = SessionRuntime["status"];

/** Status -> label + color token (consistent with the status words in the execution records / plan pages)
 *  ★ [2026-11-13 09:23] Exported for the "session status" and "host distribution" of the system status page --
 *    those two each had their own `tone: ""`, so running/failed were all blue, two color languages on one page.
 *    Color semantics live only here: change them here. */
export const STATUS_META: Record<RtStatus, { labelKey: string; dot: string; text: string }> = {
  /* Only labelKey kept -- the old label field was read by nobody, and two copies of one set of status words drift eventually. */
  running: { labelKey: "sessionStatus.running", dot: "bg-success", text: "text-success" },
  idle: { labelKey: "sessionStatus.idle", dot: "bg-muted-foreground/50", text: "text-muted-foreground" },
  failed: { labelKey: "sessionStatus.failed", dot: "bg-destructive", text: "text-destructive" },
  stalled: { labelKey: "sessionStatus.stalled", dot: "bg-warning", text: "text-warning" },
  unknown: { labelKey: "sessionStatus.unknown", dot: "bg-muted-foreground/30", text: "text-muted-foreground" },
};

/**
 * Running status dot.
 *
 * The criterion is the gateway's `status === "running"` ([2026-11-14] no longer hasActiveRun, see
 * isSessionRunning), not MySQL's "has there been an execution recently" -- the latter stays true after the
 * task ends, making it look like it is always running.
 *
 * `runtime` empty = the session is not in Redis -> show "—".
 * It must not show "idle": that would mean "we checked and it is not running", which is not "we do not know".
 */
export function SessionStatusDot({
  runtime,
  className,
  testId,
}: {
  runtime?: SessionRuntime | null;
  className?: string;
  testId?: string;
}) {
  if (!runtime) {
    return (
      <span className={cn("text-xs text-muted-foreground", className)} data-testid={testId} data-status="none">
        —
      </span>
    );
  }
  const { t } = useTranslation();
  const m = STATUS_META[runtime.status] ?? STATUS_META.unknown;
  return (
    <span
      className={cn("inline-flex items-center gap-1.5 whitespace-nowrap text-xs", m.text, className)}
      data-testid={testId}
      data-status={runtime.status}
      title={
        /* [2026-11-14] tooltip uses the same criterion as the dot: only status=running says "running now".
           Previously it judged by hasActiveRun, producing the contradiction "gray dot + tooltip says running". */
        runtime.status === "running"
          ? t("sessionStatus.runningHint", { run: runtime.activeRunId || "—" })
          : t("sessionStatus.lastEvent", { event: runtime.lastEventType || "—" })
      }
    >
      <span className="relative flex h-2 w-2 shrink-0">
        {/* Pulse only when actually running: pulsing while idle makes people think it is always moving */}
        {runtime.status === "running" && (
          <span className={cn("absolute inline-flex h-full w-full animate-ping rounded-full opacity-60", m.dot)} />
        )}
        <span className={cn("relative inline-flex h-2 w-2 rounded-full", m.dot)} />
      </span>
      {t(m.labelKey)}
    </span>
  );
}

/** Context ratio -> tone (>=100% red / >=80% amber / otherwise normal) */
export function ratioTone(ratio: number | null | undefined): {
  bar: string;
  text: string;
  level: "unknown" | "ok" | "warn" | "danger";
} {
  if (ratio === null || ratio === undefined) {
    return { bar: "", text: "text-muted-foreground", level: "unknown" };
  }
  if (ratio >= 1) return { bar: "bg-destructive", text: "text-destructive", level: "danger" };
  if (ratio >= 0.8) return { bar: "bg-warning", text: "text-warning", level: "warn" };
  return { bar: "bg-info", text: "text-muted-foreground", level: "ok" };
}

export function formatRatio(ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined) return "—";
  // 0.6949 -> 69.5%; above 100% is shown as-is (context overflow is a real state, must not be clipped to 100%)
  return `${(ratio * 100).toFixed(1)}%`;
}

/**
 * Context ratio: progress bar + percentage.
 *
 * ★ Key convention: ratio null (historical sessions with total_tokens=0) -> show "—", **do not draw a 0% bar**.
 *   "0%" and "no data" must be visually distinguishable, otherwise the board shows thousands of fake
 *   "context empty" entries.
 */
export function ContextUsage({
  runtime,
  className,
  testId,
}: {
  runtime?: SessionRuntime | null;
  className?: string;
  testId?: string;
}) {
  const ratio = runtime?.contextRatio ?? null;
  const tone = ratioTone(ratio);
  if (ratio === null) {
    return (
      <span className={cn("text-xs text-muted-foreground", className)} data-testid={testId} data-ratio="unknown">
        —
      </span>
    );
  }
  return (
    <span
      className={cn("inline-flex min-w-[104px] items-center gap-2", className)}
      data-testid={testId}
      data-ratio={ratio}
      data-level={tone.level}
      title={`${runtime?.totalTokens ?? 0} / ${runtime?.contextTokens ?? 0} tokens`}
    >
      <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted" aria-hidden>
        {/* Above 100% the bar is full and the number stays truthful (overflow is a real state) */}
        <span
          className={cn("block h-full rounded-full transition-all", tone.bar)}
          style={{ width: `${Math.min(100, Math.max(2, (ratio ?? 0) * 100))}%` }}
        />
      </span>
      <span className={cn("w-[46px] shrink-0 text-right font-mono text-xs", tone.text)}>{formatRatio(ratio)}</span>
    </span>
  );
}

/** "Is this session running now" -- the single system-wide criterion (gateway live state, not MySQL's last
 *  execution time).
 *
 * ★ [2026-11-14 10:40 Feng] "Change it, not changing it may leave hidden risks."
 *   The criterion narrows to **`status` only**, dropping the old `|| hasActiveRun`.
 *   Why drop it: `hasActiveRun` is a flag the collector picks up incidentally from raw frames,
 *   not the same source as `status` -- in the 2026-11-13 23:55 "fake running" incident,
 *   the two were out of sync (old snapshot hasActiveRun=true / status already flipped to idle by a
 *   settled frame).
 *   The collector side is fixed (`3656a4b` + `fd2514d`: terminal deltas dropped, stale snapshots ignored),
 *   and `status` is now the only authoritative value. A display layer doing a second inference from
 *   `hasActiveRun` re-introduces a possibly-failing bypass criterion on top of fixed data -- keeping it
 *   is a hidden risk.
 */
export function isSessionRunning(runtime?: SessionRuntime | null): boolean {
  if (!runtime) return false;
  return runtime.status === "running";
}

/**
 * A running marker that is just one green dot (no text) -- for narrow spots like columns and inline rows.
 *
 * [2026-11-13 23:42 Feng] "Everywhere this session appears, the session status should be marked."
 * The text-carrying `SessionStatusDot` widens table columns, so a dot-only version is added too;
 * criterion/animation are identical to `RunningMark` (send dialog), just without the label.
 */
export function SessionRunDot({
  running,
  className,
  testId,
  title,
}: {
  running: boolean;
  className?: string;
  testId?: string;
  title?: string;
}) {
  /* The default label must not live in the parameter list -- defaults are evaluated at call time, before the hook has run. */
  const { t } = useTranslation();
  if (!running) return null;
  return (
    <span
      className={cn("relative inline-flex h-1.5 w-1.5 shrink-0", className)}
      title={title ?? t("sessionStatus.running")}
      data-testid={testId}
      data-running="1"
    >
      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-60" />
      <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-success" />
    </span>
  );
}
