# Chapter 3 · Project

A project is the organizational unit and permission boundary for agents. This chapter covers what a project is for, its attributes, the creation flow, and how projects relate to hosts and members.

---

## 3.1 What a project is for

Several agents working at once need two things: a clear statement of who belongs to the same piece of work, and a clear statement of who may manage whom. A project provides both.

| Purpose | Description |
|---|---|
| Organization | Agents of one project form a working unit; their tasks, sessions and logs can be viewed together |
| Permission boundary | A project manager manages only its own members; cross-project operations are rejected |
| Business isolation | Tasks, plans and sessions all carry `project_id`, so queries and statistics can be split by project |

## 3.2 Definition of a project

A project record defines one scope of work: a name, an abbreviation, an administrator, a target platform and a resource directory.

Every agent belongs to exactly one project. Agents with no business ownership belong to `system_project`.

## 3.3 The system project

`system_project` is built in and created during installation. It carries the system-level agents:

| Agent | Duty |
|---|---|
| `agx_core` | Top-level dispatch, task catalog management |
| `agx_agentfather` | Agent creation, cloning, configuration |
| `agx_projectmother` | Project creation, audit, archiving |
| `agx_host_*` | The manager of each host |

`system_project` never needs to be created, and business agents should not be placed inside it. Only in a business project does an agent get project-level permission isolation and statistics.

## 3.4 Project attributes

| Attribute | Description |
|---|---|
| `project_id` | Unique project ID, `proj_` prefix, may be chosen by the caller |
| `project_name` | Display name |
| `project_abbr` | Project abbreviation, all lowercase letters, used as the agent name prefix |
| `platform` | Target platform type: `openclaw` / `claude_code` / `custom` |
| `admin_agent_id` | ID of the administrating agent |
| `source_dir` | Resource directory for sources and similar files |
| `project_logo` | Project logo, URL or local path |
| `x_desc` | Project specification, read by agents of the project for background and constraints |
| `active_status` | 0 disabled, 1 enabled |

<details>
<summary>Full column definitions of agx_project</summary>

| Column | Type | Description |
|---|---|---|
| `project_id` | varchar(64) | Unique project ID |
| `project_name` | varchar(128) | Display name |
| `project_abbr` | varchar(32) | Abbreviation, used as the agent name prefix |
| `project_logo` | varchar(512) | Logo address |
| `platform` | varchar(32) | Target platform |
| `x_desc` | longtext | Project specification |
| `source_dir` | varchar(512) | Resource directory |
| `admin_agent_id` | varchar(64) | Administrating agent ID |
| `active_status` | tinyint(1) | 0 disabled, 1 enabled |
| `network_id` | varchar(64) | Network |
| `created_at` | timestamp | Creation time |

</details>

### 3.4.1 Project ID

`project_id` may be chosen by the caller, with the same rules as agent naming:

| Case | Behavior |
|---|---|
| Provided | Format is validated (mandatory `proj_` prefix, suffix of lowercase letters, digits and underscore) and duplicates are rejected with an explicit error |
| Omitted | Generated as `proj_{hex}` |

A meaningful ID makes ownership easy to see: `proj_report` is easier to match than `proj_a1b2c3d4`. Random IDs already assigned to existing projects are left as they are, with no migration.

### 3.4.2 Project specification

`x_desc` holds the background, goals and constraints of the project. Agents of the project read it at startup with `agx_get_project_config()`. Putting project-wide constraints here avoids each agent keeping its own copy.

## 3.5 Creating a project

Creating a project takes six steps in a fixed order:

```
create project  →  clone PM  →  deploy PM  →  clone worker  →  deploy worker  →  bind and verify
```

"Deploy PM" is the step most often skipped. When the PM is cloned but not deployed it has a database row but no directory on disk, and every later task dispatched to it fails.

The chain can be triggered in one call and walked through by a plan:

```python
agx_create_project_pipeline(
    caller_agent_id="agx_projectmother",
    instruction='{"project_name":"Report Analysis","description":"...","project_abbr":"report"}'
)
```

The pipeline generates one step per item above and hands them to `plan_execute` in a separate session. If it fails partway, the plan shows which step stalled; the project does not have to be created again.

## 3.6 Administrator and permissions

`admin_agent_id` points at the agent administrating the project, usually the PM cloned right after creation.

What the administrator may and may not do:

| Allowed | Not allowed |
|---|---|
| Rename and describe agents of this project | Change an agent's role, model or system prompt |
| Enable or disable workers of this project | Enable or disable the project itself |
| View sessions and tasks of project members | View members of other projects |
| Create plans | Create projects |

Enabling and disabling the project itself is a system-level decision outside the project manager's authority. When a project ends its record is not deleted but set to `active_status=0` and left in place, so historical tasks and executions remain queryable.

## 3.7 Projects and hosts

A project is not bound to a host; only agents are.

Members of one project can be spread across different hosts, and each task is delivered to the `host_id` of the agent that will run it. Disabling a host record therefore does not affect the project, only the agents bound to that host.

## 3.8 Project operations

| Method | Purpose |
|---|---|
| `agx_create_project` | Create a project record |
| `agx_create_project_pipeline` | Create a project and complete cloning, deployment and binding of PM and workers in one call |
| `agx_get_project` | Read project details |
| `agx_update_project` | Update project attributes, authorized per field class |
| `agx_list_project_agents` | List the agents of a project |
| `agx_project_sessions` | Query the sessions of project members |
| `agx_plan_list` | List the plans of a project and their completion |
| `agx_project_integrity_check` | Check the project configuration for gaps |
| `agx_get_project_config` | Read the project specification `x_desc` |
| `agx_bind_agent_to_project` | Bind an agent to a project |
| `agx_unbind_agent_from_project` | Remove the binding and fall back to `system_project` |

Field classes in `agx_update_project`:

| Class | Fields | Authorization |
|---|---|---|
| Operational | `project_name`, `project_abbr`, `project_logo`, `x_desc`, `source_dir` | A project manager may change projects it administers |
| Enable/disable | `active_status` | Global roles only |

A mixed submission is rejected as a whole; no part of it takes effect.

## 3.9 When not to use

- **Personal use where every agent is yours**: `system_project` is enough. Business projects exist for multi-person, multi-role collaboration and add a layer of management for no benefit otherwise.
- **Only wanting to group or tag agents**: use agent groups, not projects. A project carries an administrator, member admission and visibility filtering, which is heavier than a tag.
- **Temporary experiments**: decide the lifecycle first. A disabled project stays in place and occupies the list for a long time.
- **Moving a project to another host**: not a thing. Projects are not bound to hosts; it is the agents inside that would move.
