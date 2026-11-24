/**
 * Dispatch exemption panel (agent detail / agent cards)
 */
export default {
  quick30: "30 min",
  quick2h: "2 hours",
  quick8h: "8 hours",
  quickReason: "Console quick setting ({{label}})",
  customReason: "Console custom setting",

  remainDays: "{{d}}d {{h}}h",
  remainHoursMinutes: "{{h}}h {{m}}m",
  remainHours: "{{h}}h",
  remainMinutes: "{{m}}m",
  remainZero: "0 min",
  remainUnderMinute: "less than a minute",

  notifyOk: "; the agent has been notified",
  notifyFail: "; but the notification failed ({{error}})",

  closedMsg: "Dispatch exemption for {{name}} is now off{{tail}}",
  grantedMsg: "{{name}} may now dispatch; expires {{until}} ({{left}} left){{tail}}",
  notEffectiveMsg: "Written, but the kernel's read-back says it is not in effect (until={{until}}) — please refresh to confirm",

  toastClosed: "Dispatch exemption turned off",
  toastGranted: "Dispatching allowed",
  permissionHint: " (a project admin or workshop_manager must enable this)",

  title: "Dispatch exemption",
  badgeActiveTitle: "Expires {{until}}",
  badgeActive: "Active · {{left}} left",
  badgeExpiredTitle: "Expired on {{until}}",
  badgeExpired: "Expired (on {{until}})",
  none: "None",
  scopeHint: "Only the three dispatch calls are opened (dispatch_task / schedule_task / quick_cmd_send) — not project creation or agent edits",
  customOpen: "Custom…",
  clear: "Turn off",

  dialogTitle: "Set dispatch exemption · {{name}}",
  dialogDesc: "Expires automatically — no need to turn it off. Only the three dispatch calls are opened; the role level is unchanged.",
  fUntil: "Expires at",
  fReason: "Reason (written to the audit log, optional)",
  reasonPlaceholder: "e.g. so it can hand results to a reviewer for a while",
  cancel: "Cancel",
  confirm: "OK",
} as const;
