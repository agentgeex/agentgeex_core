/**
 * lib/fresh-data.ts — "refresh the frontend store to the latest"
 * ────────────────────────────────────────────────────────────────
 * [2026-11-06 22:08 Feng] reported problem:
 *   the number on the dashboard cell (/v1/dashboard/stats, live aggregation, page polls every 5s) says
 *   "1 awaiting human confirmation"; open the dialog and the list has 0 — because the list comes from **the
 *   snapshot hydrated when the page opened**, which never changes afterwards.
 *   He added a very precise remark: "Shouldn't the data be refreshed when you open the window?"
 *
 * So the dialog must refresh at two moments: **on open** (automatic) and **after sitting idle too long** (manual button).
 * This file is the single implementation shared by both paths.
 *
 * ★ Why not `refreshPageData()`:
 *   it calls `notifyPageRefreshed()` → the App's `RefreshableRoutes` key changes → **the whole page remounts**,
 *   and dialog state lives inside the page → **the dialog closes itself**. A "refresh" that refreshes away its
 *   own window is worse than not refreshing.
 *   Here `hydrateFromApi()` is used: swap the store only, no global notification; the caller remounts its own content.
 *
 * ★ Cost: `/v1/snapshot` measured at **48.8MB / 0.55~0.72s** (this host). Hence:
 *   - concurrency de-duplication: simultaneous callers share one request;
 *   - `force=true` only when **the user actively clicks refresh** (they clearly want the latest, do not hand them stale).
 */
import { hydrateFromApi, type HydrateResult } from "@/providers/agentgeex";

export type FreshResult = Pick<HydrateResult, "ok" | "message">;

let inflight: Promise<FreshResult> | null = null;

/**
 * Pull the latest snapshot once into the store.
 * @param force the user-initiated refresh (bypasses in-flight reuse)
 */
export function refreshStoreOnce(force = false): Promise<FreshResult> {
  if (force) inflight = null;
  if (!inflight) {
    inflight = hydrateFromApi()
      .then((r) => ({ ok: !!r.ok, message: r.message }) as FreshResult)
      .catch((e) => ({ ok: false, message: String(e) }) as FreshResult)
      .finally(() => {
        /* clear the inflight flag on the next macrotask: two dialogs opened in the same frame share this one request */
        setTimeout(() => { inflight = null; }, 0);
      });
  }
  return inflight;
}
