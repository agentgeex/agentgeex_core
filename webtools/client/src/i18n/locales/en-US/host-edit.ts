/**
 * Compute host edit page (/hosts/:id/edit)
 */
export default {
  missing: {
    text: "Host {{id}} does not exist — it may have been removed.",
    emptyId: "(empty id)",
    back: "Back to list",
  },

  header: { edit: "Edit compute host" },

  note: {
    kernel: "Writes go through the kernel agx_update_host (field whitelist / permissions / cache sync all live there)",
    editor: "Submitted as",
  },

  field: {
    alias: "Host alias",
    ip: "IP / address",
    sshPort: "SSH port",
    sshPortHint: "1-65535",
    os: "Operating system",
    slots: "Task slot limit",
    slotsHint: "How many agent tasks this host can run concurrently",
    hostId: "Host ID",
    hostIdHint: "The ID is the addressing key and cannot be changed",
    desc: "Description",
  },

  platforms: {
    title: "Available platforms",
    subtitle:
      "Decides which platform's agents this host can run and how the address is assembled. One entry = one platform instance (add more for multiple instances).",
    add: "Add platform",
    empty: "This host has no platforms configured yet — the agents page can only get its address after you add one.",
    itemNo: "Entry {{n}}",
    remove: "Remove",
  },

  platformField: {
    id: "Platform id",
    idHint: "openclaw / dsh / codex / claude_code / custom",
    name: "Display name",
    nameHint: "Defaults to the platform id",
    mode: "Mode",
    modeHint: "process = local process / container = container",
    process: "process (local process)",
    container: "container",
    port: "Port",
    portHintProcess: "Address = host IP + this port",
    portHintContainer: "Optional in container mode",
    basePath: "Base path",
    basePathHint: "e.g. /chat",
    profile: "profile",
    profileHint: "OpenClaw profile name; leave empty for the main instance",
    addr: "Address (addr)",
    addrHint: "Address the container owns, e.g. 172.17.0.5:18789",
    note: "Note",
    noteHint: "For humans; promote anything a machine must read into its own field",
  },

  error: {
    aliasEmpty: "Host alias cannot be empty",
    portRange: "SSH port must be an integer between 1 and 65535",
    slotsInvalid: "Task slot limit must be an integer of 0 or more",
    noChange: "Nothing has been changed",
  },

  validate: {
    tag: "Platform entry {{n}}",
    platformEmpty: "{{tag}}: the platform id cannot be empty (e.g. openclaw)",
    platformDup: "{{tag}}: platform {{pid}} is duplicated (one entry per platform per host)",
    portRequired: "{{tag}}: process mode requires a port (the address is host IP + port)",
    portRange: "{{tag}}: port must be an integer between 1 and 65535",
    addrRequired: "{{tag}}: container mode requires an address (the container is its own host; the host IP cannot be used)",
    joiner: "; ",
  },

  toast: { updated: "Host updated" },

  diff: {
    submit: "Will submit {{n}} field(s): {{fields}}",
    none: "No changes",
    autoRefresh: "The list refreshes automatically after saving",
  },
} as const;
