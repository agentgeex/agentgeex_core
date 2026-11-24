/**
 * components/agentgeex/data-gate.tsx — data gate (route guard)
 *
 * [2026-10-20 Feng] "When expired, go straight to the login page, do not show test data, it is useless."
 *
 * So business pages (all routes inside AppShell) always pass this gate first:
 *   ok                    → let through (real data)
 *   authRequired (401)    → go to /login, and render **no** business page (leak not even half a table)
 *   backend unavailable   → full-screen error page + retry button (likewise render no business page, never fake with
 *                           seed mock)
 *
 * Old behaviour (the thing to root out): hydrate fails → console.warn → keep rendering seed mock,
 * so users saw 4 fake projects and thought they were real.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useLocation } from "react-router-dom";
import { AlertTriangle, LogOut, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getToken, isTokenExpired, clearSession } from "@/providers/api";
import { hydrateFromApi } from "@/providers/agentgeex";
import { getDataStatus, setDataStatus, subscribeDataStatus, type DataStatus } from "@/providers/data-status";

type TFn = (k: string, o?: Record<string, unknown>) => string;

function describe(s: DataStatus, t: TFn): string {
  if (s.code === "BFF_UNREACHABLE" || s.status === 0) return t("dataGate.unreachable");
  if (s.status === 403) return t("dataGate.forbidden");
  if (s.status && s.status >= 500) return t("dataGate.serverError", { status: s.status });
  if (s.code === "KERNEL_ERROR") return t("dataGate.kernelError");
  /* Show the backend's own message first (that is the raw fact), then code, then fallback text */
  return s.message || s.code || t("dataGate.unknown");
}

export function DataGate({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation();
  const location = useLocation();
  const [status, setStatus] = React.useState<DataStatus>(() => getDataStatus());
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => subscribeDataStatus(setStatus), []);

  const retry = React.useCallback(async () => {
    setBusy(true);
    try {
      const r = await hydrateFromApi();
      setDataStatus(
        r.ok
          ? { ok: true, at: Date.now() }
          : { ok: false, authRequired: r.authRequired, code: r.code, status: r.status, message: r.message, at: Date.now() }
      );
    } finally {
      setBusy(false);
    }
  }, []);

  // ── Credentials invalid: go to the login page (carrying the current address, so login returns here) ──────────
  if (!status.ok && status.authRequired) {
    // Also clear the locally expired token so it does not keep triggering 401
    if (isTokenExpired()) clearSession();
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }

  if (status.ok) return <>{children}</>;

  // ── Backend unavailable: full-screen error (render no business page, show no sample data) ────────
  return (
    <div
      className="flex min-h-screen w-full items-center justify-center bg-background px-4"
      data-testid="data-gate-error"
    >
      <Card className="w-full max-w-md">
        <CardHeader className="items-center text-center">
          <div className="mx-auto mb-2 flex h-11 w-11 items-center justify-center rounded-xl bg-warning/15 text-warning">
            <AlertTriangle className="h-5 w-5" />
          </div>
          <CardTitle>{t("dataGate.title")}</CardTitle>
          <CardDescription>
            {t("dataGate.body")}
            <br />
            <span className="text-xs">{t("dataGate.reasonPrefix")}{describe(status, t)}</span>
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          <Button onClick={() => void retry()} disabled={busy} data-testid="data-gate-retry">
            <RefreshCw className={busy ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            {busy ? t("dataGate.retrying") : t("dataGate.retry")}
          </Button>
          <Button
            variant="outline"
            data-testid="data-gate-relogin"
            onClick={() => {
              clearSession();
              setDataStatus({ ok: false, authRequired: true, at: Date.now() });
            }}
          >
            <LogOut className="h-4 w-4" /> {t("dataGate.relogin")}
          </Button>
          {!getToken() && (
            <p className="pt-1 text-center text-xs text-muted-foreground">
              {t("dataGate.notLoggedIn")}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
