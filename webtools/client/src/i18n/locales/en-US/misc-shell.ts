/**
 * Shell strings: tab bar / data table view switch / lock screen / human approval / long-text viewer
 */
export default {
  tabBar: {
    refreshed: "Refreshed",
    updatedAt: "Data updated at {{time}}",
    refreshFailed: "Refresh failed",
    backendDown: "Backend unavailable",
    refreshHint: "{{label}} (reload the current page's data)",
  },
  dataTable: {
    viewSwitch: "Switch view",
    tableView: "Table view",
    cardView: "Card view",
  },
  lockScreen: {
    needPassword: "Enter the lock password",
    wrongPassword: "Wrong password — try again",
    title: "Lock screen",
    password: "Lock password",
    lock: "Lock",
    unlockPlaceholder: "Enter the lock password to unlock",
    unlock: "Unlock",
  },
  humanConfirm: {
    waiting: "This task is waiting for your approval",
    body: "The runner has finished; the result is neither marked succeeded nor failed — a human decides.",
    fTask: "Task",
    fAgent: "Runner",
    resultTitle: "Result returned by the runner",
    noSummary: "(this execution has no result summary)",
    consequence: "Approve → it becomes succeeded and downstream tasks start immediately; reject → it becomes failed and a failure receipt goes to the requester. Neither action can be undone.",
    later: "Later",
    reject: "Reject",
    approve: "Approve",
  },
  richText: {
    empty: "(no content)",
    copyAll: "Copy all",
    expand: "Expand to read the full content",
    fullTitle: "Full content",
  },

  pageDialog: {
    refreshFailedCached: "Refresh failed — showing the local cache",
    refreshFailed: "Refresh failed",
    refreshAria: "Refresh data",
    refreshTitle: "Refresh to the latest data",
    loadingLatest: "Fetching the latest data…",
  },
  quickStar: {
    failed: "Could not save the bookmark",
    tipOn: "Remove from quick access",
    tipOff: "Add to quick access",
  },
  detailShell: {
    backLabel: "Back to list",
    rawData: "Raw data",
  },
  inputNumber: {
    decrease: "Decrease",
    increase: "Increase",
  },
  pagination: {
    total: "{{count}} rows",
    perPage: "Rows",
    rows: "rows",
    prevPage: "Previous page",
    nextPage: "Next page",
  },
  agentsDialog: {
    agentsOf: " · agents",
    countSuffix: " (host card says {{count}})",
  },
  openclaw: {
    openIn: "Open in OpenClaw",
    openOpenclaw: "Open OpenClaw",
    open: "Open",
  },
  shell: {
    buildStamp: "Frontend build fingerprint (a change means it was rebuilt — hard-refresh)",
  },
  tabs: {
    systemStatus: "System status",
  },
  close: "Close",
  noData: "No data",
  auth: {
    loginFailed: "Sign-in failed",
    badCredentials: "Wrong username or password",
  },

  jsonView: { copy: "Copy" },
  markdownView: { empty: "(no content)" },
  toast: { close: "Dismiss notification" },
  breadcrumb: { label: "Breadcrumb" },
  appTitle: "AgentGeeX — Agent Management Console",
  flow: "Flow",
} as const;
