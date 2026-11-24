/**
 * Adaptive grid layout — the **single implementation** of the plan flow chart's "fill one screen" algorithm
 * ──────────────────────────────────────────────────────────────
 * Promoted from the research prototype `plan-flow-fit/grid.ts` on 2026-10-30 (PM decision: production page and
 * experiment page share one copy to avoid two drifting). Both `/plan-flow` and `/plan-flow-fit` import this file.
 *
 * ★ Parameter definitions (finalized 2026-10-30, ask before changing):
 *   column gap 48 / row gap 36 / card size 240×92 unchanged; the intra-column "parent→child" offset stays 300
 *   (that is a relationship inside the card, not a column gap).
 *
 * Input: React Flow nodes (with dagre/exported raw layout coordinates) + container size
 * Output: node coordinates after re-flowing "columns" into rows as a grid + metrics (zoom / body px / fill rate)
 *
 * -- Three definitions (research conclusions, read before changing) --------------------
 * 1. **Column** = a group of nodes that must stack vertically (main-chain node + its subtasks).
 *    The grid algorithm lays out "columns", not "individual nodes" — the relative position of child and parent
 *    card must hold.
 * 2. **zoom definition**: React Flow v12's `fitView({padding})` = `avail = size / (1+p)`.
 *    Measured check: mock single row 1380 wide → 1182/1.12/1380 = 0.765, matching the measured 0.765.
 *    Using `size*(1-2p)` is off by 22%, so the "improvement factor" computed from it is all fake.
 * 3. **Optimal = cut in order**: the stage order must not be shuffled, so 0..N-1 can only be cut into consecutive
 *    **segments**, one segment per row. This is "segmentation", not free bin-packing, so the DP can solve it
 *    exactly (binary search zoom + O(N²) minimum total height).
 */
import type { Node } from "@xyflow/react";
import type { StepNodeData } from "@/pages/agentgeex/plan-flow/adapter";

/** fitView padding and zoom cap: same as /plan-flow, for side-by-side comparison */
export const PAD = 0.12;
export const MAX_ZOOM = 1.1;
/** Legibility gate: zoom ≥ 0.55 ⇒ body text (12px base) ≥ 6.6px */
export const READABLE_ZOOM = 0.55;
export const BODY_BASE = 12;
/** Column clear space (2026-10-30 decision: 80 → 48, compress gaps to raise zoom) */
export const GAP_X = 48;
/** Row clear space (2026-10-30 decision: 68 → 36, rowPitch 160 → 128) */
export const GAP_Y = 36;

export interface ColNode {
  id: string;
  dx: number;
  dy: number;
  w: number;
  h: number;
}
export interface Col {
  /** Representative node id (first node in the column) */
  id: string;
  w: number;
  h: number;
  nodes: ColNode[];
}
export interface Pack {
  /** Which columns go in each row (column indices, in reading order) */
  rows: number[][];
  /** Theoretical zoom (for comparison with measured fitView) */
  zoom: number;
  bboxW: number;
  bboxH: number;
  rowW: number[];
  rowH: number[];
  /** Fill rate = Σ column area / bounding box area (%) */
  fill: number;
}

const nw = (n: Node<StepNodeData>) => n.width ?? (n.data.isChild ? 200 : 240);
const nh = (n: Node<StepNodeData>) => n.height ?? 92;

/**
 * Derive "columns" from nodes.
 *  - with children (real export): main-chain nodes in execution order, column = main chain + its childIds
 *  - without children (mock/dagre layout): rank by x (±1px tolerance), sort by y within a column
 */
export function buildColumns(nodes: Node<StepNodeData>[]): Col[] {
  if (!nodes.length) return [];
  const hasChild = nodes.some((n) => n.data.isChild);
  let groups: Node<StepNodeData>[][];

  if (hasChild) {
    const mains = nodes
      .filter((n) => !n.data.isChild)
      .sort((a, b) => a.position.x - b.position.x);
    const used = new Set<string>();
    groups = mains.map((m) => {
      used.add(m.id);
      const kids = nodes.filter((n) => (m.data.childIds ?? []).includes(n.id));
      kids.forEach((k) => used.add(k.id));
      return [m, ...kids];
    });
    for (const n of nodes) if (!used.has(n.id)) groups.push([n]);
  } else {
    const byX = new Map<number, Node<StepNodeData>[]>();
    for (const n of nodes) {
      const x = Math.round(n.position.x);
      const key = [...byX.keys()].find((k) => Math.abs(k - x) <= 1) ?? x;
      if (!byX.has(key)) byX.set(key, []);
      byX.get(key)!.push(n);
    }
    groups = [...byX.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([, v]) => [...v].sort((a, b) => a.position.y - b.position.y));
  }

  return groups.map((g) => {
    const minX = Math.min(...g.map((n) => n.position.x));
    const minY = Math.min(...g.map((n) => n.position.y));
    const maxX = Math.max(...g.map((n) => n.position.x + nw(n)));
    const maxY = Math.max(...g.map((n) => n.position.y + nh(n)));
    return {
      id: g[0].id,
      w: Math.round(maxX - minX),
      h: Math.round(maxY - minY),
      nodes: g.map((n) => ({
        id: n.id,
        dx: n.position.x - minX,
        dy: n.position.y - minY,
        w: nw(n),
        h: nh(n),
      })),
    };
  });
}

export interface Container {
  w: number;
  h: number;
}

/** Evaluate one split → layout metrics (isomorphic to fitView's algorithm) */
export function evalSplit(cols: Col[], rows: number[][], c: Container, gapX = GAP_X, gapY = GAP_Y): Pack {
  const availW = c.w / (1 + PAD);
  const availH = c.h / (1 + PAD);
  const rowW = rows.map((r) => r.reduce((s, i) => s + cols[i].w, 0) + gapX * Math.max(0, r.length - 1));
  const rowH = rows.map((r) => Math.max(...r.map((i) => cols[i].h)));
  const bboxW = Math.max(...rowW);
  const bboxH = rowH.reduce((a, b) => a + b, 0) + gapY * Math.max(0, rows.length - 1);
  const zoom = Math.min(availW / bboxW, availH / bboxH, MAX_ZOOM);
  const colArea = cols.reduce((s, x) => s + x.w * x.h, 0);
  return {
    rows,
    zoom: Math.round(zoom * 1000) / 1000,
    bboxW: Math.round(bboxW),
    bboxH: Math.round(bboxH),
    rowW,
    rowH,
    fill: Math.round((colArea / (bboxW * bboxH)) * 1000) / 10,
  };
}

/** Exact optimum: binary search zoom + O(N²) "minimum total height" DP. N > 60 degrades to even-split greedy. */
export function optimalSplit(cols: Col[], c: Container, gapX = GAP_X, gapY = GAP_Y): Pack {
  const n = cols.length;
  if (n <= 1) return evalSplit(cols, [cols.map((_, i) => i)], c, gapX, gapY);
  if (n > 60) return uniformSplit(cols, c, gapX, gapY);

  const availW = c.w / (1 + PAD);
  const availH = c.h / (1 + PAD);
  const feasible = (Z: number): number[][] | null => {
    const capW = availW / Z;
    const capH = availH / Z;
    const INF = Number.POSITIVE_INFINITY;
    const dp = new Array(n + 1).fill(INF);
    const pre = new Array(n + 1).fill(-1);
    dp[0] = 0;
    for (let i = 1; i <= n; i++) {
      let w = 0;
      let h = 0;
      for (let j = i; j >= 1; j--) {
        w = w === 0 ? cols[j - 1].w : w + gapX + cols[j - 1].w;
        if (w > capW) break;
        h = Math.max(h, cols[j - 1].h);
        if (dp[j - 1] === INF) continue;
        const cand = dp[j - 1] + (j - 1 > 0 ? gapY : 0) + h;
        if (cand <= capH && cand < dp[i]) {
          dp[i] = cand;
          pre[i] = j - 1;
        }
      }
    }
    if (dp[n] === INF) return null;
    const rows: number[][] = [];
    let i = n;
    while (i > 0) {
      const j = pre[i];
      rows.unshift(Array.from({ length: i - j }, (_, k) => j + k));
      i = j;
    }
    return rows;
  };

  let lo = 0.02;
  let hi = MAX_ZOOM;
  let best: number[][] | null = null;
  for (let it = 0; it < 50; it++) {
    const mid = (lo + hi) / 2;
    const r = feasible(mid);
    if (r) {
      best = r;
      lo = mid;
    } else hi = mid;
  }
  return evalSplit(cols, best ?? [cols.map((_, i) => i)], c, gapX, gapY);
}

/** Greedy/even split: enumerate row count k, columns per row as equal as possible, pick the lowest cost (the 4×5-style option in Feng's table) */
export function uniformSplit(cols: Col[], c: Container, gapX = GAP_X, gapY = GAP_Y): Pack {
  const n = cols.length;
  let best: Pack | null = null;
  for (let k = 1; k <= n; k++) {
    const base = Math.floor(n / k);
    const extra = n % k;
    const rows: number[][] = [];
    let p = 0;
    for (let r = 0; r < k; r++) {
      const len = base + (r < extra ? 1 : 0);
      rows.push(Array.from({ length: len }, (_, i) => p + i));
      p += len;
    }
    const ev = evalSplit(cols, rows, c, gapX, gapY);
    if (!best || ev.zoom > best.zoom + 1e-9) best = ev;
  }
  return best!;
}

/** Manual column count: fixed N columns per row (the last row may be short) */
export function manualSplit(cols: Col[], c: Container, perRow: number, gapX = GAP_X, gapY = GAP_Y): Pack {
  const rows: number[][] = [];
  for (let i = 0; i < cols.length; i += perRow) {
    rows.push(Array.from({ length: Math.min(perRow, cols.length - i) }, (_, k) => i + k));
  }
  return evalSplit(cols, rows, c, gapX, gapY);
}

export type WrapMode = "left" | "snake";

/** Place columns into the grid by split: return the top-left coordinate of each column (flow coordinates) */
export function placeColumns(
  cols: Col[],
  pack: Pack,
  mode: WrapMode,
  gapX = GAP_X,
  gapY = GAP_Y
): Map<number, { x: number; y: number }> {
  const pos = new Map<number, { x: number; y: number }>();
  let y = 0;
  pack.rows.forEach((row, ri) => {
    const h = Math.max(...row.map((i) => cols[i].h));
    const order = mode === "snake" && ri % 2 === 1 ? [...row].reverse() : row;
    let x = 0;
    for (const i of order) {
      pos.set(i, { x, y });
      x += cols[i].w + gapX;
    }
    y += h + gapY;
  });
  return pos;
}

/**
 * "Edge readability" stats after re-flow (dep edges = timing edges between adjacent columns).
 * Endpoint definition: main-chain card right-middle → next column main-chain card left-middle (main-chain cards 240×92).
 */
export function edgeStats(cols: Col[], pack: Pack, mode: WrapMode, gapX = GAP_X, gapY = GAP_Y) {
  const pos = placeColumns(cols, pack, mode, gapX, gapY);
  const Z = pack.zoom;
  const items = [];
  for (let i = 0; i + 1 < cols.length; i++) {
    const a = pos.get(i)!;
    const b = pos.get(i + 1)!;
    const ax = a.x + 240;
    const ay = a.y + 46;
    const bx = b.x;
    const by = b.y + 46;
    const dx = bx - ax;
    const dy = by - ay;
    items.push({ dx: Math.round(dx), dy: Math.round(dy), len: Math.round(Math.hypot(dx, dy)) });
  }
  const screen = items.map((e) => e.len * Z);
  return {
    count: items.length,
    totalPx: Math.round(screen.reduce((a, b) => a + b, 0)),
    maxPx: items.length ? Math.round(Math.max(...screen)) : 0,
    crossRow: items.filter((e) => Math.abs(e.dy) > 1).length,
    backward: items.filter((e) => e.dx < 0).length,
  };
}


/**
 * One step: re-lay out nodes by "columns → optimal grid (left-aligned)".
 * The collapsed-state re-flow on `/plan-flow` uses it directly; the experiment page uses it too.
 *
 * Returns `positions` (nodeId → new coordinates) and `pack` (row split + zoom metrics).
 * Intra-column relative positions are rigidly preserved (`newOrigin + intra-column offset`), the 300px
 * parent-child relationship is not touched.
 */
export function layoutGrid(
  nodes: Node<StepNodeData>[],
  c: Container,
  opts?: { mode?: WrapMode; gapX?: number; gapY?: number; perRow?: number | null }
): { positions: Map<string, { x: number; y: number }>; pack: Pack; cols: Col[] } | null {
  if (!nodes.length || !c.w || !c.h) return null;
  const gapX = opts?.gapX ?? GAP_X;
  const gapY = opts?.gapY ?? GAP_Y;
  const cols = buildColumns(nodes);
  const pack =
    opts?.perRow && opts.perRow > 0
      ? manualSplit(cols, c, opts.perRow, gapX, gapY)
      : optimalSplit(cols, c, gapX, gapY);
  const origin = placeColumns(cols, pack, opts?.mode ?? "left", gapX, gapY);
  const positions = new Map<string, { x: number; y: number }>();
  cols.forEach((col, i) => {
    const o = origin.get(i);
    if (!o) return;
    for (const n of col.nodes) positions.set(n.id, { x: o.x + n.dx, y: o.y + n.dy });
  });
  return { positions, pack, cols };
}
