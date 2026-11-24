/**
 * components/agentgeex/agent-option.tsx — the "status badge" of an Agent dropdown option
 *
 * [2026-11-07 Feng]
 *   "Even if none are online, still list them in the dropdown, because you may need to look things up."
 *
 * So retired / template agents also became candidates. That raises a problem which must be solved at the same time:
 * **how to make it obvious at a glance that "this row is not serviceable" without implying "not clickable".**
 *
 * ## Why not a plain text suffix
 * The first version appended "· retired". Screenshot review rejected it in one line: in a column of identical
 * color and size those characters simply cannot be scanned — you must read every character to notice.
 * In other words "annotated" equals "not annotated".
 *
 * ## Why not grey out the whole row either
 * The second version made the row `text-muted-foreground`. Review pointed out the opposite problem: **it reads
 * as disabled**. Yet these entries must be clickable — selecting one is exactly how you view its historical
 * sessions from the dropdown (look things up). Saying "not clickable" visually while it is clickable is worse
 * than no annotation.
 *
 * ## So: status gets its own block (body keeps normal color, a small badge at the tail)
 * The body reads normally at a glance; the status is an independent small block, visible at a glance; it neither
 * steals attention from the body nor implies disabled. **"Can look up" and "unusable" are expressed by position,
 * not painted over with one color.**
 */
import { cn } from "@/lib/utils";
import { agentOptionText, agentStateLabel } from "@/providers/agentgeex";
import type { AgentRow } from "@/mock/agentgeex";

export function AgentOptionLabel({
  agent,
  withId = true,
  className,
}: {
  agent: AgentRow;
  /** whether to append (agent_id) after the name. Off when the custom combobox lists the id on the right */
  withId?: boolean;
  className?: string;
}) {
  const state = agentStateLabel(agent);
  return (
    /* ★ `max-w-full` is not decoration: with no width limit outside, an inner `truncate` is only **hard-clipped**
       (no ellipsis) and looks like "the UI is broken" instead of "it does not fit". Raised in the 2026-11-10
       screenshot review. */
    <span className={cn("inline-flex min-w-0 max-w-full items-center gap-1.5", className)}>
      <span className="min-w-0 truncate">{agentOptionText(agent, { withId })}</span>
      {state && (
        <span
          className="shrink-0 rounded border border-border px-1 py-px text-[10px] font-normal leading-4 text-muted-foreground"
          data-testid="agent-state-badge"
        >
          {state}
        </span>
      )}
    </span>
  );
}
