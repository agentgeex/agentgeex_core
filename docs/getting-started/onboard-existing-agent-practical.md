# Onboarding a running Agent into AgentGeeX: field SOP (with a full pitfall log)

> Maintainer: agentgeex_agent (AgentGeeX creator / maintainer, who completed the onboarding first-hand)
> Applies to: **Agents already running directly under OpenClaw that need to join the AgentGeeX database project structure (scenario B)**
> First complete run: 2026-08-19, `agentgeex_agent` (project_manager, bound to the `agentgeex` project)
> Companion official procedure: `docs/getting-started/onboard-existing-agent.md` (the theory, by agx_core)
> This document is the **field guide**: the official procedure's skeleton + the three fatal pitfalls I hit myself + a full-chain verification method.

---

## 0. Read this first — why

The bulk onboarding that follows will take in dozens of already-running agents at once. If you go by "what seems obvious", you will hit the three pitfalls I ran into while onboarding agentgeex — each one can leave a dispatched execution **stuck at running**, make the watchdog **fire a chain of alerts**, and make the delivery log cry `Agent "xxx" no longer exists in configuration`.

All three share one root: **the real identity on the OpenClaw side and the registered identity in the AgentGeeX database were not aligned**. This document gives you a **verified, working onboarding SOP**: three steps plus a passing verification.

---

## 1. Core insight: the OpenClaw side is the authoritative source of agent identity

**This is the biggest lesson of the whole onboarding, and the most important statement in this document.**

AgentGeeX is an orchestration layer: it hands tasks to OpenClaw over a WS client for execution. When delivering, the WS client uses a "session prefix" to find the agent inside OpenClaw. **OpenClaw only recognises agent ids registered in its own configuration.**

```
AgentGeeX DB (agx_agent_registry.agent_id)  ≠  OpenClaw (~/.openclaw/openclaw.json → agents.list[].id)
       the "name" you registered                     the one that can actually run
```

If the two disagree, `agx_dispatch_task` **enqueues successfully** (AgentGeeX does not block it at all), the execution moves to `running`, **but the WS delivery makes OpenClaw fail**:

```
Agent "agentgeex" no longer exists in configuration
→ runStarted: false
→ retried 5 times, all failed → error_delivery
→ the execution is stuck in running forever, and the watchdog starts a cascade of alerts
```

**So the first step is not registering — it is finding out the real agent id on the OpenClaw side.**

### How to find the authoritative agent id on the OpenClaw side

```bash
# list every registered agent and its workspace
python3 -c "
import json
cfg = json.load(open('$HOME/.openclaw/openclaw.json'))
for i, a in enumerate(cfg.get('agents', {}).get('list', [])):
    print(f'[{i}] id={a.get(\"id\")} | name={a.get(\"name\")} | ws={a.get(\"workspace\",\"\")}')
"
```

> ⚠️ Note: the agent's **workspace / agent directory / runtime identity** must all share exactly the same prefix — the same id. The pitfall I hit was: the workspace directory was called `agentgeex_agent` (correct), but when registering with AgentGeeX I mistyped `agentgeex` (dropping `_agent`), which put the DB and OpenClaw out of alignment.

---

## 2. The three-step onboarding SOP (scenario B, verified)

### Step 1: Check the state, do not duplicate

```python
# 1. First look up the real id on the OpenClaw side (command in the previous section)
# 2. Check whether AgentGeeX has already registered it
agx_get_agent(agent_id=<real-id>, caller_agent_id=...)
# 3. Check whether the system has already provisioned the tasks (avoid injecting them twice)
agx_list_agent_tasks(agent_id=<real-id>, caller_agent_id=...)
```

**Decide like this**:
- If `agx_get_agent` already returns a row with the right role / project → it is only a gap to fill; do not upsert again.
- If the tasks are already provisioned by the system (the full general / universal / watchdog_alert trio) → **do not `agx_create_task` again**, keep the system versions. I injected them twice; that was pure pollution.

### Step 2: Create the project + register the Agent (with the real id)

```python
# 2.1 Project (create it if missing; skip if it exists)
agx_create_project(
    caller_agent_id=<scheduler>,
    project_name="业务名",
    project_abbr="abbr",     # all lowercase
    description="...",
    platform="openclaw",
)

# 2.2 Register (key point: agent_id must equal the real OpenClaw id, not the name you assumed)
agx_upsert_agent(
    caller_agent_id=<scheduler>,
    network_id="lan_001",
    agent_id="<openclaw-real-id>",     # ← key! must match agents.list[].id in openclaw.json exactly
    agent_name="表意名称",
    role="project_manager",           # give the final role directly
    host_id="test_host_mac",
    model="deepseek",
    agent_type="openclaw",
    project_id="<proj_xxx>",
    description="...",
    agents_md=<full production AGENTS.md>,   # keep it in sync with workspace/AGENTS.md
)

# 2.3 Write the project x_desc (optional but recommended)
agx_update_project(project_id=..., updates={"x_desc": "# 项目说明书\n..."})
```

> 🔴 **agent_id rules** `[a-z0-9_]`; no uppercase, no spaces, no Chinese.
> 🔴 **deploy_status for scenario B should be `deployed`** (the system corrects it automatically during the upsert / registration flow). **But if registration is delegated by dispatching to `agx_agentfather` (see the red line in 5.5.5 — a project_manager cannot call `agx_upsert_agent` itself), deploy_status lands on `pending`** (agentfather follows the "create + deploy" path and does not recognise the "already-running agent, scenario B" case) — fix it to deployed with a manual SQL statement. **Verified in practice 2026-08-20**.
> 🔴 Keep `scope` at `project` (business agents belong to their own project and do not interfere with system operations).

### Step 3: Sync the configuration files (agx.env + AGENTS.md)

```bash
# 3.1 agx.env (you can copy the format of an already-deployed agent)
cat > <workspace>/agx.env << 'EOF'
AGX_AGENT_ID="<openclaw-real-id>"
AGX_AGENT_NAME="表意名称"
AGX_AGENT_ROLE="project_manager"     # same as the role environment variable
AGX_PROJECT_ID="<proj_xxx>"
AGX_PROJECT_NAME="业务名"
AGX_NETWORK_ID="lan_001"
AGX_HOST_ID="test_host_mac"
EOF
```

> 🔴 **agx.env must match the real OpenClaw id exactly.** `AGX_AGENT_ID` must equal the agent id in openclaw.json (e.g. `agentgeex_agent`, never `agentgeex`). **This is an easy typo to make** — agx.env is only an identity backup / reference, so a typo does not blow up immediately, but it plants a hidden hazard that the runtime chain will never surface on its own.
> **After writing, check every field one by one** (`AGX_AGENT_ID` / `_NAME` / `_ROLE` / `_PROJECT_ID` / `_PROJECT_NAME` / `_NETWORK_ID` / `_HOST_ID` against the DB), not just a single grep. Real record: on 2026-08-20 my agx.env was written as `agentgeex` (missing `_agent`) while both OpenClaw and the DB said `agentgeex_agent`; only the env file was wrong, it never exploded, but it had to be corrected.

### 3.2 AGENTS.md: **a business PM must merge the super_pm platform-collaboration conventions into its business AGENTS.md**

> 🔴 **This is the systemic gap most easily missed in scenario B (field pitfall, 2026-08-20).**
> If you only stuff the business AGENTS.md into the DB, the agent ends up as "a bare agent wearing the project_manager badge that does not know how to collaborate on the platform" — it does not know to use `agx_get_global_config`, does not know to delegate through agentfather / projectmother, does not know that activating a plan goes through plan_execute, and may go off polling on its own.

**Merge strategy (business as the body + insert the platform collaboration layer):**
1. After the identity statement at the top of the file, insert a section "0. AgentGeeX platform collaboration conventions" (model it on `agents/super_pm/AGENTS.md`):
   - Session start: `agx_get_global_config` → read agx.env → `agx_get_project_config`
   - Task message handling flow (agx_get_execution → execute → **agx_finish_task must close it out**)
   - Three-step decision for non-task messages (yourself → peers → decline) + **executing your own task must go through dispatch, never read x_desc directly** + **polling proactively is strictly forbidden**
   - My task list (see with `agx_list_agent_tasks` what is actually assembled on you)
   - System peer table + common delegation table (create Agent → agentfather, create project → projectmother, activate plan → your own plan_execute)
   - Platform red lines (do not create Agents / projects beyond your authority, **activating a plan goes through plan_execute, never call agx_activate_plan by hand**, escalate on timeout instead of doing it yourself, never modify yourself)
   - Hierarchical plan decision (<15 steps → plan_generate_and_deploy directly; 15+ or independent sub-pipelines → hierarchical hub)
2. **Keep every original section of the business AGENTS.md** (business capability and platform conventions coexist)
3. `agents_md` = the full merged text, byte-identical to workspace/AGENTS.md (write with pymysql and compare with `LENGTH`; Chinese counts by bytes)

> ⚠️ **Do not write scheduled inspections into an agent's AGENTS.md.** Scheduled inspection is the job of the AgentGeeX watchdog / heartbeat mechanism, not something the agent does itself with cron (scheduling is far too expensive inside OpenClaw). An agent's AGENTS.md must **not** contain self-imposed tasks such as "proactively scheduled / daily inspection"; it may only say "**when responding to a heartbeat event**, do a passive inspection" (like the "check while responding to the heartbeat" pattern in §6.3 — driven by AgentGeeX pushing heartbeats, never by a timer the agent sets itself). The "proactively polling is forbidden" red line must stay — it exists precisely to stop an agent from repeatedly checking progress on its own.

---

## 3. ⚠️ The three fatal pitfalls (all genuinely hit by me, each with its fix)

### 🔴 Pitfall 1: agent_id differs between the two systems (the primary cause)

**Symptom**: after dispatch the execution sticks at running, and ws_client.log reports `Agent "xxx" no longer exists in configuration`.

**Root cause**: the AgentGeeX registered id ≠ the OpenClaw configured id. WS delivery cannot find the agent.

**Fix**: change the AgentGeeX DB agent_id to match OpenClaw:

```sql
-- tables to sync: agent_registry (the core) plus the related task / execution / notice
UPDATE agentgeex.agx_agent_registry SET agent_id='<correct-id>' WHERE agent_id='<wrong-id>';
UPDATE agentgeex.agx_task_base SET agent_id='<correct-id>' WHERE agent_id='<wrong-id>';
UPDATE agentgeex.agx_task_execution SET agent_id='<correct-id>' WHERE agent_id='<wrong-id>';
UPDATE agentgeex.agx_agent_notice SET agent_id='<correct-id>' WHERE agent_id='<wrong-id>';
```

**The cache must be rebuilt afterwards** (see pitfall 3).

### 🔴 Pitfall 2: the default_session_id prefix is frozen (still fails after fixing agent_id)

**Symptom**: with agent_id aligned, general / receipt tasks **still** fail to deliver, still `Agent "xxx" no longer exists`.

**Root cause**: `agx_agent_registry.default_session_id` still holds the **old agent prefix** (e.g. `agent:agentgeex:main`). WS delivery uses it as the session prefix for receipts / general tasks and again looks up the wrong agent. **agent_id was changed, default_session_id was not.**

**Fix** (the step most easily missed):

```sql
UPDATE agentgeex.agx_agent_registry
SET default_session_id = 'agent:<correct-id>:main'
WHERE agent_id = '<correct-id>'
  AND default_session_id = 'agent:<old-prefix>:main';

-- align the session_id prefix of historical executions (tidier data)
UPDATE agentgeex.agx_task_execution
SET session_id = REPLACE(session_id, 'agent:<old-prefix>:', 'agent:<correct-id>:')
WHERE agent_id = '<correct-id>'
  AND session_id LIKE 'agent:<old-prefix>:%';
```

> 💡 How to determine the correct prefix: the session key of the currently running session is the correct prefix. OpenClaw generates it at runtime from the agent id in openclaw.json (e.g. `agent:agentgeex_agent:main`).

### 🔴 Pitfall 3: changing state by SQL ≠ syncing Redis → a chain of watchdog alerts

**Symptom**: you set an execution to failed by SQL, but the watchdog **keeps dispatching watchdog_alert**, creating new running rows.

**Root cause**: **the watchdog reads the Redis cache** (the source of speed), not MySQL (the source of truth). A direct SQL change only writes MySQL; in Redis that execution is still `running` and overdue, so the watchdog never sees it closed out → it keeps alerting. This is exactly why the AGENTS.md red line says "operate on `agx_*` tables through MCP tools".

**Fix**:
1. Closing a task should preferably go through `agx_finish_task` (MCP writes both MySQL and Redis and syncs the cache automatically):

```python
agx_finish_task(
    caller_agent_id='<correct-id>',
    task_run_uuid='run_xxx',
    status='succeeded' | 'failed',
    result='...',
    summary='...',
)
```

2. If you already changed it by SQL, **you must rebuild the cache** so Redis matches MySQL:

```python
# requires L7 permission (workshop_manager / agx_core)
agx_cache_rebuild(caller_agent_id='<caller-with-l7>', entity_type='execution')
# or rebuild everything
agx_cache_rebuild(caller_agent_id='<caller-with-l7>')
```

> ⚠️ Permission level: `agx_cache_rebuild` requires level 7. A project_manager only has L0,1,2,3,5 and **cannot call it**. Either use the agx_core identity or delegate through a dispatch.

---

## 4. Full-chain verification (the standard for a successful onboarding)

Seeing correct data in `agx_get_agent` **does not count** as a successful onboarding — you must verify that the real chain runs end to end.

### 4.1 Self-test the chain with a universal task

```python
# dispatch to yourself through the universal task (dedicated_session=1, an independent session)
agx_dispatch_task(
    caller_agent_id='<correct-id>',
    agent_id='<correct-id>',
    task_id='task_xxx',           # universal task id
    instruction='''请完成 4 步自测并回写：
1. agx_get_global_config 确认身份/角色/项目
2. agx_cache_stats 确认缓存健康
3. agx_get_execution 读取本次执行详情
4. agx_finish_task 回写 status=succeeded, result 含关键信息, summary 简述结论''',
    needs_reply=1,
    timeout_seconds=300,
)
```

### 4.2 The three hard indicators of success

1. **ws_client.log shows `runStarted: true, status: started`** (not `no longer exists`):

```bash
tail -50 $HOME/.agx/logs/ws_client.log | grep -iE "runStarted|no longer exists|session not found"
```

2. **The execution ends at `run_status=succeeded`** (confirmed with `agx_get_execution`: `ws_delivery_status=delivered`).

3. **No leftovers**: no running rows stuck in MySQL, no old-prefix session_id, and the watchdog stops dispatching new alerts.

```sql
-- acceptance (everything should be 0 / empty)
SELECT COUNT(*) FROM agentgeex.agx_task_execution WHERE agent_id='<correct-id>' AND run_status='running';
SELECT COUNT(*) FROM agentgeex.agx_task_execution WHERE agent_id='<correct-id>' AND session_id LIKE 'agent:<old-prefix>:%';
```

---

## 5. Bulk onboarding checklist

Tick these off for every agent onboarded later:

- [ ] Look up the real agent_id on the OpenClaw side (from openclaw.json, not from what you assume)
- [ ] Confirm that the real id's workspace / agent directory / runtime session prefix all agree
- [ ] AgentGeeX registered agent_id = the real OpenClaw id (`agx_upsert_agent`)
- [ ] Give the final role directly; **deploy_status = deployed** (delegating to agentfather leaves it at pending and needs a SQL fix); `scope` = project
- [ ] Confirm the shared trio of tasks is already provisioned by the system and **do not inject them again** (general / universal / watchdog_alert)
- [ ] **If the role is project_manager**: confirm that the two private plan tasks `plan_generate_and_deploy` + `plan_execute` are present (`agx_create_task`; they are not injected automatically)
- [ ] `default_session_id` = `agent:<correct-id>:main`
- [ ] workspace/agx.env carries the correct identity, and **`AGX_AGENT_ID` must be checked field by field against the real OpenClaw id** (guards against the `agentgeex`-style typo; do not just grep)
- [ ] **Business PM: AGENTS.md must "merge" the super_pm platform collaboration conventions** (session start / message handling / dispatch iron rules / no polling / delegation table / red lines / hierarchical plans), keeping all business content
- [ ] **AGENTS.md contains no "proactive scheduled inspection"** — that is the watchdog / heartbeat's job; only a passive inspection "when responding to a heartbeat event" may be written
- [ ] DB `agents_md` agrees with workspace/AGENTS.md (the full merged text, written with pymysql and compared by byte length)
- [ ] **universal chain self-test**: dispatch → `runStarted:true` → `succeeded` → `delivered`
- [ ] No running rows stuck, no old-prefix leftovers, no watchdog alerts
- [ ] A config / handoff notice is published recording the onboarding

---

## 5.5 🔴 Scenario B, advanced: aligning project-management capability (plan task injection and the PM knowledge chain)

> This section is the key reinforcement for **the ordinary agents awaiting bulk onboarding**, and was called out by the owner during review.
> The SOP above is written from the **developer / scheduler's point of view** — it only answers "how do I get an agent into AgentGeeX".
> This section answers: "**once an ordinary agent has joined, where do its project-management and planning capabilities come from, and what should they look like**".

### 5.5.1 Core insight: an ordinary agent cannot see what the developer sees

agentgeex is the developer of AgentGeeX and naturally understands project management and plan authoring better than anyone else — it has read the source, written the architecture documents and knows how the engine runs. **But the agents onboarded in bulk later have no such advantage.** All they have is:

- their own task definitions (visible through `agx_list_agent_tasks`)
- its own `AGENTS.md` — the code of conduct for that agent, created in its workspace when the agent is deployed
- `agx.env` (identity)
- the system, queried at runtime through `agx_get_*`

**So the "project management knowledge" has to be injected through task definitions. You cannot assume an agent will go read the source or the architecture documents.**

### 5.5.2 Mechanism check: are built-in tasks injected automatically? — **the shared trio yes, the two private plan tasks no** (corrected on the second pass)

**Creation and deployment are two separate phases.** A deployed agent (scenario B) does not need deployment again, only "created". But **"created" ≠ "automatically owns every task"** — a belief that was once mistaken and has now been corrected by evidence:

| task_labs | Description | Injected automatically |
|-----------|------|------------|
| `general` | General messages (main session) | ✅ On creation |
| `universal` | Standalone-session catch-all task | ✅ On creation |
| `watchdog_alert` | Watchdog alert handling | ✅ On creation |
| `plan_generate_and_deploy` | Generate and deploy plan JSON | ⚠️ **No**, must be added manually |
| `plan_execute` | Activate and manage a plan's lifecycle | ⚠️ **No**, must be added manually |

> 🔴 **Evidence-based lesson (second-pass correction, 2026-08-20):** both project_managers created that day — `medicine_gpt_doctor` and `agentgeex_agent` — **only had the shared trio (general / universal / watchdog_alert) assembled automatically**; `plan_generate_and_deploy` / `plan_execute` were **not injected**, they were added by hand with `agx_create_task`.
> So during bulk onboarding, if the target agent is a project_manager, **you must explicitly confirm and add these two private plan tasks** — never assume "created means it has them".

> The earlier erroneous claim (that super_pm having six tasks including the two plan ones proved they are automatic) has been overturned — super_pm is an early hand-assembled special case and does not represent how ordinary agents are created.

### 5.5.3 The project-management knowledge chain of an ordinary agent (completely different from the developer's)

```
Ordinary agent:
  agx_get_global_config obtains work_dir
    → (following the task x_desc) read {work_dir}/docs/internal/project-management.md
    → generate the plan JSON → agx_plan_deploy deploys it
    → activate it with the plan_execute task (never call agx_activate_plan by hand)

Developer (agentgeex):
  knows where the docs and mechanisms are, so can just call agx_plan_* (but that is an identity privilege, not the path an ordinary agent should take)
```

**Hard evidence**: Step 0 of `tasks/plan_generate_and_deploy.md` states — "read the file `{work_dir}/docs/internal/project-management.md` … understand the JSON plan schema and the deployment interface from the PM protocol".

So an ordinary agent's project-management knowledge is carried in by **the task's x_desc manual**. The x_desc already spells out which chapter of `docs/internal/project-management.md` to read, and how to generate, deploy and activate a plan.

### 5.5.4 Aligning with super_pm's capability (the reference standard)

An agent onboarded later **with the project_manager role** should be aligned to super_pm's capability template:

- Minimum: `general` + `universal` + `watchdog_alert` + `plan_generate_and_deploy` + `plan_execute` (five)
- `is_private=1` plan tasks are a system capability and do not appear in the public task list, but a PM model must confirm they are assembled

> 🔴 **owner_group host condition (new mechanism, 2026-08-20 — be careful to distinguish):**
>
> super_pm actually owns six tasks, but `book_project_orchestrate` is a **host-condition task** — it carries `owner_group='group_book_writer'` and is available only when the agent joins the book-authoring group. **It is not within the scope of clone inheritance and is not part of the standard project_manager kit.**
>
> The alignment baseline for an ordinary project_manager is therefore **the five above** (excluding book_project_orchestrate). If a project_manager needs book capability, it must obtain it by "joining the `group_book_writer` group" (see group injection in 5.5.7), not by cloning or copying the task by hand.
>
> To tell whether an agent owns a host-condition task: in the `agx_list_agent_tasks(agent_id=...)` result, a non-empty `owner_group` field means a host-condition task; `owner_group=null` means the task is the agent's own or shared.

### 5.5.5 🔴 Red lines (ordinary agents especially — copy them from super_pm's AGENTS.md)

- **Activating a plan goes through the `plan_execute` task; never call `agx_activate_plan` by hand** (in an ordinary context). The only exception is when you are already inside a `plan_execute` run (the message's task_labs is plan_execute) — then call `agx_activate_plan` directly, otherwise you create an infinite loop.
- **Executing your own task must go through dispatch; never read x_desc and execute in the current session** (that fills the context, causing compaction and memory loss).
- **Creating an Agent → dispatch to `agx_agentfather`; creating a project → dispatch to `agx_projectmother`.** Never call `agx_upsert_agent` / `agx_clone_agent` / `agx_create_project` yourself.
- **Proactive polling is strictly forbidden**: after dispatch, do not loop on `agx_get_execution` / `agx_running_tasks`; rely on `needs_reply` to come back naturally.
- Timeout degradation = escalate upward (agx_core / a human); never do it yourself.
- After receiving a task message, `agx_finish_task` must close it out, so the flow does not clog.

### 5.5.6 Corrections to the SOP above

- Step 2's "agx_upsert_agent" describes **the scheduler registering the agent into AgentGeeX**.
- If the goal is "the agent itself has planning capability", **you cannot rely on the developer-style direct `agx_plan_*` calls shown above**; instead use `agx_create_task` to explicitly add the two private plan tasks `plan_generate_and_deploy` + `plan_execute` (see the lesson in 5.5.2 — they are **not** injected automatically with creation), and let its AGENTS.md adopt super_pm's usage conventions (5.5.5).
- Pitfall 3 above notes that "agx_cache_rebuild needs L7" — an ordinary agent (project_manager, L0,1,2,3,5) likewise cannot call it and must delegate.

### 5.5.7 🔴 owner_group host condition: an entirely new dimension of task ownership (mechanism landed 2026-08-20)

> This section explains what changed once agent task ownership became an overlay of "host (agent_id) + condition (owner_group)" instead of "single-agent host". During bulk onboarding, be careful to distinguish the three kinds of task.

**The three kinds of task ownership:**

| Type | Characteristic | When it is available | Inherited by cloning |
|------|------|---------|------------|
| Shared task | `owner_group=null` and `is_private=0` | Automatically on creation | ✅ With the clone |
| Own private task | `owner_group=null` and `is_private=1` (e.g. the two plan tasks) | Must be added explicitly | ✅ With the clone |
| **Host-condition task** | `owner_group=<group_id>` non-empty | **Injected automatically only after joining the group** | ❌ **Not with the clone** |

**Two core rules (already landed in `agx_mcp/mcp_server.py`):**

1. **Skip on clone**: when `_clone_all_tasks_from_template` clones an agent it **skips every task with a non-empty `owner_group`**. So cloning the super_pm template does **not** give you `book_project_orchestrate`.
2. **Inject on group join**: `_inject_group_tasks_for_agent(group_id, agent_id, network_id)` fires on two paths —
   - `agx_add_agent_to_group`: adding an agent to a group injects that group's host-condition tasks
   - `agx_bind_group_to_project`: bulk-cloning a team into a project injects them into every clone

**Injection semantics**: take the template source from `agx_task_base` by `owner_group=group_id AND active_status=1` (one row per `task_labs`) and inject it for the target agent (keeping `owner_group`, deduplicated on `(agent_id, task_labs)`). After injection the task's `agent_id` is the target agent itself, visible through `own_sql`.

**Consequences (for bulk onboarding):**
- If an agent awaiting onboarding needs a group-specific capability (book authoring, say), **let it join that group** — do not copy the task by hand.
- A task created by hand with `agx_create_task` has an empty `owner_group` by default; if you need a host condition, set `owner_group` by SQL and rebuild the cache (L7).
- Host-condition task sources are normally assembled under the template host (e.g. super_pm's book_project_orchestrate hangs off `group_book_writer`) and serve only as injection sources.

> ⚠️ Cache note: **after changing a task by SQL (e.g. is_private / owner_group) the Redis cache does not sync automatically** (`agx_create_task` writes the snapshot as of that moment). You need `agx_cache_rebuild(entity_type='task')` (L7) to refresh, otherwise in-memory reads still see the old value. Always rebuild once after bulk task changes during bulk onboarding.

---

## 6. One-sentence summary

> **Before onboarding, make "the OpenClaw configured id" == "the AgentGeeX registered id" == "the default_session_id prefix" all identical. After onboarding, prove with one round of universal chain self-test that dispatch → WS → session → execution → finish really runs end to end — only then is the onboarding successful.** Do not trust "it looks right" in the DB; trust `runStarted: true` in the log and `succeeded` on the execution.
