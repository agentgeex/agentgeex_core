/**
 * Send dialog (the largest form on the site)
 * ★ "dispatch" is always written as "send" per the glossary — one action, one name.
 * ★ The template body inside a quick command preview is **data** and stays out of the language pack.
 */
export default {
  title: "信息发送",

  sessionType: {
    main: "主会话",
    case: "事项",
    task: "任务",
    oneshot: "一次性",
    assistant: "助理",
  },

  running: {
    label: "运行中",
    title: "会话正在运行",
    titleWithRun: "会话正在运行 · run {{id}}",
  },

  open: {
    agent: "打开",
    agentDisabled: "先选一个 Agent",
    agentTitle: "打开这个 Agent 的详情 · {{id}}",
    agentPopupTitle: "弹窗查看这个 Agent 的详情（不离开当前窗口）· {{id}}",
    session: "打开会话",
    sessionNoPage: "这个会话在 OpenClaw 没有对应页面（Agent 不在注册表 / 平台不支持跳转）",
    sessionDisabled: "先在左边选中一个会话",
    sessionTitle: "在 OpenClaw 打开这个会话 · {{href}}",
  },

  lockedUnset: "（未定）",

  mode: {
    text: { label: "文本", hint: "直发一段话（不建执行记录）" },
    task: { label: "Task", hint: "包装成任务发送" },
    quickcmd: { label: "快捷指令", hint: "按指令模板 + 参数发送" },
  },

  phrases: {
    title: "常用话",
    open: "把一条常用话填到光标处（不会发送）",
    chars: "{{n}} 字",
    noSender: "未选发送方",
    add: "新建",
    addTitle: "就地新建一条常用话",
    loading: "读取中…",
    empty: "还没有任何文本片段。点右上角「新建」写一条——只存一句话，不派任务。",
    ownGroup: "这个 Agent 的",
    sharedGroup: "通用 · 所有 Agent 可用",
  },

  voice: {
    recording: "正在录音——点「完成」转成文字后就能发送",
    transcribing: "正在把录音转成文字，转完就能发送",
  },

  summaryUnselected: "未选择",
  summaryNewSession: "新会话（系统自动）",
  refreshFailed: "刷新失败",

  silentHint: "该任务设计为静默",

  /* [2026-11-19] Direct text sending to an assistant session: the kernel agx_send_text injects a
     "assistant identity hint" pointer block (about 500 characters) before the text. Users cannot see that, so the UI says it —
     not to have the user add it manually, but to tell them "what you wrote is unchanged, the system adds a separate block"*/
  assistantAutoHint: "此会话是助理：发送时系统会在正文前自动附上一段身份说明（约 500 字），你输入的内容本身不变",

  validate: {
    sender: "请选择发送主体",
    target: "请选择目标 Agent",
    reply: "请选择是否回复",
    textImpossible: "文本模式只能由会话归属方发出（平台禁 Agent 互聊）；换个发送主体请改用 Task / 快捷指令",
    textEmpty: "请输入消息正文",
    textSession: "文本模式只能发给发送主体自己的会话，请选择目标会话",
    skillsMax: "技能最多选择 8 项",
    task: "请选择 Task",
    session: "请选择目标会话",
    quickcmd: "请选择快捷指令",
    paramRequired: "参数「{{name}}」为必填",
  },

  error: {
    permission: "当前身份无权执行（需要 L5 发送权限：PM / 系统核心角色）",
    notOwned: "目标会话不属于目标 Agent（请重选）",
    replyRequired: "请选择是否回复（必须二选一）",
    taskNotFound: "该指令对应的任务在接收方名下不存在（公有指令要接收方自己有同名任务）",
    cmdNotFound: "指令不存在或已归档",
    fallback: "提交失败",
  },

  emptyInstruction: "（未填指令，按「{{name}}」的任务说明书执行）",

  toolbar: {
    maximize: "全屏",
    maximizeTitle: "全屏（贴满整个屏幕）",
    restore: "还原窗口",
    restoreTitle: "还原窗口",
    starOn: "收藏到快捷入口",
    starOff: "取消收藏",
    starBlocked: "先选接收会话或接收 Agent，才能收藏",
    starAddSession: "收藏到快捷入口：该会话",
    starAddAgent: "收藏到快捷入口：该 Agent",
    starRemoveSession: "取消收藏：该会话",
    starRemoveAgent: "取消收藏：该 Agent",
    refresh: "刷新数据",
    refreshTitle: "刷新最新数据（Agent / 会话 / 任务 / 技能 / 快捷指令）",
  },

  project: "项目",
  allProjects: "全部项目（{{n}}）",
  pickAgent: "选择 Agent…",
  quickHint: "同时设置下方的发送方 / 接收方",
  lockedTarget: "接收会话",

  draft: {
    restoredAt: "已恢复 {{time}} 给「{{name}}」的未发送内容",
    restoredLast: "已恢复上次未发送的内容",
    othersTitle: "另有 {{n}} 份草稿（按接收方分别保存，切到那个接收方就会恢复）",
    othersCount: "另有 {{n}} 份",
    clear: "清除",
  },

  layer: {
    sender: "发送方",
    execMode: "执行方式",
    execModeHint: "三选一 · 决定下方可选内容",
    receiver: "接收方",
    params: "执行参数",
    paramsHint: "可选 · 所有任务公用（对齐任务运行窗口）",
    business: "业务属性",
    businessHint: "预留槽位 · 本轮不实现",
  },

  field: {
    senderAgent: "发送主体",
    senderSession: "发送会话",
    targetAgent: "接收主体",
    task: "Task",
    quickcmd: "快捷指令",
    needsReply: "是否回复",
    needsConfirm: "人工确认",
    timeout: "超时（秒）",
    title: "标题",
    projectReserved: "项目（预留）",
    moduleReserved: "模块（预留）",
  },

  ph: {
    senderAgent: "选择发送主体…",
    senderSession: "选择发送会话…",
    targetFirst: "先选接收主体",
    senderFirst: "先选发送主体",
    agent: "选择 Agent…",
    task: "选择 Task…",
    agentFirst: "先选 Agent",
    loading: "加载中…",
    quickcmd: "选择快捷指令…",
    targetSession: "选择目标会话…",
    session: "选择会话…",
    timeout: "留空 = 默认（3600）",
    execName: "留空 = 自动（如：周报生成）",
    reserved: "后续由可插拔动态表单填充",
  },

  empty: {
    noSenderSessions: "（暂无可选会话，将用主会话）",
    noTasks: "（该 Agent 暂无可选 Task）",
    cmdsCannotDeliver: "（这些指令都投不进当前会话：需发送主体 = 接收方）",
    noCmds: "（暂无可选快捷指令）",
    noSessions: "（该 Agent 暂无会话）",
  },

  cmdMapping: {
    owner: "归属 Agent",
    target: "目标任务",
    fixed: "（由指令自带，不可改）",
  },

  sessionMode: { new: "新会话", existing: "既有会话" },

  skills: {
    title: "使用技能",
    hint: "可选 · 仅列出有效技能（已选 {{n}} 项，最多 8 项）",
    remove: "移除：{{name}}",
    clear: "清空",
    search: "搜索技能（名称 / 描述 / 标签 / 分类）",
    empty: "技能库暂无可用的有效技能",
    noMatch: "无匹配技能",
    noDesc: "（无描述）",
    textHint: "文本模式：选中技能会随消息正文一起发出（附「参考技能」指针），对方据此调用 agx_skill_get 读正文。",
    taskHint: "选中后会在指令末尾追加「参考技能」指针（skill_id + 名称 + 一句说明），执行方据此调用 agx_skill_get 读取正文再执行。",
  },

  textOnly: "文本只能发进发送主体自己的会话 → 切过来会把接收方改成发送主体（会自动对齐）",

  reply: { need: "需要回复", noNeed: "不需要回复" },

  confirm: {
    title: "需要：任务执行成功也不解锁下游，落「待确认」，人点放行才转成功",
    no: "不需要（默认）",
    yes: "需要（执行完等人放行）",
    note: "「人工确认」= 执行成功也不算完成，落「待确认」，等人点放行才转成功。",
    silent: "该任务设计为静默（不需要回复）。",
  },

  paramsNote: {
    text: "文本模式为直发（不生成 execution），此三项不适用；需要这些参数请改用 Task / 快捷指令。",
    quickcmd: "三项均透传内核 agx_quick_cmd_send（不需要回复时同样强制关闭回执）。（人工确认是任务级属性，快捷指令无此参数）",
    task: "均透传 agx_dispatch_task（同任务运行窗口）。",
  },

  prompt: {
    text: "消息正文",
    task: "任务指令",
    quick: "指令参数",
    quickWithCount: "指令参数 · {{n}} 项",
    quickHint: "按参数表填写（* 为必填）",
  },

  summary: { from: "发送方", to: "接收方", none: "未选择" },

  submit: { idle: "发送", busy: "提交中…" },

  input: {
    labelContent: "内容",
    labelInstruction: "指令",
    phContent: "输入要发送的消息…",
    phInstruction: "输入/编辑任务指令…",
    zoomTitle: "放大编辑：长内容在大窗口里写，不用上下翻",
  },

  quickPanel: {
    loadingCmds: "正在加载该 Agent 的快捷指令…",
    pickFirst: "先在上方选择一条快捷指令",
    noParams: "该指令无需参数，可直接发送",
    previewTitle: "指令预览（实时，仅示意）",
    previewEmpty: "（无）",
  },

  zoom: {
    title: "放大编辑 · {{mode}}",
    count: "{{n}} 字 · {{lines}} 行",
    sync: "与下方输入框实时同步 —— 关掉就保留，不需要额外保存",
    done: "完成",
  },
} as const;
