/**
 * Session history (shared by the send dialog's right pane and session detail)
 * ★ "dispatch" is uniformly rewritten as "sent" per the glossary — one action, one name.
 * ★ Markers such as `【本次指令】` / `【任务说明书】` are **kernel protocol markers**, not UI copy, and are not translated.
 */
export default {
  kind: {
    human: "真人",
    envelope: "任务消息",
    notice: "通知",
    system: "系统",
    command: "命令",
  },

  dir: {
    dispatch: "发出",
    receipt: "回执",
    dispatchArrow: "发出 →",
    receiptArrow: "回执 ←",
    other: "消息",
    urge: "催办",
  },

  noReply: "（未采集到本轮回答）",
  noReplyHint: "这条消息之后没有留下可配对的文字回答：可能本轮只有工具调用，也可能回答被合并到了下一条消息，或采集尚未写入。",

  collapse: "收起",
  expand: "展开全文（{{n}} 字）",

  envelope: {
    title: "信封",
    processed: "（消息原文，未加工）",
    instruction: "指令",
    noInstruction: "（无指令正文 / 该执行已清理）",
    viewDetail: "查看消息详情",
    viewDetailWithLen: "查看消息详情（{{n}} 字）",
    openTitle: "点击查看消息详情",
    dialogDesc: "这条消息是派发协议的信封；指令正文来自执行记录（`agx_task_execution`），不冗余存进消息表。",
    bodyTitle: "指令正文",
    bodyLen: "（{{n}} 字{{suffix}}）",
    bodyPreview: " · 预览",
    bodyFull: " · 全文",
    fetchingFull: "正在取全文…",
    previewOnly: "仅预览",
    fullText: "全文",
    noBody: "无正文",
    fetchFailed: "全文获取失败：{{msg}}",
    noBodyHint: "无指令正文。可能原因：该 run_uuid 已从 `agx_task_execution` 清理，或信封里取不到 run_uuid。",
    meta: {
      msgType: "消息类型",
      msgTypeNoHeader: "（信封无 msg_type）",
      runUuid: "执行编号",
      runUuidMissing: "（信封里取不到）",
      execName: "执行标题",
      execNameMissing: "（该执行已清理或无标题）",
      sender: "发起方",
      runner: "执行方",
      runStatus: "执行状态",
      at: "消息时间",
      bodyLen: "正文长度",
    },
  },

  reply: {
    toolCallsHint: "回答中间夹了工具调用",
    toolCalls: "工具 ×{{n}}",
    answer: "回答",
  },

  history: {
    title: "历史对话",
    meta: "共 {{total}} 条{{loaded}}{{updated}}",
    metaLoaded: "（已载 {{n}}）",
    metaUpdated: " · {{at}} 已更新",
    metaNoSession: "选定接收会话后显示",
    refreshTitle: "刷新（消息在持续入库，刚说过的话可能还没显示）",
    newSession: "新会话还没有历史；选一个既有会话，或先发出第一条。",
    loadFailed: "读取失败：{{msg}}",
    loading: "读取中…",
    empty: "这个会话还没有对话记录{{name}}。",
    emptyName: "（{{name}}）",
    loadingMore: "载入中…",
    loadMore: "↑ 载入更早的记录",
    queued: "忙时发出 · 延后处理",
    queuedHint: "这条消息发出时 Agent 正忙于上一轮，被排队后才被处理（原样入库时的英文包装已在显示层剥掉）",
  },

  pending: {
    waiting: "已发出 · 待入库",
    slow: "已发出 · 暂未查到（仍在检查）",
    timeout: "已发出 · 未查到入库",
    modeTask: "任务",
    modeQuickCmd: "指令",
    hasSkills: "含技能说明",
    hasSkillsHint: "这条消息的正文末尾附带了「参考技能」说明块",
    realLen: "实际指令 {{n}} 字",
    readingReal: "读取实际指令…",
    readingText: "（正在读取）",
  },
} as const;
