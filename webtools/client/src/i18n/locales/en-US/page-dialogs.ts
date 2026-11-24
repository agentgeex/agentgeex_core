/**
 * Dialog titles/subtitles opened from dashboard and system-status numbers
 */
export default {
  hostSessions: " · sessions",
  hostSessionsCount: " · sessions (host card says {{count}})",
  hostExecutions: " · executions",
  hostExecutionsCount: " · executions (host card says {{count}})",

  agentExec: "{{name}} · executions",
  agentExecSub: "Last 7 days",
  agentExecSubCount: "Last 7 days (leaderboard card says {{count}})",

  runtimeSessions: "Sessions · {{label}}",
  runtimeSessionsSub: "Card shows {{count}} (gateway side) · the list is registered sessions in this state",
  contextSessions: "Sessions · context {{label}}",
  contextSessionsSub: "Card shows {{count}} (gateway side) · the list is registered sessions in this bucket",

  agentSessions: "{{name}} · sessions",
  agentSessionsSub: "(card says {{count}})",
  agentTasks: "{{name}} · tasks",
  agentTasksSub: "(card says {{count}})",
  agentRuns: "{{name}} · executions",
  agentRunsSub: "(card says {{count}})",

  execStatus: "Executions · {{label}}",
  execStatusSub: "(status {{status}})",
  execStatusSubCount: "(status {{status}} · dashboard says {{count}})",

  planStatus: "Plans · {{label}}",
  planStatusSub: "(status {{status}})",
  planStatusSubCount: "(status {{status}} · dashboard says {{count}})",

  agentStatus: "Agents · {{label}}",

  projectStatus: "Projects · {{label}}",

  activeSessions: "Sessions",
  activeSessionsSub: "({{count}} active on the dashboard · gateway side)",
} as const;
