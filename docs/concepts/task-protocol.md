# AgentGeeX Task Execution Protocol

## Overview

The first message an Agent receives after startup is the task instruction. The Agent must fetch the task details itself, execute the business logic, and write the execution result back.

## Step 1: Receive the message

The message is a JSON string:

```json
{"task_run_uuid": "run_xxx"}
```

It contains only the task's unique identifier `task_run_uuid`. The Agent uses it to look up the complete task information.

## Step 2: Fetch task details

Call the MCP tool `agx_get_execution`:

```
agx_get_execution(task_run_uuid="run_xxx")
```

The response includes:
- `exec_instruction` — the task instruction
- `agent_id` — the executing party
- `send_agent_id` — the sending party
- `task_id` — the associated static task template
- `timeout_seconds` — timeout in seconds

## Step 3: Execute the task

Execute the concrete business logic according to `exec_instruction`. Approach it with a "just do it" mindset — do not ask, do not wait, execute directly.

## Step 4: Write back the result

When the task is done you must call `agx_finish_task`:

```
agx_finish_task(
    task_run_uuid="run_xxx",
    status="succeeded",
    result="...",       # structured data (optional)
    summary="..."       # human-readable summary
)
```

**AGX_FINISH_TASK IS MANDATORY.** If it is not called, the task stays in `running` forever and the watchdog raises an alert.

## Full flow

```
{"task_run_uuid": "run_xxx"}
  → agx_get_execution(task_run_uuid="run_xxx")
  → read exec_instruction
  → execute the business logic
  → agx_finish_task(task_run_uuid="run_xxx", status="succeeded", summary="...")
```
