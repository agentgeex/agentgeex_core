/**
 * components/agentgeex/quick-star.tsx — "pin to quick access" star (shared by lists and cards)
 *
 * [2026-11-13 22:03 Feng] Quick access module: clicking ★ on any object type puts it into quick access.
 *
 * Why a standalone component:
 *   · all four list pages (projects / agents / sessions / plans) need it;
 *     writing useState + request + error handling four times inevitably drifts;
 *   · clicking the star needs `stopPropagation` (the row itself is clickable/navigable),
 *     miss it once and "favouriting also opened the detail page".
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Star, Loader2 } from "lucide-react";
import { isQuickEntry, addQuickEntry, removeQuickEntry } from "@/providers/agentgeex";
import type { QuickEntryType } from "@/mock/agentgeex";
import { cn } from "@/lib/utils";

export function QuickStar({
  type,
  entryKey,
  label,
  className,
  size = "sm",
  testId,
}: {
  type: QuickEntryType;
  entryKey: string;
  /** Name snapshot: the name at the time of favouriting (still recognizable if the target is renamed later) */
  label: string;
  className?: string;
  size?: "sm" | "xs";
  testId?: string;
}) {
  const { t } = useTranslation();
  const [on, setOn] = React.useState<boolean>(() => isQuickEntry(type, entryKey));
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState("");

  /* entryKey changes on row reuse or paging — state must be recomputed, otherwise the star "remembers" the previous row. */
  React.useEffect(() => {
    setOn(isQuickEntry(type, entryKey));
  }, [type, entryKey]);

  const toggle = async (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    if (busy || !entryKey) return;
    setBusy(true);
    setErr("");
    try {
      if (on) await removeQuickEntry(type, entryKey);
      else await addQuickEntry(type, entryKey, label);
      setOn(!on);
    } catch (ex) {
      /* Backend returns 503 while the table is not ready — show a hint instead of a dialog that interrupts the action */
      setErr((ex as { message?: string })?.message || t("miscShell.quickStar.failed"));
    } finally {
      setBusy(false);
    }
  };

  const icon = size === "xs" ? "h-3 w-3" : "h-3.5 w-3.5";

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={busy}
      title={err || (on ? t("miscShell.quickStar.tipOn") : t("miscShell.quickStar.tipOff"))}
      aria-pressed={on}
      data-testid={testId}
      data-quick={on ? "1" : "0"}
      className={cn(
        "inline-flex items-center justify-center rounded p-0.5 transition-colors disabled:opacity-50",
        on ? "text-warning" : "text-muted-foreground hover:text-warning",
        err && "text-destructive",
        className,
      )}
    >
      {busy ? (
        <Loader2 className={cn(icon, "animate-spin")} />
      ) : (
        <Star className={cn(icon, on && "fill-current")} />
      )}
    </button>
  );
}
