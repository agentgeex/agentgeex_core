# Chapter 4 · Task

A task defines what an agent can do. This chapter covers what a task is and which parameters it has, the MCP operations, the built-in tasks, and how to write a task specification.

---

## 4.1 What a task is for

You can send an agent a message and ask it to do something, but a plain message has four limits:

| Limit | Description |
|---|---|
| No reuse | The full requirement must be restated every time |
| No audit | What was done, by whom, and whether it worked stays in the chat log |
| No concurrency | Several messages land in one session and their contexts interfere |
| No scheduling | Somebody has to send the message at the right moment |

A task turns behavior into an executable unit and removes these four limits: the same kind of work can be dispatched repeatedly, every run is recorded, runs can be parallel, and runs can be scheduled.

Tasks are not limited to business work. Creating an agent is itself a task (`agent_clone`), deploying an agent is a task (`agent_deploy`). Every internal action of the system runs as a task.

## 4.2 Definition of a task

A task is a specification plus the structured attributes extracted from it.

**The specification** (`x_desc`) is the body of the task: how the work is done, in which steps, what output format, what counts as failure, and what counts as done. The executing agent reads it.

**The attributes** are the structured projection of that specification. Constraints such as the task identifier, the owning agent, the session mode, the receipt mode and visibility were originally written in the specification text; as prose they cannot be queried, filtered or used for permission decisions. Extracting the decidable parts into columns is what lets the system dispatch the specification.

| Attribute | Description |
|---|---|
| `task_labs` | Task label, the stable identifier, matching the file name under `tasks/` |
| `task_name` | Task name, for display only |
| `agent_id` | Agent the task belongs to |
| `task_type` | Task type. `normal` runs once, `plan` orchestrates several steps |
| `dedicated_session` | Whether to use a dedicated session. 1 means each run takes its own session |
| `reply_mode` | How a result is reported after a run |
| `is_private` | Visibility. 1 means only the owning agent and administrators can see it |

Changing the specification and changing the attributes are not two jobs but two levels of one: the specification states what to do, the attributes let the system dispatch it.

A task defines how the work is done, not what came out this time. The same task runs many times, each with its own instruction, parameters, result and duration; those belong to the execution record.

```
task         this kind of work    the capability "analyze network status"
 └ execution one run              "the 11-24 09:52 run, 8 minutes, concluded ..."
```

The tasks an agent owns are its capability list. Query the list instead of asking the agent:

```
agx_list_agent_tasks(agent_id="agent_id")
```

## 4.3 Task parameters

### 4.3.1 description and x_desc

A task has two pieces of prose with different readers:

| Comparison | `description` | `x_desc` |
|---|---|---|
| Reader | Whoever decides whether to dispatch (PM, other agents, the UI) | The agent that executes the task |
| Content | One line saying what capability this is | The full execution instructions |
| Length | One line | Unbounded (`longtext`) |

A wrong `description` is more damaging than a wrong `x_desc`. An agent reading the task catalog decides what the task is for from `description`; it does not cross-check every `x_desc`.

`agent_create` is an example. The task was retired (agent creation goes through clone), but its `description` kept saying "register a new agent" for a long time, so agents reading the catalog called `agx_upsert_agent` under the old semantics, wrote rows directly, and deployed zero-byte shells.

### 4.3.2 The five parameters

| Parameter | Values | Description |
|---|---|---|
| `task_type` | `normal` / `plan` | Which timeout setting applies |
| `reply_mode` | `result` / `confirm` / `silent` | Whether to return a result and whether to track the receipt |
| `dedicated_session` | `0` / `1` | Whether the run lands in the main session or a dedicated one |
| `is_private` | `0` / `1` | Whether other agents can see the task |
| `payload` | JSON | Fixed input carried by every run |

#### task_type

`normal` is an ordinary task, `plan` is a planning task.

The difference is the timeout: ordinary tasks read `task_timeout`, planning tasks read `plan_timeout`. The two settings are independent and both default to 3600 seconds. Planning tasks usually run far longer than ordinary ones, so raise `plan_timeout` alone without affecting other tasks.

An explicit `timeout_seconds` at dispatch time wins; the configuration is read only when it is omitted.

#### reply_mode

| Value | Meaning |
|---|---|
| `result` | Return a result when the run finishes |
| `confirm` | Require confirmation before running |
| `silent` | Run silently, return nothing |

`reply_mode` also decides whether the receipt is tracked. At dispatch time `result` and `confirm` set `needs_reply=1`, `silent` sets 0. With `silent` there is not only no result; the receipt state is not recorded either, so the sender cannot tell whether the message was read.

#### dedicated_session

| Value | Where the run lands | Fits |
|---|---|---|
| `0` | The agent's main session | Simple message handling, notification responses |
| `1` | A dedicated session `agent:{agent_id}:{task_labs}` | Concurrent tasks, scheduled tasks, tasks needing context isolation |

The main session is shared context. Several tasks landing in it interfere with each other and queue up serially, and as the context grows compaction kicks in and part of the earlier memory is lost.

When the same task may run several times at once, or a single run is long, use `dedicated_session=1`.

#### is_private

`is_private` controls query visibility, not execution reachability.

When set to 1 the task does not appear in other agents' catalogs. That does not stop another agent from dispatching an execution against it; execution permission has to be checked inside the specification.

`universal` is the exception. It is a private channel for each agent and the code adds a further restriction: an outside agent cannot dispatch to another agent's `universal`, regardless of the task's `is_private` value.

#### payload

`payload` stores input that every run of the task needs, such as an analysis scope or a default threshold.

Per-run parameters do not go into `payload`; they go into `exec_instruction`. Anything written into `payload` is carried again by the next run.

<details>
<summary>Column definitions of agx_task_base</summary>

| Column | Type | Meaning |
|---|---|---|
| `task_id` | varchar(64) | Logical task ID (generated at initialization, may change) |
| `task_labs` | varchar(128) | Task label, matching the file name under `tasks/`; the stable identifier |
| `task_name` | varchar(128) | Human-readable name |
| `agent_id` | varchar(64) | Owning agent |
| `parent_task_id` | varchar(64) | Parent task, self-referencing |
| `owner_group` | varchar(64) | When non-empty, the task exists only if the host agent belongs to that group; injected by group binding, not inherited by cloning |
| `description` | text | Short description |
| `x_desc` | longtext | Full specification |
| `payload` | json | Fixed input parameters |
| `priority` | tinyint | Priority, default 1 |
| `reply_mode` | varchar(20) | Default `result` |
| `task_type` | varchar(32) | Default `normal` |
| `dedicated_session` | tinyint(1) | Default 0 |
| `is_private` | tinyint(1) | Default 0 |
| `session_done_wake` | tinyint(1) | Wake the watchdog as soon as the session ends: 1 yes (execution tasks), 0 no (orchestration tasks) |
| `active_status` | tinyint(1) | 0 disabled, 1 enabled |
| `created_at` / `update_dt` | timestamp | Creation / last update |

`task_labs` is the stable identifier and the right basis for queries and configuration; `task_name` is for display only.

</details>

## 4.4 Task operations

Every task operation goes through MCP methods. The caller may be a person, a click in the UI, or an agent that is currently executing; all it supplies is parameters, and MCP handles validation, persistence and cache synchronization. No message payload has to be built by hand, no SQL has to be run, and no configuration file has to be prepared.

### 4.4.1 Methods

| Method | Purpose |
|---|---|
| `agx_create_task` | Create a task for a given agent |
| `agx_list_agent_tasks` | List all tasks of an agent, i.e. its capability list |
| `agx_get_task` | Read task details by `task_id`, including an execution overview |
| `agx_get_task_by_name` | Resolve a `task_id` from a `task_name` or `task_labs` |
| `agx_update_task` | Update task attributes and specification by `task_id` |
| `agx_update_task_by_labs` | Update the same task across several agents by `task_labs` |
| `agx_archive_task` | Archive (soft delete), reversible |
| `agx_delete_task` | Delete physically, irreversible |

Dispatch and execution methods belong to chapter 5: `agx_dispatch_task`, `agx_schedule_task`, `agx_get_execution` and others.

### 4.4.2 Creating a task

Use `agx_create_task`:

```python
agx_create_task(
    caller_agent_id="agentgeex_agent",     # caller, used for permission checks
    agent_id="target_agent_id",            # which agent owns this task
    task_labs="network_analysis",          # label; keep it equal to the file name under tasks/
    task_name="Network Analysis",          # display name
    description="Summarize network status per project and report anomalies above the threshold",   # short description
    x_desc="<full specification>",          # what the executor reads
    task_type="normal",
    reply_mode="result",
    dedicated_session=1,
    is_private=1,
)
```

Parameter notes:

| Parameter | Description |
|---|---|
| `task_labs` | Stable identifier. Later queries, quick commands and plans all reference it; keep it equal to the file name under `tasks/` |
| `task_name` | Display name, not used in logic |
| `description` | Short description; other agents decide whether to dispatch from it |
| `x_desc` | Full specification with steps, output format and failure conditions (see 4.7) |
| `is_private` | Affects query visibility only, not execution permission (see 4.3.2) |

The task is effective immediately; no reinstall or restart is needed.

Within one agent `task_labs` must be unique. A duplicate is rejected with a message such as "task already exists for agent ... use agx_update_task instead".

### 4.4.3 Querying tasks

| Method | When to use |
|---|---|
| `agx_list_agent_tasks(agent_id=...)` | See which capabilities an agent has |
| `agx_get_task_by_name(caller_agent_id=..., agent_id=..., task_name=...)` | You know the name and need the `task_id` in order to dispatch |
| `agx_get_task(task_id=...)` | You know the `task_id` and want the full definition with an execution overview |

`agx_get_task_by_name` matches `task_name` exactly first and falls back to `task_labs`, so passing the label also works; the result contains the `task_id`.

After creating a task, confirm with `agx_list_agent_tasks` that it appears under the target agent.

### 4.4.4 Modifying a task

| Situation | Method | Notes |
|---|---|---|
| Change one task of one agent | `agx_update_task(task_id=..., updates={...})` | Exact update by `task_id`; can change `x_desc`, `payload`, `is_private`, `priority`, `active_status` |
| Change the same `task_labs` under many agents | `agx_update_task_by_labs(task_labs=..., x_desc=...)` | Bulk update of `x_desc`, optionally `is_private` as well |

`agx_update_task_by_labs` has a scope parameter:

| `scope` | Range | Who may use it |
|---|---|---|
| `own_project` (default) | Tasks of the same name under agents of your own project group | Any agent |
| `global` | Tasks of the same name across the network | host_manager / workshop_manager / agent_manager |

Before a bulk update, every target is checked against the scope; if any task falls outside it the whole call is rejected.

`run_status` cannot be changed through either method. It belongs to the execution record, not the task definition (chapter 5).

### 4.4.5 Archiving and deleting

| Method | Effect | Reversible | Permission |
|---|---|---|---|
| `agx_archive_task` | Sets `active_status=0`, removes it from active listings, keeps the row and its history | Yes | Owning agent / project manager / global roles |
| `agx_delete_task` | Deletes the row | No | Same |

If the task already has executions referencing it (`agx_task_execution.task_id`), `agx_delete_task` is rejected; use `agx_archive_task` instead so the historical execution chain stays intact.

### 4.4.6 Publishing to the template

The methods above change only the task that currently sits under one agent in the database. Making later installs and clones carry the same task is template publishing, and it needs three places to agree:

| Place | Role | If missing |
|---|---|---|
| `tasks/<task_labs>.md` | The specification text, the source of truth for the template | Cloning and deployment have no specification to copy |
| `agx_struct.json` | The publishing manifest the installer reads | A new install has no such task |
| The `agx_task_base` row | Runtime data | The current instance keeps working, but later edits do not take effect |

Missing one of the three does not raise an error; it only means the change did not take effect. The specification is updated and the agent still runs the old content.

There are further blocks inside each place, and changing one block does not finish a task. Besides the attribute columns, the database also holds two pieces of prose, `description` and `x_desc`; `tasks/*.md` holds front matter and an instruction example besides the body.

When done, compare all three:

```
agx_task_base row  ←→  agx_struct.json  ←→  tasks/<task_labs>.md
```

Template publishing is repository maintenance. To add one task to one agent day to day, use `agx_create_task` from 4.4.2; none of the three places above need touching.

<details>
<summary>Actual counts of the three places in this repository</summary>

| Place | Count |
|---|---|
| `tasks/*.md` | 29 files |
| The `tasks` array in `agx_struct.json` | 28 entries |
| `agx_task_base` (active) | 41 distinct `task_labs`, 514 rows |

The three numbers need not match: `tasks/*.md` includes retired tasks kept for the record (such as `agent_create`); the database includes project-specific tasks created at runtime, which never enter the struct; and one `task_labs` is stored once per agent that owns it.

</details>

## 4.5 Built-in tasks

After installation each agent already has a set of tasks; none need to be created by hand. They fall into three tiers by who owns them.

### 4.5.1 The three every agent has

| `task_labs` | Purpose |
|---|---|
| `general` | Handles general system messages and notifications sent to the agent, in the main session |
| `universal` | General-purpose task in a dedicated session; the content is decided by the dispatch instruction |
| `watchdog_alert` | Handles timeout and anomaly alerts from the watchdog: decide whether the task is really lost and act |

Measured across 104 agents: 104 rows each for these three.

### 4.5.2 Assembled by role

| `task_labs` | Owner |
|---|---|
| `plan_execute` | PM: plan execution and lifecycle |
| `plan_generate_and_deploy` | PM: generate and deploy a plan |
| `log_organize` | PM: aggregate project logs |
| `agent_clone` / `agent_deploy` | Agent manager / host manager |
| `book_*` (9 tasks) | Agents dedicated to the book pipeline |

### 4.5.3 Project-specific

Created as a project needs them, for example `project_create`, `network_analysis`, `code_change`. These usually have `is_private=1`.

### 4.5.4 general versus universal

Both handle arbitrary content; they differ in where the run lands.

| Comparison | `general` | `universal` |
|---|---|---|
| Session | Main session, shared context | Dedicated session, isolated context |
| `dedicated_session` | 0 | 1 |
| Concurrency | No; runs in the shared session collide | Yes; each run takes its own session |
| Fits | Simple messages, notification responses | Concurrent tasks, scheduled tasks, tasks needing isolation |

Use `universal` for concurrent runs, long runs, or anything that must not disturb the main session; use `general` for handling a single message on the spot.

## 4.6 Task assembly

Task counts differ between agents.

<details>
<summary>Measured: task lists by agent type</summary>

| Agent type | Count | Tasks |
|---|---|---|
| Ordinary PM (`super_pm`, `must_pm`) | 7 | `general` `universal` `watchdog_alert` `log_organize` `plan_execute` `plan_generate_and_deploy` `book_project_orchestrate` |
| PM with a dedicated task | 8 | The seven above plus the dedicated one |
| Worker | Depends on the template | The three baseline tasks plus whatever the template carries |

</details>

Assembly works by filtering the struct by platform, adding role-specific tasks, and adding tasks injected by group binding.

An agent missing a task is therefore usually the result of role assembly, not a failed install. To confirm, query `agx_list_agent_tasks` and compare with the ownership table in 4.5.

## 4.7 The task specification

The agent reads `x_desc` at execution time; it does not read the intent in the author's head. A specification that is not complete leaves the capability unimplemented.

Three requirements.

**First, write criteria, not hopes.**

| Incomplete | Executable |
|---|---|
| Analyze the data carefully and give valuable conclusions | Group by `status` and count; report only items above 5%; include one actionable recommendation |

"Carefully" and "valuable" cannot be turned into actions, so the agent has to guess the standard.

**Second, state the output format.**

Say whether the output is a table, JSON or plain text. Specifying column names and order is more effective than asking for a "clear output".

**Third, state the failure conditions.**

Without failure criteria an agent tends to invent a result and return it, which is harder to notice than a returned failure.

An example of a failure criterion: if the target column is empty in the source table, return failed and name the missing column; do not fill in a guessed value.

## 4.8 When not to use

- **A one-off operation**: dispatch `universal` to yourself or use `agx_self_dispatch`. Task templates are for capabilities that recur and that several agents need.
- **Another agent already has a matching task**: check with `agx_list_agent_tasks` first. Create a new one only when nothing fits, and do not use `universal` as a substitute for a proper task template; it is designed as a temporary outlet for yourself.
- **Parameters for a single run**: put them in `exec_instruction`, not in `x_desc` or `payload`. `x_desc` describes the capability; `payload` holds fixed input.
- **Changing the model**: unrelated to tasks. Models bind to sessions, not to tasks or agents.
- **A task that heavily duplicates an existing one**: query first. The 41 existing `task_labs` already include several near-synonyms (`project_create`, `project_configure`, `project_audit`), and each duplicate raises the lookup cost for everyone afterwards.
