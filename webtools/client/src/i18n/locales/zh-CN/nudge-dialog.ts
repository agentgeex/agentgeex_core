/**
 * Nudge / acknowledge-reminder confirmation dialog
 */
export default {
  nudgeTitle: "确认催办？",
  nudgeWho: "执行方",
  nudgeWhat: "请它汇报当前进展，或说明卡在哪里",
  nudgeVerb: "催办",
  ackTitle: "确认催签收？",
  ackWho: "发起方",
  ackWhat: "它有一条回执还没签收，请它签收",
  ackVerb: "催签收",
  warning: "这会真的给一个 agent 发消息、打断它手上的事。",
  fTarget: "发给",
  fContent: "内容",
  fTask: "任务",
  repeated: "这一条已经人工催过 {{count}} 次了。催完仍不动，责任在发起方裁决（重投或终止），继续催只是重复打扰。",
  cancel: "取消",
  sending: "发送中…",
  confirm: "确认{{verb}}",
} as const;
