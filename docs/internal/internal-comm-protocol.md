# AgentGeeX Internal Communication Protocol v1

## Overview

Communication between Agents inside AgentGeeX is implemented with **Redis Lists (message queues)**, polled and dispatched on each host by `agx_daemon.py`.

Architecture:

```
Sender (any Agent)
    │  LPUSH message into the target queue
    ▼
Redis List (agx:queue:agent:<agent_id>)
    │  daemon polls with LPOP / BRPOP
    ▼
Daemon → parses the message → activates the target Agent for execution
```

## Queue naming

```
agx:queue:<host_id>:agent:<agent_id>  — an Agent's private message queue (host-scoped)
agx:queue:agent:<agent_id>           — legacy format (compatible during migration, being retired)
agx:queue:group:<group_id>           — team broadcast queue (optional extension)
```

- `host_id` is `agx_host_inventory.host_id` (for example `test_host_win`)
- `agent_id` is `agx_agent_registry.agent_id` (for example `agx_host_97be0f`, `agx_core`)
- Each host has its own queue namespace; the daemon polls its local queues according to its `host_id`
- The dispatching side resolves the target host's agent_id through `agx_resolve_host_manager(host_id)`
- The broadcast queue carries team notifications and may be consumed by the daemon of every Agent in the group

## Message format

All messages are JSON and carry the following top-level fields:

```json
{
  "proto_ver": "1.0",
  "msg_id": "<uuid>",
  "timestamp": 1714896000,
  "sender_id": "<agent_id>",
  "target_id": "<agent_id>",
  "msg_type": "<message type>",
  "ttl": 3600,
  "payload": { ... }
}
```

| Field | Type | Required | Description |
|------|------|------|------|
| `proto_ver` | string | yes | Protocol version, used for compatibility checks |
| `msg_id` | string | yes | Unique message id (UUID), used for de-duplication and tracing |
| `timestamp` | int | yes | Unix timestamp (seconds) |
| `sender_id` | string | yes | Sending agent_id |
| `target_id` | string | yes | Target agent_id or group_id |
| `msg_type` | string | yes | Message type, defined below |
| `ttl` | int | no | Message lifetime in seconds; may be dropped if unconsumed, default 3600 |
| `payload` | object | yes | Message body; its shape depends on `msg_type` |

## Message type definitions

### 1. `command` — instruction execution

The sender asks the target Agent to execute an instruction/task.

```json
{
  "proto_ver": "1.0",
  "msg_id": "msg_a1b2c3d4",
  "timestamp": 1714896000,
  "sender_id": "agx_core",
  "target_id": "agx_host",
  "msg_type": "command",
  "ttl": 300,
  "payload": {
    "command": "execute_task",
    "task_run_uuid": "run_xxx",
    "task_id": "task_xxx",
    "project_id": "proj_xxx",
    "instruction": "create a Worker agent named worker_001 with role worker",
    "params": {
      "agent_name": "worker_001",
      "agent_role": "worker",
      "model": "deepseek-chat"
    }
  }
}
```

`payload.command` enumeration:

| command | Description | Target |
|---------|------|------|
| `execute_task` | Execute the given task | Worker |
| `create_worker` | Create a Worker Agent on this host | host_manager |
| `stop_worker` | Stop the given Worker | host_manager |
| `restart_daemon` | Restart the daemon | host_manager |
| `shutdown` | Shut the Agent down gracefully | Worker |
| `custom` | Custom instruction, parsed by the receiver | any |

### 2. `task_result` — task result write-back

After a Worker completes a task, it returns the result to the dispatching side.

```json
{
  "proto_ver": "1.0",
  "msg_id": "msg_e5f6g7h8",
  "timestamp": 1714896300,
  "sender_id": "worker_001",
  "target_id": "agx_core",
  "msg_type": "task_result",
  "ttl": 86400,
  "payload": {
    "task_run_uuid": "run_xxx",
    "status": "succeeded",
    "summary": "Worker agent created successfully",
    "result": {
      "agent_id": "worker_001",
      "host_id": "{host_id}"
    },
    "error": null
  }
}
```

`payload.status` enumeration: `succeeded` / `failed` / `timeout`

### 3. `status_report` — status report

An Agent proactively reports the status of the host and of itself.

```json
{
  "proto_ver": "1.0",
  "msg_id": "msg_i9j0k1l2",
  "timestamp": 1714896000,
  "sender_id": "agx_host",
  "target_id": "agx_core",
  "msg_type": "status_report",
  "ttl": 60,
  "payload": {
    "host_id": "{host_id}",
    "status": "healthy",
    "timestamp": 1714896000,
    "metrics": {
      "cpu_pct": 23,
      "memory_pct": 45,
      "disk_pct": 62,
      "disk_free_gb": 45.2
    },
    "workers": [
      { "agent_id": "worker_001", "status": "running" },
      { "agent_id": "worker_002", "status": "idle" }
    ],
    "daemon_alive": true
  }
}
```

### 4. `ack` — message acknowledgement

The receiver confirms receipt (optional; used where reliable delivery matters).

```json
{
  "proto_ver": "1.0",
  "msg_id": "msg_u1v2w3x4",
  "timestamp": 1714896005,
  "sender_id": "agx_host",
  "target_id": "agx_core",
  "msg_type": "ack",
  "ttl": 60,
  "payload": {
    "ack_msg_id": "msg_a1b2c3d4",
    "status": "accepted",
    "note": "instruction received, starting execution"
  }
}
```

`payload.status` enumeration: `accepted` / `rejected` / `queued`

### 5. `error` — error report

Reported when an error occurs during execution.

```json
{
  "proto_ver": "1.0",
  "msg_id": "msg_y5z6a7b8",
  "timestamp": 1714896000,
  "sender_id": "agx_host",
  "target_id": "agx_core",
  "msg_type": "error",
  "ttl": 86400,
  "payload": {
    "ref_msg_id": "msg_a1b2c3d4",
    "error_code": "WORKER_CREATE_FAILED",
    "error_msg": "database connection failed, cannot register Worker",
    "suggestion": "check the MySQL service status and the database settings in env.sh"
  }
}
```

### 8. `broadcast` — broadcast message

Sends a notification to every Agent in a team or network.

```json
{
  "proto_ver": "1.0",
  "msg_id": "msg_c9d0e1f2",
  "timestamp": 1714896000,
  "sender_id": "agx_core",
  "target_id": "group_agx_core",
  "msg_type": "broadcast",
  "ttl": 3600,
  "payload": {
    "level": "info",
    "title": "system maintenance notice",
    "body": "database maintenance tonight at 23:00, expected to take 30 minutes",
    "action_required": false
  }
}
```

## Consumption behaviour

How the daemon handles a message is determined by `msg_type`:

| msg_type | Consumption behaviour |
|----------|---------|
| `command` | Parse `payload.command`, perform the matching operation, optionally reply with `ack` and `task_result` |
| `task_result` | Update the `agx_task_execution` status and result |
| `status_report` | Record to Redis or the database; no new message is produced |

| `ack` | Logged only; no further processing |
| `error` | Logged; whether to escalate to a human depends on `error_code` |
| `broadcast` | Consumed by every Agent in the group |

## Message uniqueness and de-duplication

1. `msg_id` must be globally unique (UUID v4); receivers cache the most recent N msg_ids for de-duplication
2. The same message must not be consumed twice (the daemon performs an idempotency check)
3. The daemon may drop messages whose `ttl` has expired

## Mapping to the existing database model

| Protocol field | Corresponding database field |
|----------|-----------|
| `sender_id` | `agx_agent_registry.agent_id` |
| `target_id` | `agx_agent_registry.agent_id` or `agx_agent_group.group_id` |
| `payload.task_run_uuid` | `agx_task_execution.task_run_uuid` |
| `payload.project_id` | `agx_project.project_id` |
| `payload.status` | `agx_task_execution.run_status` |
| `payload.result_data` | `agx_task_execution.result_data` |

## Reserved for future extension

- `msg_type` is extensible: a new type only needs a matching handler in the daemon
- `payload` may nest arbitrary JSON with no structural limit (except for instruction types)
- The broadcast queue `agx:queue:group:<group_id>` is reserved, for future team broadcast support
- A priority queue is reserved: `agx:queue:agent:<agent_id>:urgent` (consumed first by the daemon)
