<!-- English (GitHub) primary. Chinese version: README.zh-CN.md -->

# Win3 WebTools Server — BFF server (FastAPI)

> **BFF (Backend-for-Frontend)**: the aggregation / auth / adapter layer dedicated to the Win3 commercial console frontend (`../client`).
> The frontend **only speaks REST to this service**; this service **internally** reuses the AgentGeeX kernel (`agx_mcp` / `agx_core`) to reach MySQL / Redis.
>
> Maintainer: agx_ui_lead | Last updated: 2026-10-01

---

## 0. Positioning

| | |
|---|---|
| **It is** | The **server side** of the Win3 commercial console: auth + multi-tenant isolation + aggregation & trimming + rate limiting & audit, adapting the AgentGeeX kernel for the frontend |
| **It is not** | Not the AgentGeeX kernel (the kernel is a Python MCP service + MySQL + Redis, and already exists) |
| **Outward** | Browser SPA → **REST/JSON** (contract in [`../api-contract-draft.md`](../api-contract-draft.md)) |
| **Inward** | Reuses `agentgeex/agx_mcp` (`agx_core.execute_sql` / `get_redis_client`) — **never builds a second DB access layer** |
| **Red line** | The frontend **never** connects to MySQL / Redis directly; all database / cache access happens in this layer |

---

## 1. Stack

- **FastAPI 0.141** + Uvicorn (⭐ same language as the AgentGeeX kernel, so MCP-related code is reused directly)
- **No third-party JWT dependency**: `app/auth.py` implements HS256 with the stdlib `hmac/hashlib/base64`
- **Reuses the kernel**: `app/kernel.py` injects `AGX_ROOT` into `sys.path` and calls `agx_mcp.agx_core` (renamed to `AGX_ROOT` as part of the 2026-11-11 AgentGeeX rename; the old variable name is no longer recognised)
- **Runtime**: reuses `agentgeex/.venv` (Python 3.12, already has fastapi/uvicorn/redis/pymysql)

---

## 2. Current state

| Capability | Status |
|------|------|
| Service skeleton + CORS + unified error body | ✅ |
| `GET /healthz` (liveness) / `GET /readyz` (kernel connectivity self-check) | ✅ |
| `POST /v1/auth/login` `/logout` `GET /me` (JWT) | ✅ skeleton (placeholder user source) |
| **Real read-only reads (2026-10-01, batch 1)**: `/v1/agents` `/sessions` `/tasks` `/runs`(=executions) `/projects` `/project-groups` `/plans` `/dashboard/stats` `/snapshot` `/agents/{id}` | ✅ wired to the kernel (SELECT only) |
| `GET /v1/products` `/orders` | 🟡 correctly shaped placeholders (for the demo pages, next batch) |
| Multi-tenant isolation / rate limiting / audit / billing | ⬜ TODO |
| Real user source (`authenticate()`) / write operations (POST/PATCH/DELETE) | ⬜ TODO |

**Measured evidence** (2026-10-01): `/readyz` → cors_origins includes 5190/4190; `/v1/snapshot` →
agents 73 / projects 14 / groups 7 / sessions 500 / tasks 380 / executions 300 / plans 45 (matches the DB);
frontend dev:5190 and preview:4190 both show 0 console errors, `[win3] data source = API`.

> **Read-only red line**: every query in this batch executes SELECT only (see `app/realdata.py`) — no writes, and it does not touch the daemon/watchdog.

---

## 3. Directory layout

```
server/
├── app/
│   ├── main.py            FastAPI entry (CORS / error body / mounts routers under /v1)
│   ├── config.py          Settings (env-driven) + AGX_ROOT derivation
│   ├── kernel.py          ★ kernel adapter: reuses agx_mcp/agx_core for MySQL/Redis
│   ├── realdata.py        ★ real read-only layer (7 entity types + dashboard aggregation, SELECT only)
│   ├── auth.py            JWT issue/verify + current_user dependency
│   └── routers/
│       ├── system.py      /healthz /readyz
│       ├── auth.py        /v1/auth/*
│       └── resources.py   /v1/agents /sessions /tasks /runs /projects /plans /dashboard /snapshot …
├── run.sh                 ★ one-command start (reuses agentgeex/.venv)
├── pyproject.toml         dependency declaration (ruff config follows the kernel's convention)
├── .env.example           environment variable samples
└── README.md
```

---

## 4. Startup

```bash
cd agentgeex/webtools/server
./run.sh                       # → http://localhost:8090 (auto --reload)
# or manually
../../.venv/bin/python -m uvicorn app.main:app --reload --port 8090
```

- Interactive docs: <http://localhost:8090/docs>
- Port convention: **BFF 8090**; frontend dev **5190**, preview **4190**.

---

## 5. External contract (single source of truth)

```
../api-contract-draft.md
```

| Item | Convention |
|----|----|
| Base URL | production `https://api.win3.internal/v1` (injected into the frontend via `VITE_API_URL`) |
| Auth | `Authorization: Bearer <JWT>` |
| Tenant | `tenant_id` travels inside the JWT; the server enforces isolation and **does not accept a client-supplied tenant** |
| Pagination | `?_page=1&_limit=10`; response header `X-Total-Count` (matches `@refinedev/simple-rest`) |
| Sorting | `?_sort=updatedAt&_order=desc` (the server maps contract fields → real column names) |
| Error body | `{ "error": { "code", "message", "details" } }` |

**Resources**: `auth` / `agents` / `runs` / `products` / `orders` / `dashboard`.

---

## 6. How the frontend points here (zero page-code changes)

```bash
# client-side .env.production
VITE_API_URL=http://localhost:8090/v1
```

The frontend swaps its `DataProvider` for `@refinedev/simple-rest` wrapped around `VITE_API_URL`; that is the whole change.

---

## 7. Next steps (TODO)

1. Point `auth.py:authenticate()` at a real user source (the AgentGeeX user table / an external IdP).
2. Replace the placeholder handlers with real SQL / Redis (`kernel.query` / `kernel.redis_client`).
3. Write operations (POST/PATCH/DELETE) + multi-tenant filtering (`tenant_id` injected into every query).
4. Rate limiting / quota / audit log / billing instrumentation.
5. Finalise `api-contract-draft.md` → generate OpenAPI (this service already serves `/openapi.json` automatically).
