# Chapter 6 · Quick Command

A quick command turns a common operation into a parameterized entry point. This chapter covers the two delivery modes, the difference between private and public commands, the template and parameter rules, and where a run lands.

---

## 6.1 What a quick command is for

The same instruction often has to be typed over and over with only a few parameters changed. A quick command freezes it into a template, so using it means filling in parameters only.

| Purpose | Description |
|---|---|
| Less typing | The long instruction is written once; afterwards only parameters are supplied |
| Consistent wording | The same instruction reads identically no matter who sends it |
| Traceability | Each execution records `quick_cmd_id` and the parameters used |
| Usage statistics | Counts of uses and the last used time |

## 6.2 What a quick command consists of

| Part | Attribute | Description |
|---|---|---|
| Identifier | `cmd_id` | Logical command ID |
| Name and description | `cmd_name`, `cmd_desc` | Shown in the UI |
| Category | `category` | Such as `agent`, `project`, `system` |
| Template | `template` | Instruction text containing `{placeholders}` |
| Parameter definition | `params_schema` | JSON array declaring types, defaults and enums |
| Delivery mode | `send_mode` | `task` dispatches a task, `text` fills the input box |
| Target task | `task_labs` | Which task it triggers; stores the stable label, not `task_id` |
| Ownership | `is_public`, `target_agent_id` | A private command has a fixed recipient; a public one is decided by the caller |
| Landing point | `run_session_id` | Which session the run lands in |
| Statistics | `use_count`, `last_used_at` | Uses and last used time |

### 6.2.1 cmd_id and task_labs may differ

`cmd_id` identifies the command; `task_labs` is the task it triggers. The two need not share a name. A real example is `system_usage_analysis`, which triggers the task `network_analysis`.

When editing a quick command, do not change only one of them. One value is used to find the command, the other to find the task to run.

<details>
<summary>Full column definitions of agx_quick_cmd</summary>

| Column | Type | Description |
|---|---|---|
| `cmd_id` | varchar(64) | Logical command ID |
| `cmd_name` | varchar(128) | Command name |
| `cmd_desc` | varchar(512) | One-line description shown in the UI |
| `category` | varchar(32) | Category, such as `agent` / `project` / `system` |
| `target_agent_id` | varchar(64) | Fixed recipient of a private command; must be empty when `is_public=1` |
| `is_public` | tinyint(1) | 0 private, 1 public |
| `task_labs` | varchar(64) | Task triggered; stores the stable label, not `task_id` |
| `run_session_id` | varchar(128) | Session the run lands in; empty means the default main session |
| `template` | longtext | Instruction template containing `{placeholders}` |
| `params_schema` | json | Parameter definition array |
| `send_mode` | varchar(8) | `task` dispatches a task / `text` fills text |
| `sort_order` | int | Sort weight, lower first |
| `use_count` | int | Total uses |
| `last_used_at` | datetime | Last used time |
| `active_status` | tinyint(1) | 0 disabled, 1 enabled |

</details>

## 6.3 The two delivery modes

| `send_mode` | Behavior | Creates an execution |
|---|---|---|
| `task` | Dispatches the composed instruction as a task | Yes |
| `text` | Puts the composed text into the input box | No |

The two serve different purposes; they are not two spellings of one thing:

- Need a record, statistics or a receipt: use `task`.
- Only want less typing and no record: use `text`.

With `text`, the operation never enters the execution records and never appears in task statistics.

## 6.4 Private and public

| `is_public` | Meaning | `target_agent_id` |
|---|---|---|
| 0 | Private, always sent to a fixed agent | Must be filled |
| 1 | Public, not bound to an agent; the caller decides the executor | Must be empty |

Both constraints are enforced at the data layer and violated writes are rejected. A private command fits "this instruction may only be run by one agent", such as deployment operations; a public command fits general operations, such as collecting information per host.

In the current database there are 30 private and 22 public commands; 46 use `task` and 6 use `text`.

## 6.5 Templates and parameters

`template` is text containing placeholders written as `{name}`, where the name allows only letters, digits and underscore.

`params_schema` declares the parameter structure:

```json
[
  { "name": "host_id", "type": "string", "required": true, "desc": "target host" },
  { "name": "scope", "type": "enum", "options": ["host", "project"], "default": "host" }
]
```

Composition rules:

| Order | Source of the value |
|---|---|
| 1 | The value passed by the caller |
| 2 | The parameter's `default` in `params_schema` |
| 3 | Empty string |

Booleans are converted to `true` / `false`. A placeholder not declared in `params_schema` is left as it is and not replaced.

## 6.6 Where the run lands

`run_session_id` names the session the run lands in. When empty it lands in the target agent's main session.

An assistant is a session too, so a quick command can be pointed straight at an assistant's session, for example `agent:my_agent:case_my_assistant`. This suits handing a fixed piece of work to one assistant for the long term.

## 6.7 Using a quick command

Two methods:

```python
# compose only, no dispatch; useful for a preview
agx_quick_cmd_render(caller_agent_id="...", cmd_id="deploy_agent", params='{"host_id":"test_host_mac","agent_id":"x"}')

# compose and dispatch
agx_quick_cmd_send(caller_agent_id="...", cmd_id="deploy_agent", params='{"host_id":"...","agent_id":"..."}')
```

`agx_quick_cmd_send` performs the lookup, composition and dispatch in one call, and writes `quick_cmd_id` and `quick_cmd_params` (the real values, defaults filled in) onto the execution, so what was used can be checked afterwards.

Optional parameters:

| Parameter | Description |
|---|---|
| `target_session_key` | Which session of the target agent to land in; must belong to that agent |
| `self_session_id` | The sender's session, which decides where the receipt goes |
| `needs_reply` | Whether a receipt is wanted; derived from the task template when omitted |
| `timeout_seconds` | Timeout; read from configuration when omitted |
| `client_request_id` | Idempotency key; reusing the same value on a retry does not dispatch twice |

`target_session_key` and `self_session_id` point in opposite directions: the first decides where the run executes, the second where the receipt returns.

## 6.8 Creating and maintaining

Quick commands are created and edited in the console UI and written to `agx_quick_cmd`. MCP exposes the render and dispatch methods and no creation entry point.

Points to watch when maintaining:

| Item | Description |
|---|---|
| `task_labs` must exist | Once the task it points at is archived, dispatching the command fails |
| Renaming a parameter | The template placeholder and `params_schema` must change together |
| Disabling | Use `active_status=0` rather than deleting; historical executions still reference `cmd_id` |
| Ordering | Lower `sort_order` comes first |

## 6.9 When not to use

- **Content differs every time, parameters are not fixed**: a poor fit for a quick command. Placeholders cannot cover it, users still have to rewrite the text, and it ends up slower.
- **Needing to trace afterwards who sent what and when**: use `task` mode. `text` produces no execution record.
- **A one-off operation**: do not create one. In particular do not make it public, or it lands in everybody's list.
- **Putting credentials or keys into a template or a parameter default**: templates and parameters are stored in plain text and shown in the UI, so they are the wrong place for sensitive data.
