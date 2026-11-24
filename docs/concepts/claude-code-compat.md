# AgentGeeX × Claude Code Compatibility Plan

## Core design: leave the existing WS channel untouched, add a CC channel

### The problem

OpenClaw and Claude Code send and receive messages in completely different ways:

| | OpenClaw | Claude Code (CC) |
|--|----------|------------------|
| Message delivery | WS `sessions.send` / `sessions.create` | CLI `claude -p 'instruction'` |
| Message reception | WS `sessions.messages.subscribe` | none (stdin/stdout) |
| Session management | Gateway session | tmux session / Windows cmd |
| Status query | WS `sessions.describe` / `sessions.list` | process liveness / execution polling |
| Wake-up method | WS `sessions.send` | tmux send-keys / signal file |

The daemon and the watchdog contain **10 WS interaction points** (5 active + 5 passive). Changing them one by one is not realistic.

### The approach: a standalone CC client process

After BLPOP, the daemon routes by `agent_type`:

```
daemon BLPOP
  ├─ openclaw     → agx:ws:task:{host_id}   → agx_ws_client.py (WS, existing)
  └─ claude_code  → agx:cc:task:{host_id}   → agx_cc_client.py (CLI, new)
```

### New files

#### `scripts/agx_cc_client.py`

A standalone process, functionally analogous to `agx_ws_client.py`, but communicating over a CLI.

**Responsibilities (mapping the 10 WS interaction points):**

| # | Original WS interaction | CC replacement |
|---|----------|-------------|
| ① | `sessions.create` | tmux `new-session -d -s {run_uuid} "claude -p 'instruction'"` |
| ② | `sessions.send` (continue) | `tmux send-keys -t {run_uuid} "instruction" Enter` |
| ③ | `sessions.send` (register callback) | CC deployment does not register, so N/A |
| ④ | `_send_wake_via_ws` (watchdog) | `tmux send-keys -t {run_uuid} "continue" Enter` |
| ⑤ | `_send_plan_completed` notification | `tmux send-keys` or write a trigger file |
| ⑥⑦ | `sessions.messages.subscribe` | `tmux wait-for {run_uuid}` / poll execution status |
| ⑧ | `sessions.list` (token) | obtain via an MCP call |
| ⑨ | `_diagnose_session_ws` | `tmux capture-pane -t {run_uuid}` + process check |
| ⑩ | `sessions.describe` | query `agx_task_execution.run_status` |

**Queue:** `agx:cc:task:{host_id}` (a sibling of the WS queue)

**Result write-back:** after execution CC calls `agx_finish_task` over MCP, or cc_client writes it after detecting the tmux exit.

### Daemon changes (minimal)

On top of the existing `agent_type` branch (about 20 lines), change

```python
# current
r.rpush(WS_TASK_QUEUE, json.dumps(ws_task, ...))
```
to:
```python
if agent_type == "openclaw":
    r.rpush(WS_TASK_QUEUE, json.dumps(ws_task, ...))
elif agent_type == "claude_code":
    r.rpush(f"agx:cc:task:{host_id}", json.dumps(cc_task, ...))
```

### Watchdog changes (minimal)

The existing `_send_wake_to_session()` already branches by `agent_type` between openclaw (`_send_wake_via_ws`) and claude_code (`_send_wake_via_tmux`).

With the new `agx:cc:task:{host_id}` queue, CC wake-ups can also become queue-based:
- the watchdog detects a CC session timeout → pushes a wake-up task onto `agx:cc:task:{host_id}`
- cc_client reads it from the queue → sends it via tmux send-keys

This way the watchdog no longer needs to talk to WS or tmux directly; everything goes through the queue.

### Deployment changes

- `agx_deploy_agent` skips WS register for CC
- cloning skips `default_session_id` for CC
- cc_client startup configuration (one extra child process in `start_daemon.sh`)
