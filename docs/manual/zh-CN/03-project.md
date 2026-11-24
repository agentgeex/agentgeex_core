# 第 3 章 项目（Project）

项目是 Agent 的组织单位与权限边界。本章说明项目的作用、属性、创建流程，以及项目与主机、成员之间的关系。

---

## 3.1 项目的作用

多个 Agent 同时工作时需要两件事：明确谁和谁属于同一组工作，明确谁可以管理谁。项目同时承担这两件事。

| 作用 | 说明 |
|---|---|
| 组织 | 同一项目的 Agent 构成一个工作单元，可整体查看任务、会话与日志 |
| 权限边界 | 项目管理员只能管理本项目成员，跨项目的操作被拒绝 |
| 业务隔离 | 任务、计划、会话均带 `project_id`，查询与统计可按项目切分 |

## 3.2 项目的定义

一条项目记录定义一个工作范围，包含名称、代号、管理员、目标平台与资源目录。

每个 Agent 必须归属一个项目。没有业务归属的 Agent 归入 `system_project`。

## 3.3 系统项目

`system_project` 是系统内置项目，安装时自动创建，承载系统级 Agent：

| Agent | 职责 |
|---|---|
| `agx_core` | 顶层调度、任务目录管理 |
| `agx_agentfather` | Agent 创建、克隆、配置 |
| `agx_projectmother` | 项目创建、审计、归档 |
| `agx_host_*` | 各主机的管理者 |

`system_project` 不需要自行创建，也不应把业务 Agent 放进去。业务 Agent 放在业务项目中，才能获得项目级的权限隔离与统计口径。

## 3.4 项目的属性

| 属性名称 | 说明 |
|---|---|
| `project_id` | 项目唯一 ID，`proj_` 前缀，可自定义 |
| `project_name` | 显示名称 |
| `project_abbr` | 项目代号，全小写字母，用于 Agent 命名前缀 |
| `platform` | 目标平台类型，`openclaw` / `claude_code` / `custom` |
| `admin_agent_id` | 项目管理员 Agent ID |
| `source_dir` | 项目资源目录，存放源码等 |
| `project_logo` | 项目 Logo，URL 或本地路径 |
| `x_desc` | 项目说明书，供项目内 Agent 读取背景与约束 |
| `active_status` | 0 停用，1 启用 |

<details>
<summary>agx_project 完整字段定义</summary>

| 字段 | 类型 | 说明 |
|---|---|---|
| `project_id` | varchar(64) | 项目唯一 ID |
| `project_name` | varchar(128) | 显示名称 |
| `project_abbr` | varchar(32) | 项目代号，用于 Agent 命名前缀 |
| `project_logo` | varchar(512) | Logo 地址 |
| `platform` | varchar(32) | 目标平台 |
| `x_desc` | longtext | 项目说明书 |
| `source_dir` | varchar(512) | 资源目录 |
| `admin_agent_id` | varchar(64) | 管理员 Agent ID |
| `active_status` | tinyint(1) | 0 停用，1 启用 |
| `network_id` | varchar(64) | 所属网络 |
| `created_at` | timestamp | 创建时间 |

</details>

### 3.4.1 项目编号

`project_id` 可以自定义，规则与 Agent 命名一致：

| 情况 | 行为 |
|---|---|
| 传入编号 | 校验格式（强制 `proj_` 前缀，后缀为小写字母、数字、下划线）并检查重复，重复时明确报错 |
| 不传入 | 自动生成 `proj_{随机十六进制}` |

自定义编号便于识别归属，例如 `proj_report` 比 `proj_a1b2c3d4` 更容易对照。历史项目中已生成的随机编号保持原样，不做迁移。

### 3.4.2 项目说明书

`x_desc` 存放项目背景、目标与约束，项目内 Agent 启动时通过 `agx_get_project_config()` 读取。把项目级的共同约束写在这里，可以避免每个 Agent 各自维护一份副本。

## 3.5 创建项目

创建项目的完整链路有六步，顺序固定：

```
创建项目  →  克隆 PM  →  部署 PM  →  克隆 Worker  →  部署 Worker  →  绑定并验收
```

其中「部署 PM」最容易被遗漏。只克隆不部署时，PM 只有数据库记录、没有文件系统目录，后续所有派发给 PM 的任务都会失败。

这条链路可以一次触发，由计划编排自动走完：

```python
agx_create_project_pipeline(
    caller_agent_id="agx_projectmother",
    instruction='{"project_name":"报表分析","description":"...","project_abbr":"report"}'
)
```

管线内部按上述六步生成步骤，交给 `plan_execute` 在独立会话中执行。中途失败时可在计划中看到卡在哪一步，不需要重新创建项目。

## 3.6 管理员与权限

`admin_agent_id` 指向该项目的管理员 Agent，通常是项目创建后克隆出的 PM。

管理员的权限范围：

| 可执行 | 不可执行 |
|---|---|
| 修改本项目 Agent 的名称与说明 | 修改 Agent 角色、模型、系统提示词 |
| 启停本项目 worker | 启停项目本身 |
| 查看本项目成员的会话与任务 | 查看其他项目的成员 |
| 创建计划 | 创建项目 |

项目本身的启用与停用属于系统级决策，不在项目管理员权限内。项目结束时不删除记录，而是置 `active_status=0` 留驻，历史任务与执行记录可继续查询。

## 3.7 项目与主机

项目不绑定主机，只有 Agent 绑定主机。

一个项目的成员可以分布在不同主机上，任务按每个 Agent 自己的 `host_id` 投递。因此停用一条主机记录不会影响项目本身，只会影响绑定在该主机上的 Agent。

## 3.8 项目操作

| 方法 | 用途 |
|---|---|
| `agx_create_project` | 创建项目记录 |
| `agx_create_project_pipeline` | 一键创建项目并完成 PM 与 Worker 的克隆、部署、绑定 |
| `agx_get_project` | 查询项目详情 |
| `agx_update_project` | 更新项目属性，按字段分级授权 |
| `agx_list_project_agents` | 列出项目内 Agent |
| `agx_project_sessions` | 查询项目成员的会话 |
| `agx_plan_list` | 列出项目下的计划及完成情况 |
| `agx_project_integrity_check` | 检查项目配置完整性 |
| `agx_get_project_config` | 读取项目说明书 `x_desc` |
| `agx_bind_agent_to_project` | 把 Agent 绑定到项目 |
| `agx_unbind_agent_from_project` | 解除绑定，重置为 `system_project` |

`agx_update_project` 的字段分级：

| 字段类别 | 字段 | 授权 |
|---|---|---|
| 运营字段 | `project_name`、`project_abbr`、`project_logo`、`x_desc`、`source_dir` | 项目管理员可改自己管理的项目 |
| 启停字段 | `active_status` | 仅全局角色 |

混合提交会被整体拒绝，不会部分生效。

## 3.9 不适用的情况

- **个人自用、所有 Agent 都归自己**：使用 `system_project` 即可。业务项目用于多人多角色协作，单独使用会增加一层管理成本。
- **只想分类或打标签**：使用 Agent 分组，不使用项目。项目带有管理员、成员准入与可见性过滤，比标签重。
- **临时实验**：先明确生命周期再创建。项目停用后虽然留驻，但会长期占用列表。
- **想给项目换主机**：不成立。项目不绑定主机，需要迁移的是项目内的 Agent。
