/**
 * plan-flow/adapter.ts — Win3 plan flow graph adapter
 * ──────────────────────────────────────────────────────────────
 * Input: { nodes[], edges[] } returned by BFF `GET /v1/plans/{id}/graph`
 * Output: nodes / edges for @xyflow/react
 *
 * ── Rewritten 2026-10-27 (aligned with the shardkit delivery note "parent-child hierarchy fix") ──────
 * The old version fed every edge to dagre: children edges of 137 steps were laid out as one vertical line,
 * and the parent-child relation was completely unreadable (feedback verbatim: "parent-child relations are
 * unclear", "they all look connected to the same prerequisite task").
 *
 * The new version makes the **two relation kinds orthogonal in space**:
 *   sequence (next_task) → horizontally right: each main-chain step takes a column, staying at y=0
 *   structure (children) → vertically down: child tasks hang in one column right of their parent, y increasing
 * So "how the flow runs" and "who belongs to whom" are told apart by direction, with no legend to memorize.
 *
 * Differences between Win3 and shardkit:
 *   ① data comes from BFF (real run_status / task_name / agent_name), no stage regex guessing
 *   ② layout follows the real next_task chain, not bound to book-pipeline stage names
 *   ③ colors use only Win3 design tokens (info / success / destructive / warning)
 */
import { MarkerType, Position, type Edge, type Node } from "@xyflow/react";
import type { PlanGraph, PlanGraphNode } from "@/providers/api";

/* [2026-11-03 21:43] Same status vocabulary as the dashboard / executions list.
   Here it used to be a third naming scheme (to-run / running / done); one status had three names across three
   pages, and users had to remap on every page switch. Now it is unified site-wide:
   queued / running / succeeded / failed / cancelled / archived / draft (+ awaiting confirmation).
   archived / pending_confirm are added too — without them the `normalizeStatus` fallback
   silently counts them as "pending", and the graph shows nodes that are not queued at all. */
export type RunStatus =
  | "draft" | "pending" | "running" | "succeeded" | "failed"
  | "cancelled" | "archived" | "pending_confirm"
  /* [2026-11-06] awaiting human confirmation (the only value the kernel writes); pending_confirm is the old alias. */
  | "awaiting_confirm";

export interface StepNodeData extends Record<string, unknown> {
  step: PlanGraphNode;
  status: RunStatus;
  /** whether it is a container step (has children). ★ Now reads the children count, no regex guessing task names */
  isHierarchy: boolean;
  /** number of child tasks (0 = leaf node). The parent card shows "N child" at its top right */
  childCount?: number;
  /** direct child task ids (used to hide the whole subtree when collapsed) */
  childIds?: string[];
  /** whether it is a child task (non main-chain node) — decides card size and visual weight */
  isChild?: boolean;
  /** whether it is collapsed (meaningful for parent nodes only) */
  collapsed?: boolean;
  /** collapse toggle callback (injected by the page; React Flow nodes can only get external abilities through data) */
  onToggleCollapse?: (id: string) => void;
  /** playback / live: whether this is the currently executing node */
  active?: boolean;
  /** playback / live: whether execution has finished */
  done?: boolean;
}

const NODE_W = 240;
const CHILD_W = 200;
const NODE_H = 92;
const ROW_GAP = 132;
/** horizontal offset of children relative to the parent.
 *  ★ It must not be too small: when parent and child x are close, N parent-child edges overlap almost
 *    vertically and read as a serial chain "child1→child2→child3" (they are actually parallel siblings). */
const CHILD_DX = 300;

const VALID: RunStatus[] = [
  "draft", "pending", "running", "succeeded", "failed", "cancelled", "archived", "pending_confirm",
  "awaiting_confirm",
];

/** normalize backend run_status (empty falls back to pending). */
export function normalizeStatus(s?: string | null): RunStatus {
  const v = (s ?? "").trim();
  return (VALID as string[]).includes(v) ? (v as RunStatus) : "pending";
}

/** node label: task name first → execution short title → run_uuid tail */
function nodeLabel(n: PlanGraphNode): string {
  const name = (n.task_name || "").trim();
  if (name) return name.length > 12 ? `${name.slice(0, 12)}…` : name;
  const exec = (n.exec_name || "").trim();
  if (exec) return exec.length > 14 ? `${exec.slice(0, 14)}…` : exec;
  return n.run_uuid.length > 16 ? `…${n.run_uuid.slice(-14)}` : n.run_uuid;
}

/** node sub-label: short identifier such as round / section number (extracted from run_uuid, empty if absent) */
function nodeSub(n: PlanGraphNode, t: (k: string, o?: Record<string, unknown>) => string): string {
  const ru = n.run_uuid;
  const sect = /_s(\d+)(?:_|$)/.exec(ru);
  if (sect) return t("planFlow.sub.section", { n: Number(sect[1]) });
  const ch = /_ch(\d+)/.exec(ru);
  const round = /_r(\d+)_/.exec(ru);
  if (ch && round) return t("planFlow.sub.chapterRound", { ch: ch[1], n: round[1] });
  if (ch) return `ch${ch[1]}`;
  if (round) return t("planFlow.sub.round", { n: round[1] });
  return "";
}

/* ── layout: main chain in columns + children hanging below ─────────────────────────── */

function layoutByChain(g: PlanGraph): Map<string, { x: number; y: number }> {
  const byId = new Map(g.nodes.map((n) => [n.run_uuid, n]));
  const pos = new Map<string, { x: number; y: number }>();
  let chainStartX = 0;

  // 1. main chain: start from a root with no prev_task that is nobody's child, then follow next_task
  const starts = g.nodes.filter(
    (n) => !n.prev_task && !g.nodes.some((p) => (p.children ?? []).includes(n.run_uuid)),
  );
  const chains: string[][] = [];
  const claimed = new Set<string>();

  for (const s of starts) {
    if (claimed.has(s.run_uuid)) continue;
    const chain: string[] = [];
    const guard = new Set<string>();
    let cur: PlanGraphNode | undefined = s;
    while (cur && !guard.has(cur.run_uuid)) {
      guard.add(cur.run_uuid);
      chain.push(cur.run_uuid);
      claimed.add(cur.run_uuid);
      cur = cur.next_task ? byId.get(cur.next_task) : undefined;
    }
    if (chain.length) chains.push(chain);
  }

  // 2. fallback: isolated nodes that are neither on the main chain nor anyone's child (cycles / inconsistent data)
  const orphan = g.nodes
    .filter(
      (n) =>
        !claimed.has(n.run_uuid) &&
        !(n.prev_task && byId.has(n.prev_task)) &&
        !g.nodes.some((p) => (p.children ?? []).includes(n.run_uuid)),
    )
    .map((n) => n.run_uuid);
  if (orphan.length) chains.push(orphan);

  // 3. lay out columns chain by chain
  for (const chain of chains) {
    let cursorX = chainStartX;
    for (const id of chain) {
      const n = byId.get(id);
      if (!n) continue;
      if (!pos.has(id)) pos.set(id, { x: cursorX, y: 0 });

      const kids = (n.children ?? []).filter((c) => byId.has(c) && !pos.has(c));
      if (kids.length) {
        const childX = cursorX + CHILD_DX;
        kids.forEach((k, ki) => pos.set(k, { x: childX, y: (ki + 1) * ROW_GAP }));
        cursorX = childX + CHILD_W + 100;
      } else {
        cursorX = cursorX + NODE_W + 80;
      }
    }
    chainStartX = cursorX + 120;
  }

  // 4. anything still unplaced (inside a cycle) falls back to the end
  let row = 0;
  for (const n of g.nodes) {
    if (!pos.has(n.run_uuid)) {
      pos.set(n.run_uuid, { x: chainStartX, y: (row + 1) * ROW_GAP });
      row += 1;
    }
  }
  return pos;
}

/* ── main entry ───────────────────────────────────────────────── */

export function graphToFlow(
  g: PlanGraph,
  t: (k: string, o?: Record<string, unknown>) => string,
): { nodes: Node<StepNodeData>[]; edges: Edge[] } {
  const ids = new Set(g.nodes.map((n) => n.run_uuid));
  const pos = layoutByChain(g);

  const childSet = new Set<string>();
  for (const n of g.nodes) for (const c of n.children ?? []) childSet.add(c);

  const nodes: Node<StepNodeData>[] = g.nodes.map((n) => {
    const p = pos.get(n.run_uuid) ?? { x: 0, y: 0 };
    const status = normalizeStatus(n.run_status);
    const childCount = (n.children ?? []).length || n.n_children || 0;
    const isChild = childSet.has(n.run_uuid);
    const head = nodeLabel(n);
    const sub = nodeSub(n, t);
    // parent nodes show only the task name (space for the "N child" badge); child nodes carry the round, to tell siblings apart
    const label = childCount > 0 || !sub ? head : `${head} · ${sub}`;

    return {
      id: n.run_uuid,
      type: "step",
      position: { x: p.x, y: p.y },
      data: {
        step: { ...n, task_name: label },
        status,
        // ★ hierarchy detection now uses the real children count
        isHierarchy: childCount > 0,
        childCount,
        childIds: childCount > 0 ? [...(n.children ?? [])] : undefined,
        isChild,
      },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      width: isChild ? CHILD_W : NODE_W,
      height: NODE_H,
    };
  });

  // dependency edges (children structure + next sequence; prev skipped to avoid cycles)
  const rawEdges: { source: string; target: string; kind: "struct" | "dep" }[] = [];
  const seen = new Set<string>();
  const add = (source: string, target: string, kind: "struct" | "dep") => {
    if (!ids.has(source) || !ids.has(target) || source === target) return;
    const k = `${source}->${target}:${kind}`;
    if (seen.has(k)) return;
    seen.add(k);
    rawEdges.push({ source, target, kind });
  };
  for (const e of g.edges) {
    if (e.type === "children") add(e.from, e.to, "struct");
    else if (e.type === "next") add(e.from, e.to, "dep");
  }
  // fallback: self-heal from the step's own fields when the backend gives no edges
  if (rawEdges.length === 0) {
    for (const n of g.nodes) {
      (n.children ?? []).forEach((c) => add(n.run_uuid, c, "struct"));
      if (n.prev_task) add(n.prev_task, n.run_uuid, "dep");
    }
  }

  const edges: Edge[] = rawEdges.map((e, i) => {
    const isStruct = e.kind === "struct";
    return {
      id: `e-${i}-${e.source}-${e.target}-${e.kind}`,
      source: e.source,
      target: e.target,
      // ★ orthogonal in space: structure edges take the "bottom→top" channel, sequence edges the "right→left" channel
      sourceHandle: isStruct ? "struct-out" : "next-out",
      targetHandle: isStruct ? "struct-in" : "next-in",
      // parent-child edges use orthogonal polylines: N edges share one "bus" below the parent, then branch (└─)
      type: "smoothstep",
      pathOptions: isStruct ? { borderRadius: 10, offset: 16 } : undefined,
      data: { kind: e.kind },
      // ★ parent-child edges must be visible: using --border (≈ canvas color, 1.22:1) meant nothing was drawn.
      //   0.6 is the threshold for a 3.34:1 contrast ratio, do not go lower (<0.5 falls into the indistinguishable range)
      style: isStruct
        ? { stroke: "hsl(var(--muted-foreground) / 0.6)", strokeWidth: 1.5, opacity: 1 }
        : {
            stroke: "hsl(var(--muted-foreground))",
            strokeDasharray: "7 5",
            strokeWidth: 2.5,
            opacity: 1,
          },
      markerEnd: isStruct
        ? undefined
        : { type: MarkerType.ArrowClosed, width: 16, height: 16 },
    } as Edge;
  });

  return { nodes, edges };
}

/** live status overlay: change only data.status / active / done, never positions (else the graph jumps on each poll) */
export function applyLiveStatus(
  nodes: Node<StepNodeData>[],
  statuses: Record<string, string | undefined>
): Node<StepNodeData>[] {
  if (!statuses || Object.keys(statuses).length === 0) return nodes;
  return nodes.map((n) => {
    const raw = statuses[n.id];
    if (!raw) return n;
    const status = normalizeStatus(raw);
    return {
      ...n,
      data: {
        ...n.data,
        status,
        active: status === "running",
        done: status === "succeeded",
      },
    };
  });
}

export const STATUS_COLOR: Record<RunStatus, { dot: string; ring: string; text: string }> = {
  draft: { dot: "bg-muted-foreground", ring: "border-border", text: "text-muted-foreground" },
  pending: { dot: "bg-muted-foreground", ring: "border-border", text: "text-muted-foreground" },
  running: { dot: "bg-info", ring: "border-info/60", text: "text-info" },
  succeeded: { dot: "bg-success", ring: "border-success/60", text: "text-success" },
  failed: { dot: "bg-destructive", ring: "border-destructive/60", text: "text-destructive" },
  cancelled: { dot: "bg-warning", ring: "border-warning/60", text: "text-warning" },
  archived: { dot: "bg-muted-foreground", ring: "border-border", text: "text-muted-foreground" },
  pending_confirm: { dot: "bg-chart-1", ring: "border-chart-1/60", text: "text-chart-1" },
  awaiting_confirm: { dot: "bg-chart-1", ring: "border-chart-1/60", text: "text-chart-1" },
};

/** status word → global `status.*` (same vocabulary as the dashboard / executions list, no private copy any more) */
export const STATUS_KEY: Record<RunStatus, string> = {
  draft: "status.draft",
  pending: "status.pending",
  running: "status.running",
  succeeded: "status.succeeded",
  failed: "status.failed",
  cancelled: "status.cancelled",
  archived: "status.archived",
  pending_confirm: "status.awaiting_confirm",
  awaiting_confirm: "status.awaiting_confirm",
};

export function statusLabel(status: RunStatus, t: (k: string) => string): string {
  return t(STATUS_KEY[status]);
}

/** node card size (needed for relayout after collapse and for auto-fit positioning) */
export const NODE_SIZE = { parent: { w: NODE_W, h: NODE_H }, child: { w: CHILD_W, h: NODE_H } };
