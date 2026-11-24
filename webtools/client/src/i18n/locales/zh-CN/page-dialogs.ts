/**
 * Titles and subtitles of the dialogs opened from system status / dashboard numbers (all identical in shape)
 */
export default {
  hostSessions: "的会话",
  hostSessionsCount: "的会话（主机卡写 {{count}} 个）",
  hostExecutions: "的任务执行记录",
  hostExecutionsCount: "的任务执行记录（主机卡写 {{count}} 条）",

  agentExec: "{{name}} · 任务执行记录",
  agentExecSub: "近 7 日",
  agentExecSubCount: "近 7 日（排行卡写 {{count}} 条）",

  runtimeSessions: "会话 · {{label}}",
  runtimeSessionsSub: "卡片 {{count}} 个（网关侧）· 列表为已注册会话中带此状态的",
  contextSessions: "会话 · 上下文 {{label}}",
  contextSessionsSub: "卡片 {{count}} 个（网关侧）· 列表为已注册会话中落在此档的",

  agentSessions: "{{name}} · 会话",
  agentSessionsSub: "（卡片写 {{count}} 个）",
  agentTasks: "{{name}} · 任务",
  agentTasksSub: "（卡片写 {{count}} 个）",
  agentRuns: "{{name}} · 执行记录",
  agentRunsSub: "（卡片写 {{count}} 条）",

  execStatus: "任务执行记录 · {{label}}",
  execStatusSub: "（状态 {{status}}）",
  execStatusSubCount: "（状态 {{status}} · 看板写 {{count}} 条）",

  planStatus: "计划 · {{label}}",
  planStatusSub: "（状态 {{status}}）",
  planStatusSubCount: "（状态 {{status}} · 看板写 {{count}} 个）",

  agentStatus: "智能体 · {{label}}",

  projectStatus: "项目 · {{label}}",

  activeSessions: "会话",
  activeSessionsSub: "（看板活跃 {{count}} 个 · 网关口径）",
} as const;
