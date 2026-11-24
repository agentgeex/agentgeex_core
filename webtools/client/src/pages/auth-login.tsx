/**
 * Standalone auth page AuthLogin (dedicated route /login, outside AppShell)
 * ────────────────────────────────────────────────────────────────────────────
 * [2026-10-20 Feng] 'on expiry switch straight to the login page; do not show test data.'
 * So this page turned from a 'demo shell' into a **real login**:
 *   call BFF `/auth/login` → store the JWT → pull the snapshot → enter the business pages.
 * The old implementation faked login with a 1.2s setTimeout (never getting a token), so an expired jump could not
 * get through either, and it bounced between /login and the business pages.
 *
 * Supports `?next=`: on credential expiry DataGate carries the original address, and login returns you there.
 */
import * as React from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Lock, User, ArrowRight, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { useDocumentTitle } from "@/hooks/use-document-title";
import { authApi, clearSession, isTokenExpired, setSession } from "@/providers/api";
import { hydrateFromApi } from "@/providers/agentgeex";
import { setDataStatus } from "@/providers/data-status";

export default function AuthLoginPage() {
  const { t } = useTranslation();
  useDocumentTitle();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = params.get("next") || "/system";

  const [loading, setLoading] = React.useState(false);
  const [err, setErr] = React.useState("");

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (loading) return;
    const fd = new FormData(e.currentTarget);
    const username = String(fd.get("username") || "").trim();
    const password = String(fd.get("password") || "");
    if (!username || !password) {
      setErr(t("auth.needBoth"));
      return;
    }

    setLoading(true);
    setErr("");
    try {
      // Clear an already expired old credential first, avoiding a dirty token in API calls
      if (isTokenExpired()) clearSession();
      const { token, user } = await authApi.login(username, password);
      setSession(token, user);

      // Login only gets the credential; one real data pull is still needed to count as 'connected'
      const r = await hydrateFromApi();
      if (!r.ok) {
        setDataStatus({ ok: false, authRequired: r.authRequired, code: r.code, status: r.status, message: r.message, at: Date.now() });
        setErr(r.authRequired ? t("auth.invalid") : t("auth.dataFailed", { message: r.message || r.code }));
        return;
      }
      setDataStatus({ ok: true, at: Date.now() });
      navigate(next, { replace: true });
    } catch (e2) {
      setErr((e2 as Error)?.message || t("auth.failed"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative flex min-h-screen w-full items-center justify-center overflow-hidden bg-background px-4">
      {/* Fullscreen ambient light background (carried by the standalone page, not relying on Shell's background layer) */}
      <div className="pointer-events-none absolute inset-0 -z-10 overflow-hidden">
        <div className="absolute -left-40 -top-40 h-[420px] w-[420px] rounded-full bg-info/10 blur-[120px]" />
        <div className="absolute right-0 top-1/3 h-[380px] w-[380px] rounded-full bg-chart-4/10 blur-[120px]" />
        <div className="absolute bottom-0 left-1/3 h-[320px] w-[320px] rounded-full bg-chart-5/10 blur-[120px]" />
      </div>

      <Card className="w-full max-w-sm" data-testid="auth-login-page">
        <CardHeader className="items-center text-center">
          <div className="mx-auto mb-2 flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-info via-chart-5 to-chart-4 text-white shadow-[0_0_20px_-2px_hsl(var(--chart-5)/0.8)]">
            {/* [2026-11-11] 22px PNG → 28px SVG. At the old size the g's tail was only 0.8px,
                i.e. nonexistent; 28px is the upper limit that still breathes inside a 44px square. */}
            <img src="/brand/logo-mark.svg" alt="AgentGeeX" width={28} height={28} className="h-7 w-7 object-contain" />
          </div>
          <CardTitle>{t("auth.title")}</CardTitle>
          <CardDescription>{t("auth.subtitle")}</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="username">{t("auth.username")}</Label>
              <div className="relative">
                <User className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  id="username"
                  name="username"
                  placeholder={t("auth.usernamePlaceholder")}
                  className="pl-8"
                  autoComplete="username"
                  required
                  data-testid="login-username"
                />
              </div>
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="password">{t("auth.password")}</Label>
              <div className="relative">
                <Lock className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  id="password"
                  name="password"
                  type="password"
                  placeholder="••••••••"
                  className="pl-8"
                  autoComplete="current-password"
                  required
                  data-testid="login-password"
                />
              </div>
            </div>

            {err && (
              <p className="text-xs text-destructive" data-testid="login-error" role="alert">
                {err}
              </p>
            )}

            <Button type="submit" className="w-full" data-testid="login-submit" disabled={loading}>
              {loading ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" /> {t("auth.submitting")}
                </>
              ) : (
                <>
                  {t("auth.submit")} <ArrowRight className="h-4 w-4" />
                </>
              )}
            </Button>
          </form>
          <p className="mt-4 text-center text-xs text-muted-foreground">
            {t("auth.ttlHint")}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
