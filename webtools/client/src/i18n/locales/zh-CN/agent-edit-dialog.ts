/**
 * Agent edit dialog
 */
export default {
  toast: {
    noChangeTitle: "没有改动",
    noChangeDesc: "改过内容才有东西需要保存。",
    saved: "已保存",
    savedDesc: "更新了 {{n}} 个字段：{{fields}}",
  },

  error: { invalidJson: "MCP 配置不是合法 JSON —— 已停在本地，没有发给后端。" },

  title: "编辑 Agent · {{name}}",

  field: {
    name: "名称",
    namePh: "表意名称，如 前端负责人",
    role: "角色",
    host: "归属主机",
    hostPh: "选择主机…",
    status: "状态",
    statusOn: "启用（可接收任务）",
    statusOff: "停用（停止接收新任务）",
    xdesc: "说明书（x_desc）",
    xdescPh: "一句话说清它是谁、负责什么",
    prompt: "系统提示词（system_prompt）",
    promptPh: "系统提示词（整段人格设定）",
    skills: "技能白名单",
    skillsPh: "每行一个 skill 名（留空 = 不限制，继承全局）",
    skillsHint: "写入内核前会校验目录下是否真有同名 SKILL.md；一个个错都会导致整批被拒。",
    mcp: "MCP 配置（JSON）",
    mcpHint: "必须是合法 JSON 对象；格式不对会在本地就停住，不会发出去。",
    email: "邮箱",
    emailPh: "留空 = 不修改",
    telegram: "Telegram Token",
    telegramPh: "留空 = 不修改（不回显）",
    feishu: "飞书 Token",
    feishuPh: "留空 = 不修改（不回显）",
  },

  advanced: "高级（技能白名单 / MCP 配置 / 邮箱与渠道）",

  tokenNote: {
    part1: "这三个字段详情接口本来就",
    part2: "不返回",
    part3: "，所以框里永远是空的。",
    strong: "空 = 不改",
    part4: "，不会把你已有的 Token 清掉。",
  },

  footer: {
    note: "只提交",
    noteStrong: "改过的字段",
    noteTail: "；能否修改由内核裁决。",
    noteTitle: "角色 / 主机 / 系统提示词属契约字段，需要更高权限；被拒绝时内核原话会显示在下方",
    cancel: "取消",
    save: "保存",
    saveDirty: "保存（{{n}} 项）",
  },
} as const;
