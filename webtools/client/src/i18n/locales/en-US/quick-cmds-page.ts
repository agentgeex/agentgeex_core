/**
 * Agent management · quick command list (/quick-cmds)
 * Form fields live in `quickCmdForm`; this namespace covers list, cards and row actions.
 */
export default {
  mode: { text: "Text snippet", task: "Dispatch task" },
  visibility: { public: "Public", private: "Private" },
  executor: { self: "Myself (current identity)" },
  status: { active: "Active", inactive: "Inactive" },

  error: { load: "Could not load", archive: "Archive failed" },

  copy: {
    emptyTitle: "This command has no content",
    copied: "Copied",
    copiedDesc: "{{name}} · {{n}} chars (nothing is sent)",
    failedPrompt: "Copy failed — copy manually:",
  },

  archiveConfirm: "Archive quick command “{{name}}”?",

  column: {
    title: "Title",
    cmdId: "Command ID",
    category: "Category",
    kind: "Type",
    visibility: "Visibility",
    executor: "Runs as",
    targetTask: "Target task",
    params: "Params",
    status: "Status",
  },

  action: {
    copyTitle: "Copy this text (nothing is sent, no run record)",
    copy: "Copy",
    runTitle: "Pick a sender to run (the kernel decides permissions by that identity)",
    run: "Run",
  },

  card: {
    executor: "Runs as {{who}}",
    noDesc: "(no description)",
    textMode: "Text",
    taskMode: "Target task",
    chars: "{{n}} chars",
    params: "{{n}} params",
  },

  loading: "Loading…",
  searchPlaceholder: "Search title / command ID / owner / task…",
  newCmd: "New command",
  filter: { kind: "Type", category: "Category", project: "Project" },
  emptyText: "No quick commands yet",
} as const;
