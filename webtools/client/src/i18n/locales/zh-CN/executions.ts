/** Agent management · task executions (/executions) */
export default {
  unregisteredProject: "未登记项目（{{id}}）",
  agentLinkTitle: "{{name}}\n{{id}}\n（点开查看该智能体详情）",
  unknownAgent: "（未知）",

  nudge: {
    rateLimited: "同一任务 60 秒内只能催一次，稍等再点。",
    stateMismatch: "这条任务的状态已经不需要这个动作了（可能刚刚结束或被别人处理过）。",
    notFound: "数据库里找不到这条执行记录。",
    kernelNotReady: "内核方法还没上线，界面已就绪 —— 需要等内核提供后才能真的发出去。",
    permissionDenied: "操作身份没有权限。",
    noteUrge: "控制台人工催办",
    noteAck: "控制台人工催签收",
    sentUrge: "已催办",
    sentAck: "已催签收",
    sentDesc: "已发给 {{agent}}{{via}}，已催 {{n}} 次",
    sentVia: "（{{target}}）",
    rateTitle: "太频繁了，稍后再试",
    failTitle: "没能发出去",
  },

  selfSent: "自发",
  columns: {
    task: "任务",
    agentPair: "发送方 → 接收方",
    session: "会话",
    status: "状态",
    started: "开始 / 耗时",
  },

  scheduled: {
    chip: "定时",
    title: "定时任务 · 预定执行 {{at}}",
    notStarted: "定时 · 未开始",
  },

  actions: {
    confirmTitle: "执行方已干完，等你放行或驳回",
    approve: "放行",
    none: "现在没有可催的动作",
    nudged: "已催 {{n}} 次",
    refreshed: "已刷新这行的最新状态，看看还要不要处理。",
    selfApproveForbidden: "内核禁止执行方给自己放行，需由其他人操作。",
    lastNudge: "最近一次：{{at}}",
  },

  filters: {
    searchPlaceholder: "搜索 run_uuid / 任务 / 智能体…",
    status: "状态",
    trigger: "触发方式",
    triggerScheduled: "定时",
    triggerImmediate: "立即",
    project: "项目",
    range: "执行时间",
    range7d: "近 7 日",
    range30d: "7–30 日",
    rangeOlder: "30 日以上",
    rangeUnknown: "无创建时间",
    agent: "智能体",
  },
} as const;
