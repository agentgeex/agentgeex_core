/**
 * Quick access page (/quick-access)
 */
export default {
  title: "Quick access",
  loading: "Loading…",
  counted: "{{count}} saved",
  empty: "Nothing saved yet",
  refresh: "Refresh",
  readFailed: "Could not load quick entries",
  removeFailed: "Could not remove the bookmark",
  toList: "Add from the {{label}} list",
  addHint: "{{hint}} — click ★ in the list to add one",
  archived: "Archived · {{id}}",
  agentCount: "{{count}} agents",
  online: "Online",
  offline: "Offline",
  expiredSuffix: " · no longer available",
  open: "Open",
  send: "Message",
  sendTitle: "Send a message (recipient prefilled)",
  removeTitle: "Remove bookmark",
  type: {
    project: "Project",
    agent: "Agent",
    session: "Session",
    plan: "Plan",
  },
  hint: {
    project: "Projects I am following",
    agent: "Agents I work with",
    session: "Sessions I use often",
    plan: "Plan flows I check often",
  },
} as const;
