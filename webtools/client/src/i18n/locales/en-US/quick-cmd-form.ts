/**
 * Quick command · create/edit form
 * The category vocabulary is shared by the management page's filter and badges.
 */
export default {
  category: {
    network: "Network",
    system: "System",
    notice: "Notice",
    agent: "Agent",
    host: "Host",
    project: "Project",
    log: "Log",
    config: "Config",
    dev: "Dev",
    other: "Other",
  },

  sendMode: {
    task: { label: "Dispatch task", desc: "Fill parameters → creates a run record" },
    text: { label: "Text snippet", desc: "Only fills the input box, does not dispatch" },
  },

  error: {
    requiredTextPublic: "Title and text content are required",
    requiredTextPrivate: "Title, owner agent and text content are required",
    requiredTaskPublic: "Title, target task and command template are required",
    requiredTaskPrivate: "Title, owner agent, target task and command template are required",
    saveFailed: "Save failed",
  },

  title: { edit: "Edit quick command", new: "New quick command" },

  desc: {
    text: "A text snippet is a stored canned line. Clicking it only fills the input box — you decide whether to send, and no run record is created.",
    task: "A command is a parameter form plus a command template; filling the parameters sends it in one click. {name} in the template is replaced by the parameter value.",
  },

  field: {
    type: "Type",
    typeHint: "Picking the wrong type can send a canned line as a task",
    title: "Title",
    titlePh: "e.g. Clone agent",
    cmdId: "Command ID",
    cmdIdHintEdit: "Not editable",
    cmdIdHintNew: "Leave empty to derive it from the title",
    cmdIdPh: "agent_clone",
    visibility: "Visibility",
    public: "Public (whoever clicks runs it)",
    private: "Private (sent to a specific agent)",
    publicHint: "Not bound to an agent: the target is whoever clicks it (their own account must own the task)",
    privateHint: "Bound to the owner agent; always sent to that agent",
    owner: "Owner agent",
    ownerPh: "Pick an agent…",
    ownerMissing: " (not in list)",
    targetTask: "Target task",
    targetTaskPh: "task_labs (e.g. system_check)",
    category: "Category",
    skeleton: "Semantic skeleton",
    skeletonPh: "One line describing what this command does",
    templateText: "Text content",
    templateTask: "Command template",
    templateHintText: "Clicking it inserts this exact text into the input box",
    templateHintTask: "Use {parameter name} placeholders",
    templatePhText: "Write the line you want to say; clicking it fills the input box…",
    templatePhTask: "Ask {target} to perform {action}…",
    notes: "Notes",
    notesPh: "Optional",
  },

  params: {
    title: "Parameters ({{n}})",
    add: "Add parameter",
    empty: "No parameters needed — sending right away.",
    labelPh: "Label",
    required: "Required",
  },

  cancel: "Cancel",
  save: "Save",
  create: "Create",
} as const;
