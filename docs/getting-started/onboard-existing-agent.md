# Onboarding a running Agent into the AgentGeeX database project structure — access guide

> Maintainer: agx_core (AgentGeeX core developer)
> Applies to: **Agents already running directly under OpenClaw that need to join the AgentGeeX database project structure**
> First put into practice: 2026-08-19, `medicine_gpt_doctor` (worker → project_manager, bound to the `medicine_gpt` project)
> The related red lines are documented in `agents/super_pm/AGENTS.md`: governance red lines (always go through agentfather / projectmother) plus the pitfalls recorded here

---

## 0. Concept: two onboarding scenarios — do not confuse them

| Scenario | Target | deploy_status | Does it use agx_deploy_agent | Tools used |
|------|------|--------------|----------------------|---------|
| **A. A brand-new Agent joins** | Cloned or created from a template, not yet materialised on disk | `template` / `pending` | ✅ Required | `agx_clone_agent` → `agx_bind_*` → `agx_deploy_agent` |
| **B. ⭐ A running Agent joins** | Already running directly under OpenClaw, only needs to be registered into the DB project structure | **`deployed`** (registration already succeeded) | ❌ **Not used** | `agx_upsert_agent` directly + `agx_create_project` + writing the md fields |

**This document covers scenario B only.**

### The key judgement for scenario B

These agents are already up and running inside OpenClaw (with their own workspace and their own AGENTS.md). What they need is for their identity data to be registered in the AgentGeeX database so that they can:
- be discovered by `agx_list_project_agents` / `agx_list_by_network`
- be bound to a project (the role may be worker / project_manager / anything else)
- be scheduled, collaborate and be delegated to when needed

**Because the agent already really exists and runs, its `deploy_status` in `agx_agent_registry` is `deployed`, not `pending`.** Do not try to "deploy" it, and do not treat it as a new agent going through the materialisation flow.

---

## 1. Information to gather before onboarding

Collect these first:

| Item | Source | Example (medicine_gpt_doctor) |
|------|------|------------------------------|
| agent_id | The identity of the running agent | `medicine_gpt_doctor` |
| agent_name | Human-readable name | `医药大模型研究专家` |
| Target role | Business positioning | `project_manager` (research PM) |
| Owning project | New or existing | New `medicine_gpt` / bind directly if it exists |
| network_id | Owning network | `lan_001` |
| host_id | Host it runs on | `test_host_mac` |
| Project platform | openclaw / claude_code / custom | `openclaw` |

---

## 2. Onboarding flow (scenario B, five steps)

### Step 1: Create (or confirm) the project

Only when the agent belongs to a brand-new business project:

```
agx_create_project(
    caller_agent_id=<scheduler>,
    project_name="medicine_gpt",
    project_abbr="mgpt",          # all lowercase, used as the agent name prefix
    description="...",
    platform="openclaw",
)
→ 返回 project_id：proj_7b53ae2da27e
```

> ⚠️ **Red line note**: in the formal flow, projects are created through `agx_projectmother`. Here the scheduler creates it directly because "registering data for an already-running agent" is a special case, and the exception must be recorded.

After the project exists, write its description (x_desc) — agents inside the project read it through `agx_get_project_config(project_id)`:

```
agx_update_project(
    project_id="proj_7b53ae2da27e",
    updates={"x_desc": "# 项目说明书\n\n## 二元架构...\n..."}
)
```

> The project x_desc should state the project goal, the owner and the key paths (for example the dual-track research / code tracks), so that agents inside the project perceive the full context at startup.

### Step 2: Register the Agent (the core step — use upsert)

An already-running agent **must be registered with `agx_upsert_agent`** (not `agx_deploy_agent`):

```
agx_upsert_agent(
    caller_agent_id=<scheduler>,
    network_id="lan_001",
    agent_id="medicine_gpt_doctor",
    agent_name="医药大模型研究专家",
    role="project_manager",        # project_manager / worker / ...
    host_id="test_host_mac",
    model="deepseek",
    agent_type="openclaw",
    project_id="proj_7b53ae2da27e",
    description="...",
    agents_md=<full merged AGENTS.md>,   # key: write the production AGENTS.md content into the DB
)
→ "Agent processed: 医药大模型研究专家 ID: medicine_gpt_doctor"
```

**Key points:**
- 🔴 **Do not use `agx_deploy_agent`.** The agent is already running; that step would treat its status as pending and may materialise over, or overwrite, the live configuration.
- upsert is idempotent: an existing agent_id is updated, a missing one is created.
- The `agents_md` field must be supplied — it is the workspace manual registered in the DB (the source of AGENTS.md content). The `agents_md` passed to upsert must **match** the production `workspace/AGENTS.md` **exactly**.
- Give the final role directly at upsert time (e.g. project_manager) — there is no need to start as worker and change later.

### Step 3: Sync the production AGENTS.md

A running agent actually reads `workspace/AGENTS.md` on disk. The DB `agents_md` is only the registered baseline.

Two cases:
- **No AGENTS.md, or it needs upgrading**: `cp` the merged / new AGENTS.md straight to `~/.agx/agx_agents/<agent_id>/workspace/AGENTS.md`
- **It already has content**: edit or replace it directly, keeping **DB agents_md and the production file identical**

For cases such as "upgrading a worker to PM", AGENTS.md has to be merged (governance skeleton × business specifics), and the merged version is written both into the DB `agents_md` and into the production file.

### Step 4: Update agx.env (optional — brings in project information)

Add project context to `workspace/agx.env` so the agent perceives it at runtime:

```bash
AGX_AGENT_ID="medicine_gpt_doctor"
AGX_AGENT_NAME="医药大模型研究专家"
AGX_AGENT_ROLE="project_manager"
AGX_PROJECT_ID="proj_7b53ae2da27e"
AGX_PROJECT_NAME="medicine_gpt"
AGX_NETWORK_ID="lan_001"
AGX_HOST_ID="test_host_mac"
AGX_STUDY_DIR="..."   # add the business directory if there is a dual-architecture mechanism
AGX_CODE_DIR="..."
```

### Step 5: Verify

```
agx_get_agent(agent_id=...)            # confirm role / project_id / agents_md were written
agx_list_agent_tasks(agent_id=...)     # confirm the agent's tasks are in place (read-only check; add or remove nothing)
agx_get_project(project_id=...)        # confirm the project + x_desc
```

---

## 3. ⚠️ Pitfall list (including real lessons)

### ⚠️ Red line 1: when a task definition needs implementing, confirm the target's state before touching anything

Injecting and updating `agx_task_base` (task definitions) is the responsibility of **the core developer / the deployment flow**. That includes `agx_create_task` to inject and `agx_update_task` to update x_desc / active_status — all normal operations, none of them forbidden.

But **always confirm first**:
- Does the target agent already have the corresponding task (check ahead with `agx_list_agent_tasks`) — avoid injecting the same task_labs twice.
- Is the existing task already the complete version configured by the system / flow — if so, skip the injection; if it genuinely needs supplementing or fixing, `agx_update_task` is fine, but **never overwrite existing valid x_desc content** (especially never blank it with `""`).
- Updating x_desc / active_status is a strong operation: leave a trace before (notice / comment) and verify after.

**Conclusion: injecting and updating tasks is a normal responsibility. The point is to check the state first and not to damage valid existing configuration.** When a task definition must be created or extended, use `agx_create_task`; when it must be corrected, use `agx_update_task`.

### 🔴 Red line 2: a running agent's deploy_status is deployed, not pending

- Do not assume it is pending just because "it never went through `agx_deploy_agent`".
- A scenario-B agent is already registered and running successfully, so its status is `deployed`. Do not "deploy" it.

### 🔴 Red line 3: creating a project goes through projectmother

- Apart from the "registering data for an already-running agent" exception, creating a project formally must dispatch the `project_create` task to `projectmother` — **never call `agx_create_project` yourself**.
- If the exception was taken, record it (notice / document) so it can be audited later.

### 🔴 Red line 4: when unsure, stop

- For any judgement of the form "this looks duplicated / superfluous", verify the source first (was it configured by the system / by agx_core) — **never clean up on a hunch**.
- Destructive writes (deactivating something, blanking a field) must be confirmed before running.

---

## 4. Recording and handover

Once onboarding is complete, publish a config / handoff notice recording what was done (for auditing):

```
agx_write_notice(
    caller_agent_id=<scheduler>,
    agent_id=<scheduler>,
    project_id=<project or system_project>,
    notice_type="config",          # or handoff
    title="<agent_id> 融入完成（角色升级 + 项目绑定）",
    content="...关键步骤 + 项目id + deploy_status 说明...",
)
```

---

## 5. Reminders for the bulk onboarding that follows (including this maintainer)

- **Every agent (agx_core itself included)** onboards the same way, always scenario B: if it already runs, register it with `agx_upsert_agent` and keep `deploy_status` at `deployed`.
- Before onboarding, run `agx_get_agent(agent_id=<target>)` to see whether it is already registered and what its role is — **avoid double registration and accidental edits**.
- When merging AGENTS.md: leave the production SOUL / IDENTITY / USER / TOOLS / HEARTBEAT untouched, replace or extend only AGENTS.md, and when doing a "governance skeleton × business specifics" merge, remember to carry the governance red lines back in (agentfather / projectmother / plan_execute / degradation protocol).
