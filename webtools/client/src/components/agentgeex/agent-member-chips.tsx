/**
 * components/agentgeex/agent-member-chips.tsx — member chips (project members / group template members)
 * ────────────────────────────────────────────────────────────────
 * [2026-11-04 Feng]
 *   · Project list: 'agent count' and 'project manager' merged into 'project members' — the manager gets its own color,
 *     the rest follow; **show all of them, as many as there are** (wrapping is fine, no '+N').
 *   · Group template: members listed directly in the column; the PM must be colored too, otherwise you cannot tell who the PM is.
 *
 * ★ Click = **open a detail dialog**, not a page jump (Feng 2026-11-04 13:53):
 *   'just pop a window, right now you jump over there ... and one back press and it is gone'.
 *   A page jump loses the list's filter/page/scroll position, and you have to find it again. A dialog is 'look in place, close and you are back'.
 *
 * Three tradeoffs (all learned the hard way):
 *   1. The click must stopPropagation: the whole `<tr>` also has an onClick (open project detail),
 *      otherwise you get 'project dialog first, then agent dialog' and it looks like a misclick.
 *   2. **Duplicate names must be distinguishable**: in one group, host_managers on several hosts are all named `agx_host`
 *      (one instance per host, but the same stored name). Duplicate names get an id suffix,
 *      otherwise the list repeats the same words three times — looks like a bug, but they are three different agents.
 *   3. The project manager (PM) is distinguished by color, not by an ordering note: saying 'it is first' tells nobody who is special.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Crown } from "lucide-react";
import { cn } from "@/lib/utils";
import { useDetailPopup } from "@/components/agentgeex/detail-popup";
import { roleLabel } from "@/lib/role-label";

export interface MemberRef {
  id: string;
  name: string;
  role: string;
  isAdmin?: boolean;
}

/** Whether to highlight the 'owner'.
 *  Project: the backend-provided isAdmin (= agx_project.admin_agent_id);
 *  Group template: the group has no admin field, so role=project_manager counts — Feng 2026-11-04:
 *  'the group template's project members include this PM too, so the PM must get a color as well'. */
const isLead = (m: MemberRef) => !!m.isAdmin || m.role === "project_manager";

/** For duplicate names, take only the **last segment** of the id as the distinction.
 *  agx_host_test_host_srv → test_host_srv (not host_test_host_srv — those 10 chars
 *  get truncated to `host_sgai...` inside a chip, which distinguishes nothing). */
function shortId(id: string): string {
  const parts = id.split("_").filter(Boolean);
  return parts.length > 1 ? parts[parts.length - 1] : id;
}

export function AgentMemberChips({
  members,
  emptyText = "—",
  className,
}: {
  members?: MemberRef[];
  emptyText?: string;
  className?: string;
}) {
  const { t } = useTranslation();
  const popup = useDetailPopup();

  const list = React.useMemo(() => {
    const arr = [...(members ?? [])];
    // The owner is always first (the backend already sorts; repeated here as a guard: a component should not depend on one data source being correct)
    arr.sort((a, b) => Number(isLead(b)) - Number(isLead(a)));
    return arr;
  }, [members]);

  // Duplicate detection: add a suffix only when the same name appears twice or more in one list, to avoid long ids everywhere
  const dupNames = React.useMemo(() => {
    const cnt = new Map<string, number>();
    for (const m of list) cnt.set(m.name, (cnt.get(m.name) ?? 0) + 1);
    return new Set([...cnt.entries()].filter(([, n]) => n > 1).map(([k]) => k));
  }, [list]);

  if (!list.length) {
    return <span className="text-xs text-muted-foreground">{emptyText}</span>;
  }

  return (
    <div
      className={cn("flex flex-wrap items-center gap-1", className)}
      data-testid="agent-member-chips"
    >
      {list.map((m) => {
        const lead = isLead(m);
        const label = dupNames.has(m.name) ? `${m.name} · ${shortId(m.id)}` : m.name;
        const roleText = roleLabel(m.role, t);
        const title = `${m.name} · ${roleText} · ${m.id}`;
        return (
          <button
            key={m.id}
            type="button"
            title={title}
            onClick={(e) => {
              // The row itself is also clickable (opens the owning project/group); without stopping it, two layers open
              e.stopPropagation();
              popup?.open({ kind: "agent", id: m.id });
            }}
            data-testid={`member-chip-${m.id}`}
            className={cn(
              // 200px would truncate a long '<book title> project manager' label to '...project man...' —
              // since wrapping is allowed (Feng 2026-11-04 13:53), width should no longer be saved by truncation,
              // 260px ≈ 24 CJK chars, enough for one full prefixed project-manager name.
              "inline-flex max-w-[260px] items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] leading-tight transition-colors",
              lead
                ? "border-primary/40 bg-primary/10 font-medium text-primary hover:bg-primary/20"
                : "border-strong bg-muted/40 text-foreground hover:border-primary/40 hover:bg-muted",
            )}
          >
            {lead && <Crown className="h-3 w-3 shrink-0" />}
            <span className="truncate">{label}</span>
          </button>
        );
      })}
    </div>
  );
}

/** Member details (the full table in the dialog/detail page): owner on top + role tags; clicking also opens the detail. */
export function AgentMemberList({ members }: { members?: MemberRef[] }) {
  const { t } = useTranslation();
  const popup = useDetailPopup();
  const list = members ?? [];
  if (!list.length) {
    return <p className="text-sm text-muted-foreground">{t("miscUi.role.none")}</p>;
  }
  return (
    <ul className="flex flex-col divide-y divide-border" data-testid="agent-member-list">
      {list.map((m) => {
        const lead = isLead(m);
        return (
          <li key={m.id} className="flex items-center justify-between gap-3 py-1.5">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                popup?.open({ kind: "agent", id: m.id });
              }}
              className={cn(
                "flex min-w-0 items-center gap-1.5 text-left text-sm hover:underline",
                lead ? "font-medium text-primary" : "text-foreground",
              )}
              data-testid={`member-row-${m.id}`}
            >
              {lead && <Crown className="h-3.5 w-3.5 shrink-0" />}
              <span className="truncate">{m.name}</span>
            </button>
            <span className="flex shrink-0 items-center gap-2">
              <span className="text-[11px] text-muted-foreground">
                {roleLabel(m.role, t)}
              </span>
              <span className="font-mono text-[11px] text-muted-foreground">{m.id}</span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}
