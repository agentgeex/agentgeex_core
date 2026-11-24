/**
 * Service settings dialog (speech recognition hosts / MCP connection)
 */
export default {
  available: "可用",
  unreachable: "连不上",
  untested: "还没探过",
  probeHint: "点一下立刻检测这台（一次 WS 握手）",
  probing: "检测中",
  justNow: "刚刚",
  secondsAgo: "{{n}} 秒前",
  minutesAgo: "{{n}} 分钟前",
  hoursAgo: "{{n}} 小时前",
  title: "服务设置",
  tabAsr: "语音识别",
  tabMcp: "MCP 连接",

  asrPickTitle: "选的是「这台机器用哪台服务」，不是部署。",
  /* This sentence embeds two <code> and one <strong>, so it can only be pieced together from fragments —
     ★ English fragments must end with a space, otherwise they splice into "from.env" / "BFF_ASR_HOSTSon". */
  asrSourceA: "可选的主机清单在服务端",
  asrSourceB: " 的 ",
  asrSourceC: " 里（加减机器要改那里并重启）；这里选哪台",
  asrSourceStrong: "立刻生效",
  asrSourceD: "，重启后仍记得。",
  loadingHosts: "正在读取主机列表…",
  hostCount: "{{count}} 台可选",
  current: "当前：",
  inUse: "使用中",
  checkedAt: "检测于 {{when}}",
  autoProbe: "后台每 8 秒自动探一遍每一台，所以这里的「可用」会自己更新，不用一直点。右侧小按钮只是「我要现在就知道」。",
  failLoud: "选中但连不上的那台，录音时会明确报错（不会静默转成空文本）—— 那种失败最难排查。",
  reload: "重新读取",
} as const;
