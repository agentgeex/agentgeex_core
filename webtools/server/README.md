# Win3 WebTools Server — BFF 服务器端（FastAPI）

> **BFF（Backend-for-Frontend）**：Win3 商业控制台前端（`../client`）专用的聚合 / 鉴权 / 适配层。
> 前端**只对本服务说 REST**；本服务**对内**复用 AgentGeeX 内核（`agx_mcp` / `agx_core`）访问 MySQL / Redis。
>
> 维护者：agx_ui_lead　最后更新：2026-10-01

---

## 0. 定位

| | |
|---|---|
| **是** | Win3 商业控制台的**服务器端**：鉴权 + 多租户隔离 + 聚合裁剪 + 限流审计，对内适配 AgentGeeX 内核 |
| **不是** | 不是 AgentGeeX 内核（内核是 Python MCP 服务 + MySQL + Redis，已存在）|
| **对外** | 浏览器 SPA → **REST/JSON**（契约见 `../client/agent_doc/api-contract-draft.md`）|
| **对内** | 复用 `agentgeex/agx_mcp`（`agx_core.execute_sql` / `get_redis_client`），**不重复造 DB 访问层** |
| **红线** | 前端**永不**直连 MySQL / Redis；数据库 / 缓存访问只在本层发生 |

---

## 1. 技术栈

- **FastAPI 0.141** + Uvicorn（⭐ 与 AgentGeeX 内核同语言，直接复用 MCP 相关代码）
- **零第三方 JWT 依赖**：`app/auth.py` 用 stdlib `hmac/hashlib/base64` 实现 HS256
- **复用内核**：`app/kernel.py` 注入 `AGX_ROOT` 到 `sys.path`，调用 `agx_mcp.agx_core`
  （2026-11-11 已随 AgentGeeX 改名统一为 `AGX_ROOT`，旧变量名不再识别）
- **运行环境**：复用 `agentgeex/.venv`（Python 3.12，已含 fastapi/uvicorn/redis/pymysql）

---

## 2. 当前状态

| 能力 | 状态 |
|------|------|
| 服务骨架 + CORS + 统一错误体 | ✅ |
| `GET /healthz`（探活）/ `GET /readyz`（内核连通自检） | ✅ |
| `POST /v1/auth/login` `/logout` `GET /me`（JWT） | ✅ 骨架（占位用户源）|
| **真实只读读取（2026-10-01 第一批）**：
`/v1/agents` `/sessions` `/tasks` `/runs`(=executions) `/projects` `/project-groups` `/plans` `/dashboard/stats` `/snapshot` `/agents/{id}` | ✅ 已接通内核（仅 SELECT）|
| `GET /v1/products` `/orders` | 🟡 形状正确占位（案例页用，下一批）|
| 多租户隔离 / 限流 / 审计 / 计费 | ⬜ 待办 |
| 真实用户源（`authenticate()`）/ 写操作（POST/PATCH/DELETE） | ⬜ 待办 |

**实测证据**（2026-10-01）：`/readyz` → cors_origins 含 5190/4190；`/v1/snapshot` →
agents 73 / projects 14 / groups 7 / sessions 500 / tasks 380 / executions 300 / plans 45（与 DB 一致）；
前端 dev:5190 与 preview:4190 均 0 console error，`[win3] data source = API`。

> **只读红线**：本批所有查询均只执行 SELECT（见 `app/realdata.py`），不写库、不碰 daemon/watchdog。

---

## 3. 目录结构

```
server/
├── app/
│   ├── main.py            FastAPI 入口（CORS / 错误体 / 路由挂载 /v1）
│   ├── config.py          Settings（env 驱动）+ AGX_ROOT 推导
│   ├── kernel.py          ★ 内核适配层：复用 agx_mcp/agx_core 访问 MySQL/Redis
│   ├── realdata.py        ★ 真实只读读取层（7 类实体 + 仪表盘聚合，仅 SELECT）
│   ├── auth.py            JWT 签发校验 + current_user 依赖
│   └── routers/
│       ├── system.py      /healthz /readyz
│       ├── auth.py        /v1/auth/*
│       └── resources.py   /v1/agents /sessions /tasks /runs /projects /plans /dashboard /snapshot …
├── run.sh                 ★ 一键启动（复用 agentgeex/.venv）
├── pyproject.toml         依赖声明（ruff 配置沿用内核约定）
├── .env.example           环境变量样例
└── README.md
```

---

## 4. 启动

```bash
cd agentgeex/webtools/server
./run.sh                       # → http://localhost:8090 （自动 --reload）
# 或手动
../../.venv/bin/python -m uvicorn app.main:app --reload --port 8090
```

- 交互文档：<http://localhost:8090/docs>
- 端口约定：**BFF 8090**；前端 dev **5190**、preview **4190**。

---

## 5. 对外契约（唯一事实源）

```
../client/agent_doc/api-contract-draft.md
```

| 项 | 约定 |
|----|------|
| Base URL | 生产 `https://api.win3.internal/v1`（前端经 `VITE_API_URL` 注入）|
| 鉴权 | `Authorization: Bearer <JWT>` |
| 租户 | JWT 内携带 `tenant_id`，服务端强制隔离，**不接受客户端传 tenant** |
| 分页 | `?_page=1&_limit=10`；响应头 `X-Total-Count`（贴 `@refinedev/simple-rest`）|
| 排序 | `?_sort=updatedAt&_order=desc`（服务端做契约字段 → 真实列名映射）|
| 错误体 | `{ "error": { "code", "message", "details" } }` |

**资源**：`auth` / `agents` / `runs` / `products` / `orders` / `dashboard`。

---

## 6. 前端如何指过来（页面代码零改动）

```bash
# client 侧 .env.production
VITE_API_URL=http://localhost:8090/v1
```

前端把 `DataProvider` 换成 `@refinedev/simple-rest` 包 `VITE_API_URL` 即可（见 `../client/agent_doc/architecture.md` §3）。

---

## 7. 下一步（TODO）

1. `auth.py:authenticate()` 接真实用户源（AgentGeeX 用户表 / 外部 IdP）。
2. 各资源 handler 从占位换成真实 SQL / Redis（`kernel.query` / `kernel.redis_client`）。
3. 写操作（POST/PATCH/DELETE）+ 多租户过滤（所有查询强制注入 `tenant_id`）。
4. 限流 / 配额 / 审计日志 / 计费埋点。
5. `api-contract-draft.md` 定稿 → 生成 OpenAPI（本服务已自动产出 `/openapi.json`）。
