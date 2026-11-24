# Contributing to AgentGeeX Console

**English** · [简体中文](CONTRIBUTING.zh-CN.md)

This is a console built for real use, not a sample repository. A change lands directly on
someone else's workbench, so the items below aren't formalities — they were **paid for**.

## Before you start

```bash
npm install
npm run dev          # 5190
# another terminal: cd ../server && ./run.sh   (BFF, 8090)
```

Log in with any non-empty username/password (placeholder auth). The username becomes
`caller_agent_id` — **never try write operations under someone else's agent_id**, the
audit log will attribute them to the identity you borrowed.

## Must pass before committing

```bash
npx tsc --noEmit     # 0 errors
npm run build        # build passes
npm run i18n:check   # zh/en packs isomorphic + keys really exist
```

If a frontend change touches interaction, add a Playwright acceptance script (`scripts/verify_*.mjs`):

- write endpoints must be stubbed with `page.route()` — **a test must never really dispatch work to a person or send a message**;
- assert console errors are zero;
- the script must be independently re-runnable (brings its own login, its own cleanup).

## Rules for user-visible copy

1. **Use Chinese or English only, never mix them mid-sentence**; technical nouns (Agent / Task / MCP) excepted.
2. **One name per action.** Example: that submit action is called "send" — never let "dispatch" or "deliver" creep in as synonyms.
3. **Copy goes into the language pack, not hardcoded in the component**:
   ```ts
   // ❌
   <Button>发送</Button>
   // ✅
   <Button>{t("header.send")}</Button>
   ```
   A new key must be added to both `src/i18n/locales/zh-CN/<ns>.ts` and `en-US/<ns>.ts`,
   then `npm run i18n:check` confirms the two sides are isomorphic.
4. **Chinese is shorter than English.** Leave room for buttons/labels to grow; never size text with fixed pixels.
   `npm run i18n:overflow` uses pseudo-localisation (length ≈ 2.2× Chinese) to point at what bursts.

## Code conventions

| Item | Convention |
|---|---|
| Components | Reuse what already exists in `src/components/ui/*` and `src/components/agentgeex/*`; don't invent a second style system |
| Styling | Design tokens only (`bg-card` / `text-muted-foreground` / `border-strong`…), no magic colour values |
| Data | Always through `src/providers/*`; **no direct fetch inside components, and certainly no database access** |
| Storage | Key names are registered in `src/lib/storage-keys.ts`; legacy keys must stay readable (an upgrade must not lose a user's login state or drafts) |
| Comments | Say "why it's done this way", not "what this line does". Leave a trace of the pitfalls you hit |
| Interaction | A disabled button must state why (`title` + visible copy); never "greyed out but silent about the reason" |

## Directory boundaries

- `src/components/ui/` — generic primitives; confirm every caller before changing one;
- `src/components/agentgeex/` — business components;
- `src/pages/agentgeex/` — pages;
- `src/lib/` — pure logic (unit-testable, no React dependency);
- `src/mock/` — types and empty-state seeds only, **no demo business data**.

The backend (`webtools/server`) and the kernel (`agx_mcp/`) are out of scope for this directory;
if you change the interface contract, update `agent_doc/api-contract-draft.md` at the same time.

## Commit messages

Chinese, one sentence stating "what changed + why". Example:

```
fix(sessions)：会话列表按最后活跃排序 —— 原来按创建时间，翻到第二页就找不到刚聊过的
```

## License

Contributing means you agree to release it under this project's [MIT License](./LICENSE).
