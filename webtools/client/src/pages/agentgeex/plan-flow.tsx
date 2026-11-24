/**
 * Plan flow diagram (/plans/:planId/flow)
 * ──────────────────────────────────────────────────────────────
 * Renders one AgentGeeX "plan" as a left-to-right pipeline diagram, supporting:
 *   live status / collapse / fullscreen presentation / hover tracing / playback / node details
 *
 * Ported from shardkit `ui_component_library/src/pages/cases/plan-flow.tsx`
 *   (developed by win_ui_lead; notice "plan-flow parent-child hierarchy fix delivery notice 20261027")
 * Win3-side differences:
 *   ① Data comes from BFF `GET /v1/plans/{id}/graph` (real run_status + task_name)
 *   ② Single-plan view (planId from route params), no demo presets
 *   ③ [2026-10-27 Feng] live status: polls `/v1/plans/{id}/status` (kernel Redis hot cache)
 *      -- merges status only, no layout change, no reset of collapse/playback progress
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import {
  ReactFlow, Background, Controls, MiniMap,
  useNodesState, useEdgesState, useReactFlow, useNodesInitialized, useViewport,
  ReactFlowProvider, Panel,
  type Node, type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  Play, Pause, RotateCcw, Crosshair, ChevronLeft, ChevronRight, Loader2,
  FoldVertical, UnfoldVertical, Maximize2, Minimize2, Radio, RadioTower, RefreshCw,
  ArrowLeft,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Slider } from "@/components/ui/slider";
import { Progress } from "@/components/ui/progress";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
/* [2026-11-13 08:16 Feng] After going fullscreen, double-clicking a node fails to open the detail dialog --
   same root cause as the dropdown in the message-send window: Radix Portal mounts to body,
   and body is outside the native fullscreen element.
   -> While fullscreen, mount dialogs/popovers inside the fullscreen container. */
import { PortalContainerProvider } from "@/components/ui/portal-container";
import { DetailShell, DetailSection, TextBlock, RawBlock } from "@/components/agentgeex/detail-shell";
import { EntityRef, useDetailPopup } from "@/components/agentgeex/detail-popup";
import { OpenClawSessionLink } from "@/components/agentgeex/openclaw-session-link";
import { Descriptions, DescriptionsItem } from "@/components/ui/descriptions";
import type { PlanGraphNode, PlanLiveStatus } from "@/providers/api";
import { fetchPlanGraph, fetchPlanStatus, listSessions } from "@/providers/agentgeex";
import { isSessionRunning, SessionRunDot } from "@/components/agentgeex/session-runtime-badge";
import {
  graphToFlow, applyLiveStatus, STATUS_COLOR, statusLabel, type StepNodeData, type RunStatus,
} from "./plan-flow/adapter";
import { StepNode } from "./plan-flow/step-node";
import { computeExecutionOrder, usePlayback, applyPlayback, applyHover } from "./plan-flow/playback";
import {
  buildColumns, optimalSplit, placeColumns, evalSplit, GAP_X, GAP_Y, READABLE_ZOOM,
  type Container, type Pack,
} from "@/lib/plan-grid";

const nodeTypes = { step: StepNode };
const LEGEND: RunStatus[] = ["pending", "running", "succeeded", "failed", "cancelled"];

/** Default playback speed 3000ms (same as shardkit). */
const DEFAULT_SPEED = 3000;
/** Live refresh interval: 3s. Fast enough to see flow changes, not enough to saturate the BFF. */
const LIVE_INTERVAL = 3000;

/**
 * [2026-11-06 00:09 Feng]
 *   "If we came from a popup window, then the flow diagram is also a popup window;
 *    if not from a popup window, the flow diagram stays the same as before."
 * -> Same PlanFlow, just two more inputs:
 *   `embedded` = now mounted inside the graph dialog (toolbar "Back" becomes "Close";
 *                clicking it closes the dialog instead of navigate)
 *   `onClose`  = close callback of that dialog layer above
 * Page route (/plans/:id/flow) is completely unchanged, behavior not altered by a single character.
 */
export function PlanFlow({
  planId,
  embedded = false,
  onClose,
}: {
  planId: string;
  embedded?: boolean;
  onClose?: () => void;
}) {
  return (
    <ReactFlowProvider>
      <PlanFlowInner planId={planId} embedded={embedded} onClose={onClose} />
    </ReactFlowProvider>
  );
}

export default function PlanFlowPage(props: { planId?: string }) {
  return <PlanFlow planId={props.planId ?? ""} />;
}

function PlanFlowInner({
  planId, embedded = false, onClose,
}: { planId: string; embedded?: boolean; onClose?: () => void }) {
  const { t } = useTranslation();
  const { setViewport, getZoom } = useReactFlow();
  /* [2026-11-03 Feng] This page previously had only an "in" and no "out" --
     clicking "Plans" in the sidebar counts as a page change, not going back; the breadcrumb was plain
     unclickable text.
     Fullscreen presentation can be exited via "Exit fullscreen", but in the normal state no button goes
     "back to plan".
     -> Add an explicit exit at the far left of the toolbar. */
  const navigate = useNavigate();
  const rootRef = React.useRef<HTMLDivElement | null>(null);
  /* The fullscreen element's own DOM node -- stored in state for the Provider (ref is unreadable at render). */
  const [fsNode, setFsNode] = React.useState<HTMLDivElement | null>(null);
  const attachRoot = React.useCallback((node: HTMLDivElement | null) => {
    rootRef.current = node;
    setFsNode(node);
  }, []);


  const [graph, setGraph] = React.useState<Awaited<ReturnType<typeof fetchPlanGraph>> | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState<string | null>(null);

  /* ───────── Canvas size (measured, not estimated) ─────────
   * [2026-11-03 port of shardkit adaptive-grid study] Row count must be computed from real available space:
   * at 1440 it is 1182×542, at 1280 it is 1022×508 -- vertical is the bottleneck (height is only 46% of width).
   * Follow `.react-flow` with ResizeObserver, not a one-shot clientWidth read (wrong after fullscreen toggle). */
  const [canvas, setCanvas] = React.useState<Container>({ w: 0, h: 0 });
  React.useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const read = () => {
      const rf = el.querySelector(".react-flow") as HTMLElement | null;
      if (!rf) return;
      const next = { w: rf.clientWidth, h: rf.clientHeight };
      setCanvas((prev) => (prev.w === next.w && prev.h === next.h ? prev : next));
    };
    read();
    const ro = new ResizeObserver(read);
    ro.observe(el);
    const t = window.setTimeout(read, 300); // First frame: .react-flow may not be mounted yet
    return () => { ro.disconnect(); window.clearTimeout(t); };
    /* ★ deps must include loading: this component returns early while loading, so that div holding the ref
     * is not in the DOM at all -> on first mount rootRef.current === null, and returning early means it never
     * reruns, canvas stays at {0,0} forever, auto-fold and grid never trigger
     * (symptom: still one long strip after collapsing).
     * This is a real bug caught by measurement on 2026-11-03, do not narrow the deps back.*/
  }, [loading, planId]);

  /* Avoidance margins must be **measured**, not hardcoded.
   * ★ 2026-11-03 shardkit pointed out: their comment says the panel is 384×117, I measured 384×84 --
   *   same `max-w-sm` class, different height (they merged the legend into the info card).
   *   Hardcoding `84+20` means: once panel content grows it collides again, and on collision nobody
   *   suspects a stale constant.
   * Also fixes another spot just measured: the bottom legend bar (548×59) covers the last row of nodes.
   * Exclusions: minimap / controls / attribution are "corner widgets", covering corners is intended;
   *   narrow overlays (< 25% of canvas width) likewise -- avoiding them along a whole edge drops zoom for nothing.
   *
   * ★ Key split (took two tries, do not merge): this function's result is **for positioning only**,
   *   it never feeds the criteria.
   *   Criteria (fold or not / row count) use full canvas size -- stable, reproducible, matches shardkit.
   *   I once merged both into a single avail, so avail depended on DOM measurement -> on the first frame
   *   the panel was not rendered and defaults were measured -> a 6-node graph judged "does not fit" and
   *   auto-folded (acceptance: folded at start, zoom hit 1.1, button label inverted).
   *   Now: positioning subtracts top (info panel) + bottom (legend bar); when the readable size does not
   *   fit, fall back to a landing point, which is also computed from the reduced available height
   *   -> the bottom legend no longer covers nodes.*/
  const measureInsets = React.useCallback(() => {
    const base = { top: 24, right: 24, bottom: 24, left: 24 };
    const root = rootRef.current;
    if (!root) return base;
    /* ★★★ 2026-11-03 coordinate-system mismatch caught (subtle bug):
     *   the insets measured here feed fitInset, but fitInset's frame of reference is the
     *   **`.react-flow` canvas**.
     *   The original implementation used `rootRef` (whole page container) as the frame -- the two differ by
     *   a whole page header (measured 116px).
     *   Consequence: ins.top computed as 202 (the real panel is only 68~84 high), available height shrank
     *   from 413 to 295, content pushed down overall with blank space on the right -- one of the sources
     *   of the "big empty block on the left" look.
     *   Why it went unnoticed: when only avoiding the panel's top edge, "avoid a bit more" looks fine
     *   (just emptier); once used for centering, the offset becomes a visible position error. */
    const rf = root.querySelector(".react-flow") as HTMLElement | null;
    const c = (rf ?? root).getBoundingClientRect();
    const GAP = 16;
    let top = 0, bottom = 0;
    root.querySelectorAll<HTMLElement>(".react-flow__panel").forEach((el) => {
      if (el.classList.contains("react-flow__minimap")) return;
      if (el.classList.contains("react-flow__controls")) return;
      if (el.classList.contains("react-flow__attribution")) return;
      const r = el.getBoundingClientRect();
      if (r.width < 40 || r.height < 10) return;          // Empty container (conditional-render placeholder)
      if (r.width < c.width * 0.25) return;               // Narrow corner label, do not avoid the whole edge

      if (el.classList.contains("top")) top = Math.max(top, r.bottom - c.top);
      else if (el.classList.contains("bottom")) bottom = Math.max(bottom, c.bottom - r.top);
    });
    return {
      top: Math.max(base.top, top + GAP),
      bottom: Math.max(base.bottom, bottom + GAP),
      left: base.left,
      right: base.right,
    };
  }, []);

  /* ★ 2026-11-03 lesson (two tries to get right): criteria **must not use values that depend on DOM measurement**.
   *   I once made expandedPack / currentZoom / layout all use "canvas − measured overlays",
   *   so avail depended on rootRef measurement -> on the first frame the panel was not rendered, defaults
   *   were measured, autoFold decided right then -> a 6-node graph judged "does not fit" and auto-folded
   *   (acceptance: folded at start, zoom hit the 1.1 cap, button label inverted).
   *   Correct split:
   *     · criteria (fold or not / row count) use full canvas size -- stable, reproducible, matches shardkit
   *     · positioning (fitInset) measures overlays live -- real rendering, accept slightly smaller zoom
   *   If the two disagree, "fall back to a readable landing point when fitInset < 0.55" covers it.*/

  const [baseNodes, setBaseNodes] = React.useState<Node<StepNodeData>[]>([]);
  const [baseEdges, setBaseEdges] = React.useState<Edge[]>([]);

  // ── Live status (2026-10-27) ──
  const [liveOn, setLiveOn] = React.useState(true);
  const [live, setLive] = React.useState<PlanLiveStatus | null>(null);
  const [liveErr, setLiveErr] = React.useState<string>("");

  // ── Demo playback (fallback when no real status / replay when status exists) ──
  const [playing, setPlaying] = React.useState(false);
  const [speedMs, setSpeedMs] = React.useState(DEFAULT_SPEED);
  const [follow, setFollow] = React.useState(false);
  const [detail, setDetail] = React.useState<PlanGraphNode | null>(null);

  // ── Collapse (all expanded by default) / hover / fullscreen ──
  const [collapsed, setCollapsed] = React.useState<Set<string>>(new Set());
  const [hovered, setHovered] = React.useState<string | null>(null);
  const [fs, setFs] = React.useState(false);

  /* ───────── Loading diagram ───────── */
  React.useEffect(() => {
    if (!planId) {
      setLoading(false);
      setErr(t("planFlow.missingPlanId"));
      return;
    }
    let alive = true;
    setLoading(true);
    setErr(null);
    fetchPlanGraph(planId)
      .then((g) => {
        if (alive) setGraph(g);
      })
      .catch((e) => {
        if (alive) setErr(String(e?.message ?? e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [planId]);

  React.useEffect(() => {
    if (!graph) return;
    const { nodes, edges } = graphToFlow(graph, t);
    setBaseNodes(nodes);
    setBaseEdges(edges);
    setCollapsed(new Set()); // Clear collapse state on graph change, avoid stale parent ids
  }, [graph]);

  /* ───────── Live polling (Redis hot cache) ─────────
   * ★ Only pull the status map; node structure / full instruction text is not resent.
   * ★ Skip when the page is not visible (save resources, catch up once immediately on return). */
  const pull = React.useCallback(async () => {
    if (!planId) return;
    if (typeof document !== "undefined" && document.hidden) return;
    try {
      const r = await fetchPlanStatus(planId);
      setLive(r);
      setLiveErr("");
    } catch (e) {
      setLiveErr((e as Error)?.message || String(e));
    }
  }, [planId]);

  React.useEffect(() => {
    if (!planId || !liveOn) return;
    void pull();
    const t = window.setInterval(() => void pull(), LIVE_INTERVAL);
    return () => window.clearInterval(t);
  }, [planId, liveOn, pull]);

  /* ───────── Collapse -> hide the whole subtree (including grandchildren) ───────── */
  const hidden = React.useMemo(() => {
    const h = new Set<string>();
    const byId = new Map(baseNodes.map((n) => [n.id, n]));
    const walk = (id: string) => {
      const kids = (byId.get(id)?.data?.childIds as string[] | undefined) ?? [];
      for (const k of kids) {
        if (h.has(k)) continue;
        h.add(k);
        walk(k);
      }
    };
    collapsed.forEach(walk);
    return h;
  }, [baseNodes, collapsed]);

  const toggleCollapse = React.useCallback((id: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const parentIds = React.useMemo(
    () => baseNodes.filter((n) => (n.data.childIds?.length ?? 0) > 0).map((n) => n.id),
    [baseNodes],
  );

  /* ───────── Optimal zoom of the expanded grid (the sole input of the fold gate) ─────────
   * [2026-11-03 port of shardkit study / decision 4] criteria changed from "node count > 60" to
   * "optimal zoom computed from the real column bbox in expanded state < 0.55 (body text < 6.6px)".
   * Why node count is wrong: 137 steps vs 47 steps differs not in "how many nodes" but in "can you still
   * read this layout".
   * Measured: 137 expanded optimal 0.144, 47 expanded optimal 0.305 -- both < 0.55, both should fold;
   *       while a 4-column demo graph at 0.879 >= 0.55 must never fold. */
  const expandedPack = React.useMemo<Pack | null>(() => {
    if (!baseNodes.length || !canvas.w) return null;
    return optimalSplit(buildColumns(baseNodes), canvas, GAP_X, GAP_Y);
  }, [baseNodes, canvas]);

  /* ───────── Auto-fold large graphs (done automatically only once per graph) ─────────
   * After the user manually clicks "Expand all" it stays out of the way (autoFoldKey already set).
   * ★ Must wait until both baseNodes and canvas are ready:
   *   · computing with a 0-width container -> always "does not fit" -> every graph folds (worse than not folding)
   *   · computing with the previous graph's nodes -> writes old parent ids into collapsed
   *     (shardkit pitfall 5)*/
  const autoFoldKey = React.useRef("");
  React.useEffect(() => {
    if (!planId || !parentIds.length || !expandedPack) return;
    if (autoFoldKey.current === planId) return;
    autoFoldKey.current = planId;
    if (expandedPack.zoom < READABLE_ZOOM) setCollapsed(new Set(parentIds));
  }, [planId, parentIds, expandedPack]);

  /* ───────── Collapse relayout + inject collapsed state ─────────
   * A collapsed parent no longer occupies the child-task column -> the trunk tightens automatically
   * (otherwise 17 steps spread to 8120px, after fitView nodes become 20px squares, text unreadable).
   * When everything is collapsed, compute the **optimal rows/columns** from the canvas (left-aligned wrap),
   * filling one screen with the phase columns.
   *
   * [2026-11-03 port of shardkit study / decision 1+2]
   *   · column gap 80->48 (step pitch 320->288), row gap 68->36 (row pitch 160->128) -- tighter gaps are
   *     the right answer to "fill the screen"
   *   · wrapping is always **left-aligned**, no boustrophedon (serpentine saves 7% edge length but creates
   *     reversed edges = a flow that semantically goes backwards)
   *   · drop the hardcoded PER_ROW=5: row count is computed live by optimalSplit from the canvas
   *   · outward step pitch of an expanded column = 300 (parent->child offset) + 200 (child card width)
   *     + 48 (gap) = 548, not 300+240 -- child card measured 200 (step-node.tsx: isChild ? w-[200px])*/
  const visNodes = React.useMemo(() => {
    const visible = baseNodes
      .filter((n) => !hidden.has(n.id))
      .map((n) =>
        (n.data.childIds?.length ?? 0) > 0
          ? { ...n, data: { ...n.data, collapsed: collapsed.has(n.id), onToggleCollapse: toggleCollapse } }
          : n,
      );

    const mainline = visible
      .filter((n) => n.position.y === 0)
      .sort((a, b) => a.position.x - b.position.x);

    const allFolded = parentIds.length > 0 && collapsed.size >= parentIds.length;

    const STEP_PLAIN = 240 + GAP_X; // 288 (card width 240 + column gap 48)
    const STEP_WITH_KIDS = 300 + 200 + GAP_X; // 548
    const ROW_STEP = 92 + GAP_Y; // 128

    const newPos = new Map<string, { x: number; y: number }>();

    /* All collapsed -> let the algorithm compute optimal rows/columns from the canvas (left-aligned),
     * instead of hardcoding PER_ROW=5.
     *
     * ★ 2026-11-03 add an "is wrapping worth it" gate (equivalent of the study's condition ③
     *   "enable only when columns >= 8"):
     *   wrap only when "a single row is already unreadable" (single-row zoom < 0.55).
     *   Why not just write cols>=8: that 8 is empirical for a 1182px container, wrong once the window
     *   differs.
     *   Single-row zoom < 0.55 expresses the same thing and shares one concept with the fold gate, one number.
     *   Measured comparison:
     *     4 columns -> single row 0.956 >= 0.55 -> no wrap (splitting into 3+1 is only 10% bigger and adds
     *       a cross-row edge, not worth it)
     *     7 columns -> single row 0.536 < 0.55 -> wrap
     *     17 columns -> single row 0.218 < 0.55 -> wrap (-> 0.801, same as shardkit production)*/
    const allCols = allFolded && canvas.w ? buildColumns(visible) : [];
    const singleRowZoom = allCols.length
      ? evalSplit(allCols, [allCols.map((_, i) => i)], canvas, GAP_X, GAP_Y).zoom
      : 1;
    const grid = allCols.length > 1 && singleRowZoom < READABLE_ZOOM
      ? (() => {
          const pack = optimalSplit(allCols, canvas, GAP_X, GAP_Y);
          return { pack, origin: placeColumns(allCols, pack, "left", GAP_X, GAP_Y) };
        })()
      : null;

    let cursorX = 0;
    let row = 0;

    mainline.forEach((n, i) => {
      const isCollapsed = collapsed.has(n.id);
      const kids = visible.filter((k) => (n.data.childIds ?? []).includes(k.id));
      const expanded = kids.length > 0 && !isCollapsed;

      if (grid) {
        /* Grid state: origin of column index i comes from the algorithm; nodes translate rigidly by in-column offset */
        const o = grid.origin.get(i);
        if (o) {
          const dx = o.x - n.position.x;
          const dy = o.y - n.position.y;
          newPos.set(n.id, { x: o.x, y: o.y });
          if (expanded) kids.forEach((k) => newPos.set(k.id, { x: k.position.x + dx, y: k.position.y + dy }));
          return;
        }
      }

      /* Not-fully-collapsed state: keep a single horizontal row, only tighten the gap to 48 */
      const step = expanded ? STEP_WITH_KIDS : STEP_PLAIN;
      const baseY = row * ROW_STEP;
      newPos.set(n.id, { x: cursorX, y: baseY });
      if (expanded) {
        kids.forEach((k) => newPos.set(k.id, { x: cursorX + 300, y: baseY + k.position.y }));
      }
      cursorX += step;
    });

    return visible.map((n) => {
      const p = newPos.get(n.id);
      return p ? { ...n, position: p } : n;
    });
  }, [baseNodes, hidden, collapsed, toggleCollapse, parentIds, canvas]);

  /* Optimal zoom of the currently visible grid -- the sole criterion of the positioning strategy:
   * >= 0.55 -> fitView the whole graph (readable on one screen); < 0.55 -> fall back to the "readable
   * landing point" (first column + zoom 0.8).
   * Replaces the old `visible > 80` gut-feel threshold. */
  const currentZoom = React.useMemo(() => {
    if (!canvas.w || !canvas.h || !visNodes.length) return 1;
    return optimalSplit(buildColumns(visNodes), canvas, GAP_X, GAP_Y).zoom;
  }, [visNodes, canvas]);

  /* Main-chain columns: each column = one main-chain node + its child tasks. The "readable landing point" takes the first column from it. */
  const columns = React.useMemo(() => {
    const byId = new Map(visNodes.map((n) => [n.id, n]));
    /* ★ 2026-11-03: main-chain detection must not use `position.y === 0`.
     *   multi-row grid layout shifts columns from row 2 down (y=128/256...),
     *   so "every column outside the first row" is not main chain -> they neither form columns nor are
     *   ever anything but a spare for columns[0].
     *   If the anchor falls in those rows, column lookup always fails, falls back to the first column ->
     *   measured anchor pushed 1107px off-screen.
     *   The structural criterion (not a child of anyone) is independent of layout, holds no matter how many rows. */
    const isChildOf = new Set<string>();
    for (const n of visNodes) for (const c of (n.data.childIds as string[] | undefined) ?? []) isChildOf.add(c);
    const mainline = visNodes
      .filter((n) => !isChildOf.has(n.id))
      .sort((a, b) => a.position.x - b.position.x || a.position.y - b.position.y);
    const claimed = new Set(mainline.map((m) => m.id));
    /* ★ 2026-11-03: previously only **direct** children were recognized, grandchildren fell outside the columns ->
     *   so "anchor is in some column" was not found, fell back to columns[0], while the anchor was at the
     *   other end of the canvas, measured Δx=1107px (content left, anchor right).
     *   Changed to recurse along childIds to collect the whole subtree (a book pipeline's
     *   parent->child->grandchild shape is exactly this). */
    const collect = (id: string, acc: string[]) => {
      for (const k of (byId.get(id)?.data.childIds as string[] | undefined) ?? []) {
        if (claimed.has(k) || acc.includes(k) || !byId.has(k)) continue;
        acc.push(k);
        collect(k, acc);
      }
    };
    return mainline.map((m) => {
      const ids = [m.id];
      collect(m.id, ids);
      return { id: m.id, ids };
    });
  }, [visNodes]);
  const columnsRef = React.useRef(columns);
  React.useEffect(() => { columnsRef.current = columns; }, [columns]);

  const visEdges = React.useMemo(
    () => baseEdges.filter((e) => !hidden.has(e.source) && !hidden.has(e.target)),
    [baseEdges, hidden],
  );

  /* ───────── Overlay order of the three views ─────────
   * 1) live status (change data.status/active/done, positions untouched)
   * 2) demo playback (change active/done, change edge colors)
   * 3) hover tracing (change only edge opacity/accent color)
   * ★ With real status present, demo playback does not run by default -- real status is "where we are now". */
  const liveApplied = React.useMemo(
    () => (liveOn && live ? applyLiveStatus(visNodes, live.statuses) : visNodes),
    [visNodes, liveOn, live],
  );

  const order = React.useMemo(
    () => computeExecutionOrder(liveApplied, visEdges),
    [liveApplied, visEdges],
  );

  const playbackEnabled = playing || follow;
  const { state, idx, total, reset, stepTo } = usePlayback(
    order,
    visEdges,
    playbackEnabled,
    speedMs,
    // Only a graph change resets: collapse / live refresh must not zero it
    `${planId}-${baseNodes.length}`,
  );

  const view = React.useMemo(() => {
    const played = playbackEnabled
      ? applyPlayback(liveApplied, visEdges, state)
      : { nodes: liveApplied, edges: visEdges };
    return { nodes: played.nodes, edges: applyHover(played.edges, hovered) };
  }, [liveApplied, visEdges, playbackEnabled, state, hovered]);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node<StepNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  React.useEffect(() => {
    setNodes(view.nodes);
    setEdges(view.edges);
  }, [view, setNodes, setEdges]);

  /* ───────── Positioning ─────────
   * [2026-11-03 port of shardkit study / decision 4]
   * criteria changed from "visible node count > 80" to "grid optimal zoom < 0.55 (body text < 6.6px)".
   * Why: node count is not readability -- the 47-step graph has only 47 nodes, yet expanded at 0.305 it is
   * just as blurry.
   * Must wait until node sizes are measured before fitView: folding/wrapping swaps in a different node set,
   * fitting early uses the old bbox -> content pushed to the lower half of the canvas, lots of blank on
   * the right. */
  const nodesInitialized = useNodesInitialized();

  /* ───────── Positioning anchor = "the current/latest started step" ─────────
   * ★ 2026-11-03 Feng: after expanding everything, the center should be the step being executed, not the
   *   first node.
   *   His reason: "because they may be parallel" -- with parallelism "the first" says nothing about the present.
   *
   * Selection (three-level fallback, fall back to overview only when all are null):
   *   ① there are running steps -> take the one with the latest startedAt (the last started when parallel)
   *   ② no running but still pending -> take the latest among all ever-started (the one just finished,
   *      viewpoint follows current progress)
   *   ③ everything finished (no running, no pending) -> null -> fall back to overview, do not hard-center
   *      some historical node
   *      Why ③ falls back: for a fully finished plan, centering the "last step" only pushes content off
   *      center; blank on the left becomes blank on the right, the problem just moves to the other side.
   *
   * ★ Only rendered nodes are used (folded hidden ones excluded); an anchor inside a collapsed subtree is
   *   as good as no anchor. */
  const anchorNode = React.useMemo(() => {
    const sa = live?.startedAt ?? {};
    const st = live?.statuses ?? {};
    if (!Object.keys(sa).length) return null;
    const started = nodes.filter((n) => sa[n.id]);
    if (!started.length) return null;
    const latest = (arr: typeof started) =>
      arr.reduce((a, b) => (sa[b.id] > sa[a.id] ? b : a));
    const running = started.filter((n) => st[n.id] === "running");
    if (running.length) return latest(running);
    const stillPending = nodes.some((n) => (st[n.id] ?? "") === "pending" || !st[n.id]);
    if (stillPending) return latest(started);
    return null;
  }, [nodes, live]);
  const anchorRef = React.useRef<Node<StepNodeData> | null>(null);
  React.useEffect(() => { anchorRef.current = anchorNode; }, [anchorNode]);

  /* Fit visible content into the "area available after avoiding floating panels".
   * ★ 2026-11-03 measured: the info panel (top-left, measured 384×84) covers first-column nodes --
   *   the first node rect (343,271) lies entirely inside the panel (261,255->645,339), the user cannot
   *   click that node, and the acceptance script reports "panel intercepts pointer events".
   *   Root cause: React Flow's fitView only does uniform padding, it does not know "there is a panel at top-left".
   *   So compute the available area ourselves: canvas minus the edge insets -> center -> write back to viewport.
   *   (Also sidesteps the size/(1+p) semantics trap of fitView({padding}).)*/
  /* The viewport we last set programmatically ("x,y,zoom"). Usage in the effect below:
   * compare it with the current viewport to know whether the user dragged it themselves -- if so, stop
   * stealing their camera. */
  const fittedVpRef = React.useRef<string>("");
  const { x: vpX, y: vpY, zoom: vpZoom } = useViewport();

  const fitInset = React.useCallback(
    (ids: string[] | null, forcedZoom?: number) => {
      const picked = ids ? nodes.filter((n) => ids.includes(n.id)) : nodes;
      if (!picked.length || !canvas.w || !canvas.h) return;
      const wOf = (n: (typeof picked)[number]) => n.width ?? (n.data.isChild ? 200 : 240);
      const hOf = (n: (typeof picked)[number]) => n.height ?? 92;
      const minX = Math.min(...picked.map((n) => n.position.x));
      const minY = Math.min(...picked.map((n) => n.position.y));
      const maxX = Math.max(...picked.map((n) => n.position.x + wOf(n)));
      const maxY = Math.max(...picked.map((n) => n.position.y + hOf(n)));
      const W = Math.max(1, maxX - minX);
      const H = Math.max(1, maxY - minY);

      const ins = measureInsets();
      const availW = Math.max(160, canvas.w - ins.left - ins.right);
      const availH = Math.max(120, canvas.h - ins.top - ins.bottom);
      let want = forcedZoom ?? Math.min(availW / W, availH / H);
      let box = { minX, minY, W, H };
      let zoom = Math.max(0.12, Math.min(1.1, want));

      /* ★ Fallback (required by 2026-11-03 measurement): the criteria use full canvas size, while here we have the
       *   real available area after measured insets -- the two disagree. Measured on a 6-node graph:
       *   criteria 0.61 (>=0.55, considered fitting), real after insets only 0.46 (body text 5.5px, i.e. mosaic).
       *   It must not be forced here; fall back to the "readable landing point".
       *   This is the gate that must be added once "estimate" and "measurement" are split apart. */
      if (forcedZoom === undefined && zoom < READABLE_ZOOM) {
        /* ★ 2026-11-03: the landing point follows the anchor, no longer always the first column.
         *   For a graph like 137 steps, taking the first column dumps the user at "the beginning", while
         *   what he wants is "where it is now".
         *   Only when the anchor is in no column (or there is no anchor) fall back to the first column.*/
        const a = anchorRef.current;
        const first =
          (a && columnsRef.current.find((c) => c.ids.includes(a.id))) || columnsRef.current[0];
        const f = first ? nodes.filter((n) => first.ids.includes(n.id)) : [];
        if (f.length) {
          const wOf2 = (n: (typeof f)[number]) => n.width ?? (n.data.isChild ? 200 : 240);
          const hOf2 = (n: (typeof f)[number]) => n.height ?? 92;
          const fx0 = Math.min(...f.map((n) => n.position.x));
          const fy0 = Math.min(...f.map((n) => n.position.y));
          const fx1 = Math.max(...f.map((n) => n.position.x + wOf2(n)));
          const fy1 = Math.max(...f.map((n) => n.position.y + hOf2(n)));
          const fW = Math.max(1, fx1 - fx0);
          const fH = Math.max(1, fy1 - fy0);
          box = { minX: fx0, minY: fy0, W: fW, H: fH };
          /* The landing point is not a blind 0.8: width must fit (there is no habit of a horizontal scrollbar here,
           * and it would hit the minimap).
           * ★ 2026-11-03: height is no longer forced to fit -- one column measures 620 high against 303
           *   available; forcing by height squeezes zoom to 0.49 (blurry again), while vertical overflow
           *   can always be handled by centering + panning.
           *   So: width decides zoom, vertical is cropped around the anchor. */
          zoom = Math.max(0.12, Math.min(0.8, availW / fW));
        }
      }

      /* ───── Placement (three intents, priority: fullscreen overview > anchor centering > left-aligned overview) ─────
       * ① fullscreen presentation: projection, watching the whole -> content centered (symmetric blank on
       *    both sides looks right)
       * ② with anchor: put the "latest started / currently executing" step at the center of the available area
       * ③ no anchor: content **left-aligned** (Feng: "if nothing is executing, the first node should sit at
       *    the far left")
       *
       * ★ All three must pass clamp -- otherwise centering an edge node pushes the whole block off screen,
       *   leaving the user with blank space and having to find it back.
       *   clamp range meaning: content fits -> the whole block must stay inside the available area;
       *                        content does not fit -> it must at least cover the available area (no blank edge).*/
      /* Semantics of x/y = "offset of the content's top-left relative to the available area's top-left".
       * So: 0 = flush left/top; avail-size = flush right/bottom; in between = centered.
       * Allowed range: content fits -> [0, avail-size] (the block stays in bounds)
       *                 content does not fit -> [avail-size, 0] (at least fully covered, no blank edge)*/
      const cw = box.W * zoom;
      const ch = box.H * zoom;
      const span = (avail: number, size: number) =>
        size <= avail ? [0, avail - size] : [avail - size, 0];
      const [xMin, xMax] = span(availW, cw);
      const [yMin, yMax] = span(availH, ch);
      const clampSpan = (v: number, lo: number, hi: number) =>
        Math.min(Math.max(v, Math.min(lo, hi)), Math.max(lo, hi));

      const a = anchorRef.current;
      let x: number;
      if (fs) {
        x = (availW - cw) / 2;                       // Centered
      } else if (a) {
        const aw = a.width ?? (a.data.isChild ? 200 : 240);
        x = availW / 2 - (a.position.x + aw / 2 - box.minX) * zoom;
      } else {
        x = xMin;                                    // Left-aligned (0 = flush with the left edge of the available area)
      }
      let y: number;
      if (!fs && a) {
        const ah = a.height ?? 92;
        y = availH / 2 - (a.position.y + ah / 2 - box.minY) * zoom;
      } else {
        y = (availH - ch) / 2;
      }

      /* ★ Do not drop `- box.minX * zoom` (hit this on 2026-11-03):
       *   x/y is the offset of "content top-left relative to the available area", while viewport needs a
       *   world-coordinate translation.
       *   Without this term, columns whose box does not start at 0 get pushed out entirely -- measured
       *   anchor off by 1107px, and columns[0]'s box.minX happens to be 0, so "the first column looks
       *   right", which is even more confusing.*/
      /* ★ clamp is decided per axis (second correction on 2026-11-03):
       *   v1 clamped everything -> a column 620 high against 303 available, y locked, anchor off by 212px,
       *   i.e. not centered at all;
       *   v2 clamped nothing -> an overview graph that does fit got pushed out of the canvas entirely
       *   (measured: first node went to x=123, under the sidebar, unclickable, acceptance timed out).
       *
       *   The correct criterion is **whether this axis fits**:
       *     fits -> clamp to [0, avail-size]: the whole block stays visible, centering uses only the
       *     leftover, nothing is cut;
       *     does not fit -> no clamp: allow panning to the anchor, the overflow needs panning anyway.
       *   Judge each direction separately, because grid graphs often "fit horizontally, not vertically"
       *   (vertical is the bottleneck).*/
      const fitsX = box.W * zoom <= availW + 1;
      const fitsY = box.H * zoom <= availH + 1;
      const vpx = fitsX ? clampSpan(x, xMin, xMax) : x;
      const vpy = fitsY ? clampSpan(y, yMin, yMax) : y;
      const vp = {
        x: Math.round(ins.left + vpx - box.minX * zoom),
        y: Math.round(ins.top + vpy - box.minY * zoom),
        zoom,
      };
      /* Debug outlet (not shown to users; the acceptance script reads it): see at a glance which placement ran
       * this time and who the anchor is.
       * Why it stays in production code: positioning bugs cannot be described with a screenshot, but this
       * field lets you reconcile directly.*/
      (window as unknown as { __fitDebug?: unknown }).__fitDebug = {
        mode: fs ? "fs" : a ? "anchor" : "left",
        anchorId: a?.id ?? null,
        box: { minX: box.minX, minY: box.minY, W: box.W, H: box.H },
        zoom, x: vp.x, y: vp.y, availW, availH, ins, nNodes: nodes.length,
      };
      setViewport(vp, { duration: 300 });
      /* Record "where we last placed it" -- used to tell whether the user dragged afterwards (see the effect below).
       * Recorded only after the 300ms animation ends, otherwise mid-animation frames count as "user moved".*/
      window.setTimeout(() => {
        fittedVpRef.current = `${Math.round(vp.x)},${Math.round(vp.y)},${+vp.zoom.toFixed(3)}`;
      }, 380);
    },
    [nodes, canvas, setViewport, measureInsets],
  );

  /* ───────── When to auto-position (three gates, missing one causes fights) ─────────
   * ★ 2026-11-03 real bug reported by Feng: "after I move it, it immediately jumps back by itself".
   *   The root cause is not the drag, it is **live polling swapping in a new batch of node objects every 3s**:
   *   pull() -> setLive(new objects) -> view.nodes all new -> setNodes() -> React Flow re-measures
   *   -> useNodesInitialized briefly false->true -> the effect below reruns -> fitInset yanks the camera back.
   *   So the criterion cannot be "are nodes ready", it must be "**did the structure really change**".
   *
   * Gate 1 (structure signature): graph change / collapse / fullscreen / grid tier change -- always re-position.
   * Gate 2 (anchor change): re-position only when the anchor changed (moved to the next step).
   * Gate 3 (user first): the user dragged and the structure is unchanged -> only yield when the anchor
   *   changes, stop stealing the camera.
   *   (Detecting "user dragged" = current viewport != the one we last set; do not listen to onMoveStart,
   *    because setViewport itself fires onMoveStart too, so programmatic placement would be misread as
   *    user action.)*/
  const structSig = `${planId}|${baseNodes.length}|${hidden.size}|${[...collapsed].sort().join(",")}|${fs}|${currentZoom.toFixed(3)}`;
  const lastFit = React.useRef({ struct: "", anchor: "" });
  React.useEffect(() => {
    if (!nodesInitialized) return;
    const anchorId = anchorNode?.id ?? "-";
    const structChanged = lastFit.current.struct !== structSig;
    const anchorChanged = lastFit.current.anchor !== anchorId;
    if (!structChanged && !anchorChanged) return;
    if (!structChanged && anchorChanged) {
      // Structure unchanged, only the anchor advanced: if the user already dragged elsewhere, respect that, do not pull back
      const cur = `${Math.round(vpX)},${Math.round(vpY)},${+vpZoom.toFixed(3)}`;
      if (fittedVpRef.current && cur !== fittedVpRef.current) return;
    }
    const t = setTimeout(() => {
      lastFit.current = { struct: structSig, anchor: anchorId };
      if (fs) {
        /* Fullscreen presentation is projection watching the whole -> fill with the entire graph (readability yields to "see all at a glance")*/
        fitInset(null);
      } else if (currentZoom < READABLE_ZOOM) {
        /* Readable content does not fit one screen -> fall back to the "readable landing point": the anchor's column + zoom <=0.8 */
        const a = anchorRef.current;
        const col =
          (a && columnsRef.current.find((c) => c.ids.includes(a.id))) || columnsRef.current[0];
        fitInset(col ? col.ids : null, col ? 0.8 : undefined);
      } else {
        fitInset(null);
      }
    }, 60);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodesInitialized, structSig, anchorNode?.id, vpX, vpY, vpZoom]);

  /* ───────── Auto tracing ───────── */
  const focusId = state.current ?? nodes.find((n) => n.data.active)?.id ?? null;
  React.useEffect(() => {
    if (!follow || !focusId) return;
    const n = nodes.find((x) => x.id === focusId);
    if (!n) return;
    const W = n.width ?? 240, H = n.height ?? 92;
    const z = getZoom();
    const el = document.querySelector(".react-flow") as HTMLElement | null;
    const vw = el?.clientWidth ?? 1200, vh = el?.clientHeight ?? 800;
    setViewport(
      { x: vw / 2 - (n.position.x + W / 2) * z, y: vh / 2 - (n.position.y + H / 2) * z, zoom: z },
      { duration: 500 },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusId, follow]);

  /* ───────── Fullscreen presentation ───────── */
  const toggleFullscreen = React.useCallback(async () => {
    if (!fs) {
      setFs(true);
      try {
        await rootRef.current?.requestFullscreen?.();
      } catch {
        /* Browser refused (no gesture / Safari restriction) -> CSS overlay as fallback */
      }
    } else {
      if (document.fullscreenElement) {
        try {
          await document.exitFullscreen();
        } catch {
          /* ignore */
        }
      }
      setFs(false);
    }
  }, [fs]);

  React.useEffect(() => {
    const onChange = () => {
      if (!document.fullscreenElement) setFs(false);
    };
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  React.useEffect(() => {
    if (!fs) return;
    // ★ capture=true: handle before the canvas/dialog keydown,
    //   otherwise stopPropagation swallows it and "pressing ESC does nothing".
    // ★ Also explicitly exitFullscreen -- setFs(false) alone does not exit **native** fullscreen,
    //   the container stays fullscreen while React thinks it exited, state and screen disagree.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (document.fullscreenElement) {
        void document.exitFullscreen?.().catch(() => {});
      }
      setFs(false);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [fs]);

  const onNodeClick = React.useCallback((_: React.MouseEvent, n: Node<StepNodeData>) => {
    setDetail(n.data.step);
  }, []);

  /* ───────── Stats ───────── */
  const stat = React.useMemo(() => {
    const totalN = nodes.length;
    const done = nodes.filter((n) => n.data.status === "succeeded").length;
    const failed = nodes.filter((n) => n.data.status === "failed").length;
    const running = nodes.filter((n) => n.data.status === "running").length;
    const pending = nodes.filter((n) => n.data.status === "pending" || n.data.status === "draft").length;
    return { totalN, done, failed, running, pending };
  }, [nodes]);

  const runningNodes = React.useMemo(
    () => nodes.filter((n) => n.data.status === "running"),
    [nodes],
  );
  const curStep = state.current ? nodes.find((n) => n.id === state.current)?.data.step : null;
  const focusStep = playbackEnabled ? curStep : runningNodes[0]?.data.step ?? null;

  if (loading) {
    return (
      <div className="flex h-full min-h-[320px] items-center justify-center text-sm text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" /> {t("planFlow.loading")}
      </div>
    );
  }
  if (err) {
    return (
      <div className="flex h-full min-h-[320px] items-center justify-center text-sm text-destructive">
        {t("planFlow.loadFailed", { msg: err })}
      </div>
    );
  }

  return (
    /* In fullscreen, render the overlays (node detail dialog / entity ref dialog) **inside** the fullscreen container;
       non-fullscreen passes null = mount to body, behavior identical to before the change. */
    <PortalContainerProvider container={fs ? fsNode : null}>
    <div
      ref={attachRoot}
      // Use h-full to fill the main content area; switch to a fixed overlay when fullscreen
      className={cn(
        "flex flex-col gap-3",
        fs ? "fixed inset-0 z-50 bg-background p-4" : "h-full min-h-0",
      )}
      data-testid="page-plan-flow"
    >
      {/* Overview */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge variant="outline">{t("planFlow.stat.total", { n: stat.totalN })}</Badge>
        <Badge variant="outline" className="text-success">
          {t("planFlow.stat.done", { n: stat.done })}
        </Badge>
        {stat.running > 0 && (
          <Badge variant="outline" className="text-info">
            {t("planFlow.stat.running", { n: stat.running })}
          </Badge>
        )}
        {stat.failed > 0 && (
          <Badge variant="outline" className="text-destructive">
            {t("planFlow.stat.failed", { n: stat.failed })}
          </Badge>
        )}
        {hidden.size > 0 && (
          <Badge variant="outline" className="text-muted-foreground">
            {t("planFlow.stat.hidden", { n: hidden.size })}
          </Badge>
        )}
        <span className="ml-1 inline-flex items-center gap-1" data-testid="live-indicator">
          {liveOn ? (
            <Radio className={cn("h-3.5 w-3.5", liveErr ? "text-warning" : "text-success")} />
          ) : (
            <RadioTower className="h-3.5 w-3.5 text-muted-foreground" />
          )}
          <span className={cn("text-[11px]", liveErr ? "text-warning" : "text-muted-foreground")}>
            {liveOn
              ? liveErr
                ? t("planFlow.live.interrupted", { msg: liveErr })
                : t("planFlow.live.on", { at: live?.refreshedAt ?? t("planFlow.live.connecting") })
              : t("planFlow.live.off")}
          </span>
        </span>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 gap-1 px-2 text-[11px]"
          onClick={() => void pull()}
          data-testid="live-refresh"
        >
          <RefreshCw className="h-3 w-3" /> {t("planFlow.refresh")}
        </Button>
        {/* [2026-11-03 12:35 Feng] Put the close/back button in the **top-right** (it used to be crammed at the far
            left of the bottom control bar, mixed in with view toggles like "live status/replay/collapse",
            looking nothing like an exit).
            ml-auto pushes it to the end of the row = top-right of the page, the most intuitive place for
            "leave the current page".
            ★ 2026-11-06 10:39 Feng "the flow diagram popup now has two closes in the top-right, a close and
              an X... the system-wide unified close button is that X" -- in dialog mode (embedded) the
              **whole button is not rendered**: the PageDialog shell already has the one and only X in its
              top-right, the function fully overlaps; two of them side by side squeezed into the same corner
              force the user to judge "which one really closes".
              Page mode (non-embedded) keeps "Back" as before -- that is **navigation**, not closing;
              the page has no X at all, so it cannot be deleted along with it. */}
        {!embedded && (
          <Button
            size="sm"
            variant="outline"
            className="ml-auto h-7 gap-1 px-2.5 text-[11px]"
            onClick={() => navigate("/plans")}
            title={t("planFlow.backTitle")}
            data-testid="plan-flow-back"
          >
            <ArrowLeft className="h-3 w-3" /> {t("planFlow.back")}
          </Button>
        )}
      </div>

      {/* Control bar */}
      <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-card px-3 py-2">
        <Button
          size="sm"
          variant={liveOn ? "secondary" : "ghost"}
          className="h-8 gap-1.5"
          onClick={() => setLiveOn((v) => !v)}
          title={t("planFlow.live.toggleTitle")}
          data-testid="live-toggle"
        >
          {liveOn ? <Radio className="h-3.5 w-3.5" /> : <RadioTower className="h-3.5 w-3.5" />}
          {t("planFlow.live.toggle")}
        </Button>

        <span className="mx-0.5 h-5 w-px bg-border" />

        <Button
          size="sm"
          variant="secondary"
          className="h-8 gap-1.5"
          onClick={() => setPlaying((p) => !p)}
          data-testid="playback-toggle"
        >
          {playbackEnabled ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
          {playbackEnabled ? t("planFlow.playback.pause") : t("planFlow.playback.play")}
        </Button>
        <Button size="sm" variant="ghost" className="h-8 gap-1.5" onClick={reset}>
          <RotateCcw className="h-3.5 w-3.5" />
          {t("planFlow.playback.reset")}
        </Button>
        <div className="flex items-center gap-1">
          <Button size="sm" variant="ghost" className="h-8 w-8 p-0" onClick={() => stepTo(idx - 1)}>
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button size="sm" variant="ghost" className="h-8 w-8 p-0" onClick={() => stepTo(idx + 1)}>
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
        <div className="flex min-w-[160px] flex-1 items-center gap-2">
          <Progress value={total ? ((Math.min(idx, total) || 0) / total) * 100 : 0} className="h-1.5" />
          <span className="shrink-0 tabular-nums text-xs text-muted-foreground">
            {Math.min(idx + 1, total)}/{total}
          </span>
        </div>

        {playbackEnabled && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span>{t("planFlow.playback.speed")}</span>
            <Slider
              value={[speedMs]}
              min={500}
              max={6000}
              step={250}
              onValueChange={(v) => setSpeedMs(v[0])}
              className="w-24"
            />
            <span className="tabular-nums">{speedMs}ms</span>
          </div>
        )}

        <Button
          size="sm"
          variant="ghost"
          className="h-8 gap-1.5"
          disabled={parentIds.length === 0}
          onClick={() => setCollapsed(collapsed.size ? new Set() : new Set(parentIds))}
          title={collapsed.size ? t("planFlow.collapse.expandTitle") : t("planFlow.collapse.collapseTitle")}
          data-testid="collapse-all"
        >
          {collapsed.size ? <UnfoldVertical className="h-3.5 w-3.5" /> : <FoldVertical className="h-3.5 w-3.5" />}
          {collapsed.size ? t("planFlow.collapse.expandAll") : t("planFlow.collapse.collapseAll")}
        </Button>

        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Switch checked={follow} onCheckedChange={setFollow} />
          <Crosshair className="h-3.5 w-3.5" />
          {t("planFlow.follow")}
        </label>

        <Button
          size="sm"
          variant="outline"
          className="h-8 gap-1.5"
          onClick={() => void toggleFullscreen()}
          title={fs ? t("planFlow.fullscreen.exitTitle") : t("planFlow.fullscreen.enterTitle")}
          data-testid="fullscreen-toggle"
        >
          {fs ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
          {fs ? t("planFlow.fullscreen.exit") : t("planFlow.fullscreen.enter")}
        </Button>
      </div>

      {/* Main graph */}
      <Card className="min-h-0 flex-1 overflow-hidden">
        <CardContent className="h-full p-0">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={onNodeClick}
            onNodeMouseEnter={(_, n) => setHovered(n.id)}
            onNodeMouseLeave={() => setHovered(null)}
            nodeTypes={nodeTypes}
            minZoom={0.05}
            maxZoom={2}
          >
            <Background gap={16} size={1} />
            <Controls position="top-right" />
            {!fs && <MiniMap pannable zoomable className="!bg-card" />}

            {/* Plan info panel: **kept in fullscreen too** (2026-10-27 Feng: this box disappeared after fullscreen).
                Slightly narrower in fullscreen to avoid covering left-side nodes; MiniMap stays hidden
                (it is a navigation widget and would block content). */}
            <Panel
              position="top-left"
              className={cn("flex flex-col gap-2", fs ? "max-w-[260px]" : "max-w-sm")}
            >
                <div
                  className="rounded-md border bg-card/90 px-3 py-2 text-xs backdrop-blur"
                  data-testid="plan-info-panel"
                >
                  <div className="font-semibold">{graph?.planId}</div>
                  <div className="text-muted-foreground">
                    {t("planFlow.info.nodes", { n: graph?.nNodes ?? 0 })} ·{" "}
                    {t("planFlow.info.edges", { n: graph?.nEdges ?? 0 })} ·{" "}
                    {graph?.description?.slice(0, 40) || "-"}
                  </div>
                  {liveOn && live ? (
                    <div className="mt-0.5 text-[11px] text-success" data-testid="live-counts">
                      {t("planFlow.info.liveCounts", {
                        running: live.counts.running,
                        succeeded: live.counts.succeeded,
                        pending: live.counts.pending,
                      })}
                    </div>
                  ) : graph?.hasRealStatus ? (
                    <div className="mt-0.5 text-[11px] text-muted-foreground">
                      {t("planFlow.info.fromDb")}
                    </div>
                  ) : (
                    <div className="mt-0.5 text-[11px] text-warning">
                      {t("planFlow.info.noRecords")}
                    </div>
                  )}
                </div>
            </Panel>

            {/* Current execution card: in live mode shows the running step; in replay mode shows the playback cursor */}
            <Panel position="bottom-left" className="max-w-xs">
              {focusStep ? (
                <div className="rounded-lg border-2 border-destructive bg-card/95 px-3 py-2.5 shadow-lg backdrop-blur">
                  <div className="flex items-center gap-2 text-xs text-destructive">
                    <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-destructive" />
                    {playbackEnabled
                      ? t("planFlow.focus.replaying", { idx: idx + 1, total })
                      : t("planFlow.focus.running")}
                  </div>
                  <div className="mt-1 text-sm font-semibold">
                    {focusStep.task_name || focusStep.exec_name || focusStep.run_uuid}
                  </div>
                  <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                    {focusStep.run_uuid}
                  </div>
                  <div className="mt-1 line-clamp-3 text-xs text-muted-foreground">
                    {focusStep.instruction || `Agent: ${focusStep.agent_name || focusStep.agent_id}`}
                  </div>
                  {!playbackEnabled && runningNodes.length > 1 && (
                    <div className="mt-1 text-[11px] text-muted-foreground">
                      {t("planFlow.focus.parallel", { n: runningNodes.length - 1 })}
                    </div>
                  )}
                </div>
              ) : null}
            </Panel>

            <Panel
                position="bottom-center"
                className={cn(
                  "mb-1 flex flex-wrap justify-center gap-2 rounded-md border bg-card/90 backdrop-blur",
                  fs ? "gap-1.5 px-2.5 py-1.5 text-[10px]" : "px-3 py-2 text-[11px]",
                )}
                data-testid="plan-legend"
              >
                {LEGEND.map((s) => (
                  <span key={s} className="flex items-center gap-1">
                    <span className={cn("h-2 w-2 rounded-full", STATUS_COLOR[s].dot)} />
                    {statusLabel(s, t)}
                  </span>
                ))}
                <span className="flex items-center gap-1">
                  <span className="inline-block h-0 w-4 border-t-[3px] border-destructive" />
                  {t("planFlow.legend.current")}
                </span>
                <span className="flex items-center gap-1">
                  <span className="inline-block h-0 w-4 border-t-2 border-success" />
                  {t("planFlow.legend.executed")}
                </span>
                <span className="flex items-center gap-1 text-muted-foreground">
                  <span className="inline-block h-3 w-0 border-l border-dashed border-muted-foreground" />
                  {t("planFlow.legend.hierarchy")}
                </span>
              </Panel>
          </ReactFlow>
        </CardContent>
      </Card>

      {/* Node detail -- 2026-11-03 15:13 Feng's second correction:
          what he wants is not "fields laid out in two columns" but **the view of the plan detail page**
          (what you see opening `/plans/:id`).
          That page uses `DetailShell + DetailSection + Descriptions` (a real <table>, label column with a
          light gray background, full grid lines, matching Element Plus Descriptions border mode).
          My first attempt shipped FieldGrid (borderless, 7em labels) -- similar in shape, not in spirit.
          Now the whole window is DetailShell: header (title/badge/actions/close) + sections + tables + raw
          data, the same implementation as the plan detail page, so look and rules can no longer drift.
          Window max-w-5xl (1024), internal scroll when content is taller.*/}
      <Dialog open={!!detail} onOpenChange={(o) => !o && setDetail(null)}>
        {/* [2026-11-05 23:54 Feng "let us unify it"] One close entry system-wide:
            the top-right × of the dialog (built into DialogContent). Content no longer duplicates close buttons. */}
        <DialogContent className="max-w-5xl gap-0 p-0">
          <DialogTitle className="sr-only">{t("planFlow.dialogTitle")}</DialogTitle>
          <div className="max-h-[88vh] overflow-y-auto p-5" data-testid="plan-step-dialog">
            {detail && (() => {
              const st = (live?.statuses?.[detail.run_uuid] as RunStatus) ||
                (detail.run_status as RunStatus) || "pending";
              const sid = detail.session_id || "";
              return (
                <DetailShell
                  backTo="/plans"
                  onClose={() => setDetail(null)}
                  testId="plan-step-detail"
                  title={detail.task_name || detail.exec_name || detail.run_uuid}
                  subtitle={detail.run_uuid}
                  badges={
                    <span className={cn("inline-flex items-center gap-1.5 text-sm font-medium", STATUS_COLOR[st]?.text)}>
                      <span className={cn("h-2 w-2 rounded-full", STATUS_COLOR[st]?.dot)} />
                      {statusLabel(st, t)}
                    </span>
                  }
                >
                  <DetailSection title={t("planFlow.section.basic")} testId="step-section-basic">
                    <Descriptions>
                      <DescriptionsItem label={t("planFlow.field.status")}>
                        <span className={cn("inline-flex items-center gap-1.5 font-medium", STATUS_COLOR[st]?.text)}>
                          <span className={cn("h-1.5 w-1.5 rounded-full", STATUS_COLOR[st]?.dot)} />
                          {statusLabel(st, t)}
                        </span>
                      </DescriptionsItem>
                      {/* ★ 2026-11-03 Feng: "anything that has a relation should pop a dialog when clicked" --
                          navigating from inside the dialog drops the flow diagram context (you must
                          re-position on return).
                          So always use EntityRef here (opens the detail dialog stack), never <Link>.
                          Pass an empty value **directly** to DescriptionsItem (no wrapping span,
                          otherwise its blank check misses it and the cell stays blank instead of `-`). */}
                      <DescriptionsItem label={t("planFlow.field.agent")}>
                        <EntityRef
                          kind="agent"
                          id={detail.agent_id}
                          label={detail.agent_name || detail.agent_id}
                          mono={false}
                        />
                      </DescriptionsItem>
                      <DescriptionsItem label={t("planFlow.field.task")}>
                        <EntityRef
                          kind="task"
                          id={detail.task_id}
                          label={detail.task_name || detail.exec_name || detail.task_id}
                          mono={false}
                        />
                      </DescriptionsItem>
                      <DescriptionsItem label={t("planFlow.field.taskId")}>
                        <EntityRef kind="task" id={detail.task_id} />
                      </DescriptionsItem>
                      <DescriptionsItem label={t("planFlow.field.start")}>
                        {detail.started_at || (detail.scheduled_at ? t("planFlow.scheduled", { at: detail.scheduled_at }) : "") ? (
                          <span className="font-mono text-xs">
                            {detail.started_at || t("planFlow.scheduled", { at: detail.scheduled_at })}
                          </span>
                        ) : ""}
                      </DescriptionsItem>
                      <DescriptionsItem label={t("planFlow.field.end")}>
                        {detail.finished_at ? (
                          <span className="font-mono text-xs">{detail.finished_at}</span>
                        ) : ""}
                      </DescriptionsItem>
                      <DescriptionsItem label={t("planFlow.field.timeout")}>
                        {detail.timeout_seconds ? `${detail.timeout_seconds}s` : ""}
                      </DescriptionsItem>
                      <DescriptionsItem label={t("planFlow.field.children")}>
                        {detail.children?.length
                          ? t("planFlow.childCount", { n: detail.children.length })
                          : ""}
                      </DescriptionsItem>
                      <DescriptionsItem label={t("planFlow.field.prev")}>
                        <EntityRef kind="execution" id={detail.prev_task} />
                      </DescriptionsItem>
                      <DescriptionsItem label={t("planFlow.field.next")}>
                        <EntityRef kind="execution" id={detail.next_task} />
                      </DescriptionsItem>
                      {/* Two session entries (called out by Feng 15:44):
                          ① Open session -> **dialog** (view in place, do not lose the flow diagram)
                          ② Open platform page -> open the OpenClaw session URL directly (new tab)
                             Previously you had to open the dialog first and then click a marker inside to
                             reach the web page, two layers of detour. */}
                      <DescriptionsItem label={t("planFlow.field.session")} span={2}>
                        {sid ? (
                          <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1.5">
                            <SessionRunDot
                              running={sessionRunningById(sid)}
                              testId="plan-step-session-running"
                            />
                            <span className="break-all font-mono text-xs">{sid}</span>
                            <SessionButtons sessionId={sid} agentId={detail.agent_id} />
                          </span>
                        ) : (
                          <span className="text-muted-foreground">{t("planFlow.noSession")}</span>
                        )}
                      </DescriptionsItem>
                    </Descriptions>
                  </DetailSection>

                  <DetailSection title={t("planFlow.section.instruction")} testId="step-section-instruction">
                    <TextBlock>
                      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">
                        {detail.instruction || t("planFlow.empty")}
                      </pre>
                    </TextBlock>
                  </DetailSection>

                  <RawBlock data={detail} label={t("planFlow.rawBlock")} />
                </DetailShell>
              );
            })()}
          </div>
        </DialogContent>
      </Dialog>
    </div>
    </PortalContainerProvider>
  );
}

/* ------------------------------------------------------------------ */
/** Is this session running right now (the session row in step detail also needs a status, Feng 2026-11-13 23:42).
 *  If it cannot be found, treat it as not running -- the same criterion as the send dialog. */
function sessionRunningById(id: string): boolean {
  if (!id) return false;
  const hit = listSessions().find((x) => x.id === id);
  return isSessionRunning(hit?.runtime);
}

/* Two session entries (used inside the step detail dialog)                     */
/* ------------------------------------------------------------------ */
/**
 * [2026-11-03 16:14 Feng] Unify the two "session" things across the system, no two sets of names:
 *   ① local page -> always called "session detail" (the page you enter by clicking the session name)
 *   ② platform page -> always use **that small icon** (`OpenClawSessionLink`),
 *      the same component and the same icon as the session column in the "execution records" list.
 *
 * So the old "open platform page" text button is removed here, leaving only the icon --
 * a button with text makes people think it is another big deal, when it is really "go look at the web page
 * where that session lives".
 *
 * ★ Icon buttons must have title/aria-label: with icon only, that text is their sole explanation.
 *   (The component already carries `title="Open session in OpenClaw · <url>"`, so hover works.)
 */
function SessionButtons({ sessionId, agentId }: { sessionId: string; agentId?: string }) {
  const { t } = useTranslation();
  return (
    <span className="inline-flex shrink-0 flex-wrap items-center gap-1.5" data-testid="step-session-actions">
      {/* Use the same EntityRef as the other relations in the table (Agent/task) -- the same action (open detail
          dialog) should look the same. The entry label is uniformly "session detail". */}
      <span data-testid="step-open-session">
        <EntityRef kind="session" id={sessionId} label={t("planFlow.sessionDetail")} mono={false} />
      </span>
      <OpenClawSessionLink sessionId={sessionId} agentId={agentId} />
    </span>
  );
}
