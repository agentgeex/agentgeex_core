/**
 * components/agentgeex/service-settings-dialog.tsx — service settings (unified window)
 *
 * [2026-11-07 Feng]
 *   "There is only one thing in settings, checking MCP... see whether you can make a window where you can
 *     both check this MCP and set this voice host, and maybe other services later, because you cannot keep
 *     hanging them in the menu."
 *
 * So what is solved here is not "add one more setting item" but the structural question **where setting items
 * go**:
 *   the menu always has exactly one "service settings"; each service is a tab.
 *   Adding transcription/OCR/vector store later = one more tab + one API prefix, **menu untouched**.
 *
 * The MCP self-check content is moved over as-is (`McpCheckPanel`), not copied -- one copy of code used in two
 * places forks eventually.
 *
 * This file is responsible for only two parts, "container + voice host":
 *   container: tab skeleton, remembers the last viewed tab on open (people open it several times to see the
 *     same item)
 *   voice: host card list + radio switching + single-host check
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import {
  Activity, AudioLines, CheckCircle2, XCircle, HelpCircle, RefreshCw,
  Server, Info, Loader2,
} from "lucide-react";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { McpCheckPanel } from "@/components/agentgeex/mcp-check-dialog";
import { settingsApi, type AsrHost, type AsrHostsResult } from "@/providers/api";

/* ------------------------------ Status dot ------------------------------ */

function StateDot({ h, busy, onProbe }: { h: AsrHost; busy: boolean; onProbe: () => void }) {
  const { t } = useTranslation();
  const tone =
    h.state === "up"
      ? { Icon: CheckCircle2, cls: "text-success", label: t("serviceSettings.available") }
      : h.state === "down"
        ? { Icon: XCircle, cls: "text-destructive", label: t("serviceSettings.unreachable") }
        : { Icon: HelpCircle, cls: "text-muted-foreground", label: t("serviceSettings.untested") };

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation(); // Do not click "select" along the way -- checking is checking, selecting is selecting
        onProbe();
      }}
      disabled={busy}
      title={h.error || t("serviceSettings.probeHint")}
      data-testid={`asr-host-state-${h.id}`}
      data-state={h.state}
      className="inline-flex shrink-0 items-center gap-1 rounded border border-strong px-1.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-accent disabled:opacity-60"
    >
      {busy ? (
        <Loader2 className={cn("h-3 w-3 animate-spin", tone.cls)} />
      ) : (
        <tone.Icon className={cn("h-3 w-3", tone.cls)} />
      )}
      <span className={tone.cls}>{busy ? t("serviceSettings.probing") : tone.label}</span>
      <RefreshCw className="h-2.5 w-2.5 opacity-60" />
    </button>
  );
}

/* Pure function -> pass t in (hooks are unavailable at module level). */
function agoText(ms: number | null | undefined, t: TFn): string {
  if (ms === null || ms === undefined) return "";
  if (ms < 1500) return t("serviceSettings.justNow");
  if (ms < 60_000) return t("serviceSettings.secondsAgo", { n: Math.round(ms / 1000) });
  if (ms < 3_600_000) return t("serviceSettings.minutesAgo", { n: Math.round(ms / 60_000) });
  return t("serviceSettings.hoursAgo", { n: Math.round(ms / 3_600_000) });
}

type TFn = (k: string, o?: Record<string, unknown>) => string;

/* ------------------------------ Voice host ------------------------------ */

function AsrHostsPanel({ active }: { active: boolean }) {
  const { t } = useTranslation();
  const [data, setData] = React.useState<AsrHostsResult | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [err, setErr] = React.useState<string | null>(null);
  /** host id currently switching/probing (each turns independently, avoiding "click A, spinner on B") */
  const [busy, setBusy] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      setData(await settingsApi.asrHosts());
    } catch (e) {
      /* Do not keep the old value pretending it is the new one -- what a settings page fears most is "data that looks right" */
      setData(null);
      const anyErr = e as { code?: string; message?: string };
      setErr([anyErr.code, anyErr.message].filter(Boolean).join(" · ") || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    if (active) void load();
  }, [active, load]);

  const doProbe = async (id: string) => {
    setBusy(id);
    try {
      setData(await settingsApi.probeAsrHost(id));
      setErr(null);
    } catch (e) {
      const anyErr = e as { message?: string };
      setErr(anyErr.message || String(e));
    } finally {
      setBusy(null);
    }
  };

  const doSelect = async (id: string) => {
    if (data?.active === id) return;
    setBusy(id);
    try {
      /* The server probes once as a side effect of switching -- otherwise this host still shows "unknown", and
          the user just made a choice yet nothing changed, so they think the click did not register. */
      setData(await settingsApi.setAsrActive(id));
      setErr(null);
    } catch (e) {
      const anyErr = e as { message?: string };
      setErr(anyErr.message || String(e));
    } finally {
      setBusy(null);
    }
  };

  const hosts = data?.hosts ?? [];

  return (
    <div className="flex flex-col gap-3" data-testid="asr-settings">
      {/* Make clear "this selects a preference, not a deployment" -- the most easily misunderstood spot on this page */}
      <div className="rounded-lg border border-info/30 bg-info/5 p-2.5 text-[11px] leading-relaxed text-muted-foreground">
        <span className="font-medium text-foreground">{t("serviceSettings.asrPickTitle")}</span>
        <br />
        {/* One sentence embeds two <code> + one <strong>, so it can only be assembled from fragments (English fragments carry their own spaces) */}
        {t("serviceSettings.asrSourceA")} <code className="rounded bg-muted px-1 font-mono">.env</code>
        {t("serviceSettings.asrSourceB")}
        <code className="rounded bg-muted px-1 font-mono">BFF_ASR_HOSTS</code>
        {t("serviceSettings.asrSourceC")}
        <strong className="font-medium text-foreground">{t("serviceSettings.asrSourceStrong")}</strong>
        {t("serviceSettings.asrSourceD")}
      </div>

      {loading && !data && (
        <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
          <RefreshCw className="h-4 w-4 animate-spin" />
          {t("serviceSettings.loadingHosts")}
        </div>
      )}

      {err && (
        <div className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <div className="min-w-0 break-words text-xs">{err}</div>
        </div>
      )}

      {data && (
        <>
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span data-testid="asr-host-count">
              {t("serviceSettings.hostCount", { count: hosts.length })}
            </span>
            <span>
              {t("serviceSettings.current")}
              <span className="font-medium text-foreground">{hosts.find((h) => h.active)?.name}</span>
            </span>
          </div>

          <div className="flex flex-col gap-2" role="radiogroup" data-testid="asr-host-list">
            {hosts.map((h) => {
              const isBusy = busy === h.id;
              return (
                <div
                  key={h.id}
                  role="radio"
                  aria-checked={!!h.active}
                  tabIndex={0}
                  onClick={() => void doSelect(h.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      void doSelect(h.id);
                    }
                  }}
                  data-testid={`asr-host-${h.id}`}
                  data-active={String(!!h.active)}
                  /* The selected card is emphasized the same way (two kinds of "selected" in one window must not differ in
                      weight): solid outline + tinted background + bold outer ring + left color bar. The color bar
                      is for "a glance" -- people scan lists vertically, and one continuous line on the left is
                      recognized faster than a thin ring. */
                  className={cn(
                    "relative cursor-pointer rounded-lg border p-2.5 transition-colors",
                    h.active
                      ? "border-primary bg-primary/10 pl-3.5 ring-2 ring-primary/25 shadow-sm"
                      : "border-strong hover:bg-accent/60",
                    isBusy && "opacity-70",
                  )}
                >
                  {h.active && (
                    <span
                      aria-hidden="true"
                      className="absolute inset-y-1.5 left-1 w-[3px] rounded-full bg-primary"
                      data-testid={`asr-host-bar-${h.id}`}
                    />
                  )}
                  <div className="flex items-start gap-2">
                    {/* Radio dot: the semantic is "use only one", so radio, not checkbox */}
                    <span
                      className={cn(
                        "mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border",
                        h.active ? "border-primary bg-primary/10" : "border-input",
                      )}
                    >
                      {h.active && <span className="h-1.5 w-1.5 rounded-full bg-primary" />}
                    </span>

                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="text-sm font-medium" data-testid={`asr-host-name-${h.id}`}>{h.name}</span>
                        <span className="rounded border border-strong px-1 font-mono text-[10px] text-muted-foreground">
                          {h.id}
                        </span>
                        {h.active && (
                          <span className="rounded bg-primary/15 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                            {t("serviceSettings.inUse")}
                          </span>
                        )}
                        <span className="ml-auto">
                          <StateDot h={h} busy={isBusy} onProbe={() => void doProbe(h.id)} />
                        </span>
                      </div>

                      <div className="mt-0.5 break-all font-mono text-[11px] text-muted-foreground">{h.url}</div>

                      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground">
                        {h.note && <span>{h.note}</span>}
                        {h.checked_ago_ms !== null && h.checked_ago_ms !== undefined && (
                          <span>· {t("serviceSettings.checkedAt", { when: agoText(h.checked_ago_ms, t) })}</span>
                        )}
                        {h.state === "down" && h.error && (
                          <span className="break-all text-destructive/90">· {h.error}</span>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="flex items-start gap-2 rounded-lg border border-strong bg-muted/30 p-2.5 text-[11px] leading-relaxed text-muted-foreground">
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <div>
              {t("serviceSettings.autoProbe")}
              <br />
              {t("serviceSettings.failLoud")}
            </div>
          </div>

          <div className="flex justify-end">
            <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading} data-testid="asr-settings-refresh">
              <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
              {t("serviceSettings.reload")}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

/* ------------------------------ Main component ------------------------------ */

/** Remember the last viewed tab: people open it several times to see the same item, returning to the first
 *  tab every time is a nuisance.
 *  Key and old-key migration live together in lib/storage-keys.ts. */

import { readKey, writeKey } from "@/lib/storage-keys";
export function ServiceSettingsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const { t } = useTranslation();
  const [tab, setTab] = React.useState<string>(() => readKey("settingsTab") || "asr");

  const changeTab = (v: string) => {
    setTab(v);
    /* In private mode writeKey swallows exceptions internally -- failing to remember the tab is acceptable, failing to open the settings window is not */
    writeKey("settingsTab", v);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* Content is long (the MCP section has version + criterion + connectivity + cache) -> max height + internal
           scroll, so even a small laptop (1366×768) does not push the bottom buttons off screen. */}
      <DialogContent className="max-h-[88vh] max-w-[600px] overflow-y-auto" data-testid="service-settings-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Server className="h-4 w-4 text-info" />
            {t("serviceSettings.title")}
          </DialogTitle>
        </DialogHeader>

        <Tabs value={tab} onValueChange={changeTab}>
          {/* [2026-11-08 Feng] "Give the selected tab a noticeable color, right now selected and unselected look
              about the same"
              -- the old style was the shadcn default: selected = white bg + shadow, unselected = gray bg.
              One step between gray and white, almost invisible in a dark dialog, you could only guess by
              "which one has a shadow".

              Changed to **solid primary**: selected = blue bg white text, unselected = transparent + gray text.
              ★ Only this window changes, ui/tabs.tsx untouched -- that is shared site-wide, and elsewhere the
                "lighter" look may be exactly what is wanted; a local need must not change the global default.
              The color convention is still "blue = clickable / currently selected", not a new color scheme. */}
          <TabsList className="grid w-full grid-cols-2 gap-1 bg-muted/60 p-1">
            <TabsTrigger
              value="asr"
              data-testid="svc-tab-asr"
              className="gap-1.5 text-muted-foreground data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-sm data-[state=active]:hover:bg-primary"
            >
              <AudioLines className="h-3.5 w-3.5" />
              {t("serviceSettings.tabAsr")}
            </TabsTrigger>
            <TabsTrigger
              value="mcp"
              data-testid="svc-tab-mcp"
              className="gap-1.5 text-muted-foreground data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-sm data-[state=active]:hover:bg-primary"
            >
              <Activity className="h-3.5 w-3.5" />
              {t("serviceSettings.tabMcp")}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="asr" className="mt-3">
            {/* No forceMount: only the current tab should fire requests, saving one MCP round trip along the way */}
            <AsrHostsPanel active={open && tab === "asr"} />
          </TabsContent>

          <TabsContent value="mcp" className="mt-3">
            <McpCheckPanel active={open && tab === "mcp"} />
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
