<!-- English (GitHub) primary. Chinese version: README.zh-CN.md -->

# AgentGeeX Console

> The management frontend of the agent orchestration platform — it puts an agent's
> **runtime state, task chains, sessions, projects and plans** on one screen.

Not a demo, not a template: this is the real web console AgentGeeX gives its users.
All data comes from a live backend (FastAPI BFF → MySQL / Redis / MCP kernel);
the browser never touches a database credential.

**Stack**: React 18 · Vite 5 · Refine 4 (headless) · Tailwind CSS · shadcn/ui · TanStack Query · React Router 6 · i18next

---

## What it does

| Page | Route | Purpose |
|---|---|---|
| System status | `/system` | Machine and kernel health, runtime snapshot |
| Quick access | `/quick-access` | A wall of cards for common actions |
| Agents | `/agents` | Roster, online state, grouping by project, deploy state |
| Sessions | `/sessions` | Per-agent session list; send a message / dispatch a task directly |
| Tasks | `/tasks` | Task templates (spec + parameter schema) |
| Executions | `/executions` | Instruction, status, live output, duration and receipt for every dispatch |
| Quick commands | `/quick-cmds` | **Wrap a prompt as a button**: pick parameters → send |
| Skill sharing | `/skills` | The "how to do it" that agents deposit for each other, with usage / endorsement signals |
| Notices / work log | `/notices` | Pitfalls, decisions, handover records (Markdown body) |
| Projects | `/projects` · `/project-groups` | Projects and their member templates |
| Plans | `/plans` | Multi-step plan topology and **real runtime state** (React Flow diagram) |
| Hosts | `/hosts` | Host inventory and task-slot occupancy |

Design stance: **if it can be clicked, don't type it**. Customers don't memorise prompts —
they click a button and fill in parameters to drive an agent.
Every click lands as one traceable execution.

## Quick start

```bash
npm install
npm run dev        # http://127.0.0.1:5190
```

To show data the frontend needs a running BFF (`webtools/server`, FastAPI):

```bash
# in another terminal
cd ../server && ./run.sh          # http://127.0.0.1:8090
```

The address is set by an environment variable (see `.env.example`):

```
VITE_API_URL=http://127.0.0.1:8090/v1
```

> **What happens without a BFF**: the pages never pretend "there is data". On startup the
> `/readyz` probe fails → an explicit error page / redirect to login is shown,
> rather than stuffing in fake data that makes people misjudge the system as healthy
> (this is a deliberate design decision).

### Login (currently a placeholder)

Any **non-empty** username and password gets you in; the username is passed to the backend
as `caller_agent_id`, used for audit and resource-ownership checks.

> ⚠️ This is a development-era placeholder only: it is **not** a shippable auth scheme.
> A real deployment must swap in a proper identity source
> (OIDC / corporate SSO / gateway auth) and tighten permissions on the BFF side.
> The repository stores no account or password, and should not.

## Directory layout

```
src/
├── App.tsx                 route table (business pages + login)
├── components/
│   ├── shell.tsx           outer frame: sidebar / top bar / breadcrumb / tab bar / footer
│   ├── agentgeex/          business components (data tables, send dialog, voice input, flow diagram…)
│   ├── tabs/               multi-tab workspace (state / tab bar / context menu)
│   └── ui/                 shadcn primitives (button / dialog / select / table…)
├── pages/agentgeex/        business pages
├── providers/              API client / Refine data provider / startup data loading
├── lib/                    navigation config, breadcrumbs, formatting, storage keys — pure logic
├── i18n/                   language packs (one file per namespace) + pseudo-localisation
└── mock/                   types and **empty-state seeds only** (real data always comes from the BFF)
```

## Internationalisation (in progress)

- Languages: `zh-CN` (default) / `en-US`; plus `en-XA` **pseudo-localisation**, there specifically to expose "English gets longer and blows up the layout".
- Language packs are split by namespace (`app` / `common` / `header` / `nav` / `footer` / `page`),
  one file per feature domain — so nobody ends up fighting over the last line of one big file.
- Navigation, breadcrumbs, browser title, tab titles and the top bar are wired up; page bodies are still migrating.

Three commands turn "is the internationalisation done" into numbers and failures:

```bash
npm run i18n:scan       # how much Chinese is still outside the language packs (baselined; fails if it grows)
npm run i18n:check      # the zh and en trees must be isomorphic; every t("x.y") key in code must exist
npm run i18n:overflow   # run 13 routes under pseudo-localisation and find overflowing text
```

The "key must exist" check inside `i18n:check` was paid for in blood: when i18next cannot
find a key it **does not error**, it prints the key verbatim in the UI (`tab.system`),
with no build-time hint whatsoever.

## Quality gates

Run these three before committing; none is optional:

```bash
npx tsc --noEmit     # 0 errors
npm run build        # build passes
npm run i18n:check   # language packs consistent
```

End-to-end acceptance uses Playwright (`scripts/verify_*.mjs`), with these rules:

- **Stub every write endpoint with `page.route()`**; a test must never really dispatch work to a person or send a message;
- after the run, console errors must be zero;
- throwaway scripts are deleted after use; kept scripts must be independently re-runnable.

## Known limits

- **Auth is a placeholder** (see above). There is no real user system, multi-tenant isolation, rate limiting or billing — those are BFF-side TODOs.
- Pages are mostly **read**-oriented; write operations currently cover: dispatch task, send text, quick command, task/execution record editing, quick-command CRUD.
- An agent's online behaviour is decided by the backend kernel; the frontend only renders and triggers, and does no local state inference (which avoids "the UI looks successful but nothing was actually sent").

## Related documents

- `agent_doc/architecture.md` — frontend/backend layering, data flow, why the browser never talks to the database directly
- `agent_doc/api-contract-draft.md` — BFF interface contract
- `agent_doc/i18n-and-naming-plan.md` — naming clean-up and i18n plan (including the legacy-abbreviation cross-reference)
- `agent_doc/universal-input-bar-spec.md` — interaction spec for the message-sending dialog

## License

[Apache-2.0](../../LICENSE)
