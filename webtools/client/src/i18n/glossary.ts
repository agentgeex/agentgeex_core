/**
 * Domain glossary — the **single authority** for Chinese → English.
 *
 * Why a single source matters so much:
 *   The same concept appears in four places: i18n keys, routes, localStorage keys, data-testid.
 *   Writing each independently yields four variants: 'execution = executions / runs / history / traces',
 *   so after open-sourcing, outsiders have to guess again for every file they read.
 *   Therefore: **fix the words first, then translate, and only then touch code.**
 *
 * Usage:
 *   · Consult this when writing i18n keys (`executions.*`, not `runs.*`)
 *   · Consult this when adding routes/storage keys
 *   · `scripts/i18n-scan.mjs` and subsequent translation tasks all take it as the standard
 *
 * ★ Things that are not translated are also written here (agent names, IDs, raw backend text) — 'what not to touch' is a rule too.
 */

export interface Term {
  /** Chinese (the team's internal wording) */
  zh: string;
  /** English (the only way it appears in keys / routes / identifiers) */
  en: string;
  /** Plural form (almost always needed in English) */
  plural?: string;
  /** Where it is used (route / storage key / key / display only) */
  use?: string;
  /** A one-line caution where it is easily confused */
  note?: string;
}

export const GLOSSARY = {
  /* ── Core entities ── */
  agent: { zh: "智能体", en: "agent", plural: "agents", use: "路由 /agents · key agents.*" },
  session: { zh: "会话", en: "session", plural: "sessions", use: "路由 /sessions" },
  task: { zh: "任务（模板）", en: "task", plural: "tasks", use: "路由 /tasks", note: "指能力模板，不是一次运行" },
  execution: {
    zh: "执行记录",
    en: "execution",
    plural: "executions",
    use: "路由 /executions",
    note: "★ 不用 run —— run 已被 run_uuid / run_status 占用，混用会分不清'实例'与'编号'",
  },
  plan: { zh: "计划", en: "plan", plural: "plans", use: "路由 /plans" },
  project: { zh: "项目", en: "project", plural: "projects", use: "路由 /projects" },
  projectGroup: { zh: "项目组模板", en: "project group", plural: "project groups", use: "路由 /project-groups" },
  quickCommand: {
    zh: "快捷指令",
    en: "quick command",
    plural: "quick commands",
    use: "路由 /quick-cmds（历史遗留缩写）· key quickcmd.*",
    note: "UI 文案写全称；标识符里可保留 quickcmd（不带空格）",
  },
  cannedMessage: { zh: "常用话", en: "canned message", plural: "canned messages", note: "是文本片段，不派任务" },
  skill: { zh: "技能", en: "skill", plural: "skills", use: "路由 /skills" },
  notice: { zh: "公告 / 工作日志", en: "notice", plural: "notices", use: "路由 /notices" },
  host: {
    zh: "算力主机",
    en: "compute host",
    plural: "hosts",
    use: "路由 /hosts",
    note: "单说 host 会被读成'宿主/主机名'，界面文案用 compute host",
  },

  /* ── Sending-related (message-send dialog) ── */
  senderAgent: { zh: "发送主体", en: "sender agent", use: "key send.sender.*" },
  targetAgent: { zh: "接收主体", en: "target agent", use: "key send.target.*" },
  senderSession: { zh: "发送会话", en: "sender session" },
  targetSession: { zh: "接收会话", en: "target session" },
  send: {
    zh: "发送",
    en: "send",
    use: "按钮文案",
    note: "★ 同一个动作只有一个词：文本/Task/快捷指令都叫 Send。禁止再出现 dispatch 作为按钮名",
  },
  dispatch: { zh: "派活", en: "dispatch", use: "仅内部协议与代码注释，不出现在 UI" },
  receipt: { zh: "回执", en: "receipt" },
  urge: { zh: "催办", en: "urge" },

  /* ── Status and views ── */
  running: { zh: "运行中", en: "running" },
  succeeded: { zh: "已成功", en: "succeeded" },
  failed: { zh: "已失败", en: "failed" },
  pending: { zh: "待执行", en: "pending" },
  archived: { zh: "已归档", en: "archived" },
  pinned: { zh: "置顶", en: "pinned" },

  /* ── Not translated (written down to prevent mis-translation) ── */
  "（不翻）agent 名": { zh: "智能体名", en: "as-is", note: "用户数据，原样显示" },
  "（不翻）项目名": { zh: "项目名", en: "as-is" },
  "（不翻）会话名": { zh: "会话名", en: "as-is" },
  "（不翻）ID / UUID": { zh: "agent_id / run_uuid …", en: "as-is", note: "等宽字体原样" },
  "（不翻）执行输出": { zh: "summary / stdout", en: "as-is", note: "是内容不是 UI" },
  "（不翻）后端 message": {
    zh: "错误 message",
    en: "as-is",
    note: "★ 前端按 error.code 查自己的文案；未登记 code 才回落 message",
  },
} as const satisfies Record<string, Term>;

export type GlossaryKey = keyof typeof GLOSSARY;

/** Take the English form (used when writing keys / routes) */
export function termEn(k: GlossaryKey): string {
  return GLOSSARY[k].en;
}
