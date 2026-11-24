/**
 * Quick command · create/edit form
 * The category vocabulary is shared site-wide (the admin page's filter and badges read it too).
 */
export default {
  category: {
    network: "网络",
    system: "系统",
    notice: "公告",
    agent: "Agent",
    host: "主机",
    project: "项目",
    log: "日志",
    config: "配置",
    dev: "开发",
    other: "其他",
  },

  sendMode: {
    task: { label: "派任务", desc: "填参数 → 建执行记录" },
    text: { label: "文本片段", desc: "只填进输入框，不派任务" },
  },

  error: {
    requiredTextPublic: "标题 / 文本内容 均为必填",
    requiredTextPrivate: "标题 / 归属 Agent / 文本内容 均为必填",
    requiredTaskPublic: "标题 / 目标任务 / 指令模板 均为必填",
    requiredTaskPrivate: "标题 / 归属 Agent / 目标任务 / 指令模板 均为必填",
    saveFailed: "保存失败",
  },

  title: { edit: "编辑快捷指令", new: "新建快捷指令" },

  desc: {
    text: "文本片段 = 一段存起来的常用话。点一下只把它填进输入框，发不发由你决定，不产生执行记录。",
    task: "指令 = 参数表单 + 指令模板；客户填参数一键发送。模板中 {name} 会被参数值替换。",
  },

  field: {
    type: "类型",
    typeHint: "选错类型会让一条常用话被当成任务发出去",
    title: "标题",
    titlePh: "如：克隆 Agent",
    cmdId: "命令 ID",
    cmdIdHintEdit: "不可改",
    cmdIdHintNew: "留空则由标题自动生成",
    cmdIdPh: "agent_clone",
    visibility: "可见性",
    public: "公有（谁点谁执行）",
    private: "私有（发给指定 Agent）",
    publicHint: "不绑 Agent：目标 = 点这条指令的调用方自己（其名下需有该任务）",
    privateHint: "绑定归属 Agent，固定发给该 Agent",
    owner: "归属 Agent",
    ownerPh: "选择 Agent…",
    ownerMissing: "（不在列表）",
    targetTask: "目标任务",
    targetTaskPh: "task_labs（如 system_check）",
    category: "分类",
    skeleton: "语义骨架",
    skeletonPh: "一句话说明这条指令做什么",
    templateText: "文本内容",
    templateTask: "指令模板",
    templateHintText: "点一下就是这段原文填进输入框",
    templateHintTask: "用 {参数名} 占位",
    templatePhText: "写一句你要说的话，点一下就会填进输入框…",
    templatePhTask: "请对 {target} 执行 {action}…",
    notes: "备注",
    notesPh: "可选",
  },

  params: {
    title: "参数（{{n}}）",
    add: "加参数",
    empty: "无需参数，确认后直接发送。",
    labelPh: "标签",
    required: "必填",
  },

  cancel: "取消",
  save: "保存",
  create: "创建",
} as const;
