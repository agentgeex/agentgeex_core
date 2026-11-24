/**
 * SessionSendDialog — the "send a message to this session" entry (2026-11-04 Feng)
 *
 * ★ Role: not a new dialog, but a **session-scenario wrapper** of `MessageSendDialog` (message send).
 *   Feng's words: "we need this message send feature, this window, already tested, it works quite well.
 *   Now we map it to the other message-sending scenarios. Several of them are like this; the first is a session...
 *   for sending a message to this session, we use this too, that generic window from just now."
 *
 * ★ Locking rules (decided by "what came in", differs per entry — Feng: "because different modes come in,
 *   different things are locked"):
 *   ┌ recipient — targetAgent / targetSession  **locked**
 *   │   "because you're sending to this session" — the session is the whole context of this entry;
 *   │   being able to change it would turn "send a message to this session" into "dispatch to another Agent",
 *   │   which is another entry's job.
 *   ├ sender — senderAgent  **optional**
 *   │   this is the only real degree of freedom here: for the same session you may speak as the session owner,
 *   │   or as PM / another Agent. Identity decides permissions (kernel L5 judgement).
 *   └ execution mode — text / task / quickcmd  **all open**
 *       but text mode has a platform hard constraint: `agx_send_text` can only send into **the caller's own**
 *       session (Agent-to-Agent chat is forbidden). So when "sender ≠ session owner", text mode is unavailable —
 *       this is judged by MessageSendDialog uniformly with the reason; this component does not duplicate it.
 *
 * ★ Why not write the rules at the three call sites:
 *   the session list, the agent detail "sessions" tab, the agent detail "assistants" tab —
 *   the three places are the **same action**. Writing the rules three times will drift (precedent in this repo: the
 *   quick command high-risk grey-out kept blocking people in the frontend half a year after the kernel dropped the
 *   fence). Centralized in this one file; changing the rules is one edit.
 */
import * as React from "react";
import { MessageSendDialog } from "@/components/agentgeex/message-send-dialog";
import type { SessionRow } from "@/providers/agentgeex";

export interface SessionSendDialogProps {
  /** Target session (recipient, locked). null = do not open. */
  session: SessionRow | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Project to preselect in the top "quick locate" (the project of the session's owning Agent); empty = all projects */
  initialProject?: string;
}

export function SessionSendDialog({ session, open, onOpenChange, initialProject = "" }: SessionSendDialogProps) {
  const agentId = session?.agentId || "";
  const sessionId = session?.id || "";

  return (
    <MessageSendDialog
      /* key: switching session = switching context, must remount.
            otherwise MessageSendDialog's init effect only watches open (true→true does not fire),
            and switching straight from session A to session B would send with A's locked values. */
      key={sessionId}
      open={open && !!session}
      onOpenChange={onOpenChange}
      initialProject={initialProject}
      initial={{
        /* Default sender = the session owner (most common: send a sentence into your own session as yourself) */
        senderAgent: agentId,
        senderSession: agentId ? `agent:${agentId}:main` : "",
        targetAgent: agentId,
        mode: "text",
        sessionMode: "existing",
        targetSession: sessionId,
      }}
      locked={{
        targetAgent: true,
        targetSession: true,
      }}
    />
  );
}

export default SessionSendDialog;
