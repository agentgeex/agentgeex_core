/**
 * Agent list (grid / table)
 */
export default {
  kind: { template: "模板", clone: "克隆实例", native: "原生" },

  deploy: {
    template: "模板",
    deployed: "已部署",
    pending: "待部署",
    deploying: "部署中",
    failed: "部署失败",
  },

  card: {
    sendAs: "以 {{name}} 的身份发消息 / 派任务",
    sendInfo: "信息发送",
    host: "主机",
    exemptActive: "豁免中 · 还剩 {{time}}",
    exemptExpired: "豁免已过期",
    clonedFrom: "克隆自",
    viewSessions: "查看 {{name}} 的会话（弹窗）",
    viewTasks: "查看 {{name}} 的任务（弹窗）",
    viewExecutions: "查看 {{name}} 的执行记录（弹窗）",
    sessions: "会话",
    tasks: "任务",
    executions: "执行",
    execOk: "成功 {{n}}",
    execBad: "失败 {{n}}",
  },

  unregisteredProject: "未登记项目",

  search: { ph: "搜索名称 / ID / 项目…", aria: "搜索智能体" },

  scope: {
    aria: "模板视角",
    inUse: "使用中",
    all: "全部（含模板）",
    templateOnly: "仅模板",
  },

  project: { ph: "项目", all: "项目：全部（{{n}}）" },

  host: { all: "主机：全部" },

  roleFilter: {
    ph: "角色",
    all: "角色：全部",
  },

  status: {
    ph: "状态",
    all: "状态：全部",
    online: "在线",
    offline: "离线",
  },

  view: { aria: "视图切换", card: "宫格", cardTitle: "宫格视图", table: "表格", tableTitle: "表格视图" },

  empty: "没有符合条件的智能体",

  table: {
    agent: "智能体",
    platform: "平台",
    kind: "类型",
    deploy: "部署状态",
    role: "角色",
    status: "状态",
    sessions: "会话",
    tasks: "任务",
    executions: "执行",
    lastActive: "最近活跃",
  },
} as const;
