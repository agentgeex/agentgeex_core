# AgentGeeX WS Direct-Execution Manual

> This document is written for Agents under the AgentGeeX system.
> When you receive a task over WebSocket, execute it according to this manual.

---

## 1. How you receive a task

The daemon (`agx_daemon`) wakes you up directly via **WebSocket → Gateway → sessions.create**.

You receive a natural-language `instruction` containing:

| Field | Description |
|------|------|
| `task_run_uuid` | The execution id (for example `run_xxx`) |
| `instruction` | The full instruction for this task (human-readable) |
| Task specification | The complete description of the task template |

**Your responsibility: execute the task directly; do not create a sub-agent.**

---

## 2. Execution flow

### Step 1: Parse the instruction

Read the full `instruction` you received and understand:
- what to do
- what constraints apply
- the expected output format

### Step 2: Execute the task

Execute directly in the current session. Use the tools you have to get the work done.

### Step 3: Write the result back (mandatory)

When the task is complete, call the agentgeex MCP tool **`agx_finish_task`**:

| Parameter | Value |
|------|-----|
| `caller_agent_id` | your own agent_id |
| `task_run_uuid` | the execution id you received |
| `status` | `"succeeded"` or `"failed"` |
| `result` | the result data (a JSON string) |
| `summary` | a human-readable summary |

>  **No write-back = the task disappears.** The daemon's watchdog will eventually mark a timed-out task, but a correct write-back is the basic contract.

---

## 3. Full example

Suppose you receive:

```
【本次指令】
Collect this host's CPU, memory, disk and network information, and return it as JSON

【任务说明书】
# Host information collection
...
```

> The envelope markers `【本次指令】` (this instruction) and `【任务说明书】` (task specification) are emitted verbatim in Chinese by the dispatch protocol; everything inside them is natural language and may be in any language.

**What you do:**
1. Understand the task: collect this host's hardware information
2. Execute directly in the current session: `ps`, `vm_stat`, `df` and similar commands
3. Shape the result into JSON
4. Call `agx_finish_task(task_run_uuid="run_xxx", status="succeeded", result="{...}", summary="done")`

---

## 4. Difference from the old mode (sub-agent)

| Aspect | Old mode (v1) | New mode (v2 WS) |
|--------|------------|----------------|
| Task dispatch | Redis queue + tmux launches a sub-agent | Redis queue + WS sessions.create |
| Agent behaviour | Create a sub-agent → wait for completion | Execute directly → write back the result |
| Latency | 30-70 seconds (sub-agent startup overhead) | 5-15 seconds |
| concurrency | One independent sub-agent per task | The daemon controls concurrency slots |
| Failure tracing | session_id bound after the fact | session_id pre-filled at dispatch time |

---

## 5. Notes

- Execute directly in the current session; do not create a sub-agent
- You must call `agx_finish_task` to write back when the work is done
- Prefer a JSON string for the `result` field
- If the task fails, set `status` to `"failed"` and state the reason in `summary`
- Your session is monitored by the daemon — the watchdog marks it failed after the timeout

---

*Protocol version: v2.0 (WS direct-connect mode) | Effective date: 2026-05-25*
