/**
 * components/ui/online-dot.tsx — the "status green dot" in front of dropdown options
 *
 * [2026-11-07 Feng]
 *   "In all the dropdowns, the host dropdown and the session dropdown, mark the host status and the session status.
 *    If the host is online put a green one in front, if not leave it alone. Same for sessions: if it is running put a green dot in front,
 *    if it is not running, no need."
 *
 * ## Why the "no status" case still reserves space (the part of this component most likely to be broken)
 *
 * The first version rendered only when there was a status, null otherwise — the screenshot review spotted it immediately:
 * the text on rows with a green dot was pushed 8px right, so the column's left edge came out jagged. **Reading a list of options, the eye keeps getting yanked.**
 *
 * So now: an outer **fixed-width placeholder** is always there, and only the inner green dot is optional.
 * "No dot" means no green dot (not a grey dot), but **the space is still reserved**.
 * That satisfies both requirements at once: Feng's "leave it alone when not online", and an aligned column you can scan.
 *
 * ## Why the other decisions
 *   · when `show=false`, **no grey/red dot is drawn**: a row of grey dots renders "no information" as "everything is broken".
 *   · `aria-hidden`: the label already conveys the content, having the reader announce "green dot" is noise.
 *   · `data-testid` is only on the inner element (present only when there really is a dot) — acceptance scripts count dots by it;
 *     if the outer placeholder also carried a testid, "count the green dots" would become "count the options".
 */
import { cn } from "@/lib/utils";

export function OnlineDot({
  show,
  className,
  title,
  testId = "opt-dot",
}: {
  /** true = draw the green dot; false/undefined = keep the placeholder only, draw nothing */
  show?: boolean;
  className?: string;
  /** Hover hint (e.g. "running" / "online"), empty when there is none */
  title?: string;
  testId?: string;
}) {
  return (
    <span
      className={cn("inline-flex h-1.5 w-1.5 shrink-0 items-center justify-center", className)}
      aria-hidden="true"
    >
      {show && (
        <span
          className="h-1.5 w-1.5 rounded-full bg-success"
          title={title}
          data-testid={testId}
        />
      )}
    </span>
  );
}
