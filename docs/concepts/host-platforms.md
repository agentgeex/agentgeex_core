# Host platform attributes and centralised addressing (`agx_host_inventory.platforms`)

> Landed 2026-11-04. Answers one question: **which Agent platforms can a given machine run, and where is each one's access address configured.**

## 1. Why this field exists

In AgentGeeX "platform" appears at three levels with different meanings, and historically only the first two had a home:

| Level | Field | Question it answers | Location |
|---|---|---|---|
| Project | `agx_project.platform` | which platform this **project** targets | DB |
| Agent | `agx_agent_registry.agent_type` | which platform this **Agent** runs on | DB |
| Host | `agx_host_inventory.platforms` | which platforms this **machine** can run, and **how to reach** them | DB (this document) |

The third level was missing, so "how to reach it" was scattered across two places:

- the host IP lives in `endpoint_ip` (DB)
- **the port is hardcoded in the frontend** as `OPENCLAW_PORT = 18789` (code)

That had two consequences:

1. Changing a port meant changing code, rebuilding, and redeploying the frontend.
2. It implicitly assumed "every host uses the same port". In reality host 10.0.0.13 runs both `18789` (`0.0.0.0`) and `18799` (`127.0.0.1`) — one bad assumption is enough to make a link point at the wrong instance.

## 2. Field structure

```sql
platforms JSON NULL
COMMENT 'agent platforms available on this host and how to reach them (JSON array); each item
         {platform,name,mode,port,addr,base_path,note};
         mode=process means build the address from endpoint_ip+port,
         mode=container means connect via addr directly (the container has its own address; do not build it from the host IP)'
```

```json
[
  {
    "platform": "openclaw",
    "name": "OpenClaw",
    "mode": "process",
    "port": 18789,
    "addr": "10.0.0.11",
    "base_path": "/chat"
  }
]
```

| Key | Required | Description |
|---|---|---|
| `platform` | ✅ | same set as `agent_type`: `openclaw` / `dsh` / `codex` / `claude_code` / `custom` |
| `mode` | ✅ | `process` or `container`, see section 3 |
| `port` | required when `mode=process` | port |
| `addr` | required when `mode=container` | address. Under `process` it may optionally override the host part |
| `base_path` | no | path prefix of the session page; defaults to the frontend template (`/chat` for openclaw) |
| `name` / `note` | no | display name / free-form note |

## 3. `mode` is the only reason this structure exists

In the author's own words (original quote):

> 「像 docker 这种它实际上是自己有个主机嘛。那自己有个主机和自己是一个进程这个东西是有区别的」

> English: "Something like Docker actually has its own host. There's a real difference between having its own host and being just another process on the host."

That distinction has to be captured in data, otherwise the address cannot be computed:

| `mode` | How the address is derived | Typical case |
|---|---|---|
| `process` | `endpoint_ip` (or `addr`) + `port` | openclaw runs directly on the host: `10.0.0.13:18789` |
| `container` | `addr` used as-is, **never combined with `endpoint_ip`** | the container has its own network namespace: `172.17.0.5:18789`, or a host-mapped port |

**The rule in one line**: a container **is** a host of its own; its address has nothing to do with the host's `endpoint_ip`. Combining the host IP with a container port yields something that is neither the container nor the host.

### Why `process` also allows `addr`

Because `endpoint_ip` is insufficient in three situations:

1. **`endpoint_ip` is a loopback address** (e.g. `test_host_mac` = `127.0.0.1`). It always works locally, but no other machine can reach it.
2. **Different platforms on the same host are exposed on different NICs/hostnames**.
3. `endpoint_ip` is depended on by other logic and should not be changed for one platform's sake.

Here `addr` is a "host-part override", with a smaller blast radius than changing `endpoint_ip`.

## 4. Frontend resolution rules

`webtools/client/src/lib/openclaw.ts` → `resolvePlatformEndpoint(hostId, platform)`

```
1. host not found          → fail, no link generated
2. host has no such platform → fail, no link generated
3. mode=container          → http://{addr}
4. mode=process            → http://{addr || endpoint_ip}:{port}
   └ addr or endpoint_ip is loopback:
       · current page is also on loopback → allow (same-machine development)
       · current page is not on loopback  → fail, prompt "configure addr for this platform"
```

**Two deliberate "rather have nothing" choices**:

- **No link when the data is missing.** The old code fell back to `127.0.0.1` when the IP was absent, so a cross-machine visit pointed at the browser's own machine — an entry point that looks present but opens the wrong thing is worse than none.
- **Never substitute the page's hostname for a loopback address.** Substituting means assuming "webtools runs on the same machine as the target platform". Once the console is deployed centrally on another machine that assumption breaks and the link points at an entirely unrelated machine. The correct fix is to configure `addr`, not to guess.

## 5. How to configure it

### MCP (recommended)

```python
agx_update_host(
    caller_agent_id="<an agent with L6 permission>",
    host_id="test_srv_a",
    updates={
        "platforms": [
            {"platform": "openclaw", "mode": "process", "port": 18789}
        ]
    },
)
```

`platforms` is registered in `_JSON_COLUMNS`, so **pass a list directly** and the kernel calls `json.dumps` for you.
Passing `[]` or `null` clears the value.

A new host can be registered with it in one go:

```python
agx_register_host(
    ..., host_id="newhost",
    platforms=[{"platform": "openclaw", "mode": "process", "port": 18789}],
)
```

### Console

`PATCH /v1/hosts/{host_id}` accepts `platforms` (the BFF submits to the kernel as `agx_core`).

### Decision rule: when a value should not go into this field

**"How do I reach others" → `platforms`; "how do I reach myself" → keep it in `agx_config.json`.**

| Configuration | Home | Reason |
|---|---|---|
| Platform access addresses (`platforms`) | DB | the impact is "how others find me" — pure discovery information |
| `gateway.ws_url` / `token` | local file | the daemon startup chain depends on it; it must still boot when the DB is down; credentials do not belong in a table |
| `dsh.host/port` | local file | same as above |
| `mcp.url` | local file | same as above |

In one line: **bootstrap configuration stays local, discovery configuration goes to the DB.** Putting everything in the DB means the daemon cannot even start when the DB misbehaves.

## 6. Current data

| host | `endpoint_ip` | `platforms` | Resolved result |
|---|---|---|---|
| `test_host_mac` | `127.0.0.1` | openclaw / process / 18789 / `addr=10.0.0.11` | `http://10.0.0.11:18789/chat/*` |
| `test_srv_a` | `10.0.0.12` | openclaw / process / 18789 | `http://10.0.0.12:18789/chat/*` |
| `test_host_srv` | `10.0.0.13` | openclaw / process / 18789 | `http://10.0.0.13:18789/chat/*` |

`test_host_mac` relies on `addr` to state its LAN address explicitly, because its `endpoint_ip` is loopback.

> ⚠️ `endpoint_ip` stores a raw IP, which breaks as soon as DHCP reassigns it. Long term, prefer a fixed lease, or switch to an mDNS hostname (`xxx.local`) — `addr` accepts hostnames.

## 7. How to verify

```bash
# 1. the column exists and its comment is correct
mysql> SHOW FULL COLUMNS FROM agx_host_inventory LIKE 'platforms';

# 2. the dual write did not serialise the JSON as a Python repr (single quotes)
mysql> SELECT host_id, JSON_VALID(platforms), JSON_LENGTH(platforms),
              JSON_EXTRACT(platforms, '$[0].port') FROM agx_host_inventory;

# 3. truth source matches (column set + order)
#    the CREATE TABLE block in agx.sql vs SHOW FULL COLUMNS; the set-diff must be 0

# 4. frontend resolution branches (8 cases: cross-host value, unconfigured, loopback rejection, container, missing port, ...)

# 5. the link really opens
curl -o /dev/null -w '%{http_code}\n' http://10.0.0.12:18789/chat/agx_dev
```

## 8. Pitfalls

**1. `str(list)` is not JSON.**
When writing to Redis, `_sync_entity` applies `str()` uniformly. For a list that yields `"[{'a': 1}]"` — single quotes — and the frontend `JSON.parse` throws immediately. Fixed to `dict/list → json.dumps`.
(Values read from a MySQL `json` column are already str, so this only bites when an entity is hand-built in code, as in `agx_register_host`.)

**2. `_json_list` flattens arrays of objects into strings.**
The BFF already had a `_json_list` used solely for `skills_allowlist` (an array of strings); it applies `str()` to every element. Using it on `platforms` yields `["{'platform': ...}"]`. Hence a new `_json_obj_list` that keeps only `dict` elements.

**3. `_UPDATEABLE_FIELDS` silently drops unknown fields.**
It is a whitelist. A column that exists in the DB and in `agx.sql` but is absent from this set = **writes fail with an error, and there is no warning when they do not happen at all**. When adding a column, add it both here and to `_JSON_COLUMNS`.

**4. `agx_update_host` requires L6.**
A worker calling it directly is rejected. The console goes through the BFF (which always submits as `agx_core`); scripts need a privileged identity, or plain SQL.

## 9. Related documents

- `docs/development/platform-integration.md` — what must change when onboarding a **new platform**
- `docs/concepts/mcp-gateway.md` — the single-process MCP gateway (three services behind one deployment and integration path)
