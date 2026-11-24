/**
 * Agent management · quick command list page (/quick-cmds)
 * Form fields are in `quickCmdForm`; this file covers list/card/row actions.
 */
export default {
  mode: { text: "文本片段", task: "派任务" },
  visibility: { public: "公有", private: "私有" },
  executor: { self: "我自己（当前身份）" },
  status: { active: "启用", inactive: "停用" },

  error: { load: "加载失败", archive: "归档失败" },

  copy: {
    emptyTitle: "这条没有内容",
    copied: "已复制",
    copiedDesc: "{{name}} · {{n}} 字（不会发送）",
    failedPrompt: "复制失败，请手动复制：",
  },

  archiveConfirm: "确认归档快捷指令「{{name}}」？",

  column: {
    title: "标题",
    cmdId: "命令 ID",
    category: "分类",
    kind: "类型",
    visibility: "可见性",
    executor: "执行者",
    targetTask: "目标任务",
    params: "参数",
    status: "状态",
  },

  action: {
    copyTitle: "复制这段原文（不会发送、不产生执行记录）",
    copy: "复制",
    runTitle: "选择发送方后运行（权限由内核按所选身份判定）",
    run: "运行",
  },

  card: {
    executor: "执行者 {{who}}",
    noDesc: "（暂无说明）",
    textMode: "文本",
    taskMode: "目标任务",
    chars: "{{n}} 字",
    params: "参数 {{n}}",
  },

  loading: "加载中…",
  searchPlaceholder: "搜索标题 / 命令 ID / 归属 / 任务…",
  newCmd: "新建指令",
  filter: { kind: "类型", category: "分类", project: "项目" },
  emptyText: "暂无快捷指令",
} as const;
