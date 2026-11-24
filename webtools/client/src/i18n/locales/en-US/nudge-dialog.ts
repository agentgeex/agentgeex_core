/**
 * Nudge / request-ack confirmation dialog
 */
export default {
  nudgeTitle: "Send a nudge?",
  nudgeWho: "Runner",
  nudgeWhat: "Ask it to report progress or say where it is stuck",
  nudgeVerb: "Nudge",
  ackTitle: "Request acknowledgement?",
  ackWho: "Requester",
  ackWhat: "It has a receipt that is still unacknowledged; ask it to acknowledge",
  ackVerb: "Request ack",
  warning: "This really sends a message to an agent and interrupts what it is doing.",
  fTarget: "To",
  fContent: "Content",
  fTask: "Task",
  repeated: "This item has been nudged manually {{count}} times. If it still does not move, the decision belongs to the requester (redispatch or terminate); nudging again only adds noise.",
  cancel: "Cancel",
  sending: "Sending…",
  confirm: "{{verb}}",
} as const;
