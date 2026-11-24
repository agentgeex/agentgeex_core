/** Top bar: global actions (theme / language / full screen / account). */
export default {
  fullscreen: "全屏",
  exitFullscreen: "退出全屏",
  switchLanguage: "切换语言",
  switchLight: "切换浅色",
  switchDark: "切换深色",
  theme: "主题",
  repo: "代码仓库",
  help: "帮助",
  profile: "个人中心",
  settings: "设置",
  logout: "退出登录",
  logoutConfirm: "确定退出系统？",
  logoutPrompt: "确定要退出登录吗？",
  document: "文档",
  lock: "锁屏",
  /** Global "send message" entry (Feng, 2026-11-13: it must be reachable from any page) */
  send: "信息发送",
  sendHint: "发一条消息 / 派一个任务 / 跑一条快捷指令（任何页面都能用）",
  /** The "service settings" entry and badge (upgraded from the old "test → MCP self-check") */
  serviceSettings: "服务设置",
  serviceBadge: "服务",
  /** Confirm button of the logout dialog (the title is "Sign out", the button must say "confirm signing out") */
  logoutConfirmBtn: "确认退出",
} as const;
