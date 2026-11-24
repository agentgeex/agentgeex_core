/**
 * hooks/use-body-unlock.ts — self-healing for leftover overlays (fallback for a whole window "locking up")
 *
 * [2026-10-20 Feng reported] "After sending a message in text mode, the whole window is locked and nothing works."
 *
 * Symptom: the page is still there but clicks do nothing — typically Radix leaving
 * `document.body.style.pointerEvents = "none"` unrestored (the modal layer unmounted without cleanup).
 * Radix Dialog / Select / DropdownMenu all lock body temporarily; once some overlay is unmounted
 * while "open" (switching modes, parent re-render, branch switch after a successful submit),
 * the cleanup never runs, body keeps pointer-events:none, and **there may not even be a visible overlay**.
 *
 * This hook is the fallback: if body is locked and **no overlay is currently open**,
 * the lock is stale → restore it and log a warning so it can be traced.
 *
 * It uses observe + delayed re-check instead of polling: it only inspects after DOM changes, negligible cost.
 */
import * as React from "react";

/** Whether an overlay is really open right now (yes → the lock is legitimate, leave it alone). */
function hasOpenLayer(): boolean {
  return (
    document.querySelector(
      [
        '[role="dialog"][data-state="open"]',
        '[role="alertdialog"][data-state="open"]',
        '[data-radix-popper-content-wrapper]',
        '[role="listbox"]',
        '[role="menu"]',
        '[role="tooltip"][data-state="open"]',
      ].join(",")
    ) !== null
  );
}

function unlockIfStale(): void {
  const body = document.body;
  const locked = body.style.pointerEvents === "none";
  if (!locked) return;
  if (hasOpenLayer()) return; // Still an overlay open, the lock is correct
  body.style.pointerEvents = "";
  body.style.overflow = "";
  console.warn("[ui] 检测到无浮层但仍锁死 body（遮罩残留），已自动解锁");
}

export function useBodyUnlock(): void {
  React.useEffect(() => {
    let timer: number | undefined;
    const schedule = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(unlockIfStale, 150);
    };

    const mo = new MutationObserver(schedule);
    mo.observe(document.body, {
      attributes: true,
      attributeFilter: ["style", "data-state"],
      childList: true,
      subtree: true,
    });

    // Also check on route change / returning to the tab (an overlay may unmount while invisible)
    window.addEventListener("focus", schedule);
    document.addEventListener("visibilitychange", schedule);

    return () => {
      window.clearTimeout(timer);
      mo.disconnect();
      window.removeEventListener("focus", schedule);
      document.removeEventListener("visibilitychange", schedule);
    };
  }, []);
}
