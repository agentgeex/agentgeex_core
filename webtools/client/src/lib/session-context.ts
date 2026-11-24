/**
 * lib/session-context.ts — session "context watermark" buckets
 * ────────────────────────────────────────────────────────────────
 * [2026-11-06 11:10 Feng] "Including the context below, the highest ones, do that as well"
 *
 * Why a separate file:
 *   the bucket thresholds used to live only in the **backend** (`session_runtime.py::_BUCKETS`),
 *   and the front-end "live session status" card simply read the backend's ready-made `contextBuckets`.
 *   Now the session list also needs to filter by the same buckets → the front end must compute them itself.
 *   Two copies → drift sooner or later (the card says 5 sessions ≥100%, the filter returns 4).
 *   So: **bucket definitions converge into this file**, both sides read from here;
 *   the backend keeps its own implementation (a cross-language shared module is impossible), but key names / labels / boundaries must match this file,
 *   and changing one means changing the other — that constraint is written in the comments on both sides.
 *
 * Boundaries are strictly identical to the backend `_BUCKETS` (left-closed, right-open, unknown maps to null):
 *   <50% / 50–80% / 80–90% / 90–100% / ≥100% / unknown
 */

export interface ContextBucket {
  key: string;
  /** i18n key (namespace excluded). Copy lives in sessionsPage.bucket.* — this file is a pure module without access to hooks. */
  labelKey: string;
  /** Whether this ratio falls in this bucket (with a null ratio only "unknown" matches) */
  test: (ratio: number | null | undefined) => boolean;
}

export const CONTEXT_BUCKETS: ContextBucket[] = [
  { key: "lt50", labelKey: "sessionsPage.bucket.lt50", test: (v) => v != null && v < 0.5 },
  { key: "b50_80", labelKey: "sessionsPage.bucket.b50_80", test: (v) => v != null && v >= 0.5 && v < 0.8 },
  { key: "b80_90", labelKey: "sessionsPage.bucket.b80_90", test: (v) => v != null && v >= 0.8 && v < 0.9 },
  { key: "b90_100", labelKey: "sessionsPage.bucket.b90_100", test: (v) => v != null && v >= 0.9 && v < 1.0 },
  { key: "gte100", labelKey: "sessionsPage.bucket.gte100", test: (v) => v != null && v >= 1.0 },
  { key: "unknown", labelKey: "sessionsPage.bucket.unknown", test: (v) => v == null },
];

/** Which bucket a single ratio falls into (key); null/undefined → "unknown". Mutually exclusive, first match wins. */
export function contextBucketOf(ratio: number | null | undefined): string {
  for (const b of CONTEXT_BUCKETS) {
    if (b.test(ratio)) return b.key;
  }
  return "unknown";
}

/** Options for the filter dropdown (word-for-word identical to the backend's `contextBuckets[].label`).
 *  ★ `t` is passed in rather than exporting constants: module top-level evaluation has no hook and cannot translate. */
export function contextBucketOptions(t: (k: string) => string) {
  return CONTEXT_BUCKETS.map((b) => ({ label: t(b.labelKey), value: b.key }));
}

/** Gateway-side run status (identical to the backend `_STATUSES`);
 *  plus "nodata" — **the session exists in MySQL but not in Redis**, which is not the same as "unknown";
 *  merging it into "unknown" makes the filter result drift further and further from the card count. */
export function runtimeStatusOptions(t: (k: string) => string) {
  return [
    { label: t("sessionStatus.running"), value: "running" },
    { label: t("sessionStatus.idle"), value: "idle" },
    { label: t("sessionStatus.failed"), value: "failed" },
    { label: t("sessionStatus.stalled"), value: "stalled" },
    { label: t("sessionStatus.unknown"), value: "unknown" },
    { label: t("sessionsPage.noGatewayData"), value: "nodata" },
  ];
}

export function runtimeStatusOf(rt: { status?: string } | null | undefined): string {
  return rt?.status || "nodata";
}
