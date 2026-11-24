# 第 1 章 主机（Host）

主机是 Agent 的运行载体。本章说明主机的概念边界、记录属性、平台配置、同机多主机的处理方式、系统进程构成与接入方式。

---

## 1.1 主机的作用

Agent 需要有地方运行，调度方需要有地址把任务投递过去。主机记录承担这两件事：登记一个运行时的入口与可用平台，标明它同时能承载多少个执行。

**主机描述的不是一台机器，而是一个 Agent 运行时。** 一个 OpenClaw 的 gateway 进程是一个主机。一个 DSH 实例是一个主机。它们在系统里各占一条主机记录、各有一个 `host_id`。

一台物理机上可以同时运行多个主机。它们是彼此独立的主体：各自的入口端口、各自的访问凭据、各自的并发容量、各自的在线状态。调度、限流、保活都按主机计，不按机器计。

## 1.2 主机的定义

三个概念容易混在一起，先分开：

| 概念 | 是什么 | 在系统中的表示 |
|---|---|---|
| 物理机或虚拟机 | 承载进程的机器 | 没有独立 ID。由 `endpoint_ip` 与 `os_platform` 描述，多条主机记录可以共用 |
| 主机 | 一个可接收任务的 Agent 运行时 | 一条 `agx_host_inventory` 记录，一个 `host_id` |
| 平台条目 | 该运行时的接入方式 | `platforms` 数组中的一项 |

派发任务时按 `host_id` 定位入口，不使用 IP。一个 Agent 绑定一个 `host_id`（见第 2 章），因此一个 Agent 的运行位置由它所属的主机决定。

当前网络中一台物理机 `192.168.0.188` 上登记了四个主机：

| `host_id` | 运行时 | 端口 | 容量 |
|---|---|---|---|
| `test_host_srv` | OpenClaw 主实例 | 18789 | 16 |
| `openclaw2` | OpenClaw 第二实例 | 20789 | 8 |
| `test_host_srvc` | OpenClaw 第三实例 | 21789 | 8 |
| `test_host_srv` | DSH 实例 | 8791（SDK 模式不监听端口） | 8 |

四条记录的 `endpoint_ip` 都是 `192.168.0.188`，`os_platform` 都是 `Linux x86_64`。同一台机器，四个独立主机。

反过来，一台机器上只运行一个 OpenClaw 时，就只有一条记录。本机的 `test_host_mac` 属于这种情况。

## 1.3 主机的属性

| 属性名称 | 说明 |
|---|---|
| `host_id` | 主机逻辑 ID，调度与归属均使用此值 |
| `host_alias` | 主机显示名 |
| `endpoint_ip` | 所属机器的地址。同一机器上的多个主机共用同一个值 |
| `os_platform` | 所属机器的操作系统，如 `Darwin arm64`、`Linux x86_64` |
| `platforms` | 本主机的接入方式，JSON 数组，当前每条记录一项 |
| `task_slots_limit` | 本主机的最大并发执行数 |
| `active_task_count` | 本主机当前运行中的执行数，由 daemon 维护 |
| `active_status` | 准入状态。0 不参与调度与保活检查，1 准入 |
| `presence_status` | 在线状态。0 离线，1 在线，由心跳判定 |
| `last_heartbeat` | 最近心跳时间，daemon 默认每 30 秒更新 |
| `ssh_port` | SSH 端口，运维可达时填写，不参与调度 |

容量与在线状态都按主机计。同一台机器上的两个主机，各自的 `task_slots_limit` 与 `active_task_count` 互不占用：一个跑满了不影响另一个继续接任务。

<details>
<summary>agx_host_inventory 完整字段定义</summary>

| 字段 | 类型 | 说明 |
|---|---|---|
| `host_id` | varchar(64) | 主机逻辑 ID |
| `host_alias` | varchar(128) | 显示名 |
| `endpoint_ip` | varchar(45) | 所属机器地址 |
| `ssh_port` | int | SSH 端口，不参与调度 |
| `os_platform` | varchar(32) | 所属机器操作系统 |
| `platforms` | json | 接入方式数组 |
| `task_slots_limit` | int | 本主机最大并发执行数 |
| `active_task_count` | int | 本主机当前运行中的执行数 |
| `active_status` | tinyint(1) | 0 禁入，1 准入 |
| `presence_status` | tinyint(1) | 0 离线，1 在线 |
| `last_heartbeat` | datetime | 最近心跳，daemon 默认每 30 秒更新 |
| `x_desc` | longtext | 语义描述 |
| `network_id` | varchar(64) | 所属网络 |

</details>

## 1.4 平台配置

`platforms` 描述这个主机怎么被访问。它是数组，当前每条主机记录恰好一项。

```jsonc
// 主实例：profile 为空串
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
// 第二实例：独立端口、独立 profile
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

字段含义：

| 字段 | 必填 | 说明 |
|---|---|---|
| `platform` | 是 | 平台标识，如 `openclaw`、`dsh` |
| `name` | 是 | 显示名 |
| `mode` | 是 | `process` 或 `container` |
| `port` | 是 | 整数端口 |
| `base_path` | 是 | HTTP 路径，OpenClaw 为 `/chat` |
| `profile` | 条件 | OpenClaw profile 名，决定读写 `~/.openclaw-{profile}/`。多实例必填，主实例为空串 |
| `addr` | 条件 | 仅 `mode=container` 必填，容器自持地址 |
| `note` | 否 | 自由注释，不得承载程序需要读取的信息 |

两条约束：

- `mode=process` 时不要填 `addr`。访问地址由 `endpoint_ip` 加 `port` 拼出，另填地址会造成两个来源不一致。
- `mode=container` 时必须填 `addr`。容器有独立网络命名空间，不能拿机器 IP 拼。

**一条主机记录只写一个运行时。** 需要第二个实例时，新登记一条主机记录，而不是在已有记录的 `platforms` 里追加一项。追加到同一记录会让两个实例共用一个 `host_id`，容量、在线状态与限流随之混在一起，无法按实例控制。

## 1.5 一台机器上的多个主机

同一台机器运行多个 Agent 运行时，有三种形态：

| 形态 | 例子 | 每个实例各自需要 |
|---|---|---|
| 多个 OpenClaw 实例 | 主实例 + 第二实例 + 第三实例 | 独立 `host_id`、端口、profile、gateway token |
| OpenClaw 与 DSH 并存 | `test_host_srv` 与 `test_host_srv` | 独立 `host_id`、接入方式 |
| 容器化实例 | 每个容器一个实例 | 独立 `host_id`、`addr` |

四项在实例之间不能复用：

| 项 | 独立的原因 |
|---|---|
| `host_id` | 调度身份，容量与在线状态按它计 |
| 端口 | 同一机器上端口不能重复 |
| `profile` | 决定读写哪个 `~/.openclaw-{profile}/` 目录，复用会读写同一份配置 |
| gateway token | 一个 gateway 一个 token，不可通用 |

登记第二个实例时先确认这三件事，再调 `agx_register_host`，否则新实例连不上或抢走主实例的配置目录。

## 1.6 系统进程

一台机器上运行一组进程，这组进程同时服务本机的多个主机。

| 进程 | 文件 | 职责 |
|---|---|---|
| 守护进程 | `agx_daemon.py` | 接收 Redis 队列消息，按 `agent_type` 分流投递，写心跳，回写结果 |
| 看门狗 | `agx_watchdog.py` | 独立进程，扫描超时、唤醒、告警、重发，推进计划 |
| OpenClaw 桥 | `agx_ws_client.py` | 把执行转换成 OpenClaw 的 session/turn，回传结果 |
| DSH 桥 | `agx_dsh_client.py` | 对接 DeepSeek Harness 的会话接口 |
| 状态中继 | `agx_state_relay.py` | 状态同步 |
| MCP 网关 | `agx_mcp/` | 对外工具接口 |

本机多实例由桥进程统一承接：`agx_ws_client.py` 启动时读取 `agx_config.json` 的 `gateways` 数组，一个进程按条目数量建立多条连接，每条连接携带自己的 `host_id` 与 token。

```jsonc
"gateways": [
  { "host_id": "test_host_mac",   "ws_url": "ws://127.0.0.1:18789", "token": "...", "agent_type": "openclaw" },
  { "host_id": "test_host_mac_b", "ws_url": "ws://127.0.0.1:20789", "token": "...", "agent_type": "openclaw" }
]
```

不为每个主机单起一套进程，原因是一个进程约占 38 MB，且共享同一套 Redis 与 MySQL 连接池更省资源。代价是桥进程成为该机器的单点：它退出后，本机所有主机同时失去消费方。

心跳也在这条链上：daemon 按 `host_id` 各自写入 `last_heartbeat` 与 `presence_status`，因此同一机器上的两个主机可以一在线一离线。

## 1.7 接入方式

不同平台的接入方式不同，分为两类：

| 平台 | 接入方式 | 桥进程 |
|---|---|---|
| OpenClaw | `agx_ws_client.py` 通过 WebSocket 连接 | 有 |
| DSH（DeepSeek Harness） | `agx_dsh_client.py` 对接会话接口 | 有 |
| Codex | 在 `~/.codex/config.toml` 挂载 MCP | 无 |
| Claude Code | 在 `.mcp.json` 挂载 MCP | 无 |

Codex 与 Claude Code 不部署桥。它们作为 MCP 客户端主动调用 AgentGeeX 的工具，能力范围是「可以调用工具」，不能接收执行投递。需要接收任务投递的 Agent 应部署在 OpenClaw 或 DSH 上。

## 1.8 多机器

跨机器时，每台机器各自运行一组进程，共享同一套 MySQL 与 Redis。

| 事项 | 机制 |
|---|---|
| 注册 | 通过 `agx_register_host` 登记，写入 `host_id`、地址、平台与容量 |
| 心跳 | daemon 默认每 30 秒更新 `last_heartbeat` |
| 在线判定 | 看门狗按心跳时间判定，超过 `watchdog_host_heartbeat_timeout`（默认 90 秒）视为失联 |
| 容量控制 | 按 `task_slots_limit` 限制并发，`active_task_count` 记录当前占用 |
| 准入控制 | `active_status=0` 的主机不参与调度与保活检查 |

主机管理者 Agent 按 `host_id` 解析，不在代码中硬编码。派发部署任务前用 `agx_resolve_host_manager(host_id=...)` 取得对应的管理者 Agent。

## 1.9 主机操作

| 方法 | 用途 |
|---|---|
| `agx_register_host` | 登记新主机，写入地址、平台、容量 |
| `agx_get_host` | 查询主机详情，含地址、在线状态、平台列表 |
| `agx_update_host` | 更新主机属性，如别名、地址、容量、平台配置、准入状态 |
| `agx_resolve_host_manager` | 按 `host_id` 解析该主机的管理者 Agent |
| `agx_list_by_network` | 按网络列出主机（`entity_type="host"`） |

`agx_update_host` 只更新传入的字段，未传字段保持不变。`platforms` 直接传数组，由方法完成序列化。

## 1.10 不适用的情况

- **把主机当物理机建**：一台机器只建一条记录、把多个实例挤进 `platforms`，容量与在线状态会混在一起，无法按实例限流与保活。一个运行时一条记录。
- **单机单实例自用**：安装时本机已自动登记，不需要再建一条。多建一条会产生一个没有 Agent 使用的空端点。
- **需要 Agent 换机器**：属于漂移（见 2.9），不是新建主机。新建主机记录不会把已有 Agent 迁移过去。
- **手工改 `presence_status` 或 `active_task_count`**：两者由心跳与调度写入，手工修改会在下一次刷新时被覆盖。
- **更换 gateway token 只改数据库**：token 同时存在于桥进程的连接配置中，只改库不改 `agx_config.json` 会导致连接鉴权失败。
