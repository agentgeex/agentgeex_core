/**
 * Message send dialog (the largest form in the app)
 * The quick-command preview body is data, not UI copy — never translated here.
 */
export default {
  title: "Send message",

  sessionType: {
    main: "Main session",
    case: "Case",
    task: "Task",
    oneshot: "One-off",
    assistant: "Assistant",
  },

  running: {
    label: "Running",
    title: "Session is running",
    titleWithRun: "Session is running · run {{id}}",
  },

  open: {
    agent: "Open",
    agentDisabled: "Pick an agent first",
    agentTitle: "Open this agent's detail · {{id}}",
    agentPopupTitle: "Open this agent's detail in a dialog (stay on this window) · {{id}}",
    session: "Open session",
    sessionNoPage: "This session has no page in OpenClaw (agent not registered / platform does not support deep links)",
    sessionDisabled: "Pick a session on the left first",
    sessionTitle: "Open this session in OpenClaw · {{href}}",
  },

  lockedUnset: "(not set)",

  mode: {
    text: { label: "Text", hint: "Send a line of text directly (no run record)" },
    task: { label: "Task", hint: "Wrap it as a task and send" },
    quickcmd: { label: "Quick command", hint: "Send by command template + parameters" },
  },

  phrases: {
    title: "Canned text",
    open: "Insert a canned line at the cursor (does not send)",
    chars: "{{n}} chars",
    noSender: "No sender selected",
    add: "New",
    addTitle: "Create a canned line right here",
    loading: "Loading…",
    empty: "No text snippets yet. Click “New” at the top right to write one — it only stores a line, never dispatches a task.",
    ownGroup: "This agent's",
    sharedGroup: "Shared · available to all agents",
  },

  voice: {
    recording: "Recording — click “Done” to transcribe before sending",
    transcribing: "Transcribing the recording; you can send once it finishes",
  },

  summaryUnselected: "None",
  summaryNewSession: "New session (created automatically)",
  refreshFailed: "Refresh failed",

  silentHint: "This task is designed to be silent",

  assistantAutoHint: "This session is an assistant: the system automatically prepends an identity note (~500 chars) when sending; the text you type is unchanged",

  validate: {
    sender: "Pick a sender",
    target: "Pick a target agent",
    reply: "Choose whether a reply is needed",
    textImpossible: "Text mode can only be sent by the session owner (agents cannot chat with each other); switch to Task or Quick command to change the sender",
    textEmpty: "Enter the message body",
    textSession: "Text mode can only go to the sender's own session — pick a target session",
    skillsMax: "Select at most 8 skills",
    task: "Pick a Task",
    session: "Pick a target session",
    quickcmd: "Pick a quick command",
    paramRequired: "Parameter “{{name}}” is required",
  },

  error: {
    permission: "Your role cannot run this (needs L5 send permission: PM / system core role)",
    notOwned: "The target session does not belong to the target agent (pick again)",
    replyRequired: "Choose whether a reply is needed (one of the two)",
    taskNotFound: "The task behind this command does not exist for the receiver (a shared command requires the receiver to own a task with the same name)",
    cmdNotFound: "The command does not exist or is archived",
    fallback: "Submit failed",
  },

  emptyInstruction: "(No instruction given — running the “{{name}}” task description)",
  toolbar: {
    maximize: "Fullscreen",
    maximizeTitle: "Fullscreen (fill the whole screen)",
    restore: "Restore window",
    restoreTitle: "Restore window",
    starOn: "Add to shortcuts",
    starOff: "Remove from shortcuts",
    starBlocked: "Pick a receiving session or agent before adding",
    starAddSession: "Add to shortcuts: this session",
    starAddAgent: "Add to shortcuts: this agent",
    starRemoveSession: "Remove from shortcuts: this session",
    starRemoveAgent: "Remove from shortcuts: this agent",
    refresh: "Refresh data",
    refreshTitle: "Refresh data (agents / sessions / tasks / skills / quick commands)",
  },

  project: "Project",
  allProjects: "All projects ({{n}})",
  pickAgent: "Pick an agent…",
  quickHint: "Also sets the sender / receiver below",
  lockedTarget: "Receiving session",

  draft: {
    restoredAt: "Restored unsent content for “{{name}}” from {{time}}",
    restoredLast: "Restored your last unsent content",
    othersTitle: "{{n}} more draft(s) saved per receiver — switching to that receiver restores it",
    othersCount: "{{n}} more",
    clear: "Discard",
  },

  layer: {
    sender: "Sender",
    execMode: "Execution mode",
    execModeHint: "Pick one · decides what appears below",
    receiver: "Receiver",
    params: "Execution options",
    paramsHint: "Optional · shared by all tasks (matches the run dialog)",
    business: "Business attributes",
    businessHint: "Reserved slot · not in this round",
  },

  field: {
    senderAgent: "Sending agent",
    senderSession: "Sending session",
    targetAgent: "Receiving agent",
    task: "Task",
    quickcmd: "Quick command",
    needsReply: "Reply needed",
    needsConfirm: "Human confirmation",
    timeout: "Timeout (s)",
    title: "Title",
    projectReserved: "Project (reserved)",
    moduleReserved: "Module (reserved)",
  },

  ph: {
    senderAgent: "Pick a sending agent…",
    senderSession: "Pick a sending session…",
    targetFirst: "Pick a receiving agent first",
    senderFirst: "Pick a sending agent first",
    agent: "Pick an agent…",
    task: "Pick a Task…",
    agentFirst: "Pick an agent first",
    loading: "Loading…",
    quickcmd: "Pick a quick command…",
    targetSession: "Pick a target session…",
    session: "Pick a session…",
    timeout: "Empty = default (3600)",
    execName: "Empty = automatic (e.g. weekly report)",
    reserved: "To be filled by a pluggable dynamic form",
  },

  empty: {
    noSenderSessions: "(No sessions available; the main session will be used)",
    noTasks: "(This agent has no selectable Task)",
    cmdsCannotDeliver: "(None of these commands can reach the current session: the sender must equal the receiver)",
    noCmds: "(No quick commands available)",
    noSessions: "(This agent has no sessions)",
  },

  cmdMapping: {
    owner: "Owner agent",
    target: "Target task",
    fixed: " (fixed by the command, not editable)",
  },

  sessionMode: { new: "New session", existing: "Existing session" },

  skills: {
    title: "Skills",
    hint: "Optional · only active skills are listed ({{n}} selected, max 8)",
    remove: "Remove: {{name}}",
    clear: "Clear",
    search: "Search skills (name / description / tag / category)",
    empty: "No active skills in the library",
    noMatch: "No matching skills",
    noDesc: "(no description)",
    textHint: "Text mode: selected skills are sent with the message body (as “reference skills” pointers) and the receiver uses them to call agx_skill_get.",
    taskHint: "A “reference skills” pointer (skill_id + name + one line) is appended to the instruction; the runner calls agx_skill_get for the full text before executing.",
  },

  textOnly: "Text can only go to the sender's own session — switching aligns the receiver to the sender automatically",

  reply: { need: "Reply needed", noNeed: "No reply needed" },

  confirm: {
    title: "Needed: a successful run does not unlock downstream work; it lands in “awaiting confirmation” until a human approves",
    no: "Not needed (default)",
    yes: "Needed (waits for human approval)",
    note: "“Human confirmation” means a successful run is not completion: it lands in “awaiting confirmation” until a human approves.",
    silent: "This task is designed to be silent (no reply needed).",
  },

  paramsNote: {
    text: "Text mode sends directly (no execution record), so these three do not apply; use Task or Quick command if you need them.",
    quickcmd: "All three are passed to the kernel via agx_quick_cmd_send (receipts are force-disabled when no reply is needed). (Human confirmation is a task-level attribute; quick commands have no such parameter.)",
    task: "All three are passed to agx_dispatch_task (same as the run dialog).",
  },

  prompt: {
    text: "Message body",
    task: "Task instruction",
    quick: "Command parameters",
    quickWithCount: "Command parameters · {{n}}",
    quickHint: "Fill in the parameter table (* = required)",
  },

  summary: { from: "From", to: "To", none: "None" },

  submit: { idle: "Send", busy: "Sending…" },

  input: {
    labelContent: "Content",
    labelInstruction: "Instruction",
    phContent: "Type the message to send…",
    phInstruction: "Type or edit the task instruction…",
    zoomTitle: "Open the editor: write long content in a big window instead of scrolling",
  },

  quickPanel: {
    loadingCmds: "Loading this agent's quick commands…",
    pickFirst: "Pick a quick command above first",
    noParams: "This command needs no parameters — you can send it now",
    previewTitle: "Command preview (live, illustrative only)",
    previewEmpty: "(none)",
  },

  zoom: {
    title: "Full editor · {{mode}}",
    count: "{{n}} chars · {{lines}} lines",
    sync: "Synced live with the input below — closing keeps it, no extra save needed",
    done: "Done",
  },
} as const;
