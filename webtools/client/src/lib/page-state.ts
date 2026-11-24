/**
 * lib/page-state.ts — page UI state that survives across remounts
 * ────────────────────────────────────────────────────────────────
 * [2026-11-02 Feng]
 *   "this refresh button doesn't bind the current window's conditions; clicking refresh pulls in all data...
 *    having to re-set the query condition after a refresh is too unfriendly."
 *
 * The root cause is not the refresh button but how it is implemented:
 *   refresh = re-fetch snapshot → version +1 → `RefreshableRoutes` remounts the whole page with `key={v}`
 *   (see App.tsx / lib/page-refresh.ts).
 *   Remounting is necessary — pages read the store synchronously during render; without a remount they see stale data.
 *   But a remount also clears **what the user set**: search term, filters, page number, page size, sorting,
 *   table/grid view. So "refresh data" became "refresh data + forget what I was looking at".
 *
 * So what is stored here is **UI state that survives remounts**, not data:
 *   - memory (module-level Map) covers the "refresh button" path (whole-page remount);
 *   - `sessionStorage` covers F5 / close tab and reopen (valid within the same browser tab).
 *     [2026-11-02 Feng] "does F5 need to survive? if you think it should, then make it survive."
 *     It should: to the user a browser reload and clicking refresh are the same thing; why would one remember and the
 *     other forget.
 *   - **with 12h expiry**: memory is for "continue what I was just looking at", not "bring last week's filters".
 *     Expired memory is worse than no memory — it makes people think data shrank.
 *
 * ★ Relation to URL params (two sources for one page): a jump with params like `?agent=xxx` is a **new intent**,
 *   it should override the restored memory; whereas after a refresh the URL is unchanged, that is not a new intent and
 *   must not override. Criterion = remember "the batch of URL params already applied last time"; if identical do not
 *   apply again (see DataTable usage). Do not use time markers like "refreshing": that requires both sides to be
 *   time-aligned, brittle.
 */
import * as React from "react";

/** The cache itself. key uniqueness is the caller's responsibility (suggested `page:field`). */
const cache = new Map<string, unknown>();

const STORAGE_PREFIX = "agx.ui.";
/** Memory TTL: 12 hours. Overnight you should start fresh, not continue yesterday's filters. */
const TTL_MS = 12 * 60 * 60 * 1000;

/** sessionStorage may be unavailable (private mode / disabled / quota full) —
 *  every read/write here must be able to fail without affecting the page: treat it as no memory, worst case back to
 *  the initial state. So it deliberately does not throw or toast: this is "where I was last time" convenience, not
 *  business data.
 */
function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function readState<T>(key: string): T | undefined {
  if (cache.has(key)) return cache.get(key) as T;
  const st = storage();
  if (!st) return undefined;
  try {
    const raw = st.getItem(STORAGE_PREFIX + key);
    if (!raw) return undefined;
    const parsed = JSON.parse(raw) as { v?: unknown; t?: number };
    if (!parsed || typeof parsed.t !== "number") return undefined;
    if (Date.now() - parsed.t > TTL_MS) {
      st.removeItem(STORAGE_PREFIX + key); // Clear on expiry, don't leave it to mislead the next time
      return undefined;
    }
    cache.set(key, parsed.v);
    return parsed.v as T;
  } catch {
    return undefined;
  }
}

export function writeState(key: string, value: unknown): void {
  cache.set(key, value);
  const st = storage();
  if (!st) return;
  try {
    st.setItem(STORAGE_PREFIX + key, JSON.stringify({ v: value, t: Date.now() }));
  } catch {
    /* Quota full / disabled: the in-memory copy is still valid, lost after F5. Does not affect current use. */
  }
}

export function clearState(keyPrefix: string): void {
  for (const k of [...cache.keys()]) {
    if (k.startsWith(keyPrefix)) cache.delete(k);
  }
  const st = storage();
  if (!st) return;
  try {
    for (let i = st.length - 1; i >= 0; i -= 1) {
      const k = st.key(i);
      if (k && k.startsWith(STORAGE_PREFIX + keyPrefix)) st.removeItem(k);
    }
  } catch {
    /* Same as above */
  }
}

/**
 * Use like `useState`, but the value is remembered (the next mount with the same key gets the last value).
 *
 *  @param key unique key; **pass undefined → degrade to plain useState** (no memory).
 *             ★ Do not change the key during mount: the cache is read only on the first frame.
 *  @param initial initial value when there is no cache.
 *  @param preferInitial when true **ignore the cache** and overwrite with initial (for
 *         strong intents like "the URL explicitly says what to look at", e.g. `?agent=xxx`).
 */
export function usePageState<T>(
  key: string | undefined,
  initial: T,
  preferInitial = false,
): [T, React.Dispatch<React.SetStateAction<T>>] {
  const [value, setValue] = React.useState<T>(() => {
    if (key) {
      if (preferInitial) {
        writeState(key, initial);
        return initial;
      }
      const hit = readState<T>(key);
      if (hit !== undefined) return hit;
    }
    return initial;
  });

  /* ref mirror: the setter needs to read "current value" to support functional updates,
        but side effects must not go inside the setState updater — StrictMode calls it twice. */
  const ref = React.useRef(value);
  ref.current = value;

  const set = React.useCallback<React.Dispatch<React.SetStateAction<T>>>(
    (next) => {
      const v =
        typeof next === "function" ? (next as (prev: T) => T)(ref.current) : next;
      ref.current = v;
      // Must go through writeState (both memory + sessionStorage); writing only to memory loses it on F5.
      if (key) writeState(key, v);
      setValue(v);
    },
    [key],
  );

  return [value, set];
}
