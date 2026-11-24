# AgentGeeX Task Execution Contract

> This document is written for every Agent under the AgentGeeX system.
> When you receive a task, execute it according to this contract.

---

## 1. What you will receive

Over the direct WebSocket connection you receive a natural-language `instruction` containing:

- **`task_run_uuid`** — the execution id (for example `run_xxx`)
- **`exec_instruction`** — the concrete instruction to execute for this task
- **Task description** — a detailed statement of what you are expected to do

---

## 2. Your core responsibility

**Execute the task directly in the current session → write the result back.**

### Step 1: Understand the task

Read the received `instruction` and the task description in full, and make sure you understand:
- what to do
- what constraints apply (timeout, format, scope, and so on)
- the expected output

### Step 2: Execute the task

Do the work directly in the current session. Use every tool capability you have.

### Step 3: Write the result back (mandatory)

Call the agentgeex MCP tool **`agx_finish_task`**:

| Parameter | Value |
|------|-----|
| `caller_agent_id` | your own agent_id |
| `task_run_uuid` | the execution id you received |
| `status` | `"succeeded"` or `"failed"` |
| `result` | the result data (a JSON string) |
| `summary` | a human-readable summary |

>  **This step is mandatory.** No write-back = the task "disappears" — the watchdog will eventually mark it failed, but that is only a fallback.

---

## 3. Why you must write back

Because the daemon that dispatches tasks handles several tasks at the same time.

If you do not write back, it cannot know your task finished — and it cannot release the concurrency slot to the next task.

By writing back on your own, the daemon can:
1. Dispatch task A → immediately check whether more requests are pending
2. You work quietly → write back yourself when done
3. The daemon receives the write-back → releases the slot

That is **concurrent execution**.

---

## 4. Full example

Suppose you receive:

```
Execution id: run_xxx (example)
Instruction: collect this host's CPU, memory, disk and network information, and return it as JSON
Task description: host information collection task
```

**What you do:**
1. Run the collection commands in the current session
2. Shape the result into JSON
3. Call `agx_finish_task(task_run_uuid="run_xxx", status="succeeded", result="{...}", summary="host information collection complete")`

---

## 5. Notes

- Execute directly in the current session; do not create a sub-agent
- You must write back when the work is done, otherwise the task status stays at `running` forever
- If the task fails, set `status` to `"failed"` and state the reason in `summary`
- Prefer a JSON string for `result`, so other Agents can parse and use it easily

---

*Protocol version: v2.0 (WS direct-connect mode) | Effective date: 2026-05-25*
