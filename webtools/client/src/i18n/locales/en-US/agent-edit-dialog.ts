/**
 * Agent edit dialog
 */
export default {
  toast: {
    noChangeTitle: "Nothing changed",
    noChangeDesc: "Only edited content needs saving.",
    saved: "Saved",
    savedDesc: "Updated {{n}} field(s): {{fields}}",
  },

  error: { invalidJson: "MCP config is not valid JSON — stopped locally; nothing was sent." },

  title: "Edit agent · {{name}}",

  field: {
    name: "Name",
    namePh: "Display name, e.g. Frontend lead",
    role: "Role",
    host: "Host",
    hostPh: "Pick a host…",
    status: "Status",
    statusOn: "Enabled (can receive tasks)",
    statusOff: "Disabled (stops receiving new tasks)",
    xdesc: "Description (x_desc)",
    xdescPh: "One line on who it is and what it owns",
    prompt: "System prompt (system_prompt)",
    promptPh: "System prompt (the full persona)",
    skills: "Skill allowlist",
    skillsPh: "One skill name per line (empty = unrestricted, inherit global)",
    skillsHint: "Before writing, the kernel checks that each SKILL.md exists; a single bad name rejects the whole batch.",
    mcp: "MCP config (JSON)",
    mcpHint: "Must be a valid JSON object; malformed input stops locally and is never sent.",
    email: "Email",
    emailPh: "Empty = unchanged",
    telegram: "Telegram Token",
    telegramPh: "Empty = unchanged (never echoed)",
    feishu: "Feishu Token",
    feishuPh: "Empty = unchanged (never echoed)",
  },

  advanced: "Advanced (skill allowlist / MCP config / email & channels)",

  tokenNote: {
    part1: "The detail API deliberately does ",
    part2: "not return",
    part3: " these three fields, so the boxes are always empty. ",
    strong: "Empty = unchanged",
    part4: ", so an existing token is never cleared.",
  },

  footer: {
    note: "Only ",
    noteStrong: "changed fields",
    noteTail: " are submitted; the kernel decides whether they may be changed.",
    noteTitle:
      "Role / host / system prompt are contract fields requiring higher permission; the kernel's wording is shown below if rejected",
    cancel: "Cancel",
    save: "Save",
    saveDirty: "Save ({{n}})",
  },
} as const;
