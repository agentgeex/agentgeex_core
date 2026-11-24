<!-- English (GitHub) primary. Chinese version: README.zh-CN.md -->

# Partner / ecosystem project logos

Used by the AgentGeeX web frontend to identify each agent runtime / CLI in the ecosystem.
The frontend references them directly: `/logos/<file>` (Vite `public/` directory, emitted verbatim after build).

> Collected: 2026-10-16 | Collected by: agx_ui_lead

## The main four (specified by the author 2026-10-16)

| File | Project | Form | Source (official) |
|---|---|---|---|
| `openclaw.svg` | **OpenClaw** | Red lobster mascot (official site icon) | `https://openclaw.ai/favicon.svg` |
| `openclaw-mascot.svg` | OpenClaw | Pixel lobster (official repo) | repo `openclaw/openclaw` → `docs/assets/pixel-lobster.svg` |
| `dsh.svg` / `dsh-dark.svg` | **DSH** (DeepSeek Harness) | Whale mark (official web UI favicon, light/dark) | repo `deepseek-ai/deepseek-harness` → `apps/web/public/favicon[-dark].svg` |
| `dsh-icon.svg` | DSH | Desktop app icon (1104×1104) | repo `deepseek-ai/deepseek-harness` → `apps/desktop/resources/icon.svg` |
| `codex.svg` | **OpenAI Codex** | Blue circle + white knot mark (Codex docs site icon) | `https://developers.openai.com/codex` favicon |
| `claude-code.svg` | **Claude Code** | Official Claude mark (#D97757) | Simple Icons `claude` (CDN `cdn.simpleicons.org/claude`) |

## Alternatives / generic brand marks (same origin, interchangeable)

| File | Brand | Source |
|---|---|---|
| `openai.svg` | OpenAI flower mark (white, needs a dark background) | repo `openclaw/openclaw` → `docs/assets/sponsors/openai.svg` |
| `anthropic.svg` | Anthropic mark | Simple Icons `anthropic` |
| `deepseek.svg` | DeepSeek whale mark (mono #5786FE) | Simple Icons `deepseek` |

## What is actually referenced today (updated 2026-11-16)

- Platform `dsh` references **`deepseek.svg` (brand blue #5786FE)** in the UI, not `dsh.svg`.
  Why: `dsh.svg` / `dsh-dark.svg` are the **monochrome** (black / white) versions of the
  official web UI favicon, and inside a light-background card they become "one black blob" (author, 2026-11-16: "DSH is a blue icon, isn't it?").
  Both monochrome files are **kept** — they'll be needed for dark-theme pairing later.
- Platform `openclaw` references `openclaw.svg`; `codex` / `claude_code` as in the table above.

## Notes / caveats

- **DSH = DeepSeek Harness** is the official abbreviation (see its `BRAND_GUIDELINES.md`). Its logo is the DeepSeek whale mark.
- **Codex / Claude Code** ship **no standalone logo asset** in their official repos: Codex uses the Codex docs-site favicon; Claude Code uses the official Claude brand mark.
- **Trademarks**: all of the above are trademarks of their respective companies. They are used only to **refer to / identify** the corresponding product (nominative use) and must not imply official endorsement or partnership. Re-check each brand guideline and licence before commercial use or redistribution.
- 🔧 If you need the **Claude Code / Codex product marks** (rather than the brand marks), you have to get them from the official press kit; they are not available through public channels today.
