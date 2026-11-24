# Win3 BFF 接入层改造 — Step 1 交付与后续计划

> 作者：agx_ui_lead ｜ 日期：2026-10-05 ｜ 项目：agentgeex (proj_b2ed70d5ac17)
> 范围：`webtools/server`（FastAPI BFF）——**不碰内核、不碰前端页面代码**

---

## 一、背景与目标

### 1.1 为什么改
Win3 商业控制台的浏览器 SPA 走 **[本 BFF]** 取数。原 BFF 的接入方式是**进程内直接 import 内核源码**：

```python
# 旧 kernel.py（已弃用）
from agx_mcp import agx_core, agent_os_mcp
agx_core.execute_sql(...)          # 读
agx_core._sync_entity(...)         # 写缓存
agent_os_mcp.<tool>(...)          # 调工具函数
```

**问题**：`agx_core.py` 与 `agent_os_mcp.py` **各自重复定义了一整套缓存函数**（`get_redis_client` / `_sync_entity` / `_publish_event`）并各自持有 **Redis 连接池**。BFF 若走进程内 import，就会和 MCP 侧形成**两套缓存逻辑 + 两套连接池**，长期必然不一致、难维护。

### 1.2 目标
BFF 接入层**改走 MCP 官方协议（SSE transport）**，用 JSON-RPC 调用 `agent_os_mcp` 注册的工具函数。

**收益**：
- 与 MCP **完全同源**——权限（`@require_level`）、缓存写入链、连接池**只有一份**，不再双份维护。
- **不修改内核代码**（`agx_core.py` / `agent_os_mcp.py` 一行不动，只新增了一个 `mcp` 配置段）。
- BFF 侧不再感知 Redis/MySQL 细节，只认 MCP 工具契约。

### 1.3 关键决策（已确认）
| 决策 | 内容 | 来源 |
|---|---|---|
| 通道 | BFF → MCP（SSE, `127.0.0.1:31888`） | 2026-10-05 老冯 |
| 审计身份 | `caller_agent_id` **由前端传入**，BFF 透传给 MCP 工具 | 2026-10-05 老冯 |
| 身份缺省 | 回退系统身份 `win3_bff`（已验证：未注册身份会被 MCP 拒绝） | 实现约定 |
| SSE 实例常驻 | 由 agx_ui_lead 自管（`run_mcp_sse.sh`），**不登记 pm2**（客户端环境不一定有） | 2026-10-05 老冯 |
| 端口配置 | 端口可配，加进**主配置文件** `agx_config.json` 的 `mcp` 段 | 2026-10-05 老冯 |

---

## 二、架构（改造后）

```
┌─────────────────┐      HTTP/JSON      ┌──────────────────────┐   JSON-RPC(SSE)   ┌──────────────────────┐
│  浏览器 SPA     │ ──────────────────► │   BFF (FastAPI)      │ ────────────────► │  MCP SSE 实例         │
│  (React/Refine) │  Bearer JWT          │   webtools/server    │  /sse + /messages │  agent_os_mcp.py      │
│  端口 5190      │ ◄────────────────── │   端口 8090          │ ◄──────────────── │  127.0.0.1:31888      │
└─────────────────┘                     └──────────────────────┘                   └──────────┬───────────┘
        │                                          │                                          │
        │ 启动时 /snapshot 一次拉全量                │ mcp_client.py（官方 mcp SDK）             │ @require_level 权限
        │ 灌内存 store，页面代码零改动               │ 长连接 + 自动重连                          │ 缓存/连接池（唯一一份）
        └──────────────────────────────────────────┴──────────────────────────────────────────┘
                                                                                             │
                                                                                    ┌────────▼────────┐
                                                                                    │  MySQL / Redis   │
                                                                                    └─────────────────┘
```

### 2.1 单一真相源
- **主配置**：`<AGX_ROOT>/agx_config.json` —— BFF 与 MCP 实例读**同一份**的 `mcp.sse_host` / `mcp.sse_port`。
- BFF 侧 `settings.mcp_sse_url` 由此推导；SSE 实例启动亦由此读取端口。

---

## 三、Step 1 交付清单（本次改动）

> 全部在 `webtools/server/` 下。**未改任何内核文件**（`agx_mcp/*`）、**未改任何前端页面代码**。

### 3.1 新增文件

| 文件 | 作用 |
|---|---|
| **`app/mcp_client.py`** | MCP SSE 客户端封装（核心）。见 §3.3 |
| **`run_mcp_sse.sh`** | SSE 实例常驻启停脚本。见 §3.5 |
| **`app/realdata.py`** | 只读数据层：7 实体经 MCP 工具取数（本轮新建，替代旧 SQL 直查） |
| **`app/routers/mcp.py`** | MCP 整合自检路由（重写为 SSE 语义） |

### 3.2 重写/修改文件

| 文件 | 变化 |
|---|---|
| **`app/kernel.py`** | 重写：唯一内核出口 `kernel.call_tool()` 走 SSE；`ping()` 探活；**彻底删除** `agx_core` 进程内 import 与 `call_mcp_tool` |
| **`app/realdata.py`** | 重写：所有实体经 MCP 读工具；dashboard 由实体聚合（无 SQL） |
| **`app/config.py`** | 新增 `settings.mcp_sse_url` / `fetch_limit`，读主配置 `mcp` 段 |
| **`app/auth.py`** | 新增 `caller_agent_id_of(user)`；JWT claim 增 `agent_id` |
| **`app/routers/auth.py`** | login 支持前端传 `agent_id`；`/me` 回显 `agent_id` |
| **`app/routers/resources.py`** | 所有端点透传 `caller_agent_id`（从 JWT 提取） |
| **`app/main.py`** | 架构注释更新为 SSE 链路 |
| **`agx_config.json`**（AGX 根） | 新增 `mcp` 段（见下） |

### 3.3 核心：`app/mcp_client.py`

**设计**：
- 常驻**事件循环线程**（`_run_loop`），内跑 `_serve()` 长连接协程。
- `_serve()`：`sse_client(url)` → `ClientSession` → `initialize()`，保持活跃；**断开自动重连**（3s 退避，`except BaseException` 兜底 anyio 的 `ExceptionGroup`）。
- 同步门面（供 FastAPI 同步路由调用）：
  - `call(tool, arguments, timeout=60)` → 解析后的 Python 对象
  - `list_tools()`、`ready`、`start()`
- `ready` 以「当前是否持有 session」为准；会话丢失时 **clear `_ready`**，令 `call()` 阻塞等待重连（最多 15s）。

**错误约定**（`_parse_tool_result`）：
- MCP 返回 `"Error: ..."` 前缀（多为权限不足）→ 抛 `McpToolError`（**不触发重连**，是业务错误）。
- 连接类异常 → 置 `_session=None` → 后台重连，下次调用自动重试。

**关键常量**：`DEFAULT_CALLER = "win3_bff"`。

### 3.4 读路径工具映射

| BFF 实体 | MCP 工具 | 备注 |
|---|---|---|
| agents / projects / project_groups / executions | `agx_list_by_network` | 直查 MySQL（`execute_sql`），非读缓存 → 无缓存一致性顾虑 |
| sessions | `agx_list_sessions` | 含 `session_type` |
| plans | `agx_plan_list` | 按项目聚合 |
| dashboard | 上述实体聚合 | 无独立 SQL |
| 详情类 | `agx_get_agent` / `agx_get_execution` / `agx_get_session` / `agx_get_project` / `agx_get_host` / `agx_get_task` / `agx_running_tasks` | |

**规则**：
- `realdata.py` 为**只读层**——只调只读类 MCP 工具，**绝不调写工具**。
- 每个 MCP 工具**第一参数必须是** `caller_agent_id`（MCP 安全规范，审计）。

### 3.5 `run_mcp_sse.sh`（SSE 实例常驻脚本）

```bash
./run_mcp_sse.sh start|stop|restart|status|logs
```

**要点**：
- 端口从主配置 `agx_config.json` 的 `mcp.sse_host/sse_port` 读取（可 `MCP_SSE_HOST/PORT` 临时覆盖）。
- 启动时清代理（本机 SSE 勿走 clash）。
- **必须用「双 fork 守护化」**（fork → setsid → fork），否则会被内核看门狗误杀（见 §4 坑）。

---

## 四、🔴 关键坑与解法

### 坑 1：`agent_os_mcp.py` 的 `watch_stdin_sync()` 误杀后台进程
**现象**：`nohup … &` 启动 SSE 实例后，约 8 秒进程自杀，日志：
```
[ERROR] [MCP] WINDOWS MCP: Parent process detected as DEAD. Exiting...
[ERROR] [MCP] WINDOWS MCP RETURNED / EXITING NOW...
```
**根因**：`watch_stdin_sync()`（内核防 Windows 僵尸进程机制）启动时记录 `ppid = os.getppid()`；macOS 分支判定 `os.getppid() == ppid` 才算存活。用 `nohup &` 从 shell 启动后，**shell 退出 → 子进程被托孤给 launchd（ppid 变 1）→ ppid 变化 → 看门狗判定父进程死 → 硬自杀**。

**解法**：`run_mcp_sse.sh` 用**双 fork 守护化**：
```
fork → 父退出 / 子 setsid → 再 fork → 父退出 / 孙成为守护进程（ppid 稳定为 init）
```
父进程稳定为 init(1)，ppid 不再变化 → 不被误杀。**实测常驻稳定。**

### 坑 2：SSE 连接断开后 BFF 假就绪
**现象**：session 丢失后 `_ready` 仍为 set → `call()` 的 `wait()` 立即返回 → 误判就绪 → 调用失败。
**解法**：会话丢失时 `_ready.clear()`；`call()` 未就绪时 `_ready.wait(timeout=15)` 阻塞等重连。

### 坑 3：anyio `ExceptionGroup` 绕过 `except Exception`
**现象**：`sse_client` / `ClientSession` 底层是 anyio TaskGroup，断开时可能抛**包裹了 `CancelledError`（BaseException）的 `ExceptionGroup`** → `except Exception` 兜不住 → 重连循环线程静默退出。
**解法**：重连循环与 `_run_loop` 一律 `except BaseException`。

---

## 五、验证结果（Step 1 自测，全绿）

| 项 | 结果 |
|---|---|
| `ruff check app/` | ✅ All checks passed |
| `/readyz` | ✅ `status: ok`，`mcp_sse: True`，`tool_count: 71`，`mysql: True`，`redis: True` |
| agents | 73 |
| sessions | **1390**（sessionType 分布：main 32 / task 8 / case 3 / oneshot 1347）|
| tasks | 302 |
| executions | 2161 |
| plans | 47 |
| projects | 14 |
| project_groups | 7 |
| dashboard | sessions.active 1386，agents.online 67 |
| 身份过滤 | ✅ 不同 caller 可见 task 数不同（agentgeex_agent 302 vs agx_core 380）|
| **sessionType 打通 API** | ✅ **批次A 核心数据缺口已解决** |
| SSE 重启韧性 | ✅ 重启期间接口返 200（降级 mock 不报错），~10s 自动重连恢复 |

**结论**：SSE 协议链路端到端全通，且具备断线自愈能力。

---

## 六、后续计划

### Step 2 — execution ↔ case 聚合（数据层）
- **目标**：让「一次执行(execution)」能被归类到某条长期事项(case session)。
- **依赖**：`agx_execution_link` 表（已建 DDL）；当前**空表(0 边)**。
- **做法**：`realdata`/BFF 经 `agx_list_execution_links` 读取挂靠边，在 execution 列表上投影出 `caseSessionId` / `linkedCases`。
- **前置**：需要真实数据产生挂靠边（`agx_link_execution`）才能验收；否则只能做空态展示。
- **产出**：BFF 层聚合 + 接口字段；**前端展示待定**。

### Step 3 — 批次 A 前端（UI 层，样式需老冯确认）
> 遵循铁律「UI 不自创」——**样式照 shardkit 示例，不发明创造**。

1. **`/sessions` 列表加「会话类型」列 + 类型筛选**
   - `SessionRow` 加 `sessionType`（`main` / `task` / `case` / `oneshot`）。
   - 筛选器：类型下拉。
   - 分页/样式照 shardkit `pages/cases/page-crud/index.tsx` + `src/components/ui/pagination.tsx` 范式。
2. （如需）`/executions` 展示 case 归属（依赖 Step 2 数据）。

### Step 4+ — 写操作批量（后续）
- BFF 新增 POST/PATCH 路由，经 `kernel.call_tool("agx_xxx", caller_agent_id=前端传入, …)` 透传到 MCP 写工具（`agx_upsert_agent` / `agx_update_agent` / `agx_dispatch_task` / `agx_schedule_task` / `agx_finish_task` 等）。
- 自动继承 MCP 的 `@require_level` 权限与缓存写入链。

### 长期待办（技术债）
- **多租户**：`realdata.py` 中 `_NETWORK_ID = "lan_001"` 为硬编码，应按 caller 推导。
- **列裁剪缺字段**：
  - agents 缺 `created_at` / `peer_agent_id` / 子计数（`sessionCount`/`taskCount`/`execCount`/`tokens`/`lastActiveAt` 目前填 0/空）；
  - execution 缺 `total_tokens` / `exec_instruction` / `summary`。
- **BFF 功能补全**：真实用户源、限流/审计/计费。

---

## 七、运维手册

### 启动顺序
```bash
# 1) SSE 实例（常驻）
cd webtools/server && ./run_mcp_sse.sh start

# 2) BFF
cd webtools/server && AGX_ROOT=/path/to/agentgeex \
  $AGX_ROOT/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8090
```

### 主配置新增段（`agx_config.json`）
```json
"mcp": {
  "sse_host": "127.0.0.1",
  "sse_port": 31888
}
```

### 环境变量
| 变量 | 用途 |
|---|---|
| `BFF_PORT` | BFF 端口（默认 8090） |
| `BFF_MCP_SSE_URL` | 覆盖 SSE URL |
| `BFF_FETCH_LIMIT` | 防爆上限（默认 100000） |
| `AGX_ROOT` | AGX 根路径 |
| `MCP_SSE_HOST` / `MCP_SSE_PORT` | SSE 实例端口临时覆盖 |

### 端口约定
| 服务 | 端口 |
|---|---|
| 前端 dev / preview | 5190 / 4190 |
| BFF | 8090 |
| MCP SSE | 31888 |
| MySQL / Redis | 3306 / 6379 |

---

## 八、附：协作铁律回顾

- **UI 不自创**：任何 UI 先看 shardkit（`~/project/ai_project/wincubelabs/ui_test/ui_component_library`）有无现成示例，严格照抄。
- **BFF 只读层**：`realdata.py` 只调只读工具。
- **`caller_agent_id` 必传**：每个 MCP 工具第一参数。
- **前端页面代码零改动**：`providers/agentgeex.ts` 保持同步调用签名，启动时经 `/snapshot` 一次拉全量灌内存 store。
- **不越权**：专注前端 + BFF 接入层，不碰内核/后端业务逻辑。

---

*文档路径：`webtools/server/agent_doc/2026-10-05-Step1-BFF接入MCP协议改造与计划.md`*
