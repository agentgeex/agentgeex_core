# Watchdog automatic wake-up

## Problem

On the OpenClaw platform a session can go "false-dead" in the following situations:
- context overflow / compaction
- long-running script execution
- model server-side timeout

The Agent is technically still running but no longer consumes messages, so the task stays stuck until it times out.

## Approach

At 90% of the task timeout, the watchdog sends a wake-up message to the Agent session. The Agent then has a 600-second grace period to finish or extend. If the grace period expires without completion, the task is judged timed out.

## Workflow

```
task starts → runs until 90% of timeout → watchdog sends wake message → 600s grace period
                                              ↓ Agent responds
                                          ├─ finish → ends normally
                                          └─ no response → grace expires → marked timed out
```

## Configuration

> **Corrected 2026-10-24**: earlier versions read the environment variables `AGX_WATCHDOG_WAKE_*`;
> these are now the `daemon` section of `agx_config.json`. The environment-variable form is
> **deprecated** (no longer referenced anywhere in the code).

Where the config lives: `agx_config.json` → `daemon` section.

| Config key | Default | Description |
|------|--------|------|
| `watchdog_wake_enabled` | `1` | Whether wake-up is enabled (1=enabled, 0=disabled) |
| `watchdog_wake_ratio` | `0.9` | Fraction of the timeout at which the wake fires (0.0~1.0) |
| `watchdog_wake_grace` | `600` | Grace period after the wake, in seconds |
| `watchdog_wake_message` | see below | Content of the wake-up message |
| `watchdog_session_done_wake_cooldown` | `60` | Cooldown for the session-done wake, in seconds |

Example:

```json
{
  "daemon": {
    "watchdog_wake_enabled": "1",
    "watchdog_wake_ratio": "0.9",
    "watchdog_wake_grace": "600"
  }
}
```

**Restart the watchdog for changes to take effect.**

## Duplicate-send prevention

Wake state is recorded in the Redis key `agx:watchdog:wake:{run_uuid}`:

```
1st loop: key absent → send message → write key(sent_at, count=1) → wait
2nd loop: key present → now < sent_at+WAKE_GRACE → stay silent (no duplicate send)
...
Nth loop: key present → now >= sent_at+WAKE_GRACE → grace expired → judge timed out
```

- key TTL = `WAKE_GRACE × 2` (automatic cleanup)
- `_clear_wake_state` is called explicitly when the task finishes or is judged timed out

## Code location

`scripts/agx_watchdog.py`

> Line numbers calibrated 2026-10-24 (the file has grown to 2288 lines; old line numbers are
> generally off by +150~+380). **Line numbers are for orientation only — trust the function name.**

| Function | Line | Responsibility |
|------|:---:|------|
| `_try_wake()` | 1082 | Core logic: decide whether to wake / whether the grace period expired |
| `_get_wake_state()` | 259 | Read the wake record from Redis |
| `_save_wake_state()` | 300 | Write the wake record to Redis |
| `_clear_wake_state()` | 311 | Delete the wake record |
| `_send_wake_to_session()` | 863 | Route the message send according to agent_type |
| `_send_wake_via_ws()` | 889 | OpenClaw platform: wake over the WS channel |
| `_send_wake_via_tmux()` | 966 | tmux platform: wake by shell injection |
| `_check_session_done_wake()` | 999 | Wake when the session is done but the task was never closed |

## Relationship to TEST_MODE

When `daemon.watchdog_test_mode=1` (formerly the environment variable `AGX_WATCHDOG_TEST_MODE`),
the wake logic is skipped (the condition is `WAKE_ENABLED and not TEST_MODE`, see
`scripts/agx_watchdog.py:1188`).

In test mode the wake feature is skipped but the wake variables stay in effect; switching to
production mode (`TEST_MODE=0`) enables it automatically.

## Wake message content

```
 系统提醒：如果任务仍在执行中，请继续完成。如果已完成，请调用 agx_finish_task 更新状态。
```

> The line above is the literal runtime text emitted by the watchdog, and it is localized in
> Chinese: "System reminder: if the task is still running, please continue. If it is already
> done, call `agx_finish_task` to update the status."

## Change history

| Date | Change |
|------|------|
| 2026-06-04 | env.sh gained the four variables WAKE_RATIO/WAKE_GRACE/WAKE_ENABLED/WAKE_MESSAGE |
| 2026-10-24 | **Config source migrated from environment variables to the `daemon` section of `agx_config.json`**; this document corrected accordingly (line numbers recalibrated, 3 functions added, session_done cooldown entry added) |
