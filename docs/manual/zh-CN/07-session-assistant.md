# 第 7 章 会话与助理（Session & Assistant）

会话是执行的落点，助理是 Agent 的并行分身。本章说明会话类型、为什么需要多会话、助理的机制与文件规约，以及会话级模型。

---

## 7.1 会话的作用

一条执行必须落在某个会话中运行。会话决定这件事：

| 作用 | 说明 |
|---|---|
| 上下文隔离 | 不同会话的对话历史互不可见 |
| 并发 | 多个会话可以同时运行，互不排队 |
| 记忆边界 | 上下文过长会触发压缩，压缩会丢失部分早期内容 |
| 寻址 | 会话有唯一 ID，可作为投递目标 |

## 7.2 会话的定义

`session_id` 是会话的唯一标识，格式为 `agent:{agent_id}:{后缀}`。后缀决定这个会话属于哪一类。

| `session_type` | 含义 | 后缀示例 |
|---|---|---|
| `main` | 主会话，长期对话通道 | `main` |
| `task` | 任务会话，挂某个任务族的稳定会话 | 任务标签 |
| `oneshot` | 一次性临时会话，执行完即弃 | 随机串 |
| `assistant` | 助理会话，常驻，每次执行注入身份提示 | `case_{slug}` |

当前库中一次性会话约 4000 条，主会话 67 条，任务会话 50 条，助理会话 32 条。一次性会话占绝大多数，因为每次独立派发都会新建一个。

## 7.3 会话的属性

| 属性名称 | 说明 |
|---|---|
| `session_id` | 会话寻址 ID，全局唯一 |
| `session_name` | 会话名称，说明这个会话做什么用 |
| `x_desc` | 更详细的用途说明 |
| `agent_id` | 归属 Agent |
| `project_id` | 所属项目 |
| `host_id` | 会话所在主机 |
| `task_labs` | 由任务会话产生时，记录任务标签 |
| `session_type` | 会话类型 |
| `is_assistant` | 是否助理会话 |
| `assistant_slug` | 助理标识，非助理会话为空 |
| `session_model` / `model_provider` / `model_source` | 会话级模型及其来源 |
| `run_count` / `last_run_uuid` / `last_active_at` | 承载过的执行次数、最近一次执行、最近活跃时间 |
| `active_status` | 1 有效，0 归档 |

<details>
<summary>agx_session 完整字段定义</summary>

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` | varchar(128) | 会话寻址 ID |
| `session_name` | varchar(255) | 会话名称 |
| `x_desc` | text | 用途说明 |
| `agent_id` | varchar(64) | 归属 Agent |
| `project_id` | varchar(64) | 所属项目 |
| `host_id` | varchar(64) | 会话所在主机 |
| `task_labs` | varchar(64) | 任务族 |
| `session_type` | varchar(32) | main / task / oneshot / assistant |
| `is_assistant` | tinyint(1) | 是否助理会话 |
| `assistant_slug` | varchar(64) | 助理标识 |
| `avatar` | varchar(512) | 助理头像 |
| `creator_agent_id` | varchar(64) | 创建者 |
| `session_model` | varchar(64) | 会话当前模型 |
| `model_provider` | varchar(32) | 模型提供方 |
| `model_source` | varchar(16) | `user` 显式覆盖 / `auto` 自动回填 / `default` 框架默认 |
| `run_count` | int | 承载过的执行次数 |
| `last_run_uuid` | varchar(64) | 最近一次执行 |
| `last_active_at` | datetime | 最近活跃时间 |
| `active_status` | tinyint(1) | 1 有效，0 归档 |
| `is_quick` / `quick_order` | tinyint(1) / int | 快捷入口标记与排序 |

</details>

## 7.4 为什么不能都放在主会话

主会话是共享上下文。所有执行都落进主会话时会出现三个问题：

| 问题 | 具体表现 |
|---|---|
| 串行排队 | 后到的执行必须等前一个结束 |
| 上下文污染 | 两件事的对话历史混在一起，难以区分 |
| 记忆丢失 | 上下文增长触发压缩，早期内容被压缩掉 |

因此任务模板上有 `dedicated_session` 参数：置 1 时每次执行落在 `agent:{agent_id}:{task_labs}` 独立会话中，可以并发。

## 7.5 助理的定义

助理是 Agent 自己的分身：同一个 Agent 的另一个常驻会话，具备相同的工具与能力，有独立的工作目录，对本体负责。

| 方面 | 说明 |
|---|---|
| 身份 | 不是新 Agent，不占新的 `agent_id` |
| 能力 | 与本体一致，由本体赋予 |
| 归属 | 归属本体，不外借给其他 Agent |
| 会话 | 一个常驻会话，长期承接同一类工作 |
| 文件 | 独立目录，用于存放记录、笔记与交付物 |
| 判断 | 助理放大执行能力，不放大判断能力 |

创建助理后，本体可以把一类长期工作固定交给它，本体主会话保持清爽。

## 7.6 助理与项目组成员

| 对比项 | 项目组成员 | 助理 |
|---|---|---|
| 是什么 | 独立 Agent | 本体的一部分 |
| 能力来源 | 自身配置 | 本体赋予 |
| 对谁负责 | 所属项目 | 本体 |
| 协作方式 | 可协商 | 完全听命 |
| 能否被其他 Agent 使用 | 可以 | 不能 |

需要独立职责、专业判断、长期值守的工作，应创建独立 Agent。需要本体并行处理多个切面的工作，使用助理。

## 7.7 助理的操作

| 方法 | 用途 |
|---|---|
| `agx_assistant_create` | 创建助理：建会话、生成职责档案、建文件目录 |
| `agx_assistant_list` | 列出本体名下的助理 |
| `agx_assistant_send` | 给助理派活，按 slug 定位会话 |
| `agx_assistant_profile` | 生成或修改助理职责档案 |
| `agx_assistant_drift` | 换主机后补会话与目录骨架，不改档案 |

`agx_assistant_send` 内部按 slug 解析出助理的真实会话，再把指令派发过去。回执按 `self_session_id` 回到发起方会话。

助理的职责档案保存在会话的 `x_desc` 中。每次执行前助理会重新读取档案，这是它认知身份的来源：会话可能被压缩或归档，上下文不保证保留，档案保证保留。

## 7.8 助理的文件规约

助理的文件根位于本体工作区内：

```
{work_dir}/agx_agents/{agent_id}/workspace/assistants/{slug}/
  ├── daily/      日志
  ├── notes/      笔记与草稿
  └── outputs/    交付物
```

规则：

| 类别 | 落点 |
|---|---|
| 记录、文档、报告 | 助理目录 |
| 项目代码 | 项目仓库 |

代码不进助理目录，避免与版本库结构冲突。经确认需要长期归档的正式文档，仍落项目仓库的 `docs/` 下。

## 7.9 会话级模型

模型绑定在会话上，不绑定在 Agent 上。同一个 Agent 的不同会话可以使用不同模型。

| 方法 | 用途 |
|---|---|
| `agx_get_session_model` | 读取会话当前模型，并回写 `agx_session` 三列 |
| `agx_set_session_model` | 设置或清除会话模型覆盖，传空串表示回落框架默认 |

`model_source` 记录模型来源：`user` 为用户显式设置，`auto` 为自动回填，`default` 为框架默认。需要更换某个会话的模型时，只影响该会话。

## 7.10 会话操作

| 方法 | 用途 |
|---|---|
| `agx_get_session` | 按 `session_id` 查询会话身份与用途 |
| `agx_list_sessions` | 按 Agent、项目、类型、关键词查询会话 |
| `agx_my_sessions` | 查询调用方自己的会话 |
| `agx_project_sessions` | 项目管理员查询本项目成员的会话 |
| `agx_update_session` | 修改会话名称、说明、类型、归档状态、头像 |
| `agx_link_execution` | 把已有执行挂靠到某个会话，只增引用边，不搬移执行 |
| `agx_list_execution_links` | 查询执行与会话之间的挂靠关系 |

`agx_link_execution` 挂的是指针，原执行记录不被修改，因此同一条执行可以被多个会话引用。

## 7.11 不适用的情况

- **需要独立职责与判断能力**：创建独立 Agent（第 2 章），不是助理。助理不放大判断能力。
- **指望助理记住上一轮对话**：会话会被压缩或归档，身份依靠每轮重读档案，不依赖上下文残留。
- **同一件事开两个助理并行**：两个助理在同一目录或同一仓库写文件时会互相覆盖。同一目标同一时刻只应有一个助理在写。
- **把所有工作都留在主会话**：会导致串行、上下文污染与记忆丢失。需要隔离的执行应使用 `dedicated_session=1` 或助理会话。
- **通过修改 Agent 的模型字段换模型**：模型在会话上，应使用 `agx_set_session_model`。
