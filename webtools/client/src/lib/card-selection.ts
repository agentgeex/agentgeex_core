/**
 * Unified hover / selected visuals and state for grid cards (single source)
 *
 * [2026-11-05 12:41 Feng] "the effect is quite good, now roll it out to all grid pages"
 *
 * Why extract into a module instead of copying per page:
 *   the last round hardcoded one set of hover class names + a piece of sessionStorage logic only on the agent page.
 *   Copying it to the quick command page would mean changing two places next time — it will drift into two looks,
 *   and the problem this round was exactly "several ways of doing things in one product".
 *
 * Three design rules (must be consistent across pages, otherwise users must relearn):
 *   1) hover and selected use the same visuals: what hover looks like is what a clicked card keeps.
 *   2) always bolden with ring (box-shadow), not border-2 —
 *      a thicker border pushes content inward by 1px, and sweeping the mouse across a row makes the whole row jitter.
 *   3) selection is stored in sessionStorage (tab level) + a TTL:
 *      this is "which one this tab just clicked", not a long-term preference; another tab must not be affected.
 */
import * as React from "react";
import { cn } from "@/lib/utils";

/** Hover: deeper line + 2px ring + one level of shadow (all three together feel "obvious") */
export const CARD_HOVER =
  "hover:border-primary hover:ring-2 hover:ring-primary/30 hover:shadow-md";

/** Selected: a bit more solid than hover (denser ring + light blue background), but same family as hover, not another language */
export const CARD_SELECTED =
  "border-primary ring-2 ring-primary/45 shadow-md bg-primary/[0.03]";

/** Base class names for a clickable grid card (pages add their own layout classes on top) */
export const CARD_INTERACTIVE_BASE = "cursor-pointer transition-all duration-150";

/**
 * Compose the state class names of a clickable card.
 *  @param selected whether in the selected state
 *  @param selectedCls override for the selected state (e.g. PM cards use a purple ring, keeping their category color)
 */
export function cardStateClass(selected: boolean, selectedCls: string = CARD_SELECTED): string {
  return cn(CARD_INTERACTIVE_BASE, CARD_HOVER, selected && selectedCls);
}

interface SelectedCard {
  id: string;
  ts: number;
}

/** Default: selected state persists 30 minutes (no restore after that, so a day later an old card is not still lit) */
const DEFAULT_TTL_MS = 30 * 60 * 1000;
/** Default: only auto-scroll back to the list within 2 minutes of the click (a sudden screen jump much later is odd) */
const DEFAULT_SCROLL_MS = 2 * 60 * 1000;

function read(key: string, ttlMs: number): SelectedCard | null {
  try {
    const raw = window.sessionStorage.getItem(key);
    if (!raw) return null;
    const v = JSON.parse(raw) as Partial<SelectedCard>;
    if (!v || typeof v.id !== "string" || !v.id) return null;
    const ts = typeof v.ts === "number" ? v.ts : 0;
    if (Date.now() - ts > ttlMs) {
      window.sessionStorage.removeItem(key);
      return null;
    }
    return { id: v.id, ts };
  } catch {
    /* Storage unavailable / dirty data: treat as nothing selected */
    return null;
  }
}

function write(key: string, id: string) {
  try {
    window.sessionStorage.setItem(key, JSON.stringify({ id, ts: Date.now() }));
  } catch {
    /* Silent degradation: if it cannot remember, it does not; does not affect operation */
  }
}

export interface CardSelection {
  selectedId: string | null;
  isSelected: (id: string) => boolean;
  /** Call it before navigate when clicking a card — the detail page mounts immediately, one step later it may be interrupted */
  select: (id: string) => void;
  clear: () => void;
}

/**
 * Grid selection state (persists across "list → detail → back").
 *
 *  @param storageKey sessionStorage key (independent per page, do not share)
 *  @param testIdPrefix card data-testid prefix, used to auto-scroll to the just-clicked card (id composed on scroll)
 *  @param ttlMs / scrollMs see the defaults above
 */
export function useCardSelection(opts: {
  storageKey: string;
  testIdPrefix: string;
  ttlMs?: number;
  scrollMs?: number;
}): CardSelection {
  const { storageKey, testIdPrefix, ttlMs = DEFAULT_TTL_MS, scrollMs = DEFAULT_SCROLL_MS } = opts;

  // Lazy init: highlight is present on first render, no flash
  const [sel, setSel] = React.useState<SelectedCard | null>(() => read(storageKey, ttlMs));
  const selectedId = sel?.id ?? null;

  const select = React.useCallback(
    (id: string) => {
      write(storageKey, id);
      setSel({ id, ts: Date.now() });
    },
    [storageKey],
  );

  const clear = React.useCallback(() => {
    try {
      window.sessionStorage.removeItem(storageKey);
    } catch {
      /* Same as write */
    }
    setSel(null);
  }, [storageKey]);

  /* On return, scroll the just-clicked card to the middle of the view — with a long list "lit but invisible" is as
        good as not done. Only selectedId in deps: switching filter/view must not trigger a screen jump. */
  React.useEffect(() => {
    if (!selectedId) return;
    if (Date.now() - (sel?.ts ?? 0) > scrollMs) return;
    const el = document.querySelector(`[data-testid="${testIdPrefix}${selectedId}"]`);
    if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  return {
    selectedId,
    isSelected: React.useCallback((id: string) => id === selectedId, [selectedId]),
    select,
    clear,
  };
}
