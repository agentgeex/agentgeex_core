/**
 * Sidebar navigation: group names / item names / interaction hints + tab titles.
 *
 * ★ Why navigation names belong in the language pack (instead of staying in nav-agentgeex.ts):
 *   a navigation name appears in three places — the sidebar, the breadcrumb, and the browser tab title.
 *   Left in the config it can only be Chinese, and the English build ends up half Chinese, half English.
 *   The config stores **keys** only (`nav.item.agents`) and translates on display — the config itself is language-neutral.
 */
export default {
  expand: "展开侧边栏",
  collapse: "折叠侧边栏",
  newTabHint: "Ctrl/⌘+点击 新标签打开",
  /** Sidebar-bottom reference link to the AgentGeeX book site */
  book: "《高效的Agent》",

  group: {
    system: "系统",
    agents: "智能体管理",
    projects: "项目管理",
    plans: "计划",
  },

  item: {
    system: "系统状态",
    quickAccess: "快捷入口",
    hosts: "算力主机",
    agents: "智能体",
    sessions: "会话",
    tasks: "任务",
    executions: "任务执行记录",
    quickCmds: "快捷指令",
    skills: "技能共享",
    notices: "公告 / 工作日志",
    projects: "项目列表",
    projectGroups: "项目组模板",
    plans: "计划",
  },

  /** Workspace tab titles (detail pages use a more specific title, e.g. "Edit host" rather than "Host") */
  tab: {
    system: "系统状态",
    index: "案例索引",
    edit: {
      host: "编辑主机",
      task: "编辑任务",
      execution: "编辑执行记录",
      skill: "编辑技能",
    },
    detail: {
      agent: "智能体详情",
      session: "会话详情",
      execution: "执行详情",
      task: "任务详情",
      project: "项目详情",
      projectGroup: "项目组模板详情",
      skill: "技能详情",
      notice: "公告详情",
      host: "主机详情",
      plan: "计划详情",
      quickCmd: "快捷指令详情",
    },
  },
} as const;
