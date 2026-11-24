<!-- 中文版。英文版见 README.md（GitHub 主版本）。 -->

# webtools — Win3 商业控制台（前端 + 服务器端）

> AgentGeeX 面向付费客户的 **Win3 商业控制台**：前端 SPA + 专用 BFF 服务器端。
> 核心设计理念：**把 Prompt 封装为按钮** —— 客户不写 Prompt，点按钮即驱动 Agent 干活。
>
> 维护者：agx_ui_lead（UI 前端负责人）　最后更新：2026-09-29

---

## 目录结构

```
webtools/
├── client/     ★ 前端 SPA（React 18 + Refine headless + Tailwind + shadcn/ui）
└── server/     ★ 服务器端 BFF（鉴权 / 多租户 / 聚合裁剪 / 对内核适配）
```

---

## 前端（client/）

| 项 | 值 |
|----|----|
| 来源 | 从 **ShardKit** 模板（`wincubelabs/ui_test/ui_component_library`）copy-in 初始化 |
| 技术栈 | React 18 + **Refine (headless)** + React Router 6 + **Tailwind** + **shadcn/ui** + TanStack Query + i18next |
| 形态 | **成品页面案例集**（仪表盘 / 列表 CRUD 两套 / 表单 / 详情 / 查询 / 认证 / 错误 / 组件展厅）|
| 零件库 | `registry-items/` 38 个可复制 UI 零件（copy-in 消费）|
| 数据现状 | 前端内存 mock；生产切 `VITE_API_URL` 指向 BFF，**页面代码零改动** |

```bash
cd client
npm install
npm run dev          # → http://localhost:5178
npx tsc --noEmit     # 类型检查必须 TSC=0
npm run build        # 生产构建
```

工程约定 / 工作流 / cookbook 在开发仓 `client/agent_doc/` 下维护，不在本发布包内。

---

## 服务器端（server/）

**BFF（Backend-for-Frontend）**：前端只对本服务说 REST；本服务对内经 MCP / 内网 HTTP 调 AgentGeeX 内核。

- 职责：鉴权（JWT）、多租户隔离、聚合 & 字段裁剪、配额 / 计费 / 限流 / 审计、对内核适配。
- 选型：**FastAPI**（贴 Python/MCP 生态）或 **NestJS**（前后端同语言）—— 待拍板。
- 对外契约：BFF REST API 契约仍在起草，稳定后再进本树。

详见 [`server/README.md`](./server/README.md)。

---

## 三层边界（红线）

| 层 | 碰 MySQL | 碰 Redis | 对谁说话 |
|----|:---:|:---:|------|
| 浏览器 SPA（client）| ❌ | ❌ | 只对 **BFF** 说 REST |
| **BFF（server）** | ✅ | ✅ | 对内 MCP/HTTP，对外 REST |
| AgentGeeX 内核（MCP，已有）| ✅ | ✅ | MCP（SSE/stdio）|

> ⚠️ 前端**永远不直连** MySQL / Redis：浏览器无法说这两种协议，且凭证进 bundle 即被 F12 扒走。

---

## 相关文档

| 文档 | 内容 |
|------|------|
| [`client/README.md`](./client/README.md) | ShardKit 模板原始文档（定位 / 机制 / 图标 / i18n）|
