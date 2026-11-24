import ReactDOM from "react-dom/client";
import "./index.css";
import "./i18n";
import { App } from "./App";
import { hydrateFromApi } from "@/providers/agentgeex";
import { API_ORIGIN } from "@/providers/api";
import { setDataStatus } from "@/providers/data-status";
import { migrateStoredKeys } from "@/lib/storage-keys";

/**
 * SPA bootstrap (2026-10-01 "read-only wiring to the real backend"):
 *   1. probe the BFF (/readyz) → if reachable, sign in the demo account for a JWT;
 *   2. pull the full business snapshot into the runtime store (providers/agentgeex.ts);
 *   3. render the app.
 * BFF unreachable → fall back to seed mock silently, the UI still renders (does not block startup).
 *
 * [2026-10-20 Feng decided] No more "silent fallback to sample data":
 *   ① no automatic demo sign-in anymore (an expired credential should show the login page);
 *   ② the connectivity result goes into DataStatus, and DataGate decides to let through / redirect to login / show an error page.
 */
/* Legacy storage keys (win3 era / agx_web era) → agentgeex.x: migrated on startup, so a page never opened still gets migrated */
migrateStoredKeys();

async function bootstrap() {
  try {
    const res = await fetch(`${API_ORIGIN}/readyz`).catch(() => null);
    if (res?.ok) {
      // Pull the snapshot only when credentials already exist; without them, let DataGate route to the login page
      const r = await hydrateFromApi();
      setDataStatus(
        r.ok
          ? { ok: true, at: Date.now() }
          : { ok: false, authRequired: r.authRequired, code: r.code, status: r.status, message: r.message, at: Date.now() }
      );
      console.info(`[agx-web] data source = ${r.ok ? "API" : "-"}`);
    } else {
      setDataStatus({
        ok: false,
        code: "BFF_UNREACHABLE",
        status: 0,
        message: `${API_ORIGIN}/readyz 不可达`,
        at: Date.now(),
      });
      console.info("[agx-web] BFF unreachable");
    }
  } catch (e) {
    setDataStatus({ ok: false, code: "BOOTSTRAP_FAILED", message: (e as Error)?.message, at: Date.now() });
    console.warn("[agx-web] bootstrap failed:", e);
  }

  ReactDOM.createRoot(document.getElementById("root")!).render(<App />);
}

void bootstrap();
