/**
 * Quick access page (/quick-access)
 */
export default {
  title: "快捷入口",
  loading: "读取中…",
  counted: "已收藏 {{count}} 项",
  empty: "还没有收藏",
  refresh: "刷新",
  readFailed: "读取快捷入口失败",
  removeFailed: "取消收藏失败",
  toList: "去{{label}}列表收藏",
  addHint: "{{hint}}——在列表里点 ★ 就能加进来",
  archived: "已归档 · {{id}}",
  agentCount: "{{count}} 个 Agent",
  online: "在线",
  offline: "离线",
  expiredSuffix: " · 已失效",
  open: "打开",
  send: "发送消息",
  sendTitle: "发送消息（默认落点已填好）",
  removeTitle: "取消收藏",
  type: {
    project: "项目",
    agent: "Agent",
    session: "会话",
    plan: "计划",
  },
  hint: {
    project: "我正在跟的项目",
    agent: "常打交道的 Agent",
    session: "常用的落地会话",
    plan: "常看的计划流程",
  },
} as const;
