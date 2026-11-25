# Chapter 1 · Host

A host is what an agent runs on. This chapter covers the conceptual boundary of a host, its record attributes, platform configuration, how several hosts share one machine, the processes involved, and how hosts are reached.

---

## 1.1 What a host is for

An agent needs somewhere to run, and the dispatcher needs an address to deliver a task to. A host record provides both: it registers one runtime's entry point and available platform, and states how many executions it can carry at once.

**A host record describes not a machine, but an Agent runtime.** One OpenClaw gateway process is one host. One DSH instance is one host. Each occupies its own host record with its own `host_id`.

A single physical machine can run several hosts at the same time. They are independent subjects: each has its own entry port, its own access credential, its own concurrency capacity and its own liveness state. Dispatch, concurrency limiting and keepalive are all counted per host, not per machine.

## 1.2 Definition of a host

Three concepts are easily confused. Separate them first:

| Concept | What it is | How the system represents it |
|---|---|---|
| Physical or virtual machine | The machine that carries the processes | No ID of its own. Described by `endpoint_ip` and `os_platform`; several host records may share them |
| Host | One Agent runtime that can receive tasks | One `agx_host_inventory` row, one `host_id` |
| Platform entry | How that runtime is reached | One item in the `platforms` array |

A task is dispatched by `host_id`, not by IP. An agent is bound to one `host_id` (see chapter 2), so where an agent runs is determined by the host it belongs to.

In the current network, one physical machine carries four hosts:

| `host_id` | Runtime | Port | Capacity |
|---|---|---|---|
| `test_host_srv` | OpenClaw primary | 18789 | 16 |
| `test_host_srv_b` | OpenClaw second instance | 20789 | 8 |
| `test_host_srv_c` | OpenClaw third instance | 21789 | 8 |
| `test_host_srv_d` | DSH instance | 8791 (SDK mode, no listening port) | 8 |

All four rows share the same `endpoint_ip` and the same `os_platform`. One machine, four independent hosts.

Conversely, a machine running a single OpenClaw has a single row. That is the case for a default local install.

## 1.3 Host attributes

| Attribute | Description |
|---|---|
| `host_id` | Logical host ID. Dispatch and ownership both use this value |
| `host_alias` | Display name |
| `endpoint_ip` | Address of the machine. Hosts on the same machine share one value |
| `os_platform` | Operating system of the machine, e.g. `Darwin arm64`, `Linux x86_64` |
| `platforms` | How this host is reached. JSON array, currently one item per record |
| `task_slots_limit` | Maximum concurrent executions for this host |
| `active_task_count` | Executions currently running on this host, maintained by the daemon |
| `active_status` | Admission. 0 excludes the host from dispatch and keepalive checks, 1 admits it |
| `presence_status` | Liveness. 0 offline, 1 online, decided by heartbeat |
| `last_heartbeat` | Most recent heartbeat, written by the daemon every 30 seconds by default |
| `ssh_port` | SSH port, filled in when the machine is reachable for operations. Not used by dispatch |

Capacity and liveness are counted per host. Two hosts on the same machine do not consume each other's `task_slots_limit` or `active_task_count`: one being full does not stop the other from accepting work.

<details>
<summary>Full column definitions of agx_host_inventory</summary>

| Column | Type | Description |
|---|---|---|
| `host_id` | varchar(64) | Logical host ID |
| `host_alias` | varchar(128) | Display name |
| `endpoint_ip` | varchar(45) | Address of the machine |
| `ssh_port` | int | SSH port, not used by dispatch |
| `os_platform` | varchar(32) | Operating system of the machine |
| `platforms` | json | Array of access methods |
| `task_slots_limit` | int | Maximum concurrent executions for this host |
| `active_task_count` | int | Executions currently running on this host |
| `active_status` | tinyint(1) | 0 excluded, 1 admitted |
| `presence_status` | tinyint(1) | 0 offline, 1 online |
| `last_heartbeat` | datetime | Most recent heartbeat, default every 30 seconds |
| `x_desc` | longtext | Semantic description |
| `network_id` | varchar(64) | Network the host belongs to |

</details>

## 1.4 Platform configuration

`platforms` describes how this host is reached. It is an array, and currently each host record holds exactly one item.

```jsonc
// Primary instance: empty profile
"platforms": [
  {
    "platform": "openclaw",
    "name": "OpenClaw",
    "mode": "process",
    "port": 18789,
    "base_path": "/chat",
    "profile": ""
  }
]
```

```jsonc
// Second instance: its own port and its own profile
"platforms": [
  {
    "platform": "openclaw",
    "name": "OpenClaw #2",
    "mode": "process",
    "port": 20789,
    "base_path": "/chat",
    "profile": "openclaw2"
  }
]
```

Field meanings:

| Field | Required | Description |
|---|---|---|
| `platform` | yes | Platform identifier, e.g. `openclaw`, `dsh` |
| `name` | yes | Display name |
| `mode` | yes | `process` or `container` |
| `port` | yes | Integer port |
| `base_path` | yes | HTTP path; `/chat` for OpenClaw |
| `profile` | conditional | OpenClaw profile name, decides which `~/.openclaw-{profile}/` is used. Required for additional instances, empty string for the primary one |
| `addr` | conditional | Required only when `mode=container`; the address the container owns |
| `note` | no | Free-form comment. Must not carry information the program needs to read |

Two constraints:

- Do not set `addr` when `mode=process`. The address is built from `endpoint_ip` plus `port`; a second source of truth will disagree with it.
- Set `addr` when `mode=container`. A container has its own network namespace and the machine IP cannot be used to reach it.

**One host record describes one runtime.** To add a second instance, register a new host record rather than appending another item to the existing `platforms`. Appending to the same record makes two instances share one `host_id`, which merges their capacity, liveness and concurrency limiting, and neither can be controlled individually.

## 1.5 Several hosts on one machine

Running several Agent runtimes on one machine takes three shapes:

| Shape | Example | Each instance needs |
|---|---|---|
| Several OpenClaw instances | Primary + second + third | Its own `host_id`, port, profile and gateway token |
| OpenClaw next to DSH | `test_host_srv` and `test_host_srv_d` | Its own `host_id` and access method |
| Containerized instances | One instance per container | Its own `host_id` and `addr` |

Four things cannot be shared between instances:

| Item | Why it must be separate |
|---|---|
| `host_id` | It is the dispatch identity; capacity and liveness are counted against it |
| Port | Ports cannot repeat on one machine |
| `profile` | It decides which `~/.openclaw-{profile}/` is read and written; sharing it means sharing one configuration |
| Gateway token | One token per gateway; tokens are not interchangeable |

Confirm these before registering a second instance with `agx_register_host`; otherwise the new instance cannot connect, or it takes over the primary instance's configuration directory.

## 1.6 Processes

One machine runs one set of processes, and that set serves every host on the machine.

| Process | File | Responsibility |
|---|---|---|
| Daemon | `agx_daemon.py` | Consumes the Redis queue, routes by `agent_type`, writes heartbeats, writes results back |
| Watchdog | `agx_watchdog.py` | Separate process: scans for timeouts, wakes, alerts, resends, advances plans |
| OpenClaw bridge | `agx_ws_client.py` | Turns an execution into an OpenClaw session/turn and returns the result |
| DSH bridge | `agx_dsh_client.py` | Talks to the DeepSeek Harness session interface |
| State relay | `agx_state_relay.py` | State synchronization |
| MCP gateway | `agx_mcp/` | The external tool interface |

Several instances on one machine are served by the bridge: on startup `agx_ws_client.py` reads the `gateways` array from `agx_config.json` and opens one connection per entry, each carrying its own `host_id` and token.

```jsonc
"gateways": [
  { "host_id": "test_host_mac",   "ws_url": "ws://127.0.0.1:18789", "token": "...", "agent_type": "openclaw" },
  { "host_id": "test_host_mac_b", "ws_url": "ws://127.0.0.1:20789", "token": "...", "agent_type": "openclaw" }
]
```

Processes are not started per host because one process costs roughly 38 MB, and sharing a single Redis and MySQL connection pool is cheaper. The cost is that the bridge becomes a single point for the machine: when it exits, every host on that machine loses its consumer at the same time.

Heartbeats sit on the same chain: the daemon writes `last_heartbeat` and `presence_status` per `host_id`, so two hosts on one machine can be online and offline independently.

## 1.7 How hosts are reached

Platforms are reached in one of two ways:

| Platform | How | Bridge process |
|---|---|---|
| OpenClaw | `agx_ws_client.py` connects over WebSocket | yes |
| DSH (DeepSeek Harness) | `agx_dsh_client.py` talks to the session interface | yes |
| Codex | Mounts MCP in `~/.codex/config.toml` | no |
| Claude Code | Mounts MCP in `.mcp.json` | no |

Codex and Claude Code deploy no bridge. They call AgentGeeX tools as MCP clients, so their capability is "can call tools"; they cannot receive a dispatched execution. An agent that must receive tasks belongs on OpenClaw or DSH.

## 1.8 Multiple machines

Across machines, each machine runs its own set of processes and shares the same MySQL and Redis.

| Item | Mechanism |
|---|---|
| Registration | Registered with `agx_register_host`, writing `host_id`, address, platform and capacity |
| Heartbeat | The daemon updates `last_heartbeat` every 30 seconds by default |
| Liveness | The watchdog judges by heartbeat age; older than `watchdog_host_heartbeat_timeout` (default 90 seconds) counts as lost |
| Capacity | Concurrency is limited by `task_slots_limit`; `active_task_count` records current occupancy |
| Admission | A host with `active_status=0` takes no part in dispatch or keepalive checks |

The host manager agent is resolved by `host_id` rather than being hard-coded. Before dispatching a deployment, obtain the manager with `agx_resolve_host_manager(host_id=...)`.

## 1.9 Host operations

| Method | Purpose |
|---|---|
| `agx_register_host` | Register a new host with address, platform and capacity |
| `agx_get_host` | Read host details including address, liveness and platform list |
| `agx_update_host` | Update host attributes such as alias, address, capacity, platform configuration and admission |
| `agx_resolve_host_manager` | Resolve the host's manager agent by `host_id` |
| `agx_list_by_network` | List hosts in a network (`entity_type="host"`) |

`agx_update_host` writes only the fields passed in and leaves the rest untouched. Pass `platforms` as a list; the method serializes it.

## 1.10 When not to use

- **Treating a host as a machine**: one record per machine with several instances squeezed into `platforms` merges capacity and liveness, and neither concurrency limiting nor keepalive can work per instance. One record per runtime.
- **A single machine with a single instance**: the install already registers it. Another record creates an empty endpoint no agent uses.
- **Moving an agent to another machine**: that is drift (see 2.9), not a new host. A new host record does not migrate any existing agent.
- **Editing `presence_status` or `active_task_count` by hand**: both are written by heartbeat and dispatch, and any manual value is overwritten on the next refresh.
- **Changing the gateway token in the database only**: the token also lives in the bridge's connection configuration. Updating the row without updating `agx_config.json` makes the connection fail authentication.
