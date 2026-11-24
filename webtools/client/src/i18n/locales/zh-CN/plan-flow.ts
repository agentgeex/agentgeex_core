/**
 * Plan flow chart (/plans/:planId/flow)
 * ★ Status words come from the global `status.*` (same set as the dashboard and executions); only flow-specific copy lives here.
 */
export default {
  missingPlanId: "缺少 planId",
  loading: "加载计划流程图…",
  loadFailed: "加载失败：{{msg}}",

  stat: {
    total: "共 {{n}} 步",
    done: "已完成 {{n}}",
    running: "执行中 {{n}}",
    failed: "失败 {{n}}",
    hidden: "已折叠 {{n}} 步",
  },

  live: {
    interrupted: "实时中断：{{msg}}",
    on: "实时 · {{at}}",
    connecting: "连接中",
    off: "实时已关",
    toggleTitle: "实时状态来自内核 Redis 热缓存（每 3 秒）",
    toggle: "实时状态",
  },

  refresh: "刷新",
  backTitle: "关闭流程图，返回计划列表",
  back: "返回",

  playback: {
    pause: "暂停回放",
    play: "回放演示",
    reset: "重置",
    speed: "速度",
  },

  collapse: {
    expandAll: "全部展开",
    collapseAll: "全部折叠",
    expandTitle: "展开全部子任务",
    collapseTitle: "折叠全部子任务（默认展开）",
  },

  follow: "自动追踪",
  fullscreen: { exitTitle: "退出全屏（ESC）", enterTitle: "全屏演示（隐藏页面外壳）", exit: "退出全屏", enter: "全屏" },

  info: {
    nodes: "{{n}} 节点",
    edges: "{{n}} 边",
    liveCounts: "✓ 实时：运行 {{running}} / 完成 {{succeeded}} / 待执行 {{pending}}",
    fromDb: "运行态来自 agx_task_execution（实时已关）",
    noRecords: "⚠ 该计划尚无任何执行记录，可用「回放演示」看流程",
  },

  focus: {
    replaying: "正在回放 {{idx}}/{{total}}",
    running: "正在执行",
    parallel: "另 {{n}} 个并行执行中",
    agent: "Agent: {{name}}",
  },

  legend: {
    current: "当前路径",
    executed: "已执行路径",
    hierarchy: "父子归属（竖） / 虚线 = 时序（横）",
  },

  dialogTitle: "步骤详情",
  section: { basic: "步骤信息", instruction: "执行指令" },

  field: {
    status: "状态",
    agent: "执行 Agent",
    task: "任务",
    taskId: "任务 ID",
    start: "开始",
    end: "结束",
    timeout: "超时",
    children: "子步骤",
    prev: "前置任务",
    next: "后置任务",
    session: "会话",
  },

  scheduled: "定时 {{at}}",
  childCount: "{{n}} 个",
  noSession: "未派发（还没有会话）",
  empty: "（无）",
  rawBlock: "原始数据（步骤）",
  sessionDetail: "会话详情",

  node: {
    expandChildren: "展开子任务",
    collapseChildren: "折叠子任务",
    children: "{{n}} 子",
    running: "执行中…",
    collapsed: "已折叠",
  },

  sub: {
    section: "节 {{n}}",
    chapterRound: "ch{{ch}} · 第 {{n}} 轮",
    round: "第 {{n}} 轮",
  },
} as const;
