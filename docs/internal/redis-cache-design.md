# AgentGeeX Redis Cache Structure Design

## Overview

The state of AgentGeeX's core tables is synchronized into Redis in real time, avoiding frequent SQL queries and enabling millisecond-level monitoring and index lookups.

> **Calibrated against a live environment, 2026-10-24**: the first version of this document said "6 tables";
> the system has in fact grown to **9 entity types**.
> Measured environment: `127.0.0.1:6379 db=0`, 5,983 `agx:*` keys in total.
>
> | Entity type | `agx:entity` count | `agx:idx` count |
> |---|:---:|:---:|
> | execution | 4,287 | 465 |
> | task | 494 | 100 |
> | plan | 185 | 33 |
> | agent | 91 | 3 |
> | agent_group_member | 27 | 31 |
> | project | 19 | 2 |
> | agent_group | 7 | 2 |
> | skill | 1 | 3 |
> | host | 1 | 3 |
> | **total** | **5,112** | **642** |
>
> Omitted in the first version: `plan`, `skill`, `agent_group`, `agent_group_member`.
> Note also: the first version said "6 tables" in its opening line but listed only 5 — it was
> internally inconsistent.

## Design principles

1. **Dual-write consistency**: every write/update writes MySQL + Redis together; a failed write is logged but does not block the main flow
2. **Read-through**: on a Redis miss, fall back to a MySQL SELECT and backfill the cache
3. **References, not persistence**: all entities key on pkid rather than creation time; business indexes store only entity IDs, never full payloads
4. **Namespace isolation**: every key is prefixed `agx:`; entity and idx live in separate subtrees

## Key space at a glance

```
agx:entity:*       → entity detail (JSON Hash), the current latest state
agx:idx:*          → indexes (Set / Sorted Set), entity IDs only, enabling scans and filters
agx:events         → Pub/Sub channel, real-time change broadcast
```

## Key naming convention

| Pattern | Structure | Expiry | Description |
|------|------|------|------|
| `agx:entity:{type}:{id}` | Hash | 1h | Full field detail of an entity |
| `agx:idx:{type}:all` | ZSet (pkid) | none | All entities, ordered by pkid |
| `agx:idx:agent:active` | Set | none | Active Agents |
| `agx:idx:host:online` | Set | none | Online hosts |
| `agx:idx:task:active` | Set | none | Active task templates |
| `agx:idx:project:by_network:{nw}` | Set | none | Projects by network |
| `agx:idx:host:by_network:{nw}` | Set | none | Hosts by network |
| `agx:idx:agent:by_network:{nw}` | Set | none | Agents by network |
| `agx:idx:task:by_agent:{agt}` | Set | none | Tasks by Agent |
| `agx:idx:execution:by_task:{task}` | Set | none | Executions by task |
| `agx:idx:execution:by_parent:{parent}` | Set | none | Child executions by parent execution |
| `agx:idx:execution:status:{s}` | ZSet | none | Executions by status |

## Hash fields of the five core entities

### 1. Project — `agx:entity:project:{project_id}`

**Business meaning**: a logical project, such as "page development" or "automated backtesting". One project contains multiple Agents and execution records.

```json
{
  "pkid": "14",
  "project_id": "proj_xxx",
  "network_id": "nw_smoke",
  "project_name": "SmokeTest",
  "x_desc": "Updated via clean tool",
  "active_status": "1"
}
```

### 2. Host — `agx:entity:host:{host_id}`

**Business meaning**: a physical or virtual compute host. Records connection details and current resource load.

```json
{
  "pkid": "11",
  "host_id": "host_xxx",
  "network_id": "nw_update",
  "host_alias": "Updated-Host",
  "endpoint_ip": "10.0.0.30",
  "os_platform": "Linux",
  "task_slots_limit": "16",
  "active_task_count": "0",
  "active_status": "1",
  "presence_status": "0"
}
```

| Field | Meaning |
|------|------|
| `task_slots_limit` | Maximum number of concurrent tasks |
| `active_task_count` | Number of tasks currently executing |
| `active_status` | 1=enabled, 0=disabled |
| `presence_status` | 1=online, 0=offline |

### 3. Agent — `agx:entity:agent:{agent_id}`

**Business meaning**: one agent identity, holding its model, role, channel tokens and other configuration.

```json
{
  "pkid": "13",
  "agent_id": "agt_xxx",
  "network_id": "nw_update",
  "agent_name": "Updated-Worker",
  "agent_role": "worker",
  "base_model": "deepseek-chat",
  "active_status": "1",
  "system_prompt": "Updated system prompt"
}
```

Note: optional fields such as `host_id`, `agent_email` and `mcp_config` are not stored in Redis when empty (`None` is filtered out); treat a missing field as an empty value when reading.

### 4. Task Base — `agx:entity:task:{task_id}`

**Business meaning**: agx_task_base holds the capabilities or task templates an Agent has; it is not bound to a project.
The project association is made at the execution layer (agx_task_execution).

```json
{
  "pkid": "13",
  "task_id": "task_xxx",
  "network_id": "nw_update",
  "agent_id": "agt_xxx",
  "x_desc": "Updated task description",
  "run_status": "succeeded",
  "priority": "3",
  "active_status": "1"
}
```

| Field | Meaning |
|------|------|
| `priority` | 1-5, where 5 is highest |
| `run_status` | pending / processing / succeeded / failed |
| `active_status` | 1=template enabled, 0=disabled |

### 5. Execution — `agx:entity:execution:{task_run_uuid}`

**Business meaning**: one concrete execution instance of a task, bound to a project. Carries runtime data.

```json
{
  "pkid": "12",
  "task_run_uuid": "run_xxx",
  "network_id": "nw_update",
  "project_id": "proj_xxx",
  "task_id": "task_xxx",
  "agent_id": "agt_xxx",
  "host_id": "host_xxx",
  "run_status": "succeeded",
  "exec_instruction": "Updated instruction",
  "result_data": "{\"test\":\"finish after update\"}",
  "finished_at": "2026-04-29 15:30:18"
}
```

---

## Index structures in detail

### Full-set index (ZSet)
Every entity type has an `all` index that uses `pkid` as the score:
```
agx:idx:project:all        ZRANK proj_xxx  → project creation order
agx:idx:host:all           ZRANK host_xxx  → host registration order
agx:idx:agent:all          ZRANK agt_xxx   → Agent registration order
agx:idx:task:all           ZRANK task_xxx  → task creation order
agx:idx:execution:all      ZRANK run_xxx   → execution creation order
```

### Active-state index (Set)
```
agx:idx:agent:active       → every Agent ID with active_status=1
agx:idx:task:active        → every task ID with active_status=1
agx:idx:host:online        → every host ID with presence_status=1
```

### Business relation index (Set)
```
agx:idx:project:by_network:{network_id}       → all projects in that network
agx:idx:host:by_network:{network_id}          → all hosts in that network
agx:idx:agent:by_network:{network_id}         → all Agents in that network
agx:idx:task:by_agent:{agent_id}              → all task templates bound to that Agent
agx:idx:execution:by_task:{task_id}           → all execution instances of that task
agx:idx:execution:by_parent:{parent_run_uuid}  → all child execution instances of that parent execution
```

### Status filter index
```
agx:idx:execution:status:running     → IDs of running executions (ZSet, score=pkid)
agx:idx:execution:status:succeeded   → IDs of succeeded executions
agx:idx:execution:status:failed      → IDs of failed executions
agx:idx:execution:status:pending     → IDs of pending executions
```

**Automatic status-index switching**: when an execution's `run_status` changes, `_sync_entity` → `_build_extra_index` removes the ID from every old status ZSet and then adds it to the current-status ZSet only.

---

## Data flow

### Write flow (create)
```
create entity → MySQL INSERT
             → Redis HSET (write the cache with all fields)
             → ZADD the all index
             → SADD / ZADD business indexes (by_network, by_agent, etc.)
             → Pub/Sub broadcast on "agx:events"
```

### Update flow (update)
```
update entity → MySQL UPDATE (dynamic SET, only the supplied fields)
             → Redis HSET (overwrite the supplied fields, leave the rest untouched)
             → if the status changed → ZREM the old status + ZADD the new status
             → Pub/Sub broadcast on "agx:events"
```

### Read flow (read-through)
```
get entity → Redis HGETALL → hit? → return
                          ↘ Miss → MySQL SELECT
                                 → Redis HSET + set 1h TTL
                                 → return
```

### Cleanup flow (finish_task)
```
finish_task → MySQL UPDATE set run_status=succeeded/failed
            → Redis HSET update run_status + result_data + finished_at
            → ZREM agx:idx:execution:status:*
            → ZADD agx:idx:execution:status:{new_status}
            → also update the run_status of the associated task
            → Pub/Sub broadcast on "execution:finished"
```

---

## Consistency safeguards

| Scenario | Handling |
|------|----------|
| MySQL write fails | Raise the exception and skip the Redis write, so inconsistent data is never written back |
| Redis write fails | Caught by try/except, error logged, does not block the MySQL commit |
| Crash between the two writes | After restart, the next read triggers read-through and repairs the cache automatically |
| Cache expiry | Hash TTL=1h; after expiry the next read backfills from MySQL |
| Stale index entries | When an execution's status changes, it is removed from every status index before being added |

---

## Change history

| Date | Change |
|------|------|
| 2026-06 (initial version) | Cache design for 5 core tables |
| 2026-10-24 | Live calibration: 9 entity types in fact (added plan / skill / agent_group / agent_group_member); moved from `notes/` into `docs/` |
