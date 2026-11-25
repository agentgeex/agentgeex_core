# Chapter 5 · Execution

Once a task is dispatched, the system produces an execution record. This chapter covers how an execution changes state, the timeout and watchdog mechanisms, receipts and acknowledgement, and the fields that make up the execution record.

---

## 5.1 What an execution is for

A task defines how the work is done. The same work happens many times, each with its own instruction, parameters, result and duration, and each needs its own record. The execution record provides that:

| Purpose | Description |
|---|---|
| Traceability | Who dispatched which instruction when, and what came of it |
| State management | Whether it is waiting to be activated, running, finished or failed |
| Exception handling | After a timeout the watchdog wakes, alerts and resends |
| Statistics | Duration, token consumption, success rate |

## 5.2 Definition of an execution

An execution record represents one run of a task and is uniquely identified by `task_run_uuid`.

A dispatch method does three things in one call: creates the execution record, assembles and delivers the message, and synchronizes the cache. The caller does not perform them separately.

```python
agx_dispatch_task(
    caller_agent_id="agx_core",
    agent_id="some_agent",
    task_id="task_xxx",
    instruction="Count the tasks created this week",
    needs_reply=1,
)
```

The return value contains the `task_run_uuid`, which locates the run for later queries, closing and nudging.

### 5.2.1 Task and execution

```
task         this kind of work    the capability "analyze network status"
 └ execution one run              "the 11-24 09:52 run, 8 minutes, concluded ..."
```

One task can produce many executions. An execution never modifies the task definition, and changes to the definition do not affect executions already created.

## 5.3 Lifecycle

```
draft ──deploy/activate──> pending ──deliver──> running ──┬──> succeeded
                                                          ├──> failed
                                                          ├──> cancelled
                                                          └──> awaiting_confirm ──approve──> succeeded
                                                                                └──reject───> failed
```

## 5.4 States

| State | Meaning | How it is entered |
|---|---|---|
| `draft` | Created but not published. The watchdog does not scan this state | Pre-created when a plan is deployed |
| `pending` | Waiting to be activated or delivered | Plan activation; a scheduled task not yet due |
| `running` | Executing | Delivery succeeded |
| `succeeded` | Completed successfully | The executor calls the closing method |
| `failed` | Failed | The executor calls the closing method, or the watchdog decides |
| `cancelled` | Cancelled | Plan cancellation or a manual void |
| `awaiting_confirm` | Delivered, waiting for a human to approve | A successful run with `needs_confirm=1` |

In the current database most executions are `succeeded` (about 6400), with about 730 `draft`, 480 `cancelled` and 76 `failed`. A large `draft` count is normal: steps of a deployed plan that has not been activated stay in this state.

## 5.5 Waiting for approval

Some tasks do not go straight to `succeeded` when they succeed; they stop in `awaiting_confirm` until a person approves.

| Attribute | Value | Meaning |
|---|---|---|
| `needs_confirm` | 0 | No approval needed |
| | 1 | Awaiting approval. A successful run lands in `awaiting_confirm` |
| | 2 | Approved |
| | 3 | Rejected |

Approval and rejection go through `agx_confirm_task(task_run_uuid=..., action="approve"|"reject")`.

`awaiting_confirm` does not count toward the timeout. So that approval has time to happen, when `needs_confirm=1` and no timeout was given at dispatch, the system raises the timeout to at least 86400 seconds (one day).

Note that `reply_mode=confirm` and `needs_confirm` are different things: the former is the receipt mode declared on the task template, the latter is whether this particular run needs a human to approve it. The first affects receipts, the second affects state transitions.

## 5.6 Timeout

The timeout decides when the watchdog starts to intervene. The final value is chosen in this order:

| Order | Source | Description |
|---|---|---|
| 1 | `timeout_seconds` passed at dispatch | The passed value wins |
| 2 | `needs_confirm=1` and none passed | At least 86400 seconds |
| 3 | `task_type=plan` and none passed | Reads `plan_timeout` (default 3600 seconds) |
| 4 | Everything else | Reads `task_timeout` (default 3600 seconds) |

The timeout can be adjusted while the run is in progress:

```python
agx_update_execution(
    caller_agent_id="agx_core",
    task_run_uuid="run_xxx",
    updates={"timeout_seconds": 10800},
)
```

Only extension is allowed. Shortening triggers the wake tiers immediately, so only administrators and global roles may do it. The range is 60 to 86400 seconds.

## 5.7 The watchdog

The watchdog is a separate process, not a function inside the daemon. It scans execution records on a fixed cycle and intervenes in four tiers.

| Tier | Trigger | Action | Configuration |
|---|---|---|---|
| Wake | The run reaches 70%, 80% and 90% of its timeout | One reminder each, never repeated | `watchdog_wake_tiers` |
| Alert | `timeout_seconds` exceeded | Writes `alert_level` and `alert_count`, raises an alert | `watchdog_max_retries` |
| Resend | The message is judged undelivered | Redelivers, up to a limit | `send_resend_after` 600 seconds, at most 2 times |
| Manual nudge | All the above exhausted and still nothing | A person pushes it forward | `agx_nudge_execution` |

Details:

| Parameter | Default | Effect |
|---|---|---|
| `watchdog_wake_grace` | 120 seconds | Minimum interval between wakes of the same tier |
| `watchdog_grace_period` | 1800 seconds | Grace period before an alert |
| `watchdog_max_retries` | 3 | Maximum number of alerts |
| `watchdog_host_heartbeat_timeout` | 90 seconds | Host heartbeat timeout |

Meaning of `alert_level`:

| Value | Meaning |
|---|---|
| 0 | No reminder |
| 1 | Woken |
| 2 | Alerted |
| 3 | Escalated to a person |
| 9 | Settled |

Wake count and alert count are recorded separately in `wake_count` and `alert_count`, which shows whether the automatic means are exhausted.

### 5.7.1 What started_at can and cannot tell you

`started_at` is written lazily: only when delivery to the executor is confirmed. Therefore:

| Observation | Meaning |
|---|---|
| `started_at` empty, `run_status=running` | Sent, start of execution not yet confirmed |
| `started_at` non-empty | Delivery confirmed |

To decide whether a run actually executed, look at `finished_at` and `summary`, not at `started_at` alone.

## 5.8 Receipts and acknowledgement

A receipt answers "did the other side handle it, did they read it". It is not the same thing as the execution result.

### 5.8.1 needs_reply

| Value | Meaning |
|---|---|
| 0 | No reply needed |
| 1 | Reply needed |
| 2 | Replying |
| 3 | Replied |

If dispatch does not set it explicitly, the system derives it from the task's `reply_mode`: `result` and `confirm` set 1, `silent` sets 0. `force_no_reply=True` has the highest priority.

### 5.8.2 Acknowledgement

A receipt closes the loop only when the receiver reads it and acknowledges.

| Field | Description |
|---|---|
| `is_reply_to` | On a receipt record, the `task_run_uuid` of the original execution |
| `reply_sent_at` | When the receipt was sent |
| `reply_delivered_at` | When delivery of the receipt was confirmed |
| `reply_read` | Whether acknowledged: 0 no, 1 yes |
| `reply_read_at` | First acknowledgement time |
| `reply_read_last_at` | Most recent acknowledgement time |
| `ack_state` | Aggregate state: `none` / `sent` / `delivered` / `read` / `failed` |

`reply_read_at` records only the first acknowledgement; repeated acknowledgements refresh `reply_read_last_at` only. This keeps the statistic "how long after sending was the receipt read" from being erased by a repeat.

Acknowledgement uses `agx_ack_receipt(caller_agent_id=..., task_run_uuid=...)` and only works on a receipt addressed to the caller. Acknowledging pushes `needs_reply` to 3, and the watchdog takes the matter as closed.

Unacknowledged receipts are resent: `receipt_resend_after` defaults to 600 seconds, with at most 2 resends, so 3 sends including the first.

### 5.8.3 Three ways of ending

| Action | Target | Effect |
|---|---|---|
| Close `agx_finish_task` | Your own execution | Writes status, result and summary; frees the session |
| Acknowledge `agx_ack_receipt` | A receipt you received | Marks it read and stops the receipt tracking |
| Nudge `agx_nudge_execution` | A stuck execution | Creates nothing and changes no status; only pushes |

## 5.9 What an execution records

An execution stores three kinds of content:

| Field | Description |
|---|---|
| `exec_instruction` | The complete instruction handed to the agent for this run |
| `summary` | The human-readable conclusion written back by the executor |
| `result_data` | Structured result, usually JSON |
| `exec_name` | Short title of this run, one line saying what this run is doing |

`exec_name` is an instance-level description, as opposed to the task's `description`, which is type-level. When a list contains several tasks with the same name, this is what tells them apart.

<details>
<summary>Main columns of agx_task_execution</summary>

| Column | Description |
|---|---|
| `task_run_uuid` | Unique ID of this run |
| `task_id` | The related task |
| `agent_id` | Executor |
| `send_agent_id` | Sender |
| `send_session_id` | Sender's session, used to route the receipt back |
| `host_id` / `project_id` | Executing host and owning project |
| `plan_id` | Owning plan; empty for non-plan tasks |
| `prev_task` / `next_task` | Preceding and following executions, used for serial chains |
| `parent_run_uuid` | Parent execution, used to trace sub-tasks |
| `session_id` | Session the run lands in |
| `run_status` | State |
| `dispatched_at` | When it was actually sent to the executor; the baseline for resend decisions |
| `started_at` | Start time, written lazily, may be empty |
| `finished_at` | Completion time |
| `scheduled_at` | Intended run time; the watchdog activates it when due |
| `timeout_seconds` | Timeout |
| `needs_reply` | Receipt requirement 0–3 |
| `needs_confirm` | Human approval 0–3 |
| `alert_level` / `alert_count` / `wake_count` | Reminder level and counts |
| `alerted_at` / `alert_reason` / `alert_target` | Time, reason and recipient of the latest reminder |
| `quick_cmd_id` / `quick_cmd_params` | Which quick command dispatched it, and with which parameters |
| `total_tokens` / `input_tokens` / `output_tokens` | Token consumption |
| `diag_info` | Diagnostic information |

</details>

## 5.10 Execution operations

| Method | Purpose |
|---|---|
| `agx_dispatch_task` | Create an execution and deliver it immediately |
| `agx_schedule_task` | Create the record only, in `pending`, without delivering |
| `agx_activate_task` | Activate a `pending` execution and deliver it |
| `agx_get_execution` | Read execution details with preceding-execution context and child list |
| `agx_finish_task` | Close it, writing status, result and summary |
| `agx_update_execution` | Update execution attributes, authorized by identity |
| `agx_continue_task` | Continue in the existing session without creating a new one |
| `agx_confirm_task` | Approve or reject an `awaiting_confirm` execution |
| `agx_nudge_execution` | Manual nudge: creates nothing, changes no status |
| `agx_ack_receipt` | Acknowledge a receipt |
| `agx_ack_notice` | Acknowledge a notification event |
| `agx_task_events` | View the wake, alert, receipt and acknowledgement timeline of a run |
| `agx_running_tasks` | View executions currently running and how long they have run |

Write permission in `agx_update_execution` depends on the field:

| Field class | Identities that may write |
|---|---|
| `timeout_seconds`, `exec_instruction`, `alert_note`, `scheduled_at`, `x_desc` | Executor, sender, project manager, global roles |
| `session_id`, `send_agent_id`, `prev_task`, `next_task`, `plan_id`, `exec_name`, `reply_read` | Executor, project manager, global roles |

The sender may change how a run is executed and add conclusions, but may not change who it was dispatched to or which chain it hangs on.

## 5.11 When not to use

- **Just saying something, with no record wanted**: use `agx_send_text`; it creates no execution.
- **Dispatching the same task again to show progress**: that runs several executions in parallel and their results overwrite one another. Use `agx_nudge_execution` to push instead.
- **Editing `run_status` by hand**: only the closing method may change it, and the watchdog does not recognize a hand-edited value.
- **Using `started_at` to decide whether a run happened**: the field is written lazily and may be empty. Judge by `finished_at` and `summary`.
- **Watching a long task by hand for a long time**: set a sensible `timeout_seconds` and let the watchdog intervene in four tiers. A manual nudge is the last resort.
