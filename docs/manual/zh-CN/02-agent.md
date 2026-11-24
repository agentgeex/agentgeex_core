# 第 2 章 Agent

Agent 是执行工作的角色。本章说明 Agent 的构成、角色与权限、身份文件、创建与部署流程，以及迁移方法。

---

## 2.1 Agent 的作用

任务需要有人执行。Agent 就是这个执行方：它有身份标识、有角色权限、归属于某个项目、运行在某台主机上，并具备一组可被派发的任务。

同一个 Agent 可以长期承接同一类工作，它的身份文件、任务清单、会话历史都绑在 `agent_id` 上，因此不需要每次重新交代背景。

## 2.2 Agent 的构成

一个 Agent 由四项要素确定：

| 要素 | 对应属性 | 说明 |
|---|---|---|
| 身份 | `agent_id` | 全局唯一标识，用于寻址、会话命名、文件目录 |
| 角色 | `agent_role` | 决定权限范围，见 2.4 |
| 归属 | `project_id` | 所属项目，决定管理关系与可见性 |
| 运行位置 | `host_id` | 所在主机，决定任务投递到哪里 |

缺少任意一项都无法正常派发任务：没有 `host_id` 找不到投递入口，没有 `project_id` 无法判定管理关系。

## 2.3 Agent 的类型

| 类型 | 说明 |
|---|---|
| 系统 Agent | 归属 `system_project`，承担调度、Agent 管理、主机管理等系统职责，数量固定 |
| 业务 Agent | 归属业务项目，承担该项目的工作 |
| 模板 Agent | 不执行任务，只作为克隆源存在，`deploy_status='template'` |

模板 Agent 没有运行实例，不在主机上生成文件。克隆时从模板复制全部身份文件与任务清单，落到新 Agent 目录下。

## 2.4 角色与权限

角色决定这个 Agent 能操作的范围。

| 角色 | 职责 | 权限范围 |
|---|---|---|
| `workshop_manager` | 顶层调度 | 全局 |
| `agent_manager` | Agent 生命周期管理，创建、克隆、配置、下架 | 全局 |
| `host_manager` | 主机管理，部署、会话、通信 | 全局 |
| `project_manager` | 项目管理，可创建计划、管理本项目成员 | 所属项目内 |
| `worker` | 具体执行 | 自身任务范围 |

当前网络中角色分布：workshop_manager 1 个，agent_manager 1 个，host_manager 4 个，project_manager 24 个，worker 74 个。

`project_manager` 与 `worker` 的权限以项目为边界。修改本项目的 Agent 名称与说明、启停本项目成员可以执行；修改角色、模型、系统提示词需要更高角色。

另有两个与权限相关的属性：

| 属性 | 说明 |
|---|---|
| `scope` | 权限范围，`system` 为全局，`project` 为所属项目内 |
| `dispatch_exempt_until` | 临时调度豁免到期时间。非空且晚于当前时间时，该 Agent 可调用派发类方法；到期自动失效 |

`dispatch_exempt_until` 只放开派发能力，不放开创建项目、管理 Agent 等权限，且不能给自己设置。

## 2.5 身份文件

每个 Agent 有一套 Markdown 身份文件，部署时物化到工作目录，同时也保存在数据库对应字段中。

| 文件 | 数据库字段 | 内容 |
|---|---|---|
| `AGENTS.md` | `agents_md` | 工作手册：职责、协作方式、纪律 |
| `SOUL.md` | `soul_md` | 价值观与行为风格 |
| `IDENTITY.md` | `identity_md` | 身份说明 |
| `USER.md` | `user_md` | 服务对象画像与偏好 |
| `TOOLS.md` | `tools_md` | 可用技能清单 |
| `NOTEBOOK.md` | `notebook_md` | 笔记索引与记录规则 |
| `HEARTBEAT.md` | `heartbeat_md` | 动态记忆 |
| `self_md` | `self_md` | 私有笔记 |

文件内容在数据库与磁盘两处存在，修改时需要保持同步。克隆 Agent 时这些文件随模板一起复制，并替换其中的身份占位符。

## 2.6 创建

创建 Agent 统一使用克隆方式：指定模板与目标项目，由系统复制模板的全部配置。

```python
agx_clone_agent(
    caller_agent_id="agx_agentfather",
    template_agent_id="agent_template",     # 模板 Agent
    new_agent_id="my_worker",               # 可选，不填则自动生成 {template}_{hex(6)}
    agent_name="数据分析员",
    target_project_id="proj_xxx",
    host_id="test_host_mac",
)
```

`new_agent_id` 的规则：

| 情况 | 行为 |
|---|---|
| 填写 | 校验格式（小写字母、数字、下划线）并检查重复，重复时明确报错 |
| 不填 | 自动生成 `{template_agent_id}_{随机 6 位十六进制}` |

默认模板按角色区分：项目管理员使用 `super_pm`，其余使用 `agent_template`；有专属模板的角色使用专属模板。

不要使用 `agx_upsert_agent` 直接登记新 Agent。该方法不加载模板文件，部署后身份文件为空，Agent 无法正常工作。`agx_upsert_agent` 目前仅用于把已有外部 Agent 接入系统。

## 2.7 部署

克隆只写入数据库，不生成文件。部署把配置物化到主机所在机器的文件系统，并注册到 OpenClaw。

```
pending  →  deploying  →  deployed
                     ↘  failed
```

| 状态 | 含义 |
|---|---|
| `pending` | 已创建，未部署 |
| `deploying` | 文件已写入，等待注册回调 |
| `deployed` | 部署完成 |
| `failed` | 部署失败 |
| `template` | 模板，不部署 |

部署是异步的。调用部署后，文件落地时状态为 `deploying`，需要等 OpenClaw 回调后才置为 `deployed`。看到 `deploying` 属于正常中间状态，不是失败。

部署任务派发给对应主机的管理者 Agent，通过 `agx_resolve_host_manager(host_id=...)` 取得。

## 2.8 克隆与部署的关系

| 动作 | 作用 | 结果 |
|---|---|---|
| 克隆 | 从模板复制身份文件、任务清单、配置 | 数据库中有记录，`deploy_status='pending'` |
| 部署 | 物化到主机所在机器的文件系统并注册 | 磁盘有目录与文件，状态推进到 `deployed` |

两者顺序固定：先克隆，再部署。只克隆不部署的 Agent 无法接收任务。

## 2.9 漂移

漂移指把 Agent 迁移到另一个主机。正确做法是按版本化的中央仓库重新拉取，再在新主机部署，而不是复制目录。

| 做法 | 结果 |
|---|---|
| 按中央仓库重拉并部署 | 路径与软链接在新主机上重建，可用 |
| 直接复制工作目录 | 目录内的绝对路径软链接指向旧主机，迁移后成为死链 |

工作目录属于 Agent 本身，通过版本库管理可以保证内容一致。迁移完成后需要核对会话是否已在新主机建立，并更新会话记录中的主机归属。

## 2.10 Agent 操作

| 方法 | 用途 |
|---|---|
| `agx_clone_agent` | 从模板克隆新 Agent |
| `agx_deploy_agent` | 部署 Agent 到本机文件系统并注册 |
| `agx_get_agent` | 查询 Agent 详情 |
| `agx_update_agent` | 更新 Agent 属性，按字段分级授权 |
| `agx_list_project_agents` | 列出项目下的 Agent |
| `agx_list_agent_tasks` | 列出某 Agent 具备的任务 |
| `agx_set_session_model` | 设置会话模型，模型绑定在会话上而非 Agent 上 |

`agx_update_agent` 的字段分级：

| 字段类别 | 字段 | 授权 |
|---|---|---|
| 运营字段 | `agent_name`、`x_desc` | 项目管理员可改本项目成员 |
| 启停字段 | `active_status` | 项目管理员可改本项目 worker |
| 契约字段 | `agent_role`、`base_model`、`system_prompt`、`mcp_config`、`host_id` | 仅全局角色 |
| 部署字段 | `deploy_status`、`agent_root_dir`、`agent_workspace_dir` | 仅全局角色 |

任何角色都不能修改自己的配置。该限制用于防止自我提权与自锁。

<details>
<summary>agx_agent_registry 主要字段</summary>

| 字段 | 说明 |
|---|---|
| `agent_id` | 逻辑 ID，全局唯一 |
| `agent_name` | 显示名 |
| `agent_labs` | 标签，对应 `agents/` 目录名 |
| `agent_role` | 角色枚举 |
| `scope` | `system` 全局，`project` 项目内 |
| `is_project_admin` | 是否项目管理员 |
| `project_id` | 所属项目 |
| `host_id` | 所在主机 |
| `agent_type` | `openclaw` / `claude_code` / `custom` |
| `peer_agent_id` | 克隆来源模板，原生 Agent 为空 |
| `deploy_status` | 部署状态枚举 |
| `default_session_id` | 默认会话 ID，格式 `agent:{agent_id}:main` |
| `base_model` | 底层模型（已由会话级模型取代） |
| `system_prompt` | 系统提示词 |
| `mcp_config` | 工具链配置 |
| `skills_allowlist` | 自选 skill 白名单，为空表示继承全局 |
| `dispatch_exempt_until` | 临时调度豁免到期时间 |
| `active_status` | 0 禁用，1 启用 |
| `created_at` / `updated_at` | 创建时间 / 更新时间 |

</details>

## 2.11 不适用的情况

- **需要并行处理自己的多项工作**：使用助理（第 7 章），不新建 Agent。助理是同一 Agent 的另一个会话，不占新身份、不需要部署主机。
- **只执行一次的临时工作**：使用 `agx_self_dispatch` 派发给自身，不需要常驻 Agent。
- **需要更换模型**：模型绑定在会话上，用 `agx_set_session_model` 修改会话模型即可，与 Agent 无关。
- **先创建空 Agent 再手工补身份文件**：不成立。创建一律走克隆，模板文件会随克隆复制。
