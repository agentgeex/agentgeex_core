# Chapter 8 · Plan

A plan arranges several executions by dependency and lets the watchdog advance them. This chapter covers the three topologies, the three actions on a plan, activation modes, completion notification, and hierarchical plans.

---

## 8.1 What a plan is for

When a piece of work exceeds three or five steps, the steps depend on each other, and it runs for a long time, dispatching them one by one becomes unmanageable. A plan defines the steps as one whole, and the system releases them in dependency order.

| Purpose | Description |
|---|---|
| Dependency management | A step is released only after its predecessor finishes |
| Parallelism | Independent steps start together |
| Convergence | Continue only after all branches finish |
| Visible progress | Every step's state is queryable |
| Completion notice | The sender is notified when everything finishes |

## 8.2 What a plan consists of

| Attribute | Description |
|---|---|
| `plan_id` | Unique plan ID |
| `parent_plan_id` | Parent plan ID, used by hierarchical plans |
| `project_id` | Owning project |
| `created_by` | Creator |
| `host_id` | Host the plan belongs to; the watchdog isolates by it |
| `task_run_uuid` | Execution that activated the plan |
| `plan_status` | Plan state |
| `steps_json` | The original plan JSON |
| `activate_mode` | Activation mode: `watchdog` / `chain` |
| `start_task_run_uuid` | Entry execution for chain activation |
| `notified_at` | When completion was notified; used for de-duplication |

<details>
<summary>Full column definitions of agx_plan</summary>

| Column | Type | Description |
|---|---|---|
| `plan_id` | varchar(64) | Plan ID |
| `parent_plan_id` | varchar(64) | Parent plan |
| `project_id` | varchar(64) | Owning project |
| `created_by` | varchar(64) | Creator |
| `host_id` | varchar(64) | Owning host |
| `task_run_uuid` | varchar(64) | Execution that activated the plan |
| `description` | text | Human-readable description |
| `git_branch` | varchar(128) | Branch used by this plan |
| `plan_status` | varchar(16) | Plan state |
| `steps_json` | longtext | The original JSON |
| `activate_mode` | varchar(16) | Activation mode |
| `start_task_run_uuid` | varchar(64) | Chain entry execution |
| `notified_at` | datetime | Completion notification time |
| `completed_at` | datetime | Completion time |

</details>

## 8.3 Plan states

| State | Meaning |
|---|---|
| `draft` | Created but not activated. The watchdog does not scan this state |
| `activated` | Activated; steps are released by dependency |
| `cancelling` | Cancellation in progress |
| `cancelled` | Cancelled |
| `completed` | Every step settled |
| `archived` | Archived |
| `failed` | Reserved state |

The current database holds 190 completed, 42 cancelled, 14 draft and 5 archived.

## 8.4 Three topologies

A plan is made of steps, and the dependencies between them are expressed in three ways.

### 8.4.1 Serial chain

Link the steps end to end with `prev_task` / `next_task`:

```
A → B → C
```

B is activated when A finishes, C when B finishes. Suits work with a strict order.

### 8.4.2 Fan-out

Several steps with no dependency between them are activated together:

```
    ┌→ A
start ┼→ B
    └→ C
```

Suits independent work that does not interfere, such as querying several data sources at once.

### 8.4.3 Fan-in join

The parent step is activated only after all its children finish:

```
A ┐
B ┼→ join step
C ┘
```

The parent declares its children with `children`. The parent is released only when every child succeeds; if any child fails the parent is not activated.

`children` and `prev_task` are mutually exclusive: a step expresses its dependency in one way only, and cannot declare children and a predecessor at the same time.

## 8.5 Three actions

Creating, deploying and activating a plan are three separate actions:

| Action | Method | Result |
|---|---|---|
| Create | `agx_plan_create` | Writes the plan and its steps, state `draft` |
| Deploy | `agx_plan_deploy` | Writes one execution record per step, state still `draft` |
| Activate | `agx_activate_plan` | Steps move to `pending` and are handed to the watchdog |

Created but not deployed, there are no execution records; deployed but not activated, the watchdog does not scan it. The plan starts only after all three.

## 8.6 Two activation modes

| `activate_mode` | How steps are advanced |
|---|---|
| `watchdog` | The watchdog scans step states and releases them by dependency |
| `chain` | Chained advancement, driven by the entry execution |

The initial state of the steps is the same in both; the difference is the advancement policy recorded on the plan. The current database holds 206 `watchdog` and 45 `chain`.

`watchdog` is the default and fits most situations.

## 8.7 Who executes

Activation and advancement of a plan are carried by the dedicated task `plan_execute`, which runs in its own session. It is responsible for:

| Responsibility | Description |
|---|---|
| Activation | Moving step states from `draft` to `pending` |
| Receiving receipts | Handling the completion notice of each step |
| Exception handling | Deciding what to do when a step fails |
| Settling | Closing the plan and notifying when everything is done |

`plan_execute` has `task_type=plan`, so its timeout reads `plan_timeout`. Plans often run longer than ordinary tasks, so that setting can be raised on its own.

Do not call `agx_activate_plan` directly from an ordinary context. Activation should go through dispatching `plan_execute`, so that receipts and exceptions have a single owner.

## 8.8 Settling and notification

When every step finishes, the plan moves to `completed` and a completion notice is sent.

| Mechanism | Description |
|---|---|
| Recipient | The sender of the execution that activated the plan |
| De-duplication | Only a plan with an empty `notified_at` is notified; once written it is not repeated |
| Settling a join point | The parent step must point `session_key` at the sender's session explicitly, otherwise the completion notice only returns to the activator |

The third one is easy to miss: if the parent of a fan-in join does not name a session, the final conclusion returns to the session that activated the plan, and the plan's sender never receives it.

## 8.9 Hierarchical plans

For a large goal, use `parent_plan_id` to build a parent-child relation: each step of the parent plan activates a child plan.

| Size | Approach |
|---|---|
| Fewer than 15 steps | One plan, with the steps arranged directly |
| More than 15 steps, or several independent sub-pipelines | A parent plan plus several child plans; the parent's steps point at the children |

The parent only activates child plans and does not care about their internal steps. The criterion is whether the goal contains several independently deliverable sub-goals, not the step count alone.

## 8.10 Plan operations

| Method | Purpose |
|---|---|
| `agx_plan_create` | Create a plan in `draft` |
| `agx_plan_deploy` | Deploy a plan, generating one execution record per step |
| `agx_activate_plan` | Activate a plan, moving its steps to pending |
| `agx_plan_query` | Read plan metadata and the paginated step states |
| `agx_plan_list` | List every plan of a project and its completion |
| `agx_plan_cancel` | Cancel a plan, marking steps not yet started as `cancelled` |

`agx_plan_cancel` affects only `pending` steps; a step already running continues to completion.

Dispatch parameters related to plans:

| Parameter | Description |
|---|---|
| `plan_id` | Associates the execution with a plan |
| `prev_task` / `next_task` | Declares a serial dependency |
| `needs_confirm` | This step stops in `awaiting_confirm` on success, waiting for approval |

## 8.11 When not to use

- **Three steps or fewer with a fixed order**: dispatch them one by one. An extra layer of orchestration adds another surface for failure and raises the cost of diagnosing one.
- **Every step needs an ad-hoc human decision**: use `needs_confirm` or have a person run it. Arranging a decision point into a flow does not remove the decision.
- **Using a hierarchical plan for fewer than 15 steps**: parent-child plans suit goals with several independent sub-goals; at small scale they only add cost to viewing and troubleshooting.
- **Running several plan-generation tasks in one session**: each plan should get its own session; sharing one session makes them interfere.
- **Calling `agx_activate_plan` directly from an ordinary context**: receipts and exceptions have no owner; go through `plan_execute`.
- **Assuming a created plan is already running**: `draft` is not scanned; it must be deployed and activated.
