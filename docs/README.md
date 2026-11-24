# Documentation

Everything below ships with the release. Start with **Getting started** if you are installing AgentGeeX for the first time; the other sections are reference material you will come back to.

New to the project? [`../README.md`](../README.md) is the 5-minute overview.

---

## Getting started

| Document | Read it when |
|---|---|
| [Install with an agent](getting-started/install-with-agent.md) | You want your own agent to install AgentGeeX for you — hand it this file |
| [Onboarding an existing agent](getting-started/onboard-existing-agent.md) | You already run agents outside AgentGeeX and want to bring them in |
| [Onboarding an existing agent — practical SOP](getting-started/onboard-existing-agent-practical.md) | You are doing the onboarding for real and want the step-by-step, including the traps we hit |

## Concepts

| Document | Read it when |
|---|---|
| [Architecture](concepts/architecture.md) | You want the shape of the system: layers, entities, and why they are separated that way |
| [Task protocol](concepts/task-protocol.md) | You are writing a task template or an agent that consumes executions |
| [MCP gateway](concepts/mcp-gateway.md) | You are configuring the MCP endpoints, or migrating from stdio to HTTP |
| [Permission system](concepts/permissions.md) | You need to know who may call which tool, and why the levels are split the way they are |
| [Host platforms](concepts/host-platforms.md) | One machine runs several agent platforms, or you are registering a new host |
| [Claude Code compatibility](concepts/claude-code-compat.md) | You are wiring Claude Code in as a runtime |

## Development

For people changing the code.

| Document | Read it when |
|---|---|
| [Repository layout and Git conventions](development/repo-layout-and-git.md) | Before your first commit — this explains where agent files live and what goes into Git |
| [Framework contract](development/framework-contract.md) | You are extending the kernel and need the invariants it relies on |
| [Environment parity](development/environment-parity.md) | Dependencies resolve differently on your machine than in CI |
| [Platform integration](development/platform-integration.md) | You are adding a new agent runtime as a first-class platform |
| [Toolchain](development/toolchain.md) | You are setting up a development machine from scratch |

## Operations

Internal runbooks. They describe how *this* deployment is run day to day — including the incidents that shaped the rules. Useful if you operate AgentGeeX for a team; safe to skip if you are only evaluating it.

| Document | Read it when |
|---|---|
| [Project management](internal/project-management.md) | You are the PM agent, or you are defining how work is filed and tracked |
| [Agent migration runbook](internal/migration-runbook.md) | You are moving a whole project group from one host to another |
| [Redis cache design](internal/redis-cache-design.md) | You are debugging cache behaviour or changing what gets cached |
| [Internal communication protocol](internal/internal-comm-protocol.md) | You are touching daemon ↔ agent message envelopes |
| [Watchdog and wakeups](internal/watchdog-wake.md) | Tasks are timing out, or you want to understand why a wake fired |
| [Watchdog — OpenClaw specifics](internal/watchdog-openclaw.md) | The wake/alert path behaves differently on OpenClaw than you expected |
| [WS direct execution](internal/ws-direct-execution.md) | You are running an agent through the WebSocket channel rather than tmux |
| [Subagent contract](internal/subagent-contract.md) | You are implementing an executor that must honour the task contract |

---

## Not documentation

Some files look like docs but are not for humans:

| Path | What it actually is |
|---|---|
| [`../tasks/`](../tasks/) | Task templates. Loaded into `agx_task_base.x_desc` and fed to agents at runtime |
| [`../agents/`](../agents/) | Agent identity manuals. Injected into each agent's prompt |
| [`../skills/`](../skills/) | Skill definitions loaded by agents |

Editing those changes how agents behave, not how the docs read.
