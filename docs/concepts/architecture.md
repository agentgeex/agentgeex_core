# AgentGeeX System Architecture (v2.0)

> **Document status**: the architectural description is still valid (reviewed 2026-10-24).
> This document describes the **design intent and the layering model**, without line numbers; where a code location matters, the source code is authoritative.
> Last substantive revision: 2026-06-06; moved from `notes/` into `docs/` on 2026-10-24.
>
> Verified: of the 8 core tables claimed below, 7 exist in the real database (`agx_project_agent` is deprecated).

## 1. System positioning
**AgentGeeX** is a general-purpose, distributed substrate for agent management and task scheduling. Through the **MCP (Model Context Protocol)** it builds an industrial-grade management framework between the LLM's reasoning-and-decision capability and the physical resources of the real world (databases, compute, automation tooling, social-media APIs, and so on), closing the loop between "thinking" and "landing the work".

AgentGeeX exists to solve the "out of control" problem Agents hit in production: every action an agent takes must be **auditable, traceable, reproducible, and horizontally scalable**.

---

## 2. Core architecture: a three-tier linked model
AgentGeeX uses a "persistence – real-time state – execution protocol" three-tier decoupled architecture, which is what gives the system its high robustness:

### 1. Persistence layer (Persistence - MySQL)
**Responsibility**: act as the system's "Source of Truth", storing all long-lived, static structured data — static configuration, task history, the audit ledger, and other foundational data.

**Core tables:**
* **Team grouping (`agx_agent_group`)**: the logical organisational unit for Agents; supports managing multiple Agents per team.
* **Resource inventory (`agx_host_inventory`)**: manages physical/virtual host nodes, recording compute capacity and live load.
* **Agent registry (`agx_agent_registry`)**: records each Agent's identity, role definition, System Prompt and model configuration; links to a team through `agent_group_id`.
* **Project definition (`agx_project`)**: the top-level container for business-logic isolation; lets multiple projects run independently.
* **Project-Agent association (`agx_project_agent`)**: the bidirectional many-to-many table between projects and Agents, supporting flexible resource orchestration.
* **Task definition library (`agx_task_base`)**: separates business logic from execution, providing static task definitions; supports parent tasks referencing children through `parent_task_id`.
* **Task execution instance tracking (`agx_task_execution`)**: records every execution instance of a task — run instruction, output result, status transitions. Hierarchies of parent/child executions are supported through `parent_run_uuid`.

* **Notices and announcements (`agx_agent_notice`)**: the central store for Agents' work logs, decision records, pitfall notes and handover memos. It replaces traditional file-based note-taking; `visibility` controls the scope (private/project/network), and the notice_summarize task periodically distils and archives them.

### 2. Real-time state layer (Real-time - Redis)
**Responsibility**: act as the system's "Source of Speed", handling high-frequency dynamic data.
* **Heartbeat monitoring**: tracks the liveness of every Agent and physical host in real time. When an Agent drops, its Redis heartbeat disappears immediately and triggers a system warning — without polling MySQL frequently.
* **Task queues**: deliver tasks in seconds and process them asynchronously.
* **Distributed locks**: keep resource contention safe when multiple Agents run concurrently.
* **Cache acceleration**: Redis caching for all core entities (Project, Host, Agent, Task, Execution) to cut MySQL query pressure.

### 3. Protocol and execution layer (Interface - MCP & OpenClaw)
**Responsibility**: define the boundary within which Agents operate on the real world.
* **In-house MCP toolset**: through the standard MCP protocol, the underlying CRUD operations, notification service and external APIs are turned into tools the Agent can understand (Tools).
* **Security sandbox**: strict environment-variable management and code auditing keep third-party toolchains running in a controlled, safe environment.

### 4. Notes and context layer (Context - File)
**Responsibility**: a lightweight startup context for Agents, cutting the token cost of every startup.
- **SUMMARY.md**: a working summary refreshed automatically at the end of each task round (pitfall reminders, recent decisions, pending handovers, work statistics), with a hard cap of 4000 characters. On startup an Agent only has to read this file to pick up recent context, without querying the database or the memory/ files.
- **agx_query_notices**: call it when historical detail is needed; search by run_uuid or keyword.

---

## 3. Core design philosophy
1. **Decentralised scheduling, centralised auditing**: Agents may be spread across networks (NetworkID) and hosts (HostID), but every action trace must land in the centralised database in real time.
2. **Decoupling tasks from execution**: define the "work (Task)" first, then find the "person (Agent)" and the "place (Host)" — dynamic resource assignment.
3. **Two-dimensional management by team and project**: Agent templates are logically grouped through `agx_agent_group` (defined by `agx_struct.json`, read-only after initialisation) and cloned into projects with `agx_bind_group_to_project`, giving templated bulk deployment.
4. **Grounded**: Agents are never allowed to run on hallucination. All reasoning must be based on live state in the database, and every output must be verifiable.
5. **Business neutrality**: AgentGeeX knows nothing about concrete business logic. Quantitative analysis, short-video operations, social-media automation — to the platform all of them are just a combination of Task and Payload.

---

## 4. Key business flows

### Standard scheduling flow (five steps)

1. **Define the task**: create a task record in `agx_task_base`; `agent_id` names the executor, `payload` carries the business parameters, and `parent_task_id` supports splitting a task hierarchically.
2. **Bind the project**: through `agx_project_agent` the task is associated with an Agent of a concrete project, ensuring the Agent only works within its authorised project.
3. **Ignite execution**: `agx_dispatch_task` instantiates the static task into `agx_task_execution`, binding Host and Agent automatically, generating a globally unique `task_run_uuid`, and enqueuing a command message to the target Agent.
4. **Hierarchical splitting**: a complex task can be split by creating multiple `agx_task_execution` records linked through `parent_run_uuid`; each child execution instance tracks its own token consumption and execution status.
5. **Close the loop**: when the task completes, call `agx_finish_task` to update `run_status` (succeeded/failed), persist the result, and trigger follow-up notifications or automated hand-offs.

### Resource orchestration flow
1. **Team grouping**: group pre-provisioned Agents by function through `agx_agent_group` (e.g. "AgentGeeX core team"). The group is defined by `agx_struct.json` and read-only after initialisation.
2. **Agent registration**: configure role, model and credentials for each Agent in `agx_agent_registry`.
3. **Host onboarding**: register compute hosts through `agx_host_inventory` and set the concurrency ceiling.
4. **Project binding**: clone team templates into a project with `agx_bind_group_to_project` (each member is cloned into an independent Agent instance, agent_id = {template_name}_{random_suffix}).
5. **Dynamic scheduling**: the system decides dispatch strategy in real time from the host's `presence_status`, `active_task_count` and `task_slots_limit`.

---

## 5. Core entity relationships

```
agx_agent_group (team)
      │ 1:N
      ▼
agx_agent_registry (Agent) ◄─── M:N ───► agx_project (project)
      │                                    │
      │ 1:N                               │ 1:N
      ▼                                    ▼
agx_task_base (task definition) ── 1:N ──► agx_task_execution (execution instance)
      │                                    │
      │ parent_task_id (self-reference)    │
      │                                    │ parent_run_uuid (self-reference)
      ▼                                    ▼
  (task splitting)                    agx_task_execution (child execution instance)
```

---


## 6. Underlying database schema

For the database schema see `../../agx.sql`

## 7. Future direction
* **Cross-network collaboration**: large-scale collaboration between Agent clusters in different LAN segments (NetworkID).
* **Multimodal tooling**: specialised MCP plugins for video generation, image-and-text layout, and social APIs.
* **Automated knowledge engineering**: turn the accumulated sub-task execution data into private fine-tuning data, deepening the platform agents' domain expertise.
* **Dynamic resource scheduling**: intelligent dynamic task distribution and automatic host failover, driven by live load and heartbeat data in the Redis cache.
