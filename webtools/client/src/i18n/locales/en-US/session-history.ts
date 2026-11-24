/**
 * Session history (shared by the send dialog and session detail)
 * The `【本次指令】` / `【任务说明书】` markers are kernel protocol, not UI copy — untranslated.
 */
export default {
  kind: {
    human: "Human",
    envelope: "Task message",
    notice: "Notice",
    system: "System",
    command: "Command",
  },

  dir: {
    dispatch: "Sent",
    receipt: "Receipt",
    dispatchArrow: "Sent →",
    receiptArrow: "← Receipt",
    other: "Message",
    urge: "Nudge",
  },

  noReply: "(No reply captured for this turn)",
  noReplyHint: "No text reply was paired with this message: the turn may have been tool calls only, the reply may have been merged into the next message, or collection has not written it yet.",

  collapse: "Collapse",
  expand: "Show full text ({{n}} chars)",

  envelope: {
    title: "Envelope",
    processed: "(raw message, unprocessed)",
    instruction: "Instruction",
    noInstruction: "(No instruction body / run already cleaned up)",
    viewDetail: "View message",
    viewDetailWithLen: "View message ({{n}} chars)",
    openTitle: "Click to view message details",
    dialogDesc: "This message is the dispatch protocol envelope; the instruction body comes from the run record (`agx_task_execution`) and is not duplicated in the message table.",
    bodyTitle: "Instruction body",
    bodyLen: "({{n}} chars{{suffix}})",
    bodyPreview: " · preview",
    bodyFull: " · full text",
    fetchingFull: "Fetching full text…",
    previewOnly: "preview only",
    fullText: "full text",
    noBody: "No body",
    fetchFailed: "Could not fetch full text: {{msg}}",
    noBodyHint: "No instruction body. Likely the run_uuid was cleaned from `agx_task_execution`, or the envelope has no run_uuid.",
    meta: {
      msgType: "Message type",
      msgTypeNoHeader: " (envelope has no msg_type)",
      runUuid: "Run ID",
      runUuidMissing: "(not present in envelope)",
      execName: "Run title",
      execNameMissing: "(run cleaned up or untitled)",
      sender: "Sender",
      runner: "Runner",
      runStatus: "Run state",
      at: "Message time",
      bodyLen: "Body length",
    },
  },

  reply: {
    toolCallsHint: "Tool calls happened in the middle of this reply",
    toolCalls: "tools ×{{n}}",
    answer: "Reply",
  },

  history: {
    title: "History",
    meta: "{{total}} message(s){{loaded}}{{updated}}",
    metaLoaded: " ({{n}} loaded)",
    metaUpdated: " · updated {{at}}",
    metaNoSession: "Shown once a receiving session is chosen",
    refreshTitle: "Refresh (messages keep arriving; what you just said may not be stored yet)",
    newSession: "A new session has no history yet. Pick an existing session, or send the first message.",
    loadFailed: "Could not load: {{msg}}",
    loading: "Loading…",
    empty: "This session has no messages yet{{name}}.",
    emptyName: " ({{name}})",
    loadingMore: "Loading…",
    loadMore: "↑ Load earlier messages",
    queued: "Sent while busy · processed later",
    queuedHint: "The agent was busy with the previous turn, so this message was queued before being processed (the English wrapper added on ingest is stripped for display).",
  },

  pending: {
    waiting: "Sent · awaiting storage",
    slow: "Sent · not found yet (still checking)",
    timeout: "Sent · not found in storage",
    modeTask: "Task",
    modeQuickCmd: "Command",
    hasSkills: "Includes skill notes",
    hasSkillsHint: "A “reference skills” block was appended to the end of this message",
    realLen: "Actual instruction: {{n}} chars",
    readingReal: "Reading actual instruction…",
    readingText: "(loading)",
  },
} as const;
