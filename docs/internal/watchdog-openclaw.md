# OpenClaw Watchdog Supplement

> This file is the platform supplement to `watchdog_alert.md`. Read the generic flow first, then come back here.

## `diag_info.live` structure

When `platform = "openclaw"`, `diag_info.live` has this structure:

```json
{
  "gateway_session": {
    "key": "agent:agx_core:run_xxx",
    "status": "done",
    "age_seconds": 1800,
    "message_count": 17,
    "last_messages": [
      {"role": "assistant", "text": "analysis conclusion..."},
      {"role": "toolResult", "text": "..."}
    ]
  }
}
```

## Field semantics

| Field | Value | Meaning |
|------|------|------|
| `status` | `running` | The agent's Gateway session is still running; it may simply be slow |
| `status` | `done` | The agent finished but never called `agx_finish_task` (false death) |
| `status` | `not_found` | The Gateway session is gone (real death, or the process exited) |
| `age_seconds` | number | Seconds since the last message; helps judge whether it is "stuck" |
| `message_count` | number | Total message count of the session |
| `last_messages` | array | The last 5 messages — read directly what the agent is saying |

## Diagnostic technique

1. **Read `last_messages` first** — this is the most direct diagnostic. The last few messages tell you whether the agent is reasoning normally, stuck in a loop, or threw an exception.
2. **`status = done` + `last_messages` shows a normal finish** → false death: the agent completed the work but forgot to call `agx_finish_task`
3. **`status = running` + the last few `last_messages` entries are identical** → possible infinite loop
4. **`status = not_found`** → the session is gone; combine with `diag_info.log_tail` to find the crash cause

## Capability limits

Your runtime calls tools through the OpenClaw Gateway WebSocket. Note:
- **You cannot execute shell commands directly** (the `exec` tool is unavailable)
- **Reading logs**: use the `read` tool on the `log_path` file, not `tail -30`
- **Inspecting a session**: look at `diag_info.live.gateway_session`; you do not need to query it yourself
- **MCP tools**: `agx_get_execution`, `agx_finish_task`, `agx_dispatch_task` are available
