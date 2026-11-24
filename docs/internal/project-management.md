# AgentGeeX Project Task Management Specification

> **Scope:** the administrator Agent of every AgentGeeX project
> **How to adopt:** reference this file from the project administrator Agent's `AGENTS.md` as the core project-management guideline
> **Version:** v2.2 | 2026-06-23

---

## 1. Core concepts

### 1.1 Task Base vs Execution

| Level | Storage | Description |
|------|---------|------|
| **Task Base (task template)** | `agx_task_base.task_id` | Capability definition, "what can be done" — reusable; one Agent may register many tasks |
| **Task Execution (execution instance)** | `agx_task_execution.task_run_uuid` | Actual action, "what is being done" — independent per run, not reusable |

**In one sentence:** Task Base is the recipe, Execution is the cooking.

### 1.2 Parent and child tasks (parent_run_uuid)

A task can be split into several child tasks. The parent task does no work itself; it is only a "container" that groups all child tasks into one logical whole.

>  **The parent task only completes once all child tasks are complete.**

```
parent task (parent_run_uuid = null)
├── child task A (parent_run_uuid = parent)
├── child task B (parent_run_uuid = parent)
└── child task C (parent_run_uuid = parent)
```

### 1.3 Execution order (prev_task / next_task)

Tasks can declare a before/after dependency.

>  **With a predecessor task, startup must wait until the predecessor completes (succeeded).**

```
task A ──(next)──▶ task B ──(next)──▶ task C
         (prev)◀──          (prev)◀──
```

### 1.4 Scheduled execution (scheduled_at)

A task can specify a future execution time; the watchdog only activates it when that time arrives.

>  **When scheduled_at is in the future, the watchdog skips the task and activates it when the time comes.**

- Format: MySQL DATETIME, for example `2026-06-17 14:00:00`
- scheduled_at in the past → activate immediately (equivalent to no scheduling)
- scheduled_at empty → no scheduling constraint, activate immediately

### 1.4 Combined use

> 🔴 **Mutual exclusion rule: `prev_task` and `parent_run_uuid` cannot both be set on the same step.**
> - Child task (has `parent_run_uuid`) = a branch of the parent; the parent activates only after the children finish
> - Serial task (has `prev_task`) = an independent chain that waits for the previous one
> - A step is either someone's child or someone's successor, never both

The **indirect combination** of `parent_run_uuid` and `prev_task` (across different steps) is allowed:

```
parent task (parent_run_uuid=null, no prev_task)
├── child A (parent_run_uuid=parent, no prev_task) ──prev──▶ child B (parent_run_uuid=parent, prev_task=child A)
└── child C (parent_run_uuid=parent, no prev_task)
```

Here child A → child B is serial, but child A / child B / child C are all children of the parent; on the parent the watchdog only checks the children condition (all children not in running/pending), and does not check prev_task on the parent.

---

## 2. 🔴 Plan hierarchy principle

### 2.1 The core iron rule

> **Draw up every plan first, then activate them all at once. Planning and executing in parallel is strictly forbidden.**

A large goal should be decomposed into a **hierarchy plan tree**. Every plan (parent/child/grandchild) **must be fully deployed before anything may be activated**.

Why:
- **Auditable**: before activation you can see the complete execution tree and confirm every dependency is correct
- **Reversible**: if a problem is found, the whole tree can be cancelled before activation, leaving no half-finished plans
- **Drift-proof**: no ad-hoc plan generation during execution, which prevents plan bloat

### 2.2 Core concept: Plan Hub

**A Plan Hub is just an ordinary plan**; there is no special mechanism. The only difference is that its steps use orchestration tasks (`plan_generate_and_deploy` / `plan_execute`) instead of work tasks.

Steps of a Plan Hub still follow the `parent_run_uuid` / `prev_task` relations of §1 — because they are ordinary executions; it is only their task that activates another plan.

This is recursive: a sub-plan activated by one of a Plan Hub's steps can itself be a Plan Hub.

### 2.3 The two key Tasks

Every `super_pm` (and everything cloned from it) owns both of these tasks:

| Task | Responsibility | Phase |
|------|------|------|
| `plan_generate_and_deploy` | Generate a plan's JSON → `agx_plan_create` → `agx_plan_deploy` | **planning phase** |
| `plan_execute` | `agx_activate_plan(any plan ID)` → wait for that plan to finish → `agx_finish_task` | **execution phase** |

### 2.4 Plan hierarchy model

```
Plan Hub "plan_main" (main plan)
├── step_1: plan_generate_and_deploy → deploy plan_main itself
├── step_2: plan_generate_and_deploy → deploy plan_ch01 (sub-plan)
├── step_3: plan_generate_and_deploy → deploy plan_ch02 (sub-plan)
├── step_4: plan_generate_and_deploy → deploy plan_ch03 (sub-plan)
│
│   [ every plan is now deployed, audit passed ]
│
├── step_5: plan_execute → agx_activate_plan(plan_ch01)
│   └── once every step inside plan_ch01 completes, step_5 is marked succeeded
├── step_6: plan_execute → agx_activate_plan(plan_ch02)  [parallel with step_5]
├── step_7: plan_execute → agx_activate_plan(plan_ch03)  [parallel with step_5]
└── step_8: ordinary task, waits for step_5/6/7 to all finish (join)
```

>  **plan_ch01 may itself contain `plan_execute` steps that activate grandchild plans. Recursion has no depth limit.**

### 2.5 Full lifecycle

```
Phase one: planning (deploy only, activation forbidden)
────────────────────────────────────────
1. super_pm decomposes the large goal and identifies which parts are sub-plans
2. dispatch plan_generate_and_deploy for each sub-plan
   → the sub-PM generates the sub-plan JSON → agx_plan_create → agx_plan_deploy
3. every plan (parent + children + grandchildren...) is fully deployed
4. a human audits the complete plan tree
        ↓
Phase two: activate and execute
────────────────────────────────────────
5. agx_activate_plan(main plan)  ← the only entry point
6. the main plan's steps execute in topological order
   ├── on a plan_execute step → agx_activate_plan(the matching plan)
   ├── a plan_execute step inside a sub-plan → agx_activate_plan(grandchild plan)
   └── unfolding layer by layer
7. all sub-plans complete → the parent plan completes
        ↓
🔴 During execution, dispatching plan_generate_and_deploy is strictly forbidden
   To adjust → agx_plan_cancel + plan again
```

### 2.6 Decision criterion: when to use a sub-plan?

| Scenario | Single step | Sub-plan |
|------|---------|--------|
| Create one table | ✅ | ❌ over-engineering |
| Deploy one Agent | ✅ | ❌ over-engineering |
| One chapter of a book (split→write→review→merge) | ❌ | ✅ sub-plan |
| A complete analysis pipeline for a set of stocks | ❌ | ✅ sub-plan |

**In one sentence:** if a step itself contains multiple phases with multiple roles collaborating, it is a sub-plan.

### 2.7 Timeout budget

| Level | timeout_seconds | Description |
|------|----------------|------|
| plan_generate_and_deploy step | 1800-1800 | generate JSON + deploy, pure IO |
| plan_execute step (inside a parent plan) | 3600-7200 | 1.5-2× the sub-plan's total estimate |
| ordinary step inside a sub-plan | 1800 | per the usual rule |

---

## 3. The JSON plan system

### 3.1 Design rationale

After a PM agent receives a high-level instruction, it decomposes the goal into executable steps; each step is either an ordinary task or a `plan_generate_and_deploy` / `plan_execute` orchestration task. All plans are activated together after deployment, and all subsequent scheduling is handled automatically by the system.

> 🔴 **Depends on the §2 hierarchy principle:** if there are sub-plans, dispatch `plan_generate_and_deploy` first to generate and deploy every sub-plan. Only after all are deployed, run `agx_activate_plan` once.

**Division of labour between the three roles:**

| Role | Responsibility | Boundary |
|------|------|------|
| **PM agent** | decompose the goal → generate the JSON plan → deploy | does not take part in scheduling after deploy |
| **agx_plan table** | stores plan metadata, the raw JSON and status | unaware of execution detail |
| **agx_task_execution table** | stores one execution instance per step | linked to the plan through `plan_id` |

An Agent only executes the task in front of it; it does not need to know "what the next step is" or "which plan I am part of".

### 3.2 Schema definition

```json
{
  "plan_id": "plan_xxx",
  "project_id": "proj_xxx",
  "description": "human-readable plan description",
  "steps": [
    {
      "run_uuid": "run_xxx",
      "task_id": "task_xxx",
      "agent_id": "agt_xxx",
      "instruction": "execution instruction",
      "parent_run_uuid": "run_parent_xxx",
      "children": ["run_child_1", "run_child_2"],
      "timeout_seconds": 300,
      "scheduled_at": "2026-06-17 14:00:00"
    }
  ]
}
```

#### Top-level fields

| Field | Required | Description |
|------|------|------|
| `plan_id` |  | Plan unique ID, `plan_` prefix |
| `project_id` |  | Owning project |
| `description` |  | Human-readable plan description |
| `git_branch` |  | Git branch used by this plan (optional, e.g. `feat/agent-flow`) |
| `steps` |  | Step list, in topological order |

#### step fields

| Field | Required | Description |
|------|------|------|
| `run_uuid` |  | Pre-generated execution ID, `run_` prefix |
| `agent_id` |  | Executing Agent |
| `instruction` |  | Execution instruction |
| `task_id` |  | Associated task definition ID (omit to use the agent's default task) |
| `parent_run_uuid` |  | Parent execution ID (present ⇒ this is a child task) |
| `children` |  | List of child run_uuids |
| `timeout_seconds` |  | Timeout, default 1800 |
| `scheduled_at` |  | Scheduled time, MySQL DATETIME format (e.g. `2026-06-17 14:00:00`). Omitted or empty ⇒ execute immediately |

### 3.3 Topology inference rules

1. `children` non-empty → every child automatically inherits `parent_run_uuid`
2. `children` empty + no `parent_run_uuid` + not the first step → **error** (orphan task)
3. First step (steps[0]): `parent_run_uuid` must be empty

### 3.4 Complete example

```json
{
  "plan_id": "plan_trendyfam_create",
  "project_id": "proj_trendyfam",
  "description": "create the trendyfam project and deploy its Agent team",
  "steps": [
    {
      "run_uuid": "run_project_create",
      "task_id": "task_project_create",
      "agent_id": "agx_projectmother",
      "instruction": "create project trendyfam, short-video creation domain",
      "children": ["run_agent_create"],
      "timeout_seconds": 1800
    },
    {
      "run_uuid": "run_agent_create",
      "task_id": "task_agent_create",
      "agent_id": "agx_agentfather",
      "instruction": "create 7 Agents for trendyfam (admin + 6 workers)",
      "parent_run_uuid": "run_project_create",
      "children": ["run_deploy_admin", "run_deploy_workers"],
      "timeout_seconds": 1800
    },
    {
      "run_uuid": "run_deploy_admin",
      "task_id": "task_agent_deploy",
      "agent_id": "agx_host",
      "instruction": "deploy agent trendyfam_admin, including PM task inheritance",
      "parent_run_uuid": "run_agent_create",
      "timeout_seconds": 1800
    },
    {
      "run_uuid": "run_deploy_workers",
      "task_id": "task_agent_deploy",
      "agent_id": "agx_host",
      "instruction": "deploy the remaining 6 worker agents",
      "parent_run_uuid": "run_agent_create",
      "timeout_seconds": 600
    }
  ]
}
```

### 3.5 Guide to complex topologies

You do not need to understand how the system schedules internally — you only need to master six topology patterns so that the dependencies are self-consistent.

#### Pattern 1: serial pipeline

B after A, C after B.

```
A ──▶ B ──▶ C
```

How to write it: each step sets a single `children`.

```json
[
  {"run_uuid": "run_a", "children": ["run_b"]},
  {"run_uuid": "run_b", "children": ["run_c"]},
  {"run_uuid": "run_c"}
]
```

#### Pattern 2: fan-out in parallel

After A, B/C/D run at the same time.

```
        ┌── B
A ──────┼── C
        └── D
```

How to write it: A's `children` holds several entries.

```json
[
  {"run_uuid": "run_a", "children": ["run_b", "run_c", "run_d"]},
  {"run_uuid": "run_b"},
  {"run_uuid": "run_c"},
  {"run_uuid": "run_d"}
]
```

#### Pattern 3: fan-in join

Run E after B/C/D all complete.

```
B ──┐
C ──┼──▶ E
D ──┘
```

How to write it: wrap the parallel children in one container parent task; E needs no `children`, and the parent task is activated automatically once all children complete.

```json
[
  {"run_uuid": "run_group", "children": ["run_b", "run_c", "run_d"]},
  {"run_uuid": "run_b", "parent_run_uuid": "run_group"},
  {"run_uuid": "run_c", "parent_run_uuid": "run_group"},
  {"run_uuid": "run_d", "parent_run_uuid": "run_group"},
  {"run_uuid": "run_e", "parent_run_uuid": "run_group"}
]
```

#### Pattern 4: mixed topology

A → parallel (B→C, D→E) → join into F.

```
        ┌── B ──▶ C ──┐
A ──────┤              ├──▶ F
        └── D ──▶ E ──┘
```

How to write it: nest children and serial links.

```json
[
  {"run_uuid": "run_a", "children": ["run_b", "run_d"]},
  {"run_uuid": "run_b", "children": ["run_c"]},
  {"run_uuid": "run_c", "parent_run_uuid": "run_b"},
  {"run_uuid": "run_d", "children": ["run_e"]},
  {"run_uuid": "run_e", "parent_run_uuid": "run_d"},
  {"run_uuid": "run_f", "parent_run_uuid": "run_a"}
]
```

>  `run_f` is made a child of `run_a`, so it is triggered automatically once every grandchild task completes.

#### Pattern 5: scheduled serial

Each task runs at a different point in time, independent of the others.

```
14:15  A
14:17  B
14:19  C
14:21  D
14:23  E
14:25  F
```

How to write it: each step has only `scheduled_at`, no children/parent:

```json
[
  {"run_uuid": "run_a", "scheduled_at": "2026-06-17 14:15:00"},
  {"run_uuid": "run_b", "scheduled_at": "2026-06-17 14:17:00"},
  {"run_uuid": "run_c", "scheduled_at": "2026-06-17 14:19:00"},
  {"run_uuid": "run_d", "scheduled_at": "2026-06-17 14:21:00"},
  {"run_uuid": "run_e", "scheduled_at": "2026-06-17 14:23:00"},
  {"run_uuid": "run_f", "scheduled_at": "2026-06-17 14:25:00"}
]
```

>  scheduled_at combines freely with the other patterns (for example, a scheduled trigger of a parent task that has children).

---

#### Pattern 6: hierarchical plans (a plan inside a plan)  🔴 important

> **The core principle is §2: draw up every plan first, then activate them all at once.**

When a goal is too large for a single-level plan, use a hierarchical plan. The parent plan's steps fall into two batches:
- **planning phase**: `plan_generate_and_deploy` steps that generate and deploy the sub-plans
- **execution phase**: `plan_execute` steps that activate the sub-plans

##### Full flow

```
Planning phase (all plans deployed together)
──────────────────────────────────
parent Plan Hub
├── step_1: plan_generate_and_deploy → plan_create + plan_deploy(the parent plan itself)
├── step_2: plan_generate_and_deploy → plan_create + plan_deploy(sub-plan Plan_B)
├── step_3: plan_generate_and_deploy → plan_create + plan_deploy(sub-plan Plan_C)
└── step_4: plan_generate_and_deploy → plan_create + plan_deploy(grandchild plan Plan_D)
        ↓
    all deploys complete ✔
        ↓
Execution phase
──────────────────────────────────
agx_activate_plan(parent plan)
├── step_5: plan_execute → agx_activate_plan(Plan_B)
│   └── a plan_execute step inside Plan_B → agx_activate_plan(Plan_D)
├── step_6: plan_execute → agx_activate_plan(Plan_C)  [parallel with step_5]
└── step_7: ordinary task, waits for step_5/6 to all finish
```

##### 🔴 Key rules

> **`parent_run_uuid` / `prev_task` between steps work as usual; there is no special handling. A Plan Hub is just an ordinary plan.**

- between `plan_generate_and_deploy` steps: follow the topological order and generate the sub-plans
- `plan_generate_and_deploy` → `plan_execute`: serialise with `prev_task` to guarantee the sub-plan is deployed before it is activated
- between `plan_execute` steps: parallel or serial, to control the execution order of the sub-plans
- recursion: a sub-plan may itself contain `plan_generate_and_deploy` + `plan_execute` to activate grandchild plans

##### Planning flow

```
1. super_pm decomposes the high-level goal
   ├── which steps are "atomic operations" (ordinary tasks)?
   └── which steps are "composite operations" (need a sub-plan)?
        ↓
2. for each sub-plan: dispatch plan_generate_and_deploy first
   ├── agent_id = super_pm (or a clone of it)
   ├── instruction = the sub-plan's goal description (NOT a step list!)
   │   e.g. "generate and deploy the writing plan for chapter 1, 'LLM foundations'.
   │         Source file: manuscript/ch01.md.
   │         The writing flow should cover splitting, writing and review."
   └── timeout_seconds = 300-600 (generate JSON + deploy)
        ↓
3. all plans are fully deployed
        ↓
4. dispatch the plan_execute steps to activate the sub-plans
   ├── task_id = the plan_execute task_id
   ├── instruction = "activate plan plan_ch01 and wait for it to fully complete"
   └── timeout_seconds = estimated total of the sub-plan × 1.5-2
```

##### 🔴 Interface contract: how to write the instruction

The `instruction` of a `plan_generate_and_deploy` step in the parent plan is the sub-PM's only input.

| What the instruction should contain | Required | Example |
|-------------------|------|------|
| **the sub-plan's goal** | ✅ | "generate and deploy the writing plan for chapter 1" |
| **path to the context data** | as needed | "source file: manuscript/ch01.md" |
| **constraints** | as needed | "must finish within 4 hours" |
| **expected output** | as needed | "merge the final output into manuscript/ch01_final.md" |

> 🔴 **Never** list the sub-plan's steps in the instruction! That is the sub-PM's job.

**Wrong example:** `"chapter 1: 1. split 2. write the intro 3. write section 1.1 4. review 5. merge"`

**Right example:** `"generate and deploy the writing plan for chapter 1, 'LLM foundations'. Source file: manuscript/ch01.md. The writing flow covers splitting, writing and review."`

##### Topology

```
Plan_Main (parent Plan Hub)
├── step_1: plan_generate_and_deploy  → Plan_Main itself
├── step_2: plan_generate_and_deploy  → sub-plan Plan_A
├── step_3: plan_generate_and_deploy  → sub-plan Plan_B
├── step_4: plan_generate_and_deploy  → grandchild plan Plan_A1
│
│   [ all deploys complete ]
│
├── step_5: plan_execute → activate Plan_A
│   └── Plan_A.step_X: plan_execute → activate Plan_A1
├── step_6: plan_execute → activate Plan_B  [parallel with step_5]
└── step_7: ordinary task, prev_task=step_5, prev_task=step_6
```

##### Complete JSON example

```json
{
  "plan_id": "plan_parent_book",
  "description": "book authoring main plan — 3 chapters authored in parallel",
  "steps": [
    {
      "run_uuid": "run_deploy_self",
      "task_id": "task_plan_generate_and_deploy",
      "agent_id": "agt_super_pm",
      "instruction": "deploy the main plan plan_parent_book itself",
      "children": ["run_deploy_ch01", "run_deploy_ch02", "run_deploy_ch03"],
      "timeout_seconds": 1800
    },
    {
      "run_uuid": "run_deploy_ch01",
      "task_id": "task_plan_generate_and_deploy",
      "agent_id": "agt_super_pm",
      "instruction": "generate and deploy the writing plan for chapter 1, 'LLM foundations'. Source file: manuscript/ch01.md. The writing flow covers splitting, writing and review.",
      "parent_run_uuid": "run_deploy_self",
      "timeout_seconds": 1800
    },
    {
      "run_uuid": "run_deploy_ch02",
      "task_id": "task_plan_generate_and_deploy",
      "agent_id": "agt_super_pm",
      "instruction": "generate and deploy the writing plan for chapter 2, 'Transformer architecture'. Source file: manuscript/ch02.md.",
      "parent_run_uuid": "run_deploy_self",
      "timeout_seconds": 1800
    },
    {
      "run_uuid": "run_deploy_ch03",
      "task_id": "task_plan_generate_and_deploy",
      "agent_id": "agt_super_pm",
      "instruction": "generate and deploy the writing plan for chapter 3, 'Training and fine-tuning'. Source file: manuscript/ch03.md.",
      "parent_run_uuid": "run_deploy_self",
      "timeout_seconds": 1800
    },
    {
      "run_uuid": "run_activate_ch01",
      "task_id": "task_plan_execute",
      "agent_id": "agt_super_pm",
      "instruction": "activate plan plan_ch01 and wait for chapter 1 to fully complete",
      "prev_task": "run_deploy_ch01",
      "timeout_seconds": 7200
    },
    {
      "run_uuid": "run_activate_ch02",
      "task_id": "task_plan_execute",
      "agent_id": "agt_super_pm",
      "instruction": "activate plan plan_ch02 and wait for chapter 2 to fully complete",
      "prev_task": "run_deploy_ch02",
      "timeout_seconds": 7200
    },
    {
      "run_uuid": "run_activate_ch03",
      "task_id": "task_plan_execute",
      "agent_id": "agt_super_pm",
      "instruction": "activate plan plan_ch03 and wait for chapter 3 to fully complete",
      "prev_task": "run_deploy_ch03",
      "timeout_seconds": 7200
    }
  ]
}
```

>  `run_activate_ch01/ch02/ch03` have no `prev_task` between them, so they run in parallel by default. `prev_task: run_deploy_ch01` guarantees the sub-plan is deployed before it is activated.

##### Log tracing

Notices produced by a hierarchical plan are linked on two levels, `plan_id` and `run_uuid`:

```
plan_parent_book (parent plan)
├── [run_deploy_ch01 → plan_ch01 (sub-plan)]
│   ├── "chapter split into 5 sections"     (child plan_generate_and_deploy)
│   ├── "section 1 writing complete"        (writer)
│   └── "chapter 1 fully complete"          (plan_execute finish)
├── [run_deploy_ch02 → plan_ch02 (sub-plan)]
│   └── ...
└── "main plan complete, all 3 chapters succeeded"    (parent PM received every receipt)
```

---

#### Design principles

1. **Draw the tree, then fill the table** — use indentation to express hierarchy; confirm every leaf has a parent
2. **Timeout reference values** — `plan_generate_and_deploy` ordinary task 1800s, `plan_execute` (waiting on a sub-plan) 3600-7200s
3. **Separate the two phases** — the planning phase (deploy) and the execution phase (activate) are strictly separated; activate only after everything is deployed
4. **Pre-generate every run_uuid** — call `agx_generate_uuid` for all IDs first, then fill in the children references
5. **The parent PM does not manage sub-plan detail** — the instruction describes the goal only, never the steps. The sub-PM decides independently.

### 3.6 The `agx_plan` table

Every JSON plan is written into the `agx_plan` table on submission, as the plan-level metadata container.

| Field | Type | Description |
|------|------|------|
| `plan_id` | VARCHAR(64) | Plan unique ID, `plan_` prefix (PK) |
| `project_id` | VARCHAR(64) | Owning project |
| `created_by` | VARCHAR(64) | Creating agent_id |
| `description` | TEXT | Human-readable description |
| `git_branch` | VARCHAR(128) | Git branch used by this plan (optional) |
| `plan_status` | VARCHAR(16) | pending → running → completed / failed / cancelled |
| `steps_json` | JSON | The raw JSON plan (kept complete, traceable) |
| `created_at` | DATETIME | Creation time |
| `completed_at` | DATETIME | Completion time |

`agx_task_execution` links to the plan through the optional field `plan_id`. **Not every task has to belong to a plan** — a directly dispatched task has `plan_id` NULL.

---

## 5. Plan-management MCP interface

### 5.1 `agx_plan_create` — create a plan (without deploying)

```
agx_plan_create(
    caller_agent_id: str,
    plan_json: str
) -> str   # {plan_id, steps_count, entry_tasks[], plan_status: "draft"}
```

Internal flow:
1. parse the JSON and validate the schema
2. expand `children` → fill in each child step's `parent_run_uuid`
3. validate topology completeness (mutual exclusion, orphan tasks, etc.)
4. INSERT INTO `agx_plan` (raw JSON + metadata, status=draft)
5. sync the Redis cache
6. return a summary

> **Writes agx_plan only, never agx_task_execution.** After creation the plan is in draft; deploy it with `agx_plan_deploy` once a human has reviewed it.
> Review-free chaining is also supported: `agx_plan_create` → `agx_plan_deploy(plan_id=...)`

### 5.2 `agx_plan_deploy` — deploy a plan's executions

```
agx_plan_deploy(
    caller_agent_id: str,
    plan_id: str       # chained mode: an already created plan_id
    # or
    plan_json: str     # direct mode (legacy call): a JSON plan; create + deploy internally
) -> str   # {ok: true, plan_id, steps_deployed, run_uuids[]}
```

**Two ways to call it:**

| Mode | Parameter | Description |
|------|------|------|
| **chained** | `plan_id="plan_xxx"` | call `agx_plan_create` for a plan_id first, then deploy. Recommended. |
| **direct** | `plan_json=...` | legacy call; runs `agx_plan_create` + deploy internally. |

Internal flow (chained mode):
1. read the steps from `agx_plan.steps_json`
2. validate the steps' topology (fill in parent_run_uuid, check mutual exclusion)
3. batch INSERT INTO `agx_task_execution` (with `plan_id`, status=draft)
4. sync the Redis cache
5. return a summary

> **One deploy = one full insert; incremental deploy is not supported.** A plan that has already been deployed cannot be deployed again. To redeploy → `agx_plan_cancel` and start over.

### 5.3 `agx_plan_query` — query a single plan

```
agx_plan_query(
    caller_agent_id: str,
    plan_id: str
) -> str   # plan metadata + the status list of every step
```

Returns the plan detail plus the current status of each step (pending/running/succeeded/failed).

### 5.4 `agx_plan_list` — project plan overview

```
agx_plan_list(
    caller_agent_id: str,
    project_id: str
) -> str   # [{plan_id, description, plan_status, steps_count, created_at}, ...]
```

Lists every plan under the project together with its completion state.

### 5.5 `agx_plan_cancel` — cancel a plan

```
agx_plan_cancel(
    caller_agent_id: str,
    plan_id: str
) -> str   # cancelled: N executions
```

Marks every `pending` execution under the plan as `cancelled`; `running` tasks are unaffected (they end naturally).

---

## 6. Plan creation and deployment flow

A PM agent does its work through two core tasks: `plan_generate_and_deploy` / `plan_execute`.

### 6.1 How it is triggered

A human or a higher-level Agent sends the PM agent a high-level instruction, such as:
> "create the trendyfam project: a 7-Agent short-video creation team"

### 6.2 Execution flow

> 🔴 **Follow the §2 hierarchy principle:** if the goal needs sub-plans, dispatch `plan_generate_and_deploy` first to generate and deploy every sub-plan, then dispatch `plan_execute` to activate.

1. **Generate plan_id** — call `agx_generate_uuid(prefix="plan")`
2. **Confirm the Git branch** (when the project resource directory is a Git repo) — ask the human whether a new branch is needed. If so, run `git checkout -b {branch}`. Put the branch name in the plan's `git_branch` field.
3. **Reason about the steps** — decompose the goal into executable steps. Identify which are atomic operations (ordinary tasks) and which are composite operations (need a sub-plan).
4. **Query resources** — look up the available agents and tasks in the DB:
   - `agx_list_by_network(network_id, "agent")` — list of available Agents
   - `agx_list_agent_tasks(agent_id=<target agent_id>)` — fix the Agent first, then query its task catalogue (recommended)
   - `agx_list_by_network(network_id, "task")` — full task list (not recommended; may be truncated)
5. **Pre-generate run_uuid** — call `agx_generate_uuid(prefix="run")` for each step
6. **Assemble the JSON plan** — use the `json-ops` skill to build the plan JSON file in batches, then pass it to `agx_plan_deploy`. Refer to §3.5 pattern 6 to decide each step's task_id and topology.
7. **Deploy** — chain `agx_plan_create(plan_json)` → `agx_plan_deploy(plan_id=...)`
8. **Report back** — return "plan_id stored, N steps in total, entry: run_xxx"

### 6.3 The PM agent's boundary

| Does | Does not do |
|--------|---------|
| Decompose the goal into steps (including identifying composite operations) |  No manual scheduling (no agx_schedule_task / agx_activate_task) |
| Generate the JSON plan and deploy it |  No progress tracking (the watchdog does that) |
| Complete the dependencies (children / parent / prev_task) |  Does not decide "who gets activated next" (the watchdog does) |
| Write the instruction for a sub-plan |  Does not decompose a sub-plan's steps on the sub-PM's behalf |
| Run `agx_activate_plan` once after everything is deployed |  Never activates any plan while a deploy is still in flight |

---

## 7. Complete workflow example

Scenario: create a new project and deploy an Agent team.

```
Human initiates: "create the trendyfam video project"
  │
  ▼
PM agent receives the instruction
  ├─ 1. reason: 3 steps are needed (create project → create Agents → deploy Agents)
  ├─ 2. query the DB: available agents = projectmother/agentfather/host, tasks = project_create/agent_create/agent_deploy
  ├─ 3. pre-generate run_uuid: run_pc / run_ac / run_ad1 / run_ad2 ...
  ├─ 4. assemble the JSON plan (see the §3.4 example)
  └─ 5. agx_plan_create(plan_json) → agx_plan_deploy(plan_id=...)
       → returns: "plan_trendyfam_create stored, 3 levels / 4 steps, entry: run_project_create"
  │
  ▼
The system schedules the tasks by itself; each Agent receives its message in topological order and executes,
and the plan ends once everything is complete.
```

---

## 8. Appendix

### 8.1 Task specification writing standard

Every `agx_task_base.x_desc` should ideally contain the following structure:

```markdown
## Purpose
One sentence describing what this task is meant to accomplish.

## Preconditions
- What data is needed?
- Which Agent does it depend on?
- Which predecessor task does it depend on?

## Input
| Parameter | Type | Required | Description |
|------|------|------|------|
| param1 | string | yes | ... |

## Steps
1. Step 1
2. Step 2

## Output
- the status rule for calling `agx_finish_task`
- the format of the result field
- whether a notice must be written
```

### 8.2 Notice and logging standard

Key points during execution should be recorded as notices:

| Moment | notice_type | Description |
|------|------------|------|
| Plan generation complete | `decision` | record the decomposition scheme |
| Task started | `log` | "started: run_xxx" |
| Task completed | `log` | "run_xxx succeeded, output: ..." |
| Task failed | `error` | full error information + root-cause analysis |
| Whole chain complete | `handoff` | execution summary + next-step suggestions |
| Interrupted / handed over | `handoff` | "waiting for human intervention, reason: ..." |
