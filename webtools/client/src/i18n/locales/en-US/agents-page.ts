/**
 * Agent list (card grid / table)
 */
export default {
  kind: { template: "Template", clone: "Clone", native: "Native" },

  deploy: {
    template: "Template",
    deployed: "Deployed",
    pending: "Pending",
    deploying: "Deploying",
    failed: "Deploy failed",
  },

  card: {
    sendAs: "Send a message / task as {{name}}",
    sendInfo: "Send message",
    host: "Host",
    exemptActive: "Exempt · {{time}} left",
    exemptExpired: "Exemption expired",
    clonedFrom: "Cloned from",
    viewSessions: "View {{name}}'s sessions (dialog)",
    viewTasks: "View {{name}}'s tasks (dialog)",
    viewExecutions: "View {{name}}'s run history (dialog)",
    sessions: "Sessions",
    tasks: "Tasks",
    executions: "Runs",
    execOk: "{{n}} ok",
    execBad: "{{n}} failed",
  },

  unregisteredProject: "Unregistered project",

  search: { ph: "Search name / ID / project…", aria: "Search agents" },

  scope: {
    aria: "Template scope",
    inUse: "In use",
    all: "All (incl. templates)",
    templateOnly: "Templates only",
  },

  project: { ph: "Project", all: "Project: all ({{n}})" },

  host: { all: "Host: all" },

  roleFilter: {
    ph: "Role",
    all: "Role: all",
  },

  status: {
    ph: "Status",
    all: "Status: all",
    online: "Online",
    offline: "Offline",
  },

  view: { aria: "Switch view", card: "Grid", cardTitle: "Grid view", table: "Table", tableTitle: "Table view" },

  empty: "No agents match these filters",

  table: {
    agent: "Agent",
    platform: "Platform",
    kind: "Kind",
    deploy: "Deploy state",
    role: "Role",
    status: "Status",
    sessions: "Sessions",
    tasks: "Tasks",
    executions: "Runs",
    lastActive: "Last active",
  },
} as const;
