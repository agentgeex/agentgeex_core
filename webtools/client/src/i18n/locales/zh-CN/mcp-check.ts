/**
 * MCP service self-check (shared by the service settings tab and the old dialog)
 */
export default {
  status: { ok: "正常", abnormal: "{{status}}" , abnormalFallback: "异常" },

  cache: {
    project: "项目",
    agent: "智能体",
    task: "任务",
    execution: "执行",
    session: "会话",
    plan: "计划",
    host: "主机",
    notice: "公告",
    group: "团队",
    skill: "技能",
  },

  dirty: {
    nogit: "无 git（按 mtime）",
    dirty: "工作区脏",
    clean: "git 干净",
    unknown: "未知",
  },

  freshness: {
    unknown: "新旧：无法判断",
    stale: "内存里是旧码",
    fresh: "跑的是最新码",
    codeChanged: "代码改动",
    procStarted: "进程启动",
    restartHint: "执行",
    restartHintTail: "后再点「重新检测」。",
  },

  loading: "正在向 MCP 查询版本…",

  error: {
    title: "取不到 MCP 版本",
    desc: "通常意味着 BFF 连的 MCP 网关没起来（即「MCP 通道」那一行的地址）——不是「没有版本」，是「问不到」。",
  },

  build: {
    title: "Build 指纹（自动，随代码变）",
    codeTime: "代码时间",
    dirtyNote: "工作区有未提交改动 —— 线上跑的不是 HEAD，这个哈希只代表「已提交的部分」。",
  },

  release: {
    title: "发布说明（手写）",
    note: "这一步是人手动写的 release note，改代码时不一定同步 —— 判断新旧请看上面两条。",
  },

  row: {
    checkedAt: "取回时刻",
    checkedAtNote: "（本次查询时间，非进程启动时间）",
    channel: "MCP 通道",
    checkedBy: "检测身份",
  },

  connectivity: "内核连通性",
  permission: { title: "权限配置", updatedAt: "更新于 {{at}}" },
  cacheTitle: "缓存条目",

  scope: {
    line1: "这一项只看「MCP 通道」那一行的那个网关进程",
    port: "（:{{port}}）",
    line2: "自 2026-11-04 MCP 统一后，BFF 与智能体连的是同一个网关，所以这里的结论对两边同时成立。",
    line3: "但它只覆盖「这个网关进程里的内核代码」；daemon / ws_client / 看门狗是另外的进程，它们的代码新旧这里看不到。",
  },

  refresh: "重新检测",
  dialogTitle: "MCP 服务自检",
  close: "关闭",
} as const;
