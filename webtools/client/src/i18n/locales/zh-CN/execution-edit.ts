/**
 * Execution edit page (/executions/:id/edit)
 */
export default {
  title: "编辑执行记录",
  notFound: "执行记录 {{id}} 不在当前快照里，可能已被移除或尚未同步。",
  kernelHint: "写入走内核 agx_update_execution（字段权限由内核逐字段判定）",
  fTask: "任务",
  fAgent: "执行方",
  fStatus: "状态",
  fExecName: "执行短标题",
  fExecNameHint: "列表里显示的名字，给人看的",
  fTimeout: "超时（秒）",
  timeoutShrinkWarn: "⚠ 比原来短：会立刻把 0.7/0.8/0.9 三档唤醒提前，等于主动催超时",
  timeoutMinHint: "不得小于 {{min}} 秒",
  instrLabel: "执行指令（exec_instruction）",
  instrHint: "这是**已经派发出去**的那份指令。任务已在跑时改它不会改变它当前的行为，只影响后续重投/续接读取到的内容。",
  fAlertNote: "告警备注",
  fAlertNoteHint: "留空 = 不改动（列表快照里没有这个字段，无法回显现有内容）",
  alertNotePlaceholder: "仅在需要记录「为什么人工介入」时填写",
  updated: "执行记录已更新",
  errTimeoutInteger: "超时必须是整数秒",
  errTimeoutTooSmall: "超时不能小于 {{min}} 秒（内核下限）",
  errTitleEmpty: "标题不能为空",
  footerHint: "状态与签收不在此页修改（前者是执行结果，后者走签收）",
} as const;
