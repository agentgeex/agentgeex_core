/** Live session status (Redis gateway side) */
export default {
  running: "运行中",
  idle: "空闲",
  failed: "失败",
  stalled: "卡住",
  unknown: "未知",
  runningHint: "正在运行 · run {{run}}",
  lastEvent: "最后事件 {{event}}",
} as const;
