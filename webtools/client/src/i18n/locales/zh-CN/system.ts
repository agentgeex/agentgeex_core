/** System status dashboard (/system) */
export default {
  refreshFailed: "看板刷新失败",

  live: {
    label: "实时状态",
    offline: "实时已关",
    connecting: "连接中",
    on: "实时 · {{at}}",
    interrupted: "实时中断：{{err}}",
    every: "每 {{seconds}}s 从服务端聚合接口刷新",
  },

  host: {
    title: "主机",
    summary:
      "共 {{total}} 台 · 在线 {{online}} · 合计 Agent {{agents}} / 会话 {{sessions}}（在线 {{live}}）/ 执行 {{executions}}{{slots}}",
    slots: " · 槽位 {{n}}",
    empty: "暂无主机统计（接口未返回 hostStats 块）",
    disabled: "停用",
    viewAgents: "查看 {{host}} 上的 {{n}} 个 Agent",
    pendingDeploy: "{{n}} 个 Agent 部署未完成",
    viewSessions: "查看 {{host}} 上的 {{n}} 个会话",
    sessions: "会话",
    viewExecutions: "查看 {{host}} 上的 {{n}} 条任务执行记录",
    executions: "执行",
    executionsHint:
      "任务执行记录 {{n}} 条（agx_task_execution，含历史）\n不是任务模板数（agx_task_base）—— 模板只有几百个。",
    slotsHint: "运行中 {{running}} / 槽位上限 {{slots}}\n这是这台机器同时能跑的并发执行数，不是累计量。",
    slotsLabel: "槽位",
  },

  presence: {
    unknown: "无心跳记录",
    unknownHint: "该主机从未上报心跳",
    online: "在线",
    lost: "失联",
    offline: "离线",
    lastHeartbeat: "最后心跳 {{rel}}",
    lostHint: "最后心跳 {{rel}}（超过 90 秒未上报）",
    justNow: "刚刚",
    minutesAgo: "{{n}} 分钟前",
    hoursAgo: "{{n}} 小时前",
    daysAgo: "{{n}} 天前",
  },

  projects: {
    title: "项目概览",
    active: "活跃",
    archived: "归档",
    archivedLong: "已归档",
    total: "合计",
    all: "全部",
    healthRatio: "活跃占比",
  },

  plans: {
    title: "计划执行情况",
    nextRun: "下次计划执行",
    status: {
      activated: "进行中",
      completed: "已完成",
      cancelled: "已取消",
      draft: "草稿",
      cancelling: "取消中",
      archived: "已归档",
    },
  },

  agents: {
    title: "智能体状态",
    totalCount: "（共 {{n}} 个）",
    status: {
      online: "在线",
      busy: "忙碌",
      offline: "离线",
      error: "异常",
    },
  },

  tasks: {
    title: "任务执行记录",
    last24h: "（近 24h 成功 {{ok}} · 失败 {{fail}}）",
    pending: "排队中",
    pendingSub: "计划 {{queued}} · 定时 {{scheduled}}",
    status: {
      running: "运行中",
      awaiting_confirm: "待人类确认",
      succeeded: "已成功",
      failed: "失败",
      cancelled: "已取消",
      archived: "已归档",
      draft: "草稿",
    },
  },

  sessions: {
    active: "活跃会话",
    rank: "任务执行排行",
    rankHint: "近 7 日 · 各 Agent 被执行的任务数 · 前 6 名",
    liveTitle: "会话实时状态",
    liveSummary: "{{total}} 个网关侧会话（去重 {{unique}} 个 ID{{conflicts}}{{backend}}）",
    conflicts: " · {{n}} 条跨主机重名隔离",
    redisFail: " · Redis 读取失败",
    noLiveData: "暂无网关实时数据（采集器未上报或 Redis 不可达）",
    states: "会话状态",
    count: "共 {{n}}",
    viewLabel: "查看「{{label}}」的会话（卡片 {{n}} 个）",
    hostDist: "会话主机分布",
    hostCount: "{{n}} 台",
    hostTip: "{{host}}：{{total}} 个会话{{running}}{{failed}}（点开看会话列表）",
    hostRunning: " · 运行中 {{n}}",
    hostFailed: " · 失败 {{n}}",
    ctxTitle: "上下文水位",
    ctxHint: "占上限比例",
    ctxView: "查看上下文水位 {{label}} 的会话（卡片 {{n}} 个）",
    ctxTop5: "上下文占用最高 5 个",
    ctxTopHint: "占比 = 已用 / 上限",
    none: "暂无",
    openSession: "打开会话详情：{{name}}\n{{agent}} · {{host}}",
    execHistory: "查看「{{name}}」近 7 日的任务执行记录（{{n}} 条）",
    pointHint: "{{label}} · {{hint}}{{failed}}",
  },

  trend: {
    title: "近 7 日任务执行趋势",
    hint: "每天被派发的任务数",
    runs: "执行数",
    roleDist: "智能体角色分布",
  },
} as const;
