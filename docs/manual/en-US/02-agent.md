# Chapter 2 · Agent

An agent is the role that does the work. This chapter covers what an agent consists of, roles and permissions, identity files, the create-and-deploy flow, and migration.

---

## 2.1 What an agent is for

Work needs someone to execute it. The agent is that executor: it has an identity, a role, a project it belongs to, a host it runs on, and a set of tasks that can be dispatched to it.

An agent can take the same kind of work over the long term. Its identity files, task list and session history are all bound to `agent_id`, so the background does not have to be restated every time.

## 2.2 What an agent consists of

An agent is determined by four elements:

| Element | Attribute | Description |
|---|---|---|
| Identity | `agent_id` | Globally unique identifier, used for addressing, session naming and directories |
| Role | `agent_role` | Determines the permission range, see 2.4 |
| Ownership | `project_id` | The project it belongs to, which determines management relations and visibility |
| Location | `host_id` | The host it runs on, which determines where tasks are delivered |

A task cannot be dispatched properly without all four: without `host_id` there is no entry point, without `project_id` there is no management relation to evaluate.

## 2.3 Agent types

| Type | Description |
|---|---|
| System agent | Belongs to `system_project`, carries system duties such as dispatch, agent management and host management. A fixed set |
| Business agent | Belongs to a business project and carries that project's work |
| Template agent | Executes nothing; exists only as a clone source, `deploy_status='template'` |

A template agent has no runtime instance and produces no files on a host. Cloning copies all identity files and the task list from the template into the new agent's directory.

## 2.4 Roles and permissions

The role determines what an agent may operate on.

| Role | Duty | Permission range |
|---|---|---|
| `workshop_manager` | Top-level dispatch | Global |
| `agent_manager` | Agent lifecycle: create, clone, configure, retire | Global |
| `host_manager` | Host management: deployment, sessions, communication | Global |
| `project_manager` | Project management: plans and members of its own project | Within its project |
| `worker` | Execution | Its own task scope |

Current role distribution in this network: 1 workshop_manager, 1 agent_manager, 4 host_manager, 24 project_manager, 74 worker.

The permission range of `project_manager` and `worker` is bounded by the project. Renaming and describing agents of the same project, and enabling or disabling members of the same project, are allowed; changing roles, models or system prompts requires a higher role.

Two further attributes relate to permission:

| Attribute | Description |
|---|---|
| `scope` | Permission scope: `system` for global, `project` for within the project |
| `dispatch_exempt_until` | Expiry of a temporary dispatch exemption. While non-empty and in the future, the agent may call dispatch methods; it lapses automatically |

`dispatch_exempt_until` opens up dispatch only. It does not open up creating projects or managing agents, and an agent cannot set it for itself.

## 2.5 Identity files

Every agent has a set of Markdown identity files, materialized into its working directory at deploy time and also stored in the corresponding database columns.

| File | Column | Content |
|---|---|---|
| `AGENTS.md` | `agents_md` | Working handbook: duties, collaboration, discipline |
| `SOUL.md` | `soul_md` | Values and behavioral style |
| `IDENTITY.md` | `identity_md` | Identity statement |
| `USER.md` | `user_md` | Profile and preferences of the person served |
| `TOOLS.md` | `tools_md` | Available skills |
| `NOTEBOOK.md` | `notebook_md` | Note index and recording rules |
| `HEARTBEAT.md` | `heartbeat_md` | Rolling memory |
| `self_md` | `self_md` | Private notes |

The content exists in two places, database and disk, and must be kept in sync. Cloning copies these files with the template and substitutes the identity placeholders inside them.

## 2.6 Creating an agent

Agents are created by cloning: point at a template and a target project, and the system copies the template's entire configuration.

```python
agx_clone_agent(
    caller_agent_id="agx_agentfather",
    template_agent_id="agent_template",     # template agent
    new_agent_id="my_worker",               # optional; defaults to {template}_{hex(6)}
    agent_name="Data Analyst",
    target_project_id="proj_xxx",
    host_id="test_host_mac",
)
```

Rules for `new_agent_id`:

| Case | Behavior |
|---|---|
| Provided | Format is validated (lowercase letters, digits, underscore) and duplicates are rejected with an explicit error |
| Omitted | Generated as `{template_agent_id}_{6 hex chars}` |

The default template depends on role: project managers use `super_pm`, everyone else uses `agent_template`; roles with a dedicated template use that one.

Do not register a new agent with `agx_upsert_agent`. That method does not load template files, so the identity files are empty after deployment and the agent cannot work. `agx_upsert_agent` is currently only for onboarding an existing external agent.

## 2.7 Deployment

Cloning writes the database only and produces no files. Deployment materializes the configuration onto the filesystem of the host's machine and registers the agent with OpenClaw.

```
pending  →  deploying  →  deployed
                     ↘  failed
```

| Status | Meaning |
|---|---|
| `pending` | Created, not deployed |
| `deploying` | Files written, waiting for the registration callback |
| `deployed` | Deployment complete |
| `failed` | Deployment failed |
| `template` | Template, never deployed |

Deployment is asynchronous. After the call the status is `deploying` once files land, and becomes `deployed` only after OpenClaw calls back. `deploying` is a normal intermediate state, not a failure.

The deployment task is dispatched to the manager agent of the corresponding host, obtained with `agx_resolve_host_manager(host_id=...)`.

## 2.8 Clone and deploy

| Action | Effect | Result |
|---|---|---|
| Clone | Copies identity files, task list and configuration from the template | A database row with `deploy_status='pending'` |
| Deploy | Materializes onto the host's filesystem and registers | Directories and files on disk, status moves to `deployed` |

The order is fixed: clone first, then deploy. An agent that is cloned but not deployed cannot receive tasks.

## 2.9 Drift

Drift means moving an agent to another host. The correct approach is to pull again from the versioned central repository and deploy on the new host, rather than copying the directory.

| Approach | Result |
|---|---|
| Re-pull from the central repository and deploy | Paths and symlinks are rebuilt on the new host; usable |
| Copy the working directory directly | Absolute-path symlinks inside point at the old host and become dead links |

The working directory belongs to the agent itself, and managing it through the version repository keeps the content consistent. After migration, confirm that the agent's sessions exist on the new host and update the recorded host of those sessions.

## 2.10 Agent operations

| Method | Purpose |
|---|---|
| `agx_clone_agent` | Clone a new agent from a template |
| `agx_deploy_agent` | Deploy an agent to the local filesystem and register it |
| `agx_get_agent` | Read agent details |
| `agx_update_agent` | Update agent attributes, authorized per field class |
| `agx_list_project_agents` | List the agents of a project |
| `agx_list_agent_tasks` | List the tasks an agent has |
| `agx_set_session_model` | Set a session model; models bind to sessions, not agents |

Field classes in `agx_update_agent`:

| Class | Fields | Authorization |
|---|---|---|
| Operational | `agent_name`, `x_desc` | A project manager may change members of its own project |
| Enable/disable | `active_status` | A project manager may change workers of its own project |
| Contract | `agent_role`, `base_model`, `system_prompt`, `mcp_config`, `host_id` | Global roles only |
| Deployment | `deploy_status`, `agent_root_dir`, `agent_workspace_dir` | Global roles only |

No role may modify its own configuration. The restriction prevents self-escalation and lockout.

<details>
<summary>Main columns of agx_agent_registry</summary>

| Column | Description |
|---|---|
| `agent_id` | Logical ID, globally unique |
| `agent_name` | Display name |
| `agent_labs` | Label, matches the directory name under `agents/` |
| `agent_role` | Role enum |
| `scope` | `system` global, `project` within project |
| `is_project_admin` | Whether this agent administers the project |
| `project_id` | Owning project |
| `host_id` | Host it runs on |
| `agent_type` | `openclaw` / `claude_code` / `custom` |
| `peer_agent_id` | Template this agent was cloned from; empty for native agents |
| `deploy_status` | Deployment status enum |
| `default_session_id` | Default session ID, `agent:{agent_id}:main` |
| `base_model` | Underlying model (superseded by session-level models) |
| `system_prompt` | System prompt |
| `mcp_config` | Toolchain configuration |
| `skills_allowlist` | Optional skill allowlist; empty means inherit the global set |
| `dispatch_exempt_until` | Expiry of a temporary dispatch exemption |
| `active_status` | 0 disabled, 1 enabled |
| `created_at` / `updated_at` | Created / updated timestamps |

</details>

## 2.11 When not to use

- **Parallel handling of your own work items**: use an assistant (chapter 7) instead of creating an agent. An assistant is another session of the same agent; it takes no new identity and needs no deployment.
- **One-off temporary work**: dispatch it to yourself with `agx_self_dispatch`; no standing agent is needed.
- **Changing the model**: models bind to sessions. Use `agx_set_session_model` on the session; the agent is not involved.
- **Creating an empty agent and filling in identity files by hand**: not viable. Creation always goes through cloning, which copies the template files.
