/**
 * Miscellaneous shell copy: tab bar / data table view switch / lock screen / human approval / long-text viewer
 */
export default {
  tabBar: {
    refreshed: "已刷新",
    updatedAt: "数据更新于 {{time}}",
    refreshFailed: "刷新失败",
    backendDown: "后端不可用",
    refreshHint: "{{label}}（一键重取当前页数据）",
  },
  dataTable: {
    viewSwitch: "视图切换",
    tableView: "表格视图",
    cardView: "卡片视图",
  },
  lockScreen: {
    needPassword: "请输入锁定密码",
    wrongPassword: "密码错误，请重试",
    title: "锁定屏幕",
    password: "锁屏密码",
    lock: "锁定",
    unlockPlaceholder: "请输入锁屏密码解锁",
    unlock: "解锁",
  },
  humanConfirm: {
    waiting: "这条任务在等你放行",
    body: "执行方已经干完了，结果不写成功也不算失败 —— 要人看过再定。",
    fTask: "任务",
    fAgent: "执行方",
    resultTitle: "执行方交回的结果",
    noSummary: "（这条执行没有写结果摘要）",
    consequence: "放行 → 转成功，下游任务立刻开始跑；驳回 → 转失败，并给发起方发失败回执。两个动作都不能撤销。",
    later: "稍后再说",
    reject: "驳回",
    approve: "放行",
  },
  richText: {
    empty: "（暂无内容）",
    copyAll: "复制全文",
    expand: "展开查看完整内容",
    fullTitle: "完整内容",
  },

  pageDialog: {
    refreshFailedCached: "刷新失败，下面是本地缓存",
    refreshFailed: "刷新失败",
    refreshAria: "刷新数据",
    refreshTitle: "刷新最新数据",
    loadingLatest: "正在取最新数据…",
  },
  quickStar: {
    failed: "收藏失败",
    tipOn: "取消快捷（已加入快捷入口）",
    tipOff: "设为快捷（加入快捷入口）",
  },
  detailShell: {
    backLabel: "返回列表",
    rawData: "原始数据",
  },
  inputNumber: {
    decrease: "减少",
    increase: "增加",
  },
  pagination: {
    total: "共 {{count}} 条",
    perPage: "每页",
    rows: "条",
    prevPage: "上一页",
    nextPage: "下一页",
  },
  agentsDialog: {
    agentsOf: "的智能体",
    countSuffix: "（主机卡写 {{count}} 个）",
  },
  openclaw: {
    openIn: "在 OpenClaw 中打开",
    openOpenclaw: "打开 OpenClaw",
    open: "打开",
  },
  shell: {
    buildStamp: "前端构建指纹（变了说明已重新构建，需强刷）",
  },
  tabs: {
    systemStatus: "系统状态",
  },
  close: "关闭",
  noData: "暂无数据",
  auth: {
    loginFailed: "登录失败",
    badCredentials: "用户名或密码错误",
  },

  jsonView: { copy: "复制" },
  markdownView: { empty: "（无正文）" },
  toast: { close: "关闭提示" },
  breadcrumb: { label: "面包屑导航" },
  appTitle: "AgentGeeX — 智能体管理控制台",
  flow: "流程图",
} as const;
