/**
 * Page data refresh bus (Page Refresh Bus)
 * ────────────────────────────────────────────────────────────────
 * [2026-10-28 Feng] Refreshing used to take two steps: 'right-click the tab → click refresh'.
 * Now refresh is a **fixed button** on the right of the nav bar; one click refreshes the current page's data.
 *
 * Design:
 *   1. Button → `refreshCurrentPage()`: re-pull the full BFF snapshot (hydrate) → version +1;
 *   2. Version change → the current route page **remounts** (see App's RefreshableRoutes),
 *      the page re-reads the store and the data is current;
 *   3. A page wanting custom refresh behavior (e.g. its own polled realtime page) uses `usePageRefreshed(fn)`
 *      to subscribe to the version and do its own thing in the callback (no forced remount).
 *
 * ★ Why not just `navigate()` to the current route: navigating to the same route does not remount,
 *   and the data is not refetched — that is exactly why Feng felt 'refresh does nothing'.
 */
import * as React from "react";

let version = 0;
const listeners = new Set<() => void>();

function emit() {
  version += 1;
  for (const fn of [...listeners]) {
    try {
      fn();
    } catch {
      /* One failing subscriber does not affect other pages */
    }
  }
}

/** Subscribe to 'a refresh happened'; returns an unsubscribe function */
export function subscribePageRefresh(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function pageRefreshVersion(): number {
  return version;
}

/** Data refresh finished → broadcast (called by the refresh button after a successful hydrate) */
export function notifyPageRefreshed(): void {
  emit();
}

/** Current refresh version (a change triggers re-render / remount) */
export function usePageRefreshVersion(): number {
  return React.useSyncExternalStore(subscribePageRefresh, pageRefreshVersion, pageRefreshVersion);
}

/** Page side: run custom logic when a refresh happens (without remounting the page). */
export function usePageRefreshed(fn: () => void): void {
  const ref = React.useRef(fn);
  ref.current = fn;
  React.useEffect(() => subscribePageRefresh(() => ref.current()), []);
}
