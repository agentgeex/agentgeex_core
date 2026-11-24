/**
 * Custom node: plan step card (StepNode)
 * ──────────────────────────────────────────────────────────────
 * Two-level visuals (2026-10-27, aligned with shardkit "parent-child hierarchy fix"):
 *   Parent task (container) 240×92 · 2px border · bold title · top-right "N children" collapsible badge
 *   Child task (subordinate) 200×92 · 1px light border · one step lower background · normal title · no badge
 * The "manages / is managed" relation between parent and child no longer relies on position alone; the card itself
 * shows the level.
 *
 * ── Four connection ports (spatially orthogonal) ────────────────
 *   next-in  ← left   time order: the previous main-chain step advances here
 *   next-out → right  time order: advance to the next main-chain step
 *   struct-in ← up    structure: I am a child of some parent task
 *   struct-out↓ down  structure: N child tasks hang under me
 * "Before/after" uses the horizontal channel, "contains" uses the vertical channel, so the two relation kinds have
 * distinguishable directions.
 */
import * as React from "react";
import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import { Layers, Workflow, Clock, Bot, ChevronDown, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { STATUS_COLOR, statusLabel, type StepNodeData } from "./adapter";
import { useTranslation } from "react-i18next";

export function StepNode({ data, selected }: NodeProps<Node<StepNodeData>>) {
  const {
    step, status, isHierarchy, childCount, isChild, collapsed, onToggleCollapse, active, done,
  } = data;
  const { t } = useTranslation();
  const c = STATUS_COLOR[status];
  const hasKids = (childCount ?? 0) > 0;
  /** Completed (and not the one that is running) — must be an obvious state, "visible at a glance":
   *  2px solid border + green background + left green bar + a check mark on the status row.
   *  2026-10-27 Feng: "For completed ones, add a bold frame or change the background, anyway it must stand out". */
  const finished = status === "succeeded" && !active;

  return (
    <div
      data-active={active ? "1" : undefined}
      data-parent={hasKids ? "1" : undefined}
      data-child={isChild ? "1" : undefined}
      data-collapsed={collapsed ? "1" : undefined}
      data-status={status}
      className={cn(
        "relative flex h-[92px] flex-col rounded-lg px-3 py-2.5 transition-all duration-200",
        // Width / padding (two levels: parent card and child card)
        isChild ? "w-[200px] pl-4" : "w-[240px] pl-4",
        // ★ Three states give "border + background" **mutually exclusive**:
        //   when border-border and border-success are both present, which wins depends on Tailwind
        //   generation order (not class name order), showing up as "sometimes green, sometimes white".
        active
          ? "border-[3px] border-destructive bg-card shadow-[0_0_0_4px_hsl(var(--destructive)/0.25),0_8px_24px_-4px_hsl(var(--destructive)/0.5)] animate-[pulse_1.6s_ease-in-out_infinite]"
          : finished
            ? "border-2 border-success bg-success/10 shadow-sm"
            : cn(
                isChild ? "border border-border bg-background shadow-none" : "border-2 bg-card shadow-sm",
                !isChild && (hasKids ? "border-foreground/25" : c.ring),
                status === "running" && "shadow-md",
              ),
        hasKids && collapsed && !finished && !active && "border-dashed",
        selected && "ring-2 ring-primary ring-offset-2 ring-offset-background",
      )}
    >
      {/* Level color bar: parent = thick primary bar (strong anchor), child = light thin bar (subordinate) */}
      {hasKids && (
        <span
          className={cn("absolute inset-y-2 left-0 w-[3px] rounded-r", finished ? "bg-success" : "bg-primary/70")}
          aria-hidden
        />
      )}
      {isChild && (
        <span
          className={cn("absolute inset-y-3 left-0 w-[2px] rounded-r", finished ? "bg-success" : "bg-foreground/20")}
          aria-hidden
        />
      )}

      {/* Time-order channel (horizontal) */}
      <Handle id="next-in" type="target" position={Position.Left} className="!h-2 !w-2 !bg-muted-foreground" />
      {/* Structural channel (vertical) */}
      <Handle id="struct-in" type="target" position={Position.Top} className="!h-1.5 !w-1.5 !border-0 !bg-muted-foreground/40" />

      <div className="flex items-center gap-2">
        <span
          className={cn(
            "h-2 w-2 shrink-0 rounded-full",
            active ? "animate-pulse bg-destructive" : c.dot,
            !active && status === "running" && "animate-pulse"
          )}
        />
        <span className={cn("truncate", isChild ? "text-[13px] font-medium" : "text-sm font-semibold")}>
          {step.task_name || step.exec_name || step.run_uuid}
        </span>

        {hasKids ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onToggleCollapse?.(step.run_uuid);
            }}
            title={collapsed ? t("planFlow.node.expandChildren") : t("planFlow.node.collapseChildren")}
            className="nodrag ml-auto flex shrink-0 items-center gap-0.5 rounded bg-foreground/10 px-1.5 py-0.5 text-[10px] font-semibold text-foreground/80 transition-colors hover:bg-foreground/20"
            data-testid={`collapse-${step.run_uuid}`}
          >
            <Layers className="h-3 w-3" />
            {t("planFlow.node.children", { n: childCount })}
            <ChevronDown className={cn("h-3 w-3 transition-transform duration-200", collapsed && "-rotate-90")} />
          </button>
        ) : (
          !isChild && isHierarchy && <Layers className="ml-auto h-3.5 w-3.5 shrink-0 text-primary" />
        )}
      </div>

      <div className={cn("mt-1.5 flex flex-1 flex-col gap-0.5 text-[11px] text-muted-foreground", isChild && "text-[10.5px]")}>
        <div className="flex items-center gap-1">
          <Bot className="h-3 w-3 shrink-0" />
          <span className="truncate">{step.agent_name || step.agent_id}</span>
        </div>
        <div className="flex items-center gap-1">
          <Workflow className="h-3 w-3 shrink-0" />
          <span className="truncate">{step.run_uuid}</span>
        </div>
        {step.scheduled_at && (
          <div className="flex items-center gap-1">
            <Clock className="h-3 w-3 shrink-0" />
            <span className="truncate">{step.scheduled_at.slice(5, 16)}</span>
          </div>
        )}
      </div>

      <div className="flex items-center justify-between">
        <span
          className={cn(
            "inline-flex items-center gap-0.5 font-medium",
            isChild ? "text-[10.5px]" : "text-[11px]",
            active ? "text-destructive" : finished ? "font-semibold text-success" : c.text
          )}
        >
          {finished && <CheckCircle2 className="h-3 w-3" />}
          {active ? t("planFlow.node.running") : statusLabel(status, t)}
        </span>
        {hasKids && collapsed && <span className="text-[10px] text-muted-foreground">{t("planFlow.node.collapsed")}</span>}
      </div>

      <Handle id="next-out" type="source" position={Position.Right} className="!h-2 !w-2 !bg-muted-foreground" />
      <Handle id="struct-out" type="source" position={Position.Bottom} className="!h-1.5 !w-1.5 !border-0 !bg-muted-foreground/40" />
    </div>
  );
}
