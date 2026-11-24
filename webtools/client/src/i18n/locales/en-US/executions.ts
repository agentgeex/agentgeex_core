/** Agent management · Runs (/executions) */
export default {
  unregisteredProject: "Unregistered project ({{id}})",
  agentLinkTitle: "{{name}}\n{{id}}\n(Click to open this agent)",
  unknownAgent: "(unknown)",

  nudge: {
    rateLimited: "You can only nudge the same task once every 60 seconds. Try again shortly.",
    stateMismatch: "This task's state no longer needs this action (it may have just finished or been handled by someone else).",
    notFound: "This run could not be found in the database.",
    kernelNotReady: "The kernel method is not live yet — the UI is ready and will work once it is provided.",
    permissionDenied: "The acting identity is not allowed to do this.",
    noteUrge: "Manual nudge from the console",
    noteAck: "Manual ack reminder from the console",
    sentUrge: "Nudged",
    sentAck: "Ack reminder sent",
    sentDesc: "Sent to {{agent}}{{via}}; nudged {{n}} time(s)",
    sentVia: " ({{target}})",
    rateTitle: "Too frequent — try again shortly",
    failTitle: "Could not send",
  },

  selfSent: "self-sent",
  columns: {
    task: "Task",
    agentPair: "Sender → receiver",
    session: "Session",
    status: "Status",
    started: "Started / duration",
  },

  scheduled: {
    chip: "Scheduled",
    title: "Scheduled task · due {{at}}",
    notStarted: "Scheduled · not started",
  },

  actions: {
    confirmTitle: "The runner finished — waiting for your approval",
    approve: "Approve",
    none: "Nothing to nudge right now",
    nudged: "Nudged {{n}} time(s)",
    refreshed: "This row has been refreshed — check whether it still needs action.",
    selfApproveForbidden: "The kernel does not allow a runner to approve its own work; someone else must do it.",
    lastNudge: "Last: {{at}}",
  },

  filters: {
    searchPlaceholder: "Search run_uuid / task / agent…",
    status: "Status",
    trigger: "Trigger",
    triggerScheduled: "Scheduled",
    triggerImmediate: "Immediate",
    project: "Project",
    range: "Run time",
    range7d: "Last 7 days",
    range30d: "7–30 days",
    rangeOlder: "Over 30 days",
    rangeUnknown: "No creation time",
    agent: "Agent",
  },
} as const;
