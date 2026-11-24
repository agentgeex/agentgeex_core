/**
 * MCP self-check (service settings tab / legacy dialog)
 */
export default {
  status: { ok: "Healthy", abnormal: "{{status}}", abnormalFallback: "Error" },

  cache: {
    project: "Projects",
    agent: "Agents",
    task: "Tasks",
    execution: "Runs",
    session: "Sessions",
    plan: "Plans",
    host: "Hosts",
    notice: "Notices",
    group: "Teams",
    skill: "Skills",
  },

  dirty: {
    nogit: "no git (mtime only)",
    dirty: "working tree dirty",
    clean: "git clean",
    unknown: "unknown",
  },

  freshness: {
    unknown: "Freshness: cannot tell",
    stale: "Running old code",
    fresh: "Running the latest code",
    codeChanged: "Code changed",
    procStarted: "Process started",
    restartHint: "Run",
    restartHintTail: " then click “Re-check”.",
  },

  loading: "Querying the MCP version…",

  error: {
    title: "Cannot get the MCP version",
    desc: "Usually means the MCP gateway behind the BFF is down (the address on the “MCP channel” row) — not “no version”, but “cannot ask”.",
  },

  build: {
    title: "Build fingerprint (automatic, follows code)",
    codeTime: "Code time",
    dirtyNote: "The working tree has uncommitted changes — production is not running HEAD; this hash only covers what was committed.",
  },

  release: {
    title: "Release note (hand-written)",
    note: "This is written by hand and may not track code changes — read the two rows above to judge freshness.",
  },

  row: {
    checkedAt: "Fetched at",
    checkedAtNote: "(when this query ran, not when the process started)",
    channel: "MCP channel",
    checkedBy: "Checked as",
  },

  connectivity: "Kernel connectivity",
  permission: { title: "Permissions", updatedAt: "Updated {{at}}" },
  cacheTitle: "Cache entries",

  scope: {
    line1: "This only covers the gateway process shown on the “MCP channel” row",
    port: " ({{port}})",
    line2: "Since MCP was unified on 2026-11-04, the BFF and the agents connect to the same gateway, so this conclusion holds for both.",
    line3: "It only covers the kernel code inside this gateway process; daemon / ws_client / watchdog are separate processes and their code freshness is not visible here.",
  },

  refresh: "Re-check",
  dialogTitle: "MCP self-check",
  close: "Close",
} as const;
