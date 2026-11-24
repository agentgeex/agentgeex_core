/**
 * Demo execution player (Playback)
 * ──────────────────────────────────────────────────────────────
 * Purpose: the real export contains no run_status, so a 'simulated executor' makes the flow chart run,
 *       visualizing 'which step is executing / path flow / auto-follow centering'.
 *
 * Semantics:
 *   - Execution order = topological order (fan out children first, then advance serially along next), consistent
 *     with the plan's activate_mode=chain advancement semantics.
 *   - done: the set of finished nodes (never changes again)
 *   - current: the node currently executing (exactly one)
 *   - Advances one step per tick; 'follow the auto demo' is optional
 *
 * Real integration: once agx_task_execution.run_status is available, drive
 *           done/current from it directly; this player is then unnecessary (just align the interface).
 */
import * as React from "react";
import type { Edge, Node } from "@xyflow/react";

/** Compute the topological execution order: start from roots (no in-edges), BFS; within a level children→next takes priority */
export function computeExecutionOrder(nodes: Node[], edges: Edge[]): string[] {
  const ids = new Set(nodes.map((n) => n.id));
  const adj = new Map<string, string[]>();
  const indeg = new Map<string, number>();
  nodes.forEach((n) => { adj.set(n.id, []); indeg.set(n.id, 0); });
  for (const e of edges) {
    if (!ids.has(e.source) || !ids.has(e.target)) continue;
    adj.get(e.source)!.push(e.target);
    indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1);
  }
  // Start point: in-degree 0; if there is a cycle, fall back to the first one
  const queue = nodes.filter((n) => (indeg.get(n.id) ?? 0) === 0).map((n) => n.id);
  if (queue.length === 0 && nodes[0]) queue.push(nodes[0].id);
  const order: string[] = [];
  const seen = new Set<string>();
  while (queue.length) {
    const id = queue.shift()!;
    if (seen.has(id)) continue;
    seen.add(id);
    order.push(id);
    for (const nx of adj.get(id) ?? []) {
      indeg.set(nx, (indeg.get(nx) ?? 1) - 1);
      if (!seen.has(nx)) queue.push(nx);
    }
  }
  // Fallback: nodes not covered (inside a cycle) get appended
  nodes.forEach((n) => { if (!seen.has(n.id)) order.push(n.id); });
  return order;
}

export interface PlaybackState {
  /** Id of the currently executing node */
  current: string | null;
  /** Set of finished nodes */
  done: Set<string>;
  /** Set of edge ids being traversed (in-edges of current) */
  activeEdges: Set<string>;
  /** Set of traversed edge ids (edges associated with done) */
  flowedEdges: Set<string>;
}

/**
 * Player hook
 * @param order execution order (array of node ids)
 * @param edges edge set (used to compute activeEdges/flowedEdges)
 * @param playing whether it is playing
 * @param speedMs interval per step
 */
export function usePlayback(
  order: string[],
  edges: Edge[],
  playing: boolean,
  speedMs = 700,
  /** Reset signal: changes **only when 'the chart itself changed'**.
   *  Collapse/expand and realtime status refreshes must not change it, otherwise collapsing once zeroes the user's progress. */
  resetKey?: string
): { state: PlaybackState; idx: number; total: number; reset: () => void; stepTo: (i: number) => void } {
  const [idx, setIdx] = React.useState(0);

  const key = resetKey ?? String(order.length);
  React.useEffect(() => { setIdx(0); }, [key]);

  React.useEffect(() => {
    if (!playing || order.length === 0) return;
    const t = setInterval(() => {
      setIdx((i) => (i >= order.length ? i : i + 1));
    }, speedMs);
    return () => clearInterval(t);
  }, [playing, order, speedMs]);

  const state = React.useMemo<PlaybackState>(() => {
    const done = new Set<string>();
    for (let i = 0; i < Math.min(idx, order.length); i++) done.add(order[i]);
    const current = idx < order.length ? order[idx] : null;
    const activeEdges = new Set<string>();
    const flowedEdges = new Set<string>();
    const doneSet = new Set([...done, ...(current ? [current] : [])]);
    for (const e of edges) {
      if (current && e.target === current) activeEdges.add(e.id);
      if (e.target !== current && doneSet.has(e.source) && doneSet.has(e.target)) flowedEdges.add(e.id);
    }
    return { current, done, activeEdges, flowedEdges };
  }, [idx, order, edges]);

  const reset = React.useCallback(() => setIdx(0), []);
  const stepTo = React.useCallback((i: number) => setIdx(Math.max(0, Math.min(i, order.length))), [order.length]);

  return { state, idx, total: order.length, reset, stepTo };
}

/** Color nodes/edges by playback state (current=red, traversed=green, normal=gray) */
export function applyPlayback<T extends Record<string, unknown>>(
  nodes: Node<T>[],
  edges: Edge[],
  st: PlaybackState
): { nodes: Node<T>[]; edges: Edge[] } {
  const n2 = nodes.map((n) => {
    const active = n.id === st.current;
    const done = st.done.has(n.id);
    return { ...n, data: { ...n.data, active, done } } as Node<T>;
  });
  const e2 = edges.map((e) => {
    const kind = (e.data as { kind?: string } | undefined)?.kind;
    // ★ Parent-child edges (structure) do not take part in traversal coloring: they express 'belonging', not 'execution order',
    //   and must not turn green just because they were 'walked' — otherwise the two relation kinds blur into one during playback.
    if (kind === "struct") return e;

    const base = (e.style ?? {}) as React.CSSProperties;
    if (st.activeEdges.has(e.id)) {
      return {
        ...e,
        animated: true,
        // ★ Keep the original strokeDasharray: the old version hardcoded undefined, turning timing dashed lines into solid ones
        style: { ...base, stroke: "hsl(var(--destructive))", strokeWidth: 3.5, opacity: 1 },
        markerEnd: e.markerEnd
          ? { ...(e.markerEnd as object), color: "hsl(var(--destructive))" }
          : e.markerEnd,
        zIndex: 10,
      } as Edge;
    }
    if (st.flowedEdges.has(e.id)) {
      return {
        ...e,
        animated: false,
        style: { ...base, stroke: "hsl(var(--success))", strokeWidth: 2.5, opacity: 1 },
        markerEnd: e.markerEnd
          ? { ...(e.markerEnd as object), color: "hsl(var(--success))" }
          : e.markerEnd,
      } as Edge;
    }
    return e;
  });
  return { nodes: n2, edges: e2 };
}

/* ── hover tracing ─────────────────────────────────────────
 * In a dense chart, N parent-child edges overlap; looking at lines alone you cannot tell which connects whom.
 * Hovering any node → directly connected edges take the primary color, the rest dim to 8%. */
export function applyHover(edges: Edge[], hovered: string | null): Edge[] {
  if (!hovered) return edges;
  return edges.map((e) => {
    const related = e.source === hovered || e.target === hovered;
    const base = (e.style ?? {}) as React.CSSProperties;
    return {
      ...e,
      style: related
        ? { ...base, stroke: "hsl(var(--primary))", opacity: 1 }
        : { ...base, opacity: 0.08 },
      zIndex: related ? 8 : 0,
    } as Edge;
  });
}
