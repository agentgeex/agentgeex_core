/**
 * components/agentgeex/mcp-check-dialog.tsx — MCP service self-check **panel**
 *
 * ★ 2026-11-07 refactor: from "standalone dialog" to "panel", mounted into a tab of `service-settings-dialog.tsx`.
 *   Feng: "In settings there is only one thing, the MCP check… can you make a window where I can both check MCP and set
 *   the voice host, since there may be other services in the future, because you cannot keep hanging them in the menu."
 *   → Menu items collapsed into one "service settings", each service its own tab. Adding a service = adding a tab, no menu change.
 *   The old name `McpCheckDialog` keeps a thin wrapper (external references remain); everything inside goes to `McpCheckPanel`.
 *
 * [2026-10-29 Feng] The MCP core updates often, but **an MCP process already running is still the old code**
 * (without a restart it keeps returning the old version). Feng wanted to see "which version am I connected to"
 * right in the UI, so an item "Test" was added to the login-name dropdown; clicking it opens this window.
 *
 * Data source: `GET /v1/mcp/check-services` → BFF passes through the core `agx_check_services`.
 * Key point: `mcp_version` is a **constant in the core code**, so
 *   old-code process → returns the old version string; new-code process → returns the new version string.
 * The version string itself is the evidence of "old or new process"; no need to compare file mtimes.
 *
 * Three disciplines:
 *   ① No caching — the only value of this window is "who that process is right now";
 *   ② No silent failure — if the channel is down, lay out the error reason as-is, leave no blank pane;
 *   ③ Break it apart — version time / version note / code identity / retrieval moment each on its own row,
 *      "old process" and "old data" must not be conflated.
 */
import { useTranslation } from "react-i18next";
import * as React from "react";
import {
  Activity, RefreshCw, Database, Boxes, ShieldCheck, Clock,
  CheckCircle2, AlertTriangle, XCircle, FileCode2, RotateCw,
} from "lucide-react";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { mcpApi, type McpCheckResult } from "@/providers/api";

/* ------------------------------ Helpers ------------------------------ */

/** `"2026-10-22 09:47:00 | REFACTOR(...)..."` → { at, note } */
function splitVersion(v?: string): { at: string; note: string } {
  const s = (v ?? "").trim();
  if (!s) return { at: "—", note: "" };
  const i = s.indexOf("|");
  if (i < 0) return { at: s, note: "" };
  return { at: s.slice(0, i).trim(), note: s.slice(i + 1).trim() };
}

function statusTone(status: string | undefined, t: (k: string, o?: Record<string, unknown>) => string) {
  const ok = (status ?? "").toLowerCase() === "healthy";
  return ok
    ? { cls: "border-success/40 bg-success/10 text-success", Icon: CheckCircle2, label: t("mcpCheck.status.ok") }
    : {
        cls: "border-destructive/40 bg-destructive/10 text-destructive",
        Icon: XCircle,
        label: status || t("mcpCheck.status.abnormalFallback"),
      };
}

const CACHE_LABELS: Array<[string, string]> = [
  ["project_count", "mcpCheck.cache.project"],
  ["agent_count", "mcpCheck.cache.agent"],
  ["task_count", "mcpCheck.cache.task"],
  ["execution_count", "mcpCheck.cache.execution"],
  ["session_count", "mcpCheck.cache.session"],
  ["plan_count", "mcpCheck.cache.plan"],
  ["host_count", "mcpCheck.cache.host"],
  ["notice_count", "mcpCheck.cache.notice"],
  ["agent_group_count", "mcpCheck.cache.group"],
  ["skill_count", "mcpCheck.cache.skill"],
];

function Row({ icon: Icon, label, children, title }: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  children: React.ReactNode;
  title?: string;
}) {
  return (
    <div className="flex items-start gap-2.5 py-2" title={title}>
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
      <div className="w-[92px] shrink-0 text-xs text-muted-foreground">{label}</div>
      <div className="min-w-0 flex-1 text-sm">{children}</div>
    </div>
  );
}

/** Small badge next to the build fingerprint: clean / dirty / unknown */
function DirtyBadge({ dirty, source }: { dirty?: boolean | null; source?: string }) {
  const { t } = useTranslation();
  const nogit = source === "nogit";
  const text = nogit
    ? t("mcpCheck.dirty.nogit")
    : dirty === true
      ? t("mcpCheck.dirty.dirty")
      : dirty === false
        ? t("mcpCheck.dirty.clean")
        : t("mcpCheck.dirty.unknown");
  const tone = nogit
    ? "border-strong text-muted-foreground"
    : dirty === true
      ? "border-warning/50 bg-warning/10 text-warning-foreground"
      : dirty === false
        ? "border-success/45 bg-success/10 text-success"
        : "border-strong text-muted-foreground";
  return (
    <span
      className={cn("rounded border px-1.5 py-0.5 text-[10px]", tone)}
      data-testid="mcp-build-dirty"
      data-dirty={String(dirty)}
    >
      {text}
    </span>
  );
}

/** Code freshness banner — answers "is the process older than the disk" */
function FreshnessBanner({ f }: { f: NonNullable<McpCheckResult["code_freshness"]> }) {
  const { t } = useTranslation();
  const stale = f.stale === true;
  const unknown = f.stale === null;
  const cls = unknown
    ? "border-strong bg-muted/40 text-muted-foreground"
    : stale
      ? "border-destructive/45 bg-destructive/10"
      : "border-success/45 bg-success/10";
  const Icon = unknown ? AlertTriangle : stale ? XCircle : CheckCircle2;
  const iconCls = unknown ? "text-muted-foreground" : stale ? "text-destructive" : "text-success";

  return (
    <div
      className={cn("rounded-lg border p-3", cls)}
      data-testid="mcp-check-freshness"
      data-stale={String(f.stale)}
    >
      <div className="flex items-center gap-2">
        <Icon className={cn("h-4 w-4 shrink-0", iconCls)} />
        <div className={cn("text-sm font-medium", iconCls)}>
          {unknown
            ? t("mcpCheck.freshness.unknown")
            : stale
              ? t("mcpCheck.freshness.stale")
              : t("mcpCheck.freshness.fresh")}
        </div>
      </div>
      <div className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground" data-testid="mcp-check-verdict">
        {f.verdict}
      </div>
      <div className="mt-2 flex flex-col gap-1 text-[11px]">
        <div className="flex items-center gap-1.5">
          <FileCode2 className="h-3 w-3 shrink-0 text-muted-foreground" />
          <span className="w-[86px] shrink-0 text-muted-foreground">
            {t("mcpCheck.freshness.codeChanged")}
          </span>
          <span className="font-mono" data-testid="mcp-kernel-mtime">{f.kernel_mtime || "—"}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <RotateCw className="h-3 w-3 shrink-0 text-muted-foreground" />
          <span className="w-[86px] shrink-0 text-muted-foreground">
            {t("mcpCheck.freshness.procStarted")}
          </span>
          <span className="font-mono" data-testid="mcp-proc-start">{f.sse_started_at || "—"}</span>
          {f.sse_pid !== null && (
            <span className="font-mono text-muted-foreground">pid {f.sse_pid}</span>
          )}
        </div>
      </div>
      {stale && (
        <div className="mt-2 rounded border border-destructive/30 bg-card/60 px-2 py-1 text-[11px] text-destructive">
          {/* 2026-11-04: the MCP process is now managed by PM2 (agx-mcp-gateway); start.sh only probes health, never starts/stops.
              Here it used to say "./start.sh restart" — following it does nothing, and makes people think it restarted. */}
          {t("mcpCheck.freshness.restartHint")}{" "}
          <code className="rounded bg-muted px-1 font-mono">pm2 restart agx-mcp-gateway</code>
          {" "}
          {t("mcpCheck.freshness.restartHintTail")}
        </div>
      )}
    </div>
  );
}

/* ------------------------------ Main component ------------------------------ */

export function McpCheckPanel({ active = true }: { active?: boolean }) {
  const { t } = useTranslation();
  const [data, setData] = React.useState<McpCheckResult | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [err, setErr] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await mcpApi.checkServices();
      setData(r);
    } catch (e) {
      /* No degradation, no pretending old values are new: clear + explicit error */
      setData(null);
      const anyErr = e as { code?: string; message?: string; status?: number };
      setErr(
        [anyErr.code, anyErr.message].filter(Boolean).join(" · ") ||
          String(e),
      );
    } finally {
      setLoading(false);
    }
  }, []);

  /* Re-fetch every time this tab is activated — "I looked a moment ago" does not mean "the process is still that one".
     ★ No "fetch once" cache: the only value of this panel is "who that process is right now". */
  React.useEffect(() => {
    if (active) void load();
  }, [active, load]);

  const my = data?.services?.mysql_check_info;
  const rd = data?.services?.redis_check_info;
  const ver = splitVersion(rd?.mcp_version);
  const raw = data?.services?.raw;
  const fresh = data?.code_freshness;
  const dirty = rd?.build_dirty;
  const myTone = statusTone(my?.status, t);
  const rdTone = statusTone(rd?.status, t);

  return (
    <div className="flex flex-col gap-3" data-testid="mcp-check-dialog">
        {/* Loading */}
        {loading && !data && (
          <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground" data-testid="mcp-check-loading">
            <RefreshCw className="h-4 w-4 animate-spin" />
            {t("mcpCheck.loading")}
          </div>
        )}

        {/* Failure: lay the reason out as-is, do not pretend nothing happened */}
        {err && (
          <div
            className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive"
            data-testid="mcp-check-error"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <div className="min-w-0">
              <div className="font-medium">{t("mcpCheck.error.title")}</div>
              <div className="mt-1 break-words text-xs opacity-90">{err}</div>
              <div className="mt-1.5 text-xs opacity-80">
                {t("mcpCheck.error.desc")}
              </div>
            </div>
          </div>
        )}

        {/* Success */}
        {data && !err && (
          <div className="flex flex-col gap-3" data-testid="mcp-check-body">
            {/* ① Build fingerprint (the star) — the automatic fingerprint added to the core on 2026-10-29 22:06,
                 this is the authoritative answer to "which build is running in production". */}
            <div className="rounded-lg border border-strong bg-muted/40 p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="text-xs text-muted-foreground">{t("mcpCheck.build.title")}</div>
                <DirtyBadge dirty={rd?.build_dirty} source={rd?.build_source} />
              </div>
              <div
                className="mt-1.5 break-all font-mono text-xl font-semibold tracking-tight"
                data-testid="mcp-build-sha"
              >
                {rd?.build_sha || "unknown"}
              </div>
              <div className="mt-1 break-words text-xs leading-relaxed text-muted-foreground">
                {rd?.build_detail || "—"}
              </div>
              {rd?.build_at && (
                <div className="mt-1.5 text-[11px] text-muted-foreground">
                  {t("mcpCheck.build.codeTime")} <span className="font-mono">{rd.build_at}</span>
                </div>
              )}
              {dirty === true && (
                <div className="mt-2 rounded border border-warning/40 bg-warning/10 px-2 py-1 text-[11px] text-warning-foreground">
                  {t("mcpCheck.build.dirtyNote")}
                </div>
              )}
            </div>

            {/* ①b The truly reliable freshness criterion: code change time vs process start time */}
            {fresh && <FreshnessBanner f={fresh} />}

            {/* ①c Release notes (hand-written constant, degraded to one line of history) */}
            <div className="rounded-lg border border-border px-3 py-2">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <span className="text-[11px] text-muted-foreground">{t("mcpCheck.release.title")}</span>
                <span className="font-mono text-xs text-muted-foreground" data-testid="mcp-release-version">
                  {ver.at}
                </span>
                {rd?.mcp_sha && (
                  <span className="rounded border border-strong px-1 font-mono text-[10px] text-muted-foreground">
                    {rd.mcp_sha}
                  </span>
                )}
              </div>
              {ver.note && (
                <div className="mt-0.5 break-words text-[11px] leading-relaxed text-muted-foreground">
                  {ver.note}
                </div>
              )}
              <div className="mt-1 text-[11px] text-muted-foreground/80">
                {t("mcpCheck.release.note")}
              </div>
            </div>

            {/* ② Retrieval info (separating "process version" from "the moment of this query") */}
            <div className="divide-y divide-border rounded-lg border border-strong px-3">
              <Row icon={Clock} label={t("mcpCheck.row.checkedAt")}>
                <span className="font-mono text-xs">{data.checked_at}</span>
                <span className="ml-1.5 text-xs text-muted-foreground">{t("mcpCheck.row.checkedAtNote")}</span>
              </Row>
              <Row icon={Boxes} label={t("mcpCheck.row.channel")} title={data.mcp_sse_url}>
                <span className="break-all font-mono text-xs">{data.mcp_sse_url}</span>
                {data.mcp_transport && (
                  /* 2026-11-04: after switching to the shared gateway, transport became streamable-http.
                     If the self-check does not state it, the first reaction to a problem will be investigating the wrong protocol. */
                  <span className="ml-1.5 shrink-0 rounded bg-muted px-1 py-0.5 font-mono text-[10px] text-muted-foreground">
                    {data.mcp_transport}
                  </span>
                )}
              </Row>
              <Row icon={ShieldCheck} label={t("mcpCheck.row.checkedBy")}>
                <span className="font-mono text-xs">{data.checked_by}</span>
              </Row>
            </div>

            {/* ③ Core connectivity */}
            <div className="flex flex-col gap-2">
              <div className="text-xs font-medium text-muted-foreground">{t("mcpCheck.connectivity")}</div>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <div className={cn("rounded-lg border p-2.5", myTone.cls)}>
                  <div className="flex items-center gap-1.5 text-xs font-medium">
                    <Database className="h-3.5 w-3.5" />
                    MySQL
                    <myTone.Icon className="ml-auto h-3.5 w-3.5" />
                  </div>
                  <div className="mt-1 font-mono text-sm">{my?.server_version || "—"}</div>
                  <div className="mt-0.5 truncate text-[11px] opacity-80" title={String(my?.endpoint ?? "")}>
                    {String(my?.database ?? "")} @ {String(my?.endpoint ?? "")}
                  </div>
                </div>
                <div className={cn("rounded-lg border p-2.5", rdTone.cls)}>
                  <div className="flex items-center gap-1.5 text-xs font-medium">
                    <Boxes className="h-3.5 w-3.5" />
                    Redis
                    <rdTone.Icon className="ml-auto h-3.5 w-3.5" />
                  </div>
                  <div className="mt-1 font-mono text-sm">{rd?.redis_version || "—"}</div>
                  <div className="mt-0.5 truncate text-[11px] opacity-80" title={String(rd?.endpoint ?? "")}>
                    db{String(rd?.db_index ?? 0)} @ {String(rd?.endpoint ?? "")}
                  </div>
                </div>
              </div>
            </div>

            {/* ④ Permission configuration */}
            <div className="rounded-lg border border-strong px-3 py-1.5">
              <Row icon={ShieldCheck} label={t("mcpCheck.permission.title")} title={String(rd?.permissions_sha ?? "")}>
                <div className="break-words font-mono text-xs">{rd?.permissions_sha || "—"}</div>
                <div className="mt-0.5 text-[11px] text-muted-foreground">
                  {t("mcpCheck.permission.updatedAt", { at: rd?.permissions_updated_at || "—" })}
                </div>
              </Row>
            </div>

            {/* ⑤ Cache stats */}
            {rd?.cache_stats && Object.keys(rd.cache_stats).length > 0 && (
              <div>
                <div className="mb-1.5 text-xs font-medium text-muted-foreground">{t("mcpCheck.cacheTitle")}</div>
                <div className="grid grid-cols-3 gap-1.5 sm:grid-cols-5">
                  {CACHE_LABELS.filter(([k]) => k in (rd.cache_stats ?? {})).map(([k, labelKey]) => (
                    <div key={k} className="rounded border border-strong bg-muted/30 px-1.5 py-1 text-center">
                      <div className="font-mono text-sm">{rd.cache_stats?.[k] ?? 0}</div>
                      <div className="text-[10px] text-muted-foreground">{t(labelKey)}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* ⑥ State clearly what this item covers — boundaries matter more than conclusions */}
            <div
              className="rounded-lg border border-info/30 bg-info/5 p-2.5 text-[11px] leading-relaxed text-muted-foreground"
              data-testid="mcp-check-scope"
            >
              <span className="font-medium text-foreground">
                {t("mcpCheck.scope.line1")}
                {fresh?.port ? t("mcpCheck.scope.port", { port: fresh.port }) : ""}.
              </span>
              <br />
              {/* 2026-11-04 correction: it used to say "the gateway/agent side has its own MCP process, the version may differ".
                   That was true under the "BFF self-hosted SSE + each agent its own stdio" architecture —
                   now the BFF and the agents (OpenClaw gateway) connect to the **same** gateway process,
                   keeping it would make people think the two sides may run different versions, creating doubt. */}
              {t("mcpCheck.scope.line2")}
              <br />
              {t("mcpCheck.scope.line3")}
            </div>

            {/* Show non-JSON content from the core as-is */}
            {raw !== undefined && raw !== null && (
              <pre className="max-h-32 min-w-0 overflow-auto rounded-lg border border-strong bg-muted/40 p-2 font-mono text-[11px]">
                {typeof raw === "string" ? raw : JSON.stringify(raw, null, 2)}
              </pre>
            )}
          </div>
        )}

      {/* Put the refresh button on the **panel root**, not inside the success block:
           when the version cannot be fetched (err), what people need most is "try again", so the button must not
           disappear together with the success block. */}
      <div className="flex justify-end">
        <Button
          variant="outline"
          size="sm"
          onClick={() => void load()}
          disabled={loading}
          data-testid="mcp-check-refresh"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
          {t("mcpCheck.refresh")}
        </Button>
      </div>
    </div>
  );
}

/**
 * Old entry (thin wrapper). **New code should use `McpCheckPanel`**, put it in the settings tab.
 * It is kept so as not to break any place still doing `import { McpCheckDialog }` —
 * deleting it would only buy one "module not found", with no benefit.
 */
export function McpCheckDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] max-w-[560px] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-info" />
            {t("mcpCheck.dialogTitle")}
          </DialogTitle>
        </DialogHeader>
        <McpCheckPanel active={open} />
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("mcpCheck.close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
