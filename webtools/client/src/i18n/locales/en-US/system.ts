/** System status dashboard (/system) */
export default {
  refreshFailed: "Failed to refresh dashboard",

  live: {
    label: "Live",
    offline: "Live off",
    connecting: "Connecting",
    on: "Live · {{at}}",
    interrupted: "Live interrupted: {{err}}",
    every: "Refresh from the aggregated endpoint every {{seconds}}s",
  },

  host: {
    title: "Hosts",
    summary:
      "{{total}} hosts · {{online}} online · {{agents}} agents / {{sessions}} sessions ({{live}} live) / {{executions}} runs{{slots}}",
    slots: " · {{n}} slots",
    empty: "No host stats (the endpoint returned no hostStats block)",
    disabled: "Disabled",
    viewAgents: "View {{n}} agents on {{host}}",
    pendingDeploy: "{{n}} agents not fully deployed",
    viewSessions: "View {{n}} sessions on {{host}}",
    sessions: "Sessions",
    viewExecutions: "View {{n}} runs on {{host}}",
    executions: "Runs",
    executionsHint:
      "{{n}} run records (agx_task_execution, including history)\nnot the number of task templates (agx_task_base) — those number in the hundreds.",
    slotsHint: "{{running}} running / {{slots}} slot limit\nThis is how many runs the machine can execute concurrently, not a total.",
    slotsLabel: "Slots",
  },

  presence: {
    unknown: "No heartbeat",
    unknownHint: "This host has never reported a heartbeat",
    online: "Online",
    lost: "Unreachable",
    offline: "Offline",
    lastHeartbeat: "Last heartbeat {{rel}}",
    lostHint: "Last heartbeat {{rel}} (no report for over 90s)",
    justNow: "just now",
    minutesAgo: "{{n}} min ago",
    hoursAgo: "{{n}} h ago",
    daysAgo: "{{n}} d ago",
  },

  projects: {
    title: "Projects",
    active: "Active",
    archived: "Archived",
    archivedLong: "Archived",
    total: "Total",
    all: "All",
    healthRatio: "Active ratio",
  },

  plans: {
    title: "Plans",
    nextRun: "Next scheduled run",
    status: {
      activated: "Running",
      completed: "Completed",
      cancelled: "Cancelled",
      draft: "Draft",
      cancelling: "Cancelling",
      archived: "Archived",
    },
  },

  agents: {
    title: "Agents",
    totalCount: "({{n}} total)",
    status: {
      online: "Online",
      busy: "Busy",
      offline: "Offline",
      error: "Error",
    },
  },

  tasks: {
    title: "Runs",
    last24h: "(last 24h: {{ok}} succeeded · {{fail}} failed)",
    pending: "Queued",
    pendingSub: "planned {{queued}} · scheduled {{scheduled}}",
    status: {
      running: "Running",
      awaiting_confirm: "Awaiting approval",
      succeeded: "Succeeded",
      failed: "Failed",
      cancelled: "Cancelled",
      archived: "Archived",
      draft: "Draft",
    },
  },

  sessions: {
    active: "Active sessions",
    rank: "Execution ranking",
    rankHint: "Last 7 days · runs per agent · top 6",
    liveTitle: "Live session state",
    liveSummary: "{{total}} gateway-side sessions ({{unique}} unique IDs{{conflicts}}{{backend}})",
    conflicts: " · {{n}} cross-host name collisions isolated",
    redisFail: " · Redis read failed",
    noLiveData: "No live gateway data (collector silent or Redis unreachable)",
    states: "Session states",
    count: "{{n}} total",
    viewLabel: "View sessions for \"{{label}}\" ({{n}} on the card)",
    hostDist: "Sessions by host",
    hostCount: "{{n}} hosts",
    hostTip: "{{host}}: {{total}} sessions{{running}}{{failed}} (click to open the list)",
    hostRunning: " · {{n}} running",
    hostFailed: " · {{n}} failed",
    ctxTitle: "Context usage",
    ctxHint: "Share of the limit",
    ctxView: "View sessions with context usage {{label}} ({{n}} on the card)",
    ctxTop5: "Top 5 by context usage",
    ctxTopHint: "ratio = used / limit",
    none: "None",
    openSession: "Open session: {{name}}\n{{agent}} · {{host}}",
    execHistory: "View the last 7 days of runs for \"{{name}}\" ({{n}})",
    pointHint: "{{label}} · {{hint}}{{failed}}",
  },

  trend: {
    title: "Runs over the last 7 days",
    hint: "Runs dispatched per day",
    runs: "Runs",
    roleDist: "Agents by role",
  },
} as const;
