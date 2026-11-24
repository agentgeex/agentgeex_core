/**
 * QuickCmdSendDialog — the "run this quick command" entry (2026-11-04 Feng)
 *
 * ★ Positioning: not a new dialog, but a **quick-command scenario wrapper** around `MessageSendDialog` (message send).
 *   Feng's exact words: "In this quick command there is a method to send this quick command. Match it with the
 *   generic one from before, and then some things get locked — for example this quick command is certainly locked,
 *   it cannot be changed. And this quick command has public and private ones: if it is private, then the executor
 *   is also locked, only the sender is not locked; if the quick command is public, then the receiver can be selected."
 *
 * ★ Locking rules (three; the first two come from kernel semantics, not our choice):
 *
 *   ┌ the quick command itself  cmdId  **locked**
 *   │   entered from "this one"; being able to change it means a different operation.
 *   │
 *   ├ private command (ownerAgent non-empty) → executor(targetAgent)  **locked = ownerAgent**
 *   │   the kernel dispatches by target_agent_id in the command table → the executor is a property of the
 *   │   command, not an option. Only the **sender** (who presses the button / who places the order) can change.
 *   │
 *   └ public command (ownerAgent empty) → executor = sender  **decided by the sender**
 *       kernel `is_public` branch: target_agent_id = caller_agent_id.
 *       So "choosing the receiver" and "choosing the sender" are the same thing — the UI keeps a single
 *       selectable (the sender) and shows the receiver as "= sender", instead of two controls that look
 *       independently settable but are always equal.
 *
 * ★ Why this decision is not made in this file:
 *   the public/private effect on the executor is **kernel semantics** and holds for every entry (including the
 *   quick path on the agent page), so the decision lives in MessageSendDialog
 *   (`targetDefinedByCmd` / `targetDerivedFromSender`). This file only handles "which command we came from →
 *   what to prefill, what to lock".
 */
import * as React from "react";
import { MessageSendDialog } from "@/components/agentgeex/message-send-dialog";
import type { QuickCmd } from "@/providers/agentgeex";

/** sender identity memory key (human != agent: identity is selectable, remember the last choice). */
/** read the last sender; the old key win3.qc.caller migrates automatically (see lib/storage-keys.ts). */
function rememberedCaller(): string {
  return readKey("quickCmdCaller") || "";
}

import { readKey } from "@/lib/storage-keys";
export interface QuickCmdSendDialogProps {
  /** the quick command to run (locked). null = do not open. */
  cmd: QuickCmd | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function QuickCmdSendDialog({ cmd, open, onOpenChange }: QuickCmdSendDialogProps) {
  const isPrivate = !!cmd?.ownerAgent;
  /* default sender: remembered last choice → falls back to the command owner.
     A public command has no owner, only "the identity used last time"; if neither exists leave it empty for the user. */
  const defaultSender = React.useMemo(
    () => (cmd ? rememberedCaller() || cmd.ownerAgent || "" : ""),
    [cmd],
  );

  return (
    <MessageSendDialog
      /* key: changing the command = changing the context, must remount (the init effect only watches open). */
      key={cmd?.cmdId}
      open={open && !!cmd}
      onOpenChange={onOpenChange}
      fixedCmd={cmd}
      initial={{
        senderAgent: defaultSender,
        senderSession: defaultSender ? `agent:${defaultSender}:main` : "",
        targetAgent: cmd?.ownerAgent || "",
        mode: "quickcmd",
        cmdId: cmd?.cmdId || "",
      }}
      locked={{
        quickcmd: true,
        /* private command: the executor is defined by the command; public commands are not locked (they follow the sender). */
        ...(isPrivate ? { targetAgent: true } : {}),
      }}
    />
  );
}

export default QuickCmdSendDialog;
