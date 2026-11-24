/**
 * Dispatch exemption panel (agent detail / agent card)
 */
export default {
  quick30: "30 分钟",
  quick2h: "2 小时",
  quick8h: "8 小时",
  quickReason: "控制台快捷设置（{{label}}）",
  customReason: "控制台自定义设置",

  remainDays: "{{d}} 天 {{h}} 小时",
  remainHoursMinutes: "{{h}} 小时 {{m}} 分",
  remainHours: "{{h}} 小时",
  remainMinutes: "{{m}} 分钟",
  remainZero: "0 分钟",
  remainUnderMinute: "不到 1 分钟",

  notifyOk: "，已通知它本人",
  notifyFail: "，但通知没发出去（{{error}}）",

  closedMsg: "已关闭 {{name}} 的调度豁免{{tail}}",
  grantedMsg: "已允许 {{name}} 派活，到期 {{until}}（剩 {{left}}）{{tail}}",
  notEffectiveMsg: "写入成功但内核回读判定未生效（until={{until}}）—— 请刷新确认",

  toastClosed: "已关闭调度豁免",
  toastGranted: "已允许派活",
  permissionHint: "（需要项目管理员或 workshop_manager 来开）",

  title: "调度豁免",
  badgeActiveTitle: "到期 {{until}}",
  badgeActive: "豁免中 · 还剩 {{left}}",
  badgeExpiredTitle: "已于 {{until}} 到期",
  badgeExpired: "已过期（{{until}} 到期）",
  none: "无",
  scopeHint: "只放开派活三件套（dispatch_task / schedule_task / quick_cmd_send），不含建项目、改 Agent",
  customOpen: "自定义…",
  clear: "关闭",

  dialogTitle: "设置调度豁免 · {{name}}",
  dialogDesc: "到期自动失效，不需要手动关。只放开派活三件套，不改变角色等级。",
  fUntil: "到期时间",
  fReason: "原因（写进审计日志，可选）",
  reasonPlaceholder: "例：让它临时把结果派给 reviewer",
  cancel: "取消",
  confirm: "确定",
} as const;
