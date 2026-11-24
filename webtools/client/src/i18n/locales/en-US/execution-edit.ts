/**
 * Execution edit page (/executions/:id/edit)
 */
export default {
  title: "Edit execution",
  notFound: "Execution {{id}} is not in the current snapshot; it may have been removed or not synced yet.",
  kernelHint: "Writes go through the kernel (agx_update_execution); field permissions are decided per field",
  fTask: "Task",
  fAgent: "Runner",
  fStatus: "Status",
  fExecName: "Short title",
  fExecNameHint: "The name shown in lists — for humans",
  fTimeout: "Timeout (s)",
  timeoutShrinkWarn: "⚠ Shorter than before: this pulls the 0.7/0.8/0.9 wake-up thresholds earlier, i.e. actively rushing a timeout",
  timeoutMinHint: "Must be at least {{min}} seconds",
  instrLabel: "Instruction (exec_instruction)",
  instrHint: "This is the instruction as it was already dispatched. Editing it will not change a running task's behaviour — only what later redeliveries or continuations read.",
  fAlertNote: "Alert note",
  fAlertNoteHint: "Leave empty to keep it unchanged (this field is not in the list snapshot, so the current value cannot be shown)",
  alertNotePlaceholder: "Only fill in when recording why a human intervened",
  updated: "Execution updated",
  errTimeoutInteger: "Timeout must be a whole number of seconds",
  errTimeoutTooSmall: "Timeout cannot be less than {{min}} seconds (kernel minimum)",
  errTitleEmpty: "Title cannot be empty",
  footerHint: "Status and acknowledgement are not edited here (the former is an outcome, the latter goes through acknowledgement)",
} as const;
