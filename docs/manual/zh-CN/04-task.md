# 第 4 章 任务（Task）

任务定义 Agent 能做什么。本章说明任务的定义与参数、MCP 操作方法、内置任务，以及任务说明书的写法。

---

## 4.1 任务的作用

可以直接给 Agent 发消息让它做事，但直接发消息有四项限制：

| 限制 | 说明 |
|---|---|
| 无法复用 | 每次都要重新描述一遍完整要求 |
| 无法审计 | 做过什么、谁做的、成没成，只留在聊天记录里 |
| 无法并发 | 多条消息落进同一个会话，上下文互相干扰 |
| 无法定时 | 必须有人在合适的时间发出消息 |

任务把行为固化成可执行单元，解决这四类问题：同一类工作可以反复调度、每次执行都有记录、可并行、可定时。

任务的适用范围不限于业务流程。创建 Agent 本身也是一条任务（`agent_clone`），部署 Agent 是一条任务（`agent_deploy`）。系统内部的每个动作都通过任务执行。

## 4.2 任务的定义

一条任务是一份说明书，加上从这份说明书中抽出来的结构化属性。

**说明书**（`x_desc`）是任务的主体：这件事怎么做、按什么步骤做、输出什么格式、什么情况算失败、做到什么程度算完成。执行任务的 Agent 读的就是它。

**属性**是说明书的结构化映射。任务标识、归属 Agent、会话模式、回执方式、可见性这些约束，本来都写在说明书正文中；写成文字就无法查询、无法筛选，也无法用于权限判断。把其中可以判断的部分抽成字段后，说明书才能被系统调度。

| 属性名称 | 说明 |
|---|---|
| `task_labs` | 任务标签，任务的稳定标识，与 `tasks/` 下的文件名一致 |
| `task_name` | 任务名称，仅用于展示 |
| `agent_id` | 任务归属的 Agent ID |
| `task_type` | 任务类型。`normal` 为单次执行，`plan` 为编排多步骤 |
| `dedicated_session` | 是否使用独立会话。1 表示每次执行各占一个会话 |
| `reply_mode` | 执行完成后的回执方式 |
| `is_private` | 可见性。1 表示仅归属 Agent 与管理员可见 |

改说明书与改属性不是两件事，而是同一件事的两个层次：说明书讲清楚要做什么，属性让系统能调度它。

任务定义的是「这件事会怎么被做」，不记录「这一次做成了什么」。同一件事会执行很多次，每次的指令、参数、结果、耗时各不相同，那些数据属于执行记录。

```
任务        这一类工作     「分析网络态势」这个能力
 └ 执行     某一次         「11-24 09:52 那次分析，耗时 8 分钟，结论为……」
```

一个 Agent 拥有的任务构成它的能力清单。查询能力用任务列表，不需要询问 Agent 本人：

```
agx_list_agent_tasks(agent_id="agent_id")
```

## 4.3 任务参数

### 4.3.1 description 与 x_desc

一条任务有两段文字说明，用途不同：

| 对比项 | `description` | `x_desc` |
|---|---|---|
| 读者 | 决定是否派发的人（PM、其他 Agent、界面） | 执行任务的 Agent |
| 内容 | 一句话说明这是什么能力 | 完整的执行说明 |
| 长度 | 一行 | 不限（`longtext`） |

`description` 写错的后果比 `x_desc` 严重。读取任务目录的 Agent 依据 `description` 判断任务用途，不会逐条核对 `x_desc`。

`agent_create` 是一个实例。该任务已废除（创建 Agent 统一改用 clone），但 `description` 长期保留旧描述「注册一个新 Agent」，导致后续读目录的 Agent 按旧语义调用 `agx_upsert_agent` 直写数据库，部署出 0 字节空壳。

### 4.3.2 五个参数

| 参数 | 取值 | 说明 |
|---|---|---|
| `task_type` | `normal` / `plan` | 超时取哪个配置项 |
| `reply_mode` | `result` / `confirm` / `silent` | 是否回结果、是否跟踪回执 |
| `dedicated_session` | `0` / `1` | 执行落在主会话还是独立会话 |
| `is_private` | `0` / `1` | 任务对其他 Agent 是否可见 |
| `payload` | JSON | 每次执行都携带的固定输入 |

#### task_type

`normal` 为普通任务，`plan` 为计划类任务。

两者的区别在超时：普通任务读 `task_timeout`，计划类读 `plan_timeout`，两个配置项独立，默认值都是 3600 秒。计划类任务通常运行时间远超普通任务，需要单独调大 `plan_timeout` 而不影响其他任务。

派发时显式传入 `timeout_seconds` 则以传入值为准，不传才读配置。

#### reply_mode

| 取值 | 含义 |
|---|---|
| `result` | 执行完成后返回结果 |
| `confirm` | 执行前需要确认 |
| `silent` | 静默执行，不返回结果 |

`reply_mode` 同时决定是否跟踪回执。派发时，`result` 与 `confirm` 自动置 `needs_reply=1`，`silent` 置 0。使用 `silent` 后不仅没有结果，回执状态也不会被记录，发起方无法得知对方是否已读取。

#### dedicated_session

| 取值 | 执行落点 | 适用 |
|---|---|---|
| `0` | Agent 主会话 | 简单消息处理、通知响应 |
| `1` | `agent:{agent_id}:{task_labs}` 独立会话 | 并发任务、定时任务、需要上下文隔离的任务 |

主会话是共享上下文。多条任务同时落进主会话会相互干扰并串行排队，上下文增长后触发压缩，压缩过程中会丢失部分记忆。

同一任务可能同时运行多份，或单次运行时间较长时，使用 `dedicated_session=1`。

#### is_private

`is_private` 控制任务的查询可见性，不控制执行可达性。

设为 1 后，该任务不出现在其他 Agent 的任务目录中。这不阻止其他 Agent 向该任务派发执行，执行权限需要在任务说明书内自行校验。

`universal` 是例外。它是每个 Agent 的私有通道，代码层额外做了限制：外部 Agent 不能向其他 Agent 的 `universal` 派发任务，与该任务的 `is_private` 取值无关。

#### payload

`payload` 存这条任务每次执行都需要的固定输入，例如分析范围、默认阈值。

单次执行的参数不写入 `payload`，写入 `exec_instruction`。写入 `payload` 后，下次执行仍会携带上一次的参数。

<details>
<summary>agx_task_base 字段定义</summary>

| 字段 | 类型 | 含义 |
|---|---|---|
| `task_id` | varchar(64) | 任务逻辑 ID（初始化时生成，会变化） |
| `task_labs` | varchar(128) | 任务标签，对应 `tasks/` 下的文件名，为稳定标识 |
| `task_name` | varchar(128) | 任务可读名称 |
| `agent_id` | varchar(64) | 任务归属的 Agent |
| `parent_task_id` | varchar(64) | 父任务，自引用 |
| `owner_group` | varchar(64) | 非空时仅当宿主 Agent 属于该小组才具备此任务，随小组绑定注入，不走克隆继承 |
| `description` | text | 简短描述 |
| `x_desc` | longtext | 完整说明书 |
| `payload` | json | 固定输入参数 |
| `priority` | tinyint | 优先级，默认 1 |
| `reply_mode` | varchar(20) | 默认 `result` |
| `task_type` | varchar(32) | 默认 `normal` |
| `dedicated_session` | tinyint(1) | 默认 0 |
| `is_private` | tinyint(1) | 默认 0 |
| `session_done_wake` | tinyint(1) | 会话结束时是否立即唤醒看门狗：1 唤醒（执行型任务），0 不唤醒（编排/调度型任务） |
| `active_status` | tinyint(1) | 0 禁用，1 启用 |
| `created_at` / `update_dt` | timestamp | 创建时间 / 最后更新时间 |

`task_labs` 是稳定标识，可作为查询与配置的依据；`task_name` 只用于展示。

</details>

## 4.4 任务的操作方式

任务的全部操作通过 MCP 方法完成。调用方可以是人，也可以是界面上的一次操作，还可以是一个正在执行的 Agent；只需给出参数，MCP 负责参数校验、落库与缓存同步。不需要手工构造消息报文，不需要直接执行 SQL，也不需要事先准备配置文件。

### 4.4.1 方法一览

| 方法 | 用途 |
|---|---|
| `agx_create_task` | 为指定 Agent 创建一条任务 |
| `agx_list_agent_tasks` | 列出某个 Agent 的全部任务，即能力清单 |
| `agx_get_task` | 按 `task_id` 查询任务详情，含关联执行概览 |
| `agx_get_task_by_name` | 按 `task_name` 或 `task_labs` 查询 `task_id` |
| `agx_update_task` | 按 `task_id` 更新任务属性与说明书 |
| `agx_update_task_by_labs` | 按 `task_labs` 批量更新多个 Agent 下的同名任务 |
| `agx_archive_task` | 归档（软删除），可恢复 |
| `agx_delete_task` | 物理删除，不可恢复 |

派发与执行相关的方法属于第 5 章：`agx_dispatch_task`、`agx_schedule_task`、`agx_get_execution` 等。

### 4.4.2 创建任务

使用 `agx_create_task`：

```python
agx_create_task(
    caller_agent_id="agentgeex_agent",     # 调用方，用于权限校验
    agent_id="target_agent_id",            # 这条任务归给哪个 Agent
    task_labs="network_analysis",          # 任务标签，建议与 tasks/ 下文件名一致
    task_name="网络态势分析",                # 显示名称
    description="按项目汇总网络态势，输出占比超过阈值的异常项",   # 简短描述
    x_desc="<说明书全文>",                   # 执行方读取的内容
    task_type="normal",
    reply_mode="result",
    dedicated_session=1,
    is_private=1,
)
```

参数说明：

| 参数 | 说明 |
|---|---|
| `task_labs` | 稳定标识，后续查询、快捷指令、计划均引用此值；建议与 `tasks/` 下的文件名一致 |
| `task_name` | 显示名称，不参与逻辑判断 |
| `description` | 简短描述，其他 Agent 据此判断是否派发任务 |
| `x_desc` | 完整说明书，包含步骤、输出格式与失败条件（见 4.7） |
| `is_private` | 仅影响查询可见性，不影响执行权限（见 4.3.2） |

写入后立即生效，不需要重装或重启。

同一 Agent 下 `task_labs` 不能重复。重复创建会被拒绝，返回提示「task already exists for agent ... 请改用 agx_update_task」。

### 4.4.3 查询任务

| 方法 | 使用场景 |
|---|---|
| `agx_list_agent_tasks(agent_id=...)` | 查看某个 Agent 具备哪些能力 |
| `agx_get_task_by_name(caller_agent_id=..., agent_id=..., task_name=...)` | 已知任务名，取 `task_id` 用于派发 |
| `agx_get_task(task_id=...)` | 已知 `task_id`，查看完整定义与历史执行概览 |

`agx_get_task_by_name` 先按 `task_name` 精确匹配，未命中再按 `task_labs` 匹配，所以传标签也能查到，返回结果中包含 `task_id`。

创建任务后可用 `agx_list_agent_tasks` 确认已经出现在目标 Agent 名下。

### 4.4.4 修改任务

| 场景 | 方法 | 说明 |
|---|---|---|
| 改某个 Agent 的某一条任务 | `agx_update_task(task_id=..., updates={...})` | 按 `task_id` 精确更新，可改 `x_desc`、`payload`、`is_private`、`priority`、`active_status` |
| 同一 `task_labs` 在多个 Agent 名下都要改 | `agx_update_task_by_labs(task_labs=..., x_desc=...)` | 批量更新 `x_desc`，可选一并更新 `is_private` |

`agx_update_task_by_labs` 有作用域参数：

| `scope` | 范围 | 可用角色 |
|---|---|---|
| `own_project`（默认） | 自己项目组内 Agent 的同名任务 | 任何 Agent |
| `global` | 全网同名任务 | host_manager / workshop_manager / agent_manager |

批量更新前会校验目标是否全部落在作用域内，混入域外任务时整体拒绝。

`run_status` 不能通过这两个方法修改。它属于执行记录，不属于任务定义（第 5 章）。

### 4.4.5 归档与删除

| 方法 | 效果 | 可否恢复 | 权限 |
|---|---|---|---|
| `agx_archive_task` | 置 `active_status=0`，从活跃列表移除，保留行与历史 | 可恢复 | 归属 Agent / 项目管理员 / 全局角色 |
| `agx_delete_task` | 物理删除 | 不可恢复 | 同上 |

任务已有执行记录时（`agx_task_execution.task_id` 引用该任务），`agx_delete_task` 会被拒绝，应改用 `agx_archive_task`，避免破坏历史执行链。

### 4.4.6 模板发布

前几节的方法只改动当前数据库中某个 Agent 名下的任务。让后续新安装、新克隆的 Agent 自带同一条任务，属于模板发布，需要三个位置同时存在：

| 位置 | 作用 | 缺失后果 |
|---|---|---|
| `tasks/<task_labs>.md` | 说明书正文，模板真源 | 克隆或部署时无法取得说明书 |
| `agx_struct.json` | 发布清单，安装时据此装配入库 | 新安装的 Agent 没有这条任务 |
| `agx_task_base` 表行 | 运行态数据 | 当前实例可用，但后续修改不生效 |

漏改其中一处不会报错，只会造成修改未生效：说明书已更新，Agent 仍按旧内容执行。

每个位置内部还有多个区块，改一个区块不等于改完一条任务。数据库侧除属性字段外，还保存 `description` 与 `x_desc` 两段文字；`tasks/*.md` 除正文外还有 front matter 与指令示例。

改完做三方比对：

```
agx_task_base 行  ←→  agx_struct.json  ←→  tasks/<task_labs>.md
```

模板发布是仓库维护工作。日常给某个 Agent 加一条任务，走 4.4.2 的 `agx_create_task` 即可，不需要改动上面三个位置。

<details>
<summary>当前仓库三个位置的实际数量</summary>

| 位置 | 数量 |
|---|---|
| `tasks/*.md` | 29 篇 |
| `agx_struct.json` 的 `tasks` 数组 | 28 条 |
| `agx_task_base`（active） | 41 个不同 `task_labs`，514 行 |

三个数字不相等属正常情况：`tasks/*.md` 包含已废除但保留存档的任务（如 `agent_create`）；数据库包含运行时创建的项目专属任务，这类任务不写入 struct；同一个 `task_labs` 会在多个 Agent 名下各存一行。

</details>

## 4.5 内置任务

安装完成后，每个 Agent 名下都有一批任务，不需要自行创建。按归属分为三档。

### 4.5.1 所有 Agent 都有的三条

| `task_labs` | 用途 |
|---|---|
| `general` | 处理发给该 Agent 的通用系统消息与通知，落在主会话 |
| `universal` | 通用任务，落在独立会话，具体内容由派发时的指令决定 |
| `watchdog_alert` | 处理看门狗发来的超时与异常告警，判断任务是否真的失联并处置 |

实测 104 个 Agent，这三条各 104 行。

### 4.5.2 按角色装配

| `task_labs` | 归属 |
|---|---|
| `plan_execute` | PM，计划执行与生命周期管理 |
| `plan_generate_and_deploy` | PM，生成计划并部署 |
| `log_organize` | PM，聚合项目组日志 |
| `agent_clone` / `agent_deploy` | Agent 管理者 / 主机管理者 |
| `book_*`（9 条） | 图书线专职 Agent |

### 4.5.3 项目专属

按项目需要创建，例如 `project_create`、`network_analysis`、`code_change`。这类任务通常 `is_private=1`。

### 4.5.4 general 与 universal 的分工

这两条任务都用于处理任意内容，区别在执行落点。

| 对比项 | `general` | `universal` |
|---|---|---|
| 会话 | 主会话，共享上下文 | 独立会话，上下文隔离 |
| `dedicated_session` | 0 | 1 |
| 并发 | 不支持，共享会话会冲突 | 支持，每次执行各占一个会话 |
| 适用 | 简单消息、通知响应 | 并发任务、定时任务、需要隔离的任务 |

需要并发执行、运行时间较长、不希望影响主会话时使用 `universal`；单条消息的即时处理使用 `general`。

## 4.6 任务装配

不同 Agent 的任务数量不同。

<details>
<summary>实测：不同类型 Agent 的任务清单</summary>

| Agent 类型 | 条数 | 具体任务 |
|---|---|---|
| 普通 PM（`super_pm`、`must_pm`） | 7 | `general` `universal` `watchdog_alert` `log_organize` `plan_execute` `plan_generate_and_deploy` `book_project_orchestrate` |
| 带专属任务的 PM | 8 | 上述 7 条加专属任务 |
| Worker | 视模板而定 | 基线三条加模板携带的专属任务 |

</details>

装配过程为：按平台筛选 struct 中的任务，加上角色专属任务，加上小组绑定注入的任务。

因此某个 Agent 缺少某条任务，通常是角色装配的结果，不是安装失败。需要确认时查询 `agx_list_agent_tasks`，再对照 4.5 的归属表。

## 4.7 任务说明书

Agent 执行时读取 `x_desc`，不读取建设者脑中的意图。说明书写得不完整，该任务的能力就无法实现。

三条写法要求。

**一、写判据，不写期望**

| 不完整的写法 | 可执行的写法 |
|---|---|
| 仔细分析数据，给出有价值的结论 | 按 `status` 分组统计；只报占比大于 5% 的项；结论包含一条可执行建议 |

「仔细」「有价值」无法转成动作，Agent 只能自行猜测标准。

**二、写明输出格式**

输出表格、JSON 还是纯文本，需要写清楚。指定列名与列顺序比描述「输出要清晰」有效。

**三、写明失败条件**

缺少失败判据时，Agent 倾向于编造一个结果返回，这比返回失败更难发现。

失败判据的写法示例：源表中目标字段为空时，直接返回 failed 并说明缺失字段，不填充推测值。

## 4.8 不适用的情况

- **只执行一次的操作**：使用 `universal` 派发给自身，或使用 `agx_self_dispatch`。任务模板适用于会反复发生、多个 Agent 都需要的能力。
- **其他 Agent 已有对应任务**：先用 `agx_list_agent_tasks` 查看。确实没有合适任务时再考虑新建，不使用 `universal` 替代正式任务模板，它设计上是给自身的临时出口。
- **单次执行的参数**：写入 `exec_instruction`，不写入 `x_desc` 或 `payload`。`x_desc` 描述能力，`payload` 存固定输入。
- **更换模型**：与任务无关。模型绑定在会话上，不在任务或 Agent 上。
- **与现有任务高度重复**：先查询是否已有可用任务。现有 41 个 `task_labs` 中已包含多组近义能力（`project_create`、`project_configure`、`project_audit`），新增重复任务会增加后续所有人的查询成本。
