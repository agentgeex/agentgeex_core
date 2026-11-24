/**
 * Plan flow graph (/plans/:planId/flow)
 * Run statuses come from the global `status.*` namespace (same words as boards and runs).
 */
export default {
  missingPlanId: "Missing planId",
  loading: "Loading plan graph…",
  loadFailed: "Could not load: {{msg}}",

  stat: {
    total: "{{n}} steps",
    done: "{{n}} done",
    running: "{{n}} running",
    failed: "{{n}} failed",
    hidden: "{{n}} collapsed",
  },

  live: {
    interrupted: "Live feed interrupted: {{msg}}",
    on: "Live · {{at}}",
    connecting: "connecting",
    off: "Live off",
    toggleTitle: "Live status comes from the kernel Redis hot cache (every 3s)",
    toggle: "Live status",
  },

  refresh: "Refresh",
  backTitle: "Close the graph and return to the plan list",
  back: "Back",

  playback: {
    pause: "Pause replay",
    play: "Replay demo",
    reset: "Reset",
    speed: "Speed",
  },

  collapse: {
    expandAll: "Expand all",
    collapseAll: "Collapse all",
    expandTitle: "Expand all subtasks",
    collapseTitle: "Collapse all subtasks (expanded by default)",
  },

  follow: "Auto follow",
  fullscreen: {
    exitTitle: "Exit fullscreen (ESC)",
    enterTitle: "Fullscreen demo (hide the page shell)",
    exit: "Exit fullscreen",
    enter: "Fullscreen",
  },

  info: {
    nodes: "{{n}} nodes",
    edges: "{{n}} edges",
    liveCounts: "✓ Live: {{running}} running / {{succeeded}} done / {{pending}} pending",
    fromDb: "Run state comes from agx_task_execution (live off)",
    noRecords: "⚠ This plan has no run records yet — use “Replay demo” to walk the flow",
  },

  focus: {
    replaying: "Replaying {{idx}}/{{total}}",
    running: "Running",
    parallel: "{{n}} more running in parallel",
    agent: "Agent: {{name}}",
  },

  legend: {
    current: "Current path",
    executed: "Executed path",
    hierarchy: "Hierarchy (vertical) / dashed = order (horizontal)",
  },

  dialogTitle: "Step details",
  section: { basic: "Step info", instruction: "Instruction" },

  field: {
    status: "Status",
    agent: "Agent",
    task: "Task",
    taskId: "Task ID",
    start: "Start",
    end: "End",
    timeout: "Timeout",
    children: "Subtasks",
    prev: "Previous",
    next: "Next",
    session: "Session",
  },

  scheduled: "Scheduled {{at}}",
  childCount: "{{n}}",
  noSession: "Not dispatched (no session yet)",
  empty: "(none)",
  rawBlock: "Raw data (step)",
  sessionDetail: "Session details",

  node: {
    expandChildren: "Expand subtasks",
    collapseChildren: "Collapse subtasks",
    children: "{{n}} sub",
    running: "Running…",
    collapsed: "Collapsed",
  },

  sub: {
    section: "section {{n}}",
    chapterRound: "ch{{ch}} · round {{n}}",
    round: "round {{n}}",
  },
} as const;
