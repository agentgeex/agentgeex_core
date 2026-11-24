<!-- 中文版。英文版见 README.md（GitHub 主版本）。 -->

# 合作方 / 生态项目 Logo

用于 AgentGeeX Web 前端展示生态内各 Agent 运行时 / CLI 的识别标识。
前端直接引用：`/logos/<file>`（Vite `public/` 目录，构建后原样输出）。

> 采集日期：2026-10-16 ｜ 采集人：agx_ui_lead

## 主要 4 个（产品负责人 2026-10-16 指定）

| 文件 | 项目 | 形态 | 来源（官方） |
|---|---|---|---|
| `openclaw.svg` | **OpenClaw** | 红色龙虾吉祥物（官方站图标）| `https://openclaw.ai/favicon.svg` |
| `openclaw-mascot.svg` | OpenClaw | 像素龙虾（官方仓库）| repo `openclaw/openclaw` → `docs/assets/pixel-lobster.svg` |
| `dsh.svg` / `dsh-dark.svg` | **DSH**（DeepSeek Harness）| 鲸标（官方 Web UI favicon，light/dark）| repo `deepseek-ai/deepseek-harness` → `apps/web/public/favicon[-dark].svg` |
| `dsh-icon.svg` | DSH | 桌面应用图标（1104×1104）| repo `deepseek-ai/deepseek-harness` → `apps/desktop/resources/icon.svg` |
| `codex.svg` | **OpenAI Codex** | 蓝圆 + 白色结标（Codex 文档站图标）| `https://developers.openai.com/codex` favicon |
| `claude-code.svg` | **Claude Code** | Claude 官方标记（#D97757）| Simple Icons `claude`（CDN `cdn.simpleicons.org/claude`）|

## 备选 / 通用品牌标（同源可替换）

| 文件 | 品牌 | 来源 |
|---|---|---|
| `openai.svg` | OpenAI 花标（白色，需深底）| repo `openclaw/openclaw` → `docs/assets/sponsors/openai.svg` |
| `anthropic.svg` | Anthropic 标记 | Simple Icons `anthropic` |
| `deepseek.svg` | DeepSeek 鲸标（单色 #5786FE）| Simple Icons `deepseek` |

## 当前实际引用（2026-11-16 更新）

- 平台 `dsh` 在界面上引用 **`deepseek.svg`（品牌蓝 #5786FE）**，不是 `dsh.svg`。
  原因：`dsh.svg` / `dsh-dark.svg` 是官方 Web UI favicon 的**单色**版（黑 / 白），
  放进浅底色卡片里就是"一坨黑"（产品负责人 2026-11-16：「DSH 是个蓝色的图标吧」）。
  两个单色文件**保留**，将来做深色主题配对时要用。
- 平台 `openclaw` 引用 `openclaw.svg`；`codex` / `claude_code` 同上表。

## 说明 / 注意

- **DSH = DeepSeek Harness** 的官方缩写（见其 `BRAND_GUIDELINES.md`）。其 logo 即 DeepSeek 鲸标。
- **Codex / Claude Code** 官方仓库内**无独立 logo 资产**：Codex 取 Codex 文档站 favicon；Claude Code 取 Claude 官方品牌标记。
- **商标**：以上均为各公司商标。仅用于**指代/识别**对应产品（"纳指性使用"），不得暗示官方背书或合作。商用/再分发前请复核各自品牌指南与授权。
- 🔧 如需 **Claude Code / Codex 专属产品标**（而非品牌标），需从官方 press kit 获取，当前公开渠道未提供。
