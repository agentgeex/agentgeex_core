# Chapter 7 · Session and Assistant

A session is where an execution lands; an assistant is an agent's parallel self. This chapter covers session types, why several sessions are needed, how assistants work, their file layout, and session-level models.

---

## 7.1 What a session is for

An execution must land in some session to run. The session decides four things:

| Purpose | Description |
|---|---|
| Context isolation | Conversation history is not visible across sessions |
| Concurrency | Several sessions run at the same time without queueing |
| Memory boundary | An overlong context triggers compaction, which loses part of the earlier content |
| Addressing | A session has a unique ID and can be a delivery target |

## 7.2 Definition of a session

`session_id` uniquely identifies a session and has the form `agent:{agent_id}:{suffix}`. The suffix decides which kind of session it is.

| `session_type` | Meaning | Example suffix |
|---|---|---|
| `main` | Main session, the long-term conversation channel | `main` |
| `task` | Task session, a stable session tied to one task family | the task label |
| `oneshot` | One-off temporary session, discarded after the run | random string |
| `assistant` | Assistant session, persistent, with an identity hint injected before every run | `case_{slug}` |

The current database holds about 4000 one-off sessions, 67 main sessions, 50 task sessions and 32 assistant sessions. One-off sessions dominate because every independent dispatch creates one.

## 7.3 Session attributes

| Attribute | Description |
|---|---|
| `session_id` | Addressing ID, globally unique |
| `session_name` | Session name, stating what the session is for |
| `x_desc` | More detailed purpose |
| `agent_id` | Owning agent |
| `project_id` | Owning project |
| `host_id` | Host the session is on |
| `task_labs` | Task label, recorded when the session came from a task session |
| `session_type` | Session type |
| `is_assistant` | Whether it is an assistant session |
| `assistant_slug` | Assistant identifier; empty for non-assistant sessions |
| `session_model` / `model_provider` / `model_source` | Session-level model and where it comes from |
| `run_count` / `last_run_uuid` / `last_active_at` | Runs carried, most recent run, last activity |
| `active_status` | 1 active, 0 archived |

<details>
<summary>Full column definitions of agx_session</summary>

| Column | Type | Description |
|---|---|---|
| `session_id` | varchar(128) | Addressing ID |
| `session_name` | varchar(255) | Session name |
| `x_desc` | text | Purpose description |
| `agent_id` | varchar(64) | Owning agent |
| `project_id` | varchar(64) | Owning project |
| `host_id` | varchar(64) | Host the session is on |
| `task_labs` | varchar(64) | Task family |
| `session_type` | varchar(32) | main / task / oneshot / assistant |
| `is_assistant` | tinyint(1) | Whether it is an assistant session |
| `assistant_slug` | varchar(64) | Assistant identifier |
| `avatar` | varchar(512) | Assistant avatar |
| `creator_agent_id` | varchar(64) | Creator |
| `session_model` | varchar(64) | Current session model |
| `model_provider` | varchar(32) | Model provider |
| `model_source` | varchar(16) | `user` explicit override / `auto` auto-filled / `default` framework default |
| `run_count` | int | Runs carried |
| `last_run_uuid` | varchar(64) | Most recent run |
| `last_active_at` | datetime | Last activity |
| `active_status` | tinyint(1) | 1 active, 0 archived |
| `is_quick` / `quick_order` | tinyint(1) / int | Quick-entry flag and ordering |

</details>

## 7.4 Why not put everything in the main session

The main session is shared context. Putting every execution in it causes three problems:

| Problem | How it shows |
|---|---|
| Serial queueing | A later run must wait for the previous one to finish |
| Context pollution | The history of two pieces of work is mixed and hard to separate |
| Memory loss | A growing context triggers compaction and the earlier part is dropped |

Hence the `dedicated_session` parameter on task templates: when set to 1 each run lands in its own session `agent:{agent_id}:{task_labs}` and runs can be concurrent.

## 7.5 Definition of an assistant

An assistant is an agent's own parallel self: another persistent session of the same agent, with the same tools and capabilities, its own working directory, answering to the parent agent.

| Aspect | Description |
|---|---|
| Identity | Not a new agent; takes no new `agent_id` |
| Capability | Same as the parent, granted by the parent |
| Ownership | Belongs to the parent and is not lent to other agents |
| Session | One persistent session, taking one kind of work over the long term |
| Files | Its own directory for records, notes and deliverables |
| Judgment | An assistant scales execution, not judgment |

Once an assistant exists, the parent can hand one kind of long-running work to it permanently and keep its own main session clean.

## 7.6 Assistants and project members

| Comparison | Project member | Assistant |
|---|---|---|
| What it is | An independent agent | A part of the parent agent |
| Where capability comes from | Its own configuration | Granted by the parent |
| Answers to | Its project | The parent agent |
| Collaboration | Negotiable | Fully obedient |
| Usable by other agents | Yes | No |

Work that needs an independent remit, professional judgment or long-term watch deserves an independent agent. Work that is several facets of the parent's own job, to be done in parallel, suits an assistant.

## 7.7 Assistant operations

| Method | Purpose |
|---|---|
| `agx_assistant_create` | Create an assistant: session, responsibility profile, file directories |
| `agx_assistant_list` | List the assistants under the caller |
| `agx_assistant_send` | Dispatch work to an assistant, locating the session by slug |
| `agx_assistant_profile` | Generate or edit an assistant's responsibility profile |
| `agx_assistant_drift` | After moving hosts, restore the session and directory skeleton without rewriting the profile |

`agx_assistant_send` resolves the assistant's real session from its slug and dispatches there; the receipt returns to the sender's session according to `self_session_id`.

An assistant's responsibility profile lives in the session's `x_desc`. The assistant re-reads it before every run, and that is the source of its self-knowledge: a session can be compacted or archived and its context is not guaranteed to survive, but the profile is.

## 7.8 Assistant file layout

An assistant's file root sits inside the parent's workspace:

```
{work_dir}/agx_agents/{agent_id}/workspace/assistants/{slug}/
  ├── daily/      logs
  ├── notes/      notes and drafts
  └── outputs/    deliverables
```

Rules:

| Category | Location |
|---|---|
| Records, documents, reports | The assistant directory |
| Project code | The project repository |

Code does not go into an assistant directory, which keeps it clear of the repository layout. A formal document that is confirmed for long-term archiving still goes under `docs/` in the project repository.

## 7.9 Session-level models

Models bind to sessions, not to agents. Different sessions of one agent may use different models.

| Method | Purpose |
|---|---|
| `agx_get_session_model` | Read the session's current model and write the three `agx_session` columns back |
| `agx_set_session_model` | Set or clear the session model override; an empty string falls back to the framework default |

`model_source` records where the model came from: `user` for an explicit setting, `auto` for auto-filled, `default` for the framework default. Changing one session's model affects only that session.

## 7.10 Session operations

| Method | Purpose |
|---|---|
| `agx_get_session` | Look up a session's identity and purpose by `session_id` |
| `agx_list_sessions` | Query sessions by agent, project, type or keyword |
| `agx_my_sessions` | Query the caller's own sessions |
| `agx_project_sessions` | Let a project manager query the sessions of project members |
| `agx_update_session` | Change session name, description, type, archive state or avatar |
| `agx_link_execution` | Attach an existing execution to a session by adding a reference edge, without moving it |
| `agx_list_execution_links` | Query the attachment relations between executions and sessions |

`agx_link_execution` attaches a pointer and never modifies the original execution, so one execution can be referenced by several sessions.

## 7.11 When not to use

- **Needing an independent remit and judgment**: create an independent agent (chapter 2), not an assistant. Assistants do not scale judgment.
- **Expecting an assistant to remember the previous conversation**: sessions get compacted or archived, so identity relies on re-reading the profile every run, not on leftover context.
- **Two assistants working the same task in parallel**: they overwrite each other's files when writing to the same directory or repository. Only one assistant should write to one target at a time.
- **Keeping all work in the main session**: that leads to serial execution, context pollution and memory loss. Runs needing isolation should use `dedicated_session=1` or an assistant session.
- **Changing the model by editing an agent's model field**: the model lives on the session; use `agx_set_session_model`.
