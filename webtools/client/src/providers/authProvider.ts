/**
 * providers/authProvider.ts — Refine AuthProvider (talks to the Win3 BFF /auth/*)
 *
 * [2026-10-01] Mirrors contract §3.1: login / logout / me.
 * This round is a read-only demo: the token is stored in localStorage after login and the DataProvider attaches the JWT automatically.
 */
import type { AuthProvider } from "@refinedev/core";
import i18n from "@/i18n";
import { authApi, clearSession, getToken, getUser, isTokenExpired, setSession } from "@/providers/api";

export const authProvider: AuthProvider = {
  login: async ({ username, password }) => {
    try {
      const { token, user } = await authApi.login(String(username), String(password));
      setSession(token, user);
      return { success: true, redirectTo: "/system" };
    } catch (e) {
      return {
        success: false,
        error: {
          name: i18n.t("miscShell.auth.loginFailed"),
          message: (e as Error)?.message || i18n.t("miscShell.auth.badCredentials"),
        },
      };
    }
  },

  logout: async () => {
    try {
      await authApi.logout();
    } catch {
      /* BFF logout is idempotent; ignore network errors */
    }
    clearSession();
    return { success: true, redirectTo: "/login" };
  },

  check: async () => {
    if (!getToken()) return { authenticated: false, redirectTo: "/login" };
    try {
      await authApi.me();
      return { authenticated: true };
    } catch {
      clearSession();
      return { authenticated: false, redirectTo: "/login" };
    }
  },

  getIdentity: async () => {
    const u = getUser<{ id: string; name: string; role: string }>();
    if (!u) return null;
    return { id: u.id, name: u.name, avatar: "" };
  },

  getPermissions: async () => getUser<{ role: string }>()?.role ?? null,

  onError: async (error) => {
    const status = (error as { status?: number })?.status;
    if (status === 401) return { logout: true, redirectTo: "/login" };
    return { error: error as Error };
  },
};

/**
 * Demo convenience: in the read-only wiring round, sign in automatically with the default human account when not signed in (so page browsing is not interrupted).
 * [2026-10-15 Feng correction: a human is not an agent] An account is a human (operator), no longer bound to any agent.
 * A human may act as any agent; [sender agent identity] is chosen per action (dialog / list page selector),
 * never bound to the login account.
 * (The old value treated the login account as a super-role agent = backwards, now corrected. Login account and agent identity are two separate lines.)
 */
export const DEFAULT_IDENTITY = "operator";

export async function ensureDemoSession(force = false): Promise<void> {
  // [2026-10-20 fix] The old `if (getToken()) return;` only checked whether a token existed, not whether it had expired.
  // JWT TTL is 12h → overnight the token is still there but the server returns 401 → startup skips login →
  // /snapshot returns 401 → silent fallback to seed mock (the "only 4 projects left" Feng reported).
  // Now: force (retry after 401) or a missing/expired token → sign in again; otherwise reuse.
  if (!force && getToken() && !isTokenExpired()) return;
  try {
    const { token, user } = await authApi.login(DEFAULT_IDENTITY, DEFAULT_IDENTITY);
    setSession(token, user);
  } catch {
    /* Ignore: stay signed out when the BFF is unreachable, pages fall back to mock */
  }
}
