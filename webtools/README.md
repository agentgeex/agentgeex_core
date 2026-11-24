<!-- English (GitHub) primary. Chinese version: README.zh-CN.md -->

# webtools — Win3 commercial console (frontend + server)

> The **Win3 commercial console** AgentGeeX ships to paying customers: a frontend SPA
> plus a dedicated BFF server.
> Core design principle: **wrap prompts as buttons** — customers don't write prompts,
> they click a button and the agent goes to work.
>
> Maintainer: agx_ui_lead (UI frontend lead) | Last updated: 2026-09-29

---

## Directory layout

```
webtools/
├── client/     ★ frontend SPA (React 18 + Refine headless + Tailwind + shadcn/ui)
└── server/     ★ server-side BFF (auth / multi-tenancy / aggregation & trimming / kernel adapter)
```

---

## Frontend (client/)

| Item | Value |
|----|----|
| Origin | Copy-in initialised from the **ShardKit** template (`wincubelabs/ui_test/ui_component_library`) |
| Stack | React 18 + **Refine (headless)** + React Router 6 + **Tailwind** + **shadcn/ui** + TanStack Query + i18next |
| Shape | **A set of finished page cases** (dashboard / two list-CRUD variants / forms / detail / query / auth / error / component gallery) |
| Widget library | `registry-items/` — 38 copy-in UI widgets (consumed by copy-in) |
| Data today | Frontend in-memory mock; in production point `VITE_API_URL` at the BFF, **zero page-code changes** |

```bash
cd client
npm install
npm run dev          # → http://localhost:5178
npx tsc --noEmit     # type check must stay at TSC=0
npm run build        # production build
```

Engineering conventions / workflow / cookbook live in the development repository under `client/agent_doc/`; they are not part of this distribution.

---

## Server (server/)

**BFF (Backend-for-Frontend)**: the frontend speaks REST only to this service; this service reaches the AgentGeeX kernel over MCP / internal HTTP.

- Responsibilities: auth (JWT), multi-tenant isolation, aggregation & field trimming, quota / billing / rate limiting / audit, kernel adapter.
- Stack choice: **FastAPI** (close to the Python/MCP ecosystem) or **NestJS** (one language front to back) — pending decision.
- External contract (single source of truth): [`api-contract-draft.md`](./api-contract-draft.md) — draft v0.1.

Details: [`server/README.md`](./server/README.md).

---

## Three-layer boundary (red line)

| Layer | Touches MySQL | Touches Redis | Talks to |
|----|:---:|:---:|------|
| Browser SPA (client) | ❌ | ❌ | REST to the **BFF** only |
| **BFF (server)** | ✅ | ✅ | MCP/HTTP inward, REST outward |
| AgentGeeX kernel (MCP, existing) | ✅ | ✅ | MCP (SSE/stdio) |

> ⚠️ The frontend **never** connects to MySQL / Redis directly: browsers cannot speak
> either protocol, and any credential that reaches the bundle is one F12 away from being stolen.

---

## Related documents

| Document | Contents |
|------|------|
| [`docs/manual.md`](./docs/manual.md) | **Operations manual** -- start it, sign in, every page explained, one full walkthrough, troubleshooting |
| [`api-contract-draft.md`](./api-contract-draft.md) | BFF REST API contract (draft v0.1, binding on both sides) |
| [`server/README.md`](./server/README.md) | BFF server: positioning, stack, current state, startup, next steps |
| [`client/README.md`](./client/README.md) | Frontend console: pages, routes, quick start, i18n, quality gates |
