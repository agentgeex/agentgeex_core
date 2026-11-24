<!-- 中文版。英文版见 README.md（GitHub 主版本）。 -->

# AgentGeeX Console

> 智能体编排平台的管理前端 —— 把 Agent 的**运行状态、任务链、会话、项目与计划**放到同一块屏幕上。

不是 demo，不是模板：这是 AgentGeeX 面向使用者的正式 Web 控制台。
数据全部来自真实后端（FastAPI BFF → MySQL / Redis / MCP 内核），
浏览器不接触任何数据库凭证。

**技术栈**：React 18 · Vite 5 · Refine 4（headless）· Tailwind CSS · shadcn/ui · TanStack Query · React Router 6 · i18next

---

## 它能做什么

| 页面 | 路由 | 干什么 |
|---|---|---|
| 系统状态 | `/system` | 机器与内核健康度、运行时快照 |
| 快捷入口 | `/quick-access` | 常用动作的一张卡片墙 |
| 智能体 | `/agents` | 目录、在线状态、按项目分组、部署状态 |
| 会话 | `/sessions` | 每个 Agent 的会话清单，可直接发消息 / 派任务 |
| 任务 | `/tasks` | 任务模板（说明书 + 参数 schema） |
| 任务执行记录 | `/executions` | 每一次派发的指令、状态、实时输出、耗时、回执 |
| 快捷指令 | `/quick-cmds` | **把 Prompt 封装成按钮**：选参数 → 发出去 |
| 技能共享 | `/skills` | Agent 之间沉淀的「怎么做」，带使用/认可信号 |
| 公告 / 工作日志 | `/notices` | 踩坑、决策、交接记录（Markdown 正文） |
| 项目 | `/projects` · `/project-groups` | 项目与其成员模板 |
| 计划 | `/plans` | 多步计划的拓扑与**真实运行态**（React Flow 流程图） |
| 算力主机 | `/hosts` | 主机清单与任务槽占用 |

设计取向：**能点就不写**。客户不必记 Prompt，点按钮、填参数即可驱动 Agent；
每次点击都落成一条可追溯的 execution。

## 快速开始

```bash
npm install
npm run dev        # http://127.0.0.1:5190
```

前端要显示数据，需要一个 BFF（`webtools/server`，FastAPI）在跑：

```bash
# 另开一个终端
cd ../server && ./run.sh          # http://127.0.0.1:8090
```

地址由环境变量指定（见 `.env.example`）：

```
VITE_API_URL=http://127.0.0.1:8090/v1
```

> **没有 BFF 会怎样**：页面不会假装"有数据"。启动时探测 `/readyz` 失败 → 显示明确的错误页 / 跳登录页，
> 而不是塞一份假数据让人误判系统正常（这是刻意的设计决定）。

### 登录（当前是占位实现）

输入任意**非空**用户名与密码即可进入；用户名会被当作 `caller_agent_id` 传给后端，
用于审计与资源归属校验。

> ⚠️ 这只是开发期的占位：**不是**可上线的认证方案。真实部署必须换成正规的身份源
> （OIDC / 企业 SSO / 网关鉴权），并在 BFF 侧做权限收敛。
> 仓库里不存任何账号密码，也不应存。

## 目录结构

```
src/
├── App.tsx                 路由表（业务页 + 登录）
├── components/
│   ├── shell.tsx           整体框架：侧边栏 / 顶栏 / 面包屑 / 标签栏 / 页脚
│   ├── agentgeex/          业务组件（数据表、发送弹窗、语音输入、流程图…）
│   ├── tabs/               多标签工作区（状态 / 标签栏 / 右键菜单）
│   └── ui/                 shadcn 原语（button / dialog / select / table…）
├── pages/agentgeex/        业务页面
├── providers/              API 客户端 / Refine data provider / 启动时数据装载
├── lib/                    导航配置、面包屑、格式化、存储键等纯逻辑
├── i18n/                   语言包（按命名空间拆文件）+ 伪本地化
└── mock/                   仅类型与**空态种子**（真实数据一律来自 BFF）
```

## 国际化（进行中）

- 语言：`zh-CN`（默认）/ `en-US`；另有 `en-XA` **伪本地化**，专门用来暴露"英文变长后挤爆布局"。
- 语言包按命名空间拆分（`app` / `common` / `header` / `nav` / `footer` / `page`），
  一个功能域一个文件 —— 避免所有人在同一个大文件最后一行打架。
- 导航、面包屑、浏览器标题、标签页标题、顶栏已接入；页面正文仍在迁移中。

三条命令把"国际化做完了没有"变成数字和失败：

```bash
npm run i18n:scan       # 还有多少处中文没进语言包（带基线，变多会失败）
npm run i18n:check      # 中英两棵树必须同构；代码里 t("x.y") 的 key 必须真实存在
npm run i18n:overflow   # 用伪本地化跑 13 条路由，找文本溢出
```

`i18n:check` 里那条"key 必须存在"是血泪：i18next 找不到 key 时**不报错**，
直接把 key 原样显示在界面上（`tab.system`），构建期毫无提示。

## 质量门槛

提交前跑这三条，缺一不可：

```bash
npx tsc --noEmit     # 0 错
npm run build        # 构建通过
npm run i18n:check   # 语言包一致
```

端到端验收用 Playwright（`scripts/verify_*.mjs`），约定：

- **写接口一律 `page.route()` 回桩**，测试绝不真的给人派活 / 发消息；
- 跑完检查控制台异常必须为 0；
- 临时脚本用完即删，留下的脚本要能独立复跑。

## 已知边界

- **认证是占位**（见上）。没有真实用户体系、多租户隔离、限流与计费 —— 这些属 BFF 侧待办。
- 页面以**读**为主；写操作目前覆盖：派任务、发文本、快捷指令、任务/执行记录编辑、快捷指令 CRUD。
- Agent 的线上行为由后端内核决定，前端只呈现与触发，不做本地状态推断（避免"界面看着成功、实际没发出去"）。

## 相关文档

- `agent_doc/architecture.md` —— 前后端分层、数据流、为什么浏览器不直连数据库
- `agent_doc/api-contract-draft.md` —— BFF 接口契约
- `agent_doc/i18n-and-naming-plan.md` —— 命名整理与国际化规划（含遗留缩写对照）
- `agent_doc/universal-input-bar-spec.md` —— 信息发送弹窗的交互规格

## License

[MIT](./LICENSE)
