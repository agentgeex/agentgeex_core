<!-- English (GitHub) primary. Chinese upstream draft: this document is translated from the internal development repository. -->

# AgentGeeX / Win3 backend API contract draft (Draft v0.1)

> **Status: draft (not finalised)** — for BFF developers and the frontend to align on.
> This draft corresponds strictly to the frontend's existing mock data model (`src/mock/data.ts`).
> When implemented, the backend follows this document and the frontend **only needs to point its `DataProvider` at the BFF address**.
>
> Maintainer: agx_ui_lead | Last updated: 2026-09-29

---

## 0. Conventions

| Item | Convention |
|----|----|
| **Base URL** | production `https://your-bff.example.com/v1`; the frontend injects it via `VITE_API_URL` |
| **Protocol** | HTTPS + JSON (`Content-Type: application/json`) |
| **Auth** | `Authorization: Bearer <JWT>` (issued by the login endpoint; except for endpoints that need no auth) |
| **Tenant** | `tenant_id` is carried inside the JWT; the server enforces isolation and **does not accept a client-supplied tenant** |
| **Pagination** | request `?_page=1&_limit=10`; response header carries `X-Total-Count` (Refine `simple-rest` convention) |
| **Sorting** | `?_sort=updatedAt&_order=desc` |
| **Filtering** | filter directly by field: `?status=active&category=consumer-electronics&q=earphones` |
| **Time** | ISO-8601 (`2026-09-29T20:00:00+08:00`), or `YYYY-MM-DD` (date-only fields) |
| **Error body** | `{ "error": { "code": "STRING_CODE", "message": "human readable", "details": {} } }` |
| **HTTP status** | 200/201 success, 400 bad parameters, 401 unauthenticated, 403 forbidden, 404 not found, 409 conflict, 429 rate-limited, 5xx server error |

> 🔑 **How this maps onto Refine**: the contract deliberately follows `@refinedev/simple-rest`
> (`_page`/`_limit`/`X-Total-Count`/`_sort`/`_order`) so the frontend can plug in the off-the-shelf provider with zero adaptation.

---

## 1. Resources

| Resource | Corresponding frontend case | Description |
|------|-------------|------|
| `agents` | Dashboard | Agent runtime state |
| `runs` | Dashboard | Task execution records |
| `products` | List page (dialog-style / page-style CRUD) | Product collection |
| `orders` | Search page `/search/orders` | Order search |
| `auth` | Login page / user menu | Authentication |

---

## 2. Schemas

### 2.1 Agent
```ts
interface Agent {
  id: string;                                    // "agt_001"
  name: string;                                  // "topic-hunter"
  role: "worker" | "project_manager" | "host_manager" | "workshop_manager";
  status: "online" | "offline" | "error";
  model: string;                                 // "gpt-4o"
  tasks: number;                                 // cumulative task count
  tokens: number;                                // cumulative token spend
}
```

### 2.2 Run (task execution record)
```ts
interface Run {
  id: string;                                    // "run_9001"
  agent: string;                                 // agent name
  recipe: string;                                // task recipe name
  status: "succeeded" | "failed" | "running";
  startedAt: string;                             // "09:12" (current frontend format)
  duration: string;                              // "42s"
}
```

### 2.3 Product
```ts
interface Product {
  id: string;                                    // "P-1001"
  hs6: string;                                   // "8517.12" HS6 customs code
  name: string;
  category: string;                              // "consumer-electronics"
  platform: string;                              // "Amazon" | "Shopee" | ...
  price: number;                                 // 39.9
  status: "active" | "draft" | "offline";
  updatedAt: string;                             // "2026-09-15"
}
```

### 2.4 Order (search page)
```ts
interface Order {
  id: string;                                    // order number
  product: string;                               // product name
  platform: string;
  amount: number;
  status: "paid" | "pending" | "refunded";
  createdAt: string;
}
```

### 2.5 Dashboard stats (aggregate endpoint)
```ts
interface DashboardStats {
  trend: { date: string; runs: number; tokens: number }[];
  roleDistribution: { name: string; value: number }[];
}
```

---

## 3. Endpoints

### 3.1 Auth `/auth`

| Method | Path | Description | Request | Response |
|------|------|------|------|------|
| POST | `/auth/login` | Log in | `{ username, password }` | `{ token, user: { id, name, role } }` |
| POST | `/auth/logout` | Log out | — | `204` |
| GET  | `/auth/me` | Current user | — | `{ id, name, role, tenant_id }` |
| POST | `/auth/refresh` | Refresh token | `{ refresh_token }` | `{ token }` |

### 3.2 Agents `/agents` (standard CRUD)

| Method | Path | Description |
|------|------|------|
| GET | `/agents?_page=1&_limit=10&_sort=name&_order=asc&status=online` | Paginated list (with `X-Total-Count`) |
| GET | `/agents/:id` | Single record |
| POST | `/agents` | Create |
| PATCH | `/agents/:id` | Partial update |
| DELETE | `/agents/:id` | Delete |

### 3.3 Runs `/runs`
| Method | Path | Description |
|------|------|------|
| GET | `/runs?...` | Execution record list (filterable by status / agent) |
| GET | `/runs/:id` | Single record |

### 3.4 Products `/products` (backend for the list-CRUD case)
| Method | Path | Description |
|------|------|------|
| GET | `/products?_page=1&_limit=10&_sort=id&_order=asc&q=earphones&category=consumer-electronics&status=active` | Pagination + keyword + filters |
| GET | `/products/:id` | Single record (detail page) |
| POST | `/products` | Create (returns 201 + the full entity) |
| PATCH | `/products/:id` | Partial update |
| DELETE | `/products/:id` | Delete one |
| DELETE | `/products?ids=P-1001,P-1002` | Bulk delete (used by the frontend's "delete selected") |
| GET | `/products/categories` | Category dropdown options (backs `getCategoryOptions`) |

### 3.5 Orders `/orders`
| Method | Path | Description |
|------|------|------|
| GET | `/orders?_page=1&_limit=10&q=...&status=paid&dateFrom=...&dateTo=...` | Order search |

### 3.6 Dashboard `/dashboard`
| Method | Path | Description |
|------|------|------|
| GET | `/dashboard/stats` | Aggregate stats (trend + roleDistribution), corresponds to `DashboardStats` |

---

## 4. Examples: request / response

### 4.1 Login
```http
POST /v1/auth/login
Content-Type: application/json

{ "username": "alice", "password": "******" }
```
```json
200 OK
{
  "token": "eyJhbGciOi...",
  "user": { "id": "u_001", "name": "Alice", "role": "admin" }
}
```

### 4.2 Paginated product list
```http
GET /v1/products?_page=1&_limit=10&_sort=updatedAt&_order=desc&status=active
Authorization: Bearer <JWT>
```
```json
200 OK
X-Total-Count: 42
Content-Type: application/json

[
  { "id": "P-1001", "hs6": "8517.12", "name": "Wireless Bluetooth Earphones Pro",
    "category": "consumer-electronics", "platform": "Amazon", "price": 39.9,
    "status": "active", "updatedAt": "2026-09-15" }
]
```

### 4.3 Create a product
```http
POST /v1/products
Authorization: Bearer <JWT>

{ "name": "New product A", "hs6": "9999.99", "category": "test",
  "platform": "Amazon", "price": 9.9, "status": "draft" }
```
```json
201 Created
{ "id": "P-1009", "name": "New product A", "hs6": "9999.99", "category": "test",
  "platform": "Amazon", "price": 9.9, "status": "draft",
  "updatedAt": "2026-09-29" }
```

### 4.4 Error responses
```json
401 Unauthorized
{ "error": { "code": "UNAUTHENTICATED", "message": "Your session has expired, please sign in again" } }
```
```json
403 Forbidden
{ "error": { "code": "TENANT_FORBIDDEN", "message": "You do not have access to this resource" } }
```

---

## 5. Frontend mapping (contract → code)

| Contract | Frontend location | Description |
|------|---------|------|
| `VITE_API_URL` | `.env.production` | BFF address — the only config that has to change |
| REST CRUD | `src/providers/dataProvider.ts` (to be created) | Wrapped by `@refinedev/simple-rest` |
| Auth | `src/providers/authProvider.ts` (to be created) | Backs `/auth/*` |
| Resource declaration | `src/App.tsx`'s `<Refine resources={[...]}>` | `name` matches the contract's resource name |
| Existing mock | `src/mock/data.ts` / `*/productStore.ts` | In production, **replace** with DataProvider calls |

---

## 6. OpenAPI skeleton (optional, for Swagger generation)

```yaml
openapi: 3.0.3
info: { title: Win3 BFF API, version: 0.1.0-draft }
servers: [{ url: https://your-bff.example.com/v1 }]
security: [{ bearerAuth: [] }]
components:
  securitySchemes:
    bearerAuth: { type: http, scheme: bearer, bearerFormat: JWT }
  schemas:
    Product:
      type: object
      required: [id, name, status]
      properties:
        id:        { type: string, example: "P-1001" }
        hs6:       { type: string, example: "8517.12" }
        name:      { type: string }
        category:  { type: string }
        platform:  { type: string }
        price:     { type: number, format: float }
        status:    { type: string, enum: [active, draft, offline] }
        updatedAt: { type: string, format: date }
paths:
  /products:
    get:
      summary: Paginated product list
      parameters:
        - { name: _page,  in: query, schema: { type: integer, default: 1 } }
        - { name: _limit, in: query, schema: { type: integer, default: 10 } }
        - { name: _sort,  in: query, schema: { type: string } }
        - { name: _order, in: query, schema: { type: string, enum: [asc, desc] } }
        - { name: q,      in: query, schema: { type: string } }
      responses:
        "200":
          description: OK
          headers:
            X-Total-Count: { schema: { type: integer } }
          content:
            application/json:
              schema: { type: array, items: { $ref: "#/components/schemas/Product" } }
    post:
      summary: Create a product
      response: { "201": { description: Created } }
```

> 📌 **Open items (TODO)**: error-code dictionary, rate-limit thresholds, WebSocket live channel (agent runtime state),
> storage plan for file uploads (images / rich text) — object-storage presign URL.
