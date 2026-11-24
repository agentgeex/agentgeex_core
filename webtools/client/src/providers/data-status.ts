/**
 * providers/data-status.ts — data connectivity status (global singleton + subscription)
 *
 * [2026-10-20 Feng reported] "After a refresh it cannot reach the backend, the project list has only 4 rows
 * (= seed mock)"
 *
 * The root cause is not the data, it is [silent impersonation]: hydrateFromApi only console.warn'd on
 * failure, the UI kept rendering from the seed mock, so the user saw 4 fake projects while believing the
 * backend had data.
 *
 * So this module turns "is the current data source real" into an explicit status:
 *   - ok=true               -> from the BFF (normal, render business pages)
 *   - ok=false+authRequired -> credentials expired -> go to the login page (2026-10-20 Feng: do not show
 *     sample data)
 *   - ok=false              -> backend unavailable -> fullscreen error + retry (likewise no business pages)
 *
 * Who writes: main.tsx (startup) / DataGate (retry) / auth-login (login success)
 * Who reads: DataGate (route guard) / DataStatusBanner
 */

export interface DataStatus {
  /** Whether real data has been successfully hydrated from the BFF. */
  ok: boolean;
  /**
   * [2026-10-20 Feng's decision] Credentials expired (401) -> go straight to the login page, **must not** show
   * any sample data.
   */
  authRequired?: boolean;
  /** Failure reason code (HTTP_401 / BFF_UNREACHABLE / KERNEL_ERROR ...). */
  code?: string;
  /** HTTP status code (if any). */
  status?: number;
  /** Short reason that can be displayed directly. */
  message?: string;
  /** Recorded time (ms). */
  at: number;
}

let current: DataStatus = { ok: true, at: 0 };
const listeners = new Set<(s: DataStatus) => void>();

export function getDataStatus(): DataStatus {
  return current;
}

export function setDataStatus(s: DataStatus): void {
  current = s;
  listeners.forEach((f) => f(s));
}

export function subscribeDataStatus(f: (s: DataStatus) => void): () => void {
  listeners.add(f);
  return () => {
    listeners.delete(f);
  };
}
