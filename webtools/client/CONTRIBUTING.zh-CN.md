# 参与 AgentGeeX Console

[English](CONTRIBUTING.md) · **简体中文**

这是一个面向真实使用的控制台，不是示例仓库。改动会直接影响别人的工作台，
所以下面几条不是形式要求，是**代价换来的**。

## 开发前

```bash
npm install
npm run dev          # 5190
# 另开终端：cd ../server && ./run.sh   （BFF，8090）
```

登录用任意非空用户名/密码（占位认证）。用户名会成为 `caller_agent_id`，
**别用他人的 agent_id 试写操作** —— 审计日志会记在你冒用的身份上。

## 提交前必须通过

```bash
npx tsc --noEmit     # 0 错
npm run build        # 构建通过
npm run i18n:check   # 中英语言包同构 + key 真实存在
```

前端改动涉及交互的，补一条 Playwright 验收（`scripts/verify_*.mjs`）：

- 写接口必须 `page.route()` 回桩 —— **测试绝不真的给人派活、发消息**；
- 断言控制台异常为 0；
- 脚本要能独立复跑（自带登录、自带清理）。

## 写用户可见文案的规矩

1. **只用中文或英文，不中英混排**；技术名词（Agent / Task / MCP）除外。
2. **同一个动作只有一个名字**。例：那个提交动作叫「发送」，不许出现「派发」「投递」混用。
3. **文案进语言包，不写死在组件里**：
   ```ts
   // ❌
   <Button>发送</Button>
   // ✅
   <Button>{t("header.send")}</Button>
   ```
   新增 key 要同时在 `src/i18n/locales/zh-CN/<ns>.ts` 与 `en-US/<ns>.ts` 里加，
   跑 `npm run i18n:check` 确认两边同构。
4. **中文比英文短**。给按钮/标签留出变宽的余地；不要用固定像素宽度装文字。
   `npm run i18n:overflow` 会用伪本地化（长度≈中文 2.2 倍）把挤爆的地方指出来。

## 代码约定

| 项 | 约定 |
|---|---|
| 组件 | 复用 `src/components/ui/*` 与 `src/components/agentgeex/*` 里已有的；不新造样式体系 |
| 样式 | 只用设计令牌（`bg-card` / `text-muted-foreground` / `border-strong`…），不写魔法色值 |
| 数据 | 一律走 `src/providers/*`；**组件里不直连 fetch，更不碰数据库** |
| 存储 | 键名统一登记在 `src/lib/storage-keys.ts`；旧键必须可读（升级不能把用户登录态/草稿弄丢） |
| 注释 | 写「为什么这样做」，不写「这行在做什么」。踩过的坑要留下痕迹 |
| 交互 | 按钮禁用必须给出原因（`title` + 可见文案），不做"灰着但不说为什么" |

## 目录边界

- `src/components/ui/` —— 通用原语，改动要确认所有调用方；
- `src/components/agentgeex/` —— 业务组件；
- `src/pages/agentgeex/` —— 页面；
- `src/lib/` —— 纯逻辑（可单测，不依赖 React）；
- `src/mock/` —— 只有类型与空态种子，**不放演示业务数据**。

后端（`webtools/server`）与内核（`agx_mcp/`）不在本目录职责内；
改接口契约请同步 `agent_doc/api-contract-draft.md`。

## 提交信息

英文，一句话说清「改了什么 + 为什么」。示例：

```
fix(sessions): sort the session list by last activity -- it used to sort by creation time,
so a conversation opened a minute ago was already on page two
```

## License

贡献即表示同意以本项目的 [Apache-2.0 License](../../LICENSE) 发布。
