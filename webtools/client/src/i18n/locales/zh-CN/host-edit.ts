/**
 * Host edit page (/hosts/:id/edit)
 */
export default {
  missing: {
    text: "主机 {{id}} 不存在，可能已被移除。",
    emptyId: "（空 id）",
    back: "返回列表",
  },

  header: { edit: "编辑算力主机" },

  note: {
    kernel: "写入走内核 agx_update_host（字段白名单 / 权限 / 缓存同步都在内核）",
    editor: "提交身份",
  },

  field: {
    alias: "主机别名",
    ip: "IP / 访问地址",
    sshPort: "SSH 端口",
    sshPortHint: "1-65535",
    os: "操作系统",
    slots: "任务槽上限",
    slotsHint: "该主机可同时承载的 Agent 任务数",
    hostId: "主机 ID",
    hostIdHint: "ID 是寻址主键，不可修改",
    desc: "描述",
  },

  platforms: {
    title: "可用平台（platforms）",
    subtitle: "决定「这台主机能跑哪个平台的 Agent、地址怎么拼」。一条 = 一个平台实例（多实例就多条）。",
    add: "添加平台",
    empty: "这台主机还没配置任何平台 —— 配了之后，智能体页才拿得到它的访问地址。",
    itemNo: "第 {{n}} 条",
    remove: "删除",
  },

  platformField: {
    id: "平台标识",
    idHint: "openclaw / dsh / codex / claude_code / custom",
    name: "显示名",
    nameHint: "缺省用平台标识",
    mode: "模式",
    modeHint: "process=本机进程 / container=容器",
    process: "process（本机进程）",
    container: "container（容器）",
    port: "端口",
    portHintProcess: "地址 = 主机 IP + 这个端口",
    portHintContainer: "容器模式可不填",
    basePath: "会话路径",
    basePathHint: "如 /chat",
    profile: "profile",
    profileHint: "OpenClaw profile 名；主实例留空",
    addr: "地址（addr）",
    addrHint: "容器自持地址，如 172.17.0.5:18789",
    note: "备注",
    noteHint: "给人看的；机器要读的信息请提到独立字段",
  },

  error: {
    aliasEmpty: "主机别名不能为空",
    portRange: "SSH 端口须为 1-65535 的整数",
    slotsInvalid: "任务槽上限须为不小于 0 的整数",
    noChange: "没有任何修改",
  },

  validate: {
    tag: "第 {{n}} 条平台配置",
    platformEmpty: "{{tag}}：平台标识不能为空（如 openclaw）",
    platformDup: "{{tag}}：平台 {{pid}} 重复了（同一主机一个平台只配一条）",
    portRequired: "{{tag}}：process 模式必须填端口（地址由主机 IP + 端口拼出）",
    portRange: "{{tag}}：端口须为 1-65535 的整数",
    addrRequired: "{{tag}}：container 模式必须填地址（容器自己就是一台主机，不能拿主机 IP 拼）",
    joiner: "；",
  },

  toast: { updated: "主机已更新" },

  diff: {
    submit: "将提交 {{n}} 个字段：{{fields}}",
    none: "无改动",
    autoRefresh: "保存后自动刷新列表",
  },
} as const;
