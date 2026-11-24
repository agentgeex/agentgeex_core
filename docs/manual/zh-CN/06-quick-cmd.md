# 第 6 章 快捷指令（Quick Command）

快捷指令把常用操作固化成带参数的入口。本章说明两种封装方式、私有与公有的区别、模板与参数规则，以及调度落点。

---

## 6.1 快捷指令的作用

同一句指令往往要重复输入，只有少数参数不同。快捷指令把它固化成模板，使用时只填参数。

| 作用 | 说明 |
|---|---|
| 减少输入 | 长指令只写一次，之后只填参数 |
| 统一格式 | 同一条指令由所有人发起时措辞一致 |
| 可追溯 | 每条执行记录关联 `quick_cmd_id` 与本次参数 |
| 可统计 | 记录使用次数与最后使用时间 |

## 6.2 快捷指令的组成

| 组成部分 | 属性 | 说明 |
|---|---|---|
| 标识 | `cmd_id` | 指令逻辑 ID |
| 名称与说明 | `cmd_name`、`cmd_desc` | 界面显示用 |
| 分类 | `category` | 如 `agent`、`project`、`system` |
| 模板 | `template` | 含 `{占位符}` 的指令文本 |
| 参数定义 | `params_schema` | JSON 数组，声明参数类型、默认值、枚举 |
| 发送方式 | `send_mode` | `task` 派任务，`text` 填文本 |
| 目标任务 | `task_labs` | 走哪条任务，存稳定标签而非 `task_id` |
| 归属 | `is_public`、`target_agent_id` | 私有指令固定收件人，公有指令由调用方决定 |
| 落点 | `run_session_id` | 执行落在哪个会话 |
| 统计 | `use_count`、`last_used_at` | 使用次数与最后使用时间 |

### 6.2.1 cmd_id 与 task_labs 未必同名

`cmd_id` 是指令的标识，`task_labs` 是它要触发的任务。两者可以不同名。真实的例子是 `system_usage_analysis`，它触发的任务是 `network_analysis`。

修改快捷指令时不要只改其中一个。两个值分别用于「查到这条指令」和「找到要执行的任务」。

<details>
<summary>agx_quick_cmd 完整字段定义</summary>

| 字段 | 类型 | 说明 |
|---|---|---|
| `cmd_id` | varchar(64) | 指令逻辑 ID |
| `cmd_name` | varchar(128) | 指令名 |
| `cmd_desc` | varchar(512) | 一句话说明，界面提示用 |
| `category` | varchar(32) | 分类，如 `agent` / `project` / `system` |
| `target_agent_id` | varchar(64) | 私有指令的固定收件人；`is_public=1` 时必须为空 |
| `is_public` | tinyint(1) | 0 私有，1 公有 |
| `task_labs` | varchar(64) | 触发的任务，存稳定标签而非 `task_id` |
| `run_session_id` | varchar(128) | 执行落点会话，空为默认主会话 |
| `template` | longtext | 含 `{占位符}` 的指令模板 |
| `params_schema` | json | 参数定义数组 |
| `send_mode` | varchar(8) | `task` 派任务 / `text` 填文本 |
| `sort_order` | int | 排序权重，数值小的在前 |
| `use_count` | int | 累计使用次数 |
| `last_used_at` | datetime | 最后使用时间 |
| `active_status` | tinyint(1) | 0 停用，1 启用 |

</details>

## 6.3 两种发送方式

| `send_mode` | 行为 | 是否产生执行 |
|---|---|---|
| `task` | 把合成后的指令作为一次任务派发 | 产生 |
| `text` | 把合成后的文本直接填入输入框 | 不产生 |

两种方式的用途不同，不是同一种东西的两种写法：

- 需要留痕、需要统计、需要回执，使用 `task`。
- 只想减少输入、不需要记录，使用 `text`。

选择 `text` 后，这次操作不会进入执行记录，也不会出现在任务统计中。

## 6.4 私有与公有

| `is_public` | 含义 | `target_agent_id` |
|---|---|---|
| 0 | 私有，固定发给指定 Agent | 必须填写 |
| 1 | 公有，不绑定 Agent，由调用方决定执行者 | 必须为空 |

两条约束在数据层强制，写入时违反会被拒绝。私有指令用于「这条指令只能由某个 Agent 执行」的场景，例如部署类操作；公有指令用于通用操作，例如按主机收集信息。

当前库中私有指令 30 条，公有 22 条；`task` 方式 46 条，`text` 方式 6 条。

## 6.5 模板与参数

`template` 是含占位符的文本，占位符写作 `{参数名}`，只允许字母、数字与下划线。

`params_schema` 声明参数结构：

```json
[
  { "name": "host_id", "type": "string", "required": true, "desc": "目标主机" },
  { "name": "scope", "type": "enum", "options": ["host", "project"], "default": "host" }
]
```

合成规则：

| 顺序 | 取值来源 |
|---|---|
| 1 | 调用时传入的参数值 |
| 2 | `params_schema` 中该参数的 `default` |
| 3 | 空串 |

布尔值转换为 `true` / `false`。未在 `params_schema` 中声明的占位符保持原样，不会被替换。

## 6.6 调度落点

`run_session_id` 指定执行落在哪个会话。为空时落在目标 Agent 的主会话。

助理也是会话，因此可以把快捷指令直接投到某个助理的会话中，例如 `agent:my_agent:case_my_assistant`。这种方式适合把固定工作长期交给某个助理处理。

## 6.7 使用

两个方法：

```python
# 只合成，不派发，用于预览
agx_quick_cmd_render(caller_agent_id="...", cmd_id="deploy_agent", params='{"host_id":"test_host_mac","agent_id":"x"}')

# 合成并派发
agx_quick_cmd_send(caller_agent_id="...", cmd_id="deploy_agent", params='{"host_id":"...","agent_id":"..."}')
```

`agx_quick_cmd_send` 内部完成查表、合成、派发三步，并在执行记录上写入 `quick_cmd_id` 与 `quick_cmd_params`（补全默认值后的真值），便于事后核对这次用了什么参数。

可选参数：

| 参数 | 说明 |
|---|---|
| `target_session_key` | 指定落在目标 Agent 的哪个会话，须属该 Agent |
| `self_session_id` | 发起方会话，决定回执回到哪里 |
| `needs_reply` | 是否需要回执，不传则按任务模板推导 |
| `timeout_seconds` | 超时，不传则按配置 |
| `client_request_id` | 幂等键，重试时复用同一值不会重复派发 |

`target_session_key` 与 `self_session_id` 是两个不同方向的定位：前者决定执行在哪里跑，后者决定回执回到哪里。

## 6.8 创建与维护

快捷指令的创建与编辑在控制台界面完成，写入 `agx_quick_cmd` 表。MCP 提供渲染与派发两个方法，不提供创建入口。

维护时需要注意：

| 事项 | 说明 |
|---|---|
| `task_labs` 必须存在 | 指向的任务被归档后，指令派发会失败 |
| 参数改名 | 模板占位符与 `params_schema` 需同时修改 |
| 停用 | 用 `active_status=0`，不物理删除，历史执行记录仍引用 `cmd_id` |
| 排序 | `sort_order` 数值小的在前 |

## 6.9 不适用的情况

- **每次内容都不同、参数不固定**：不适合做成快捷指令。占位符无法覆盖，使用者仍需手工改写，反而更慢。
- **需要事后追溯谁在何时发了什么**：必须使用 `task` 方式。`text` 方式不产生执行记录。
- **一次性操作**：不必创建。尤其不要设为公有，会进入所有人的列表。
- **把凭证或密钥写进模板或参数默认值**：模板与参数以明文存储并在界面展示，不适合承载敏感信息。
