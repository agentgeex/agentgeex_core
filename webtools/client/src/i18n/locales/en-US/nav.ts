/**
 * Sidebar: group names / item names / interaction hints + tab titles.
 *
 * ★ Why nav names belong in the locale files:
 *   they show up in three places — sidebar, breadcrumb, and browser tab title.
 *   Keeping them in the config means the English build is half Chinese.
 *   The config stores **keys** (`nav.item.agents`); translation happens at render.
 */
export default {
  expand: "Expand sidebar",
  collapse: "Collapse sidebar",
  newTabHint: "Ctrl/⌘+click to open in a new tab",

  group: {
    system: "System",
    agents: "Agent Management",
    projects: "Project Management",
    plans: "Plans",
  },

  item: {
    system: "System Status",
    quickAccess: "Quick Access",
    hosts: "Hosts",
    agents: "Agents",
    sessions: "Sessions",
    tasks: "Tasks",
    executions: "Runs",
    quickCmds: "Quick Commands",
    skills: "Skills",
    notices: "Notices",
    projects: "Projects",
    projectGroups: "Project Templates",
    plans: "Plans",
  },

  /** Workspace tab titles (detail pages are more specific: "Edit host", not "Hosts") */
  tab: {
    system: "System Status",
    index: "Case Index",
    edit: {
      host: "Edit host",
      task: "Edit task",
      execution: "Edit run",
      skill: "Edit skill",
    },
    detail: {
      agent: "Agent details",
      session: "Session details",
      execution: "Run details",
      task: "Task details",
      project: "Project details",
      projectGroup: "Project template details",
      skill: "Skill details",
      notice: "Notice details",
      host: "Host details",
      plan: "Plan details",
      quickCmd: "Quick command details",
    },
  },
} as const;
