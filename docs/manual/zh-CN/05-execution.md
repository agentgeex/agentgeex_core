# 第 5 章 任务执行（Execution）

一条任务被派出去之后，系统产生一条执行记录。本章说明执行的状态流转、超时与看门狗机制、回执与签收，以及执行现场的各个字段。

---

## 5.1 执行的作用

任务定义的是「这件事会怎么被做」。同一件事会发生很多次，每次的指令、参数、结果、耗时都不同，需要各自留痕。执行记录承担这件事：

| 作用 | 说明 |
|---|---|
| 追溯 | 谁在什么时间派了什么指令，结论是什么 |
| 状态管理 | 当前是待激活、执行中、已完成还是失败 |
| 异常处理 | 超时后由看门狗唤醒、告警、重发 |
| 统计 | 耗时、token 消耗、成功率 |

## 5.2 执行的定义

一条执行记录代表任务的一次运行，用 `task_run_uuid` 唯一标识。

派发方法一次调用会完成三件事：创建执行记录、组装并投递消息、同步缓存。调用方不需要分别执行。

```python
agx_dispatch_task(
    caller_agent_id="agx_core",
    agent_id="some_agent",
    task_id="task_xxx",
    instruction="请统计本周新增任务数",
    needs_reply=1,
)
```

返回值中带 `task_run_uuid`，后续查询、结案、催办都用这个值定位。

### 5.2.1 任务与执行的关系

```
任务        这一类工作     「分析网络态势」这个能力
 └ 执行     某一次         「11-24 09:52 那次，耗时 8 分钟，结论为……」
```

一条任务可以产生多条执行。执行不会修改任务定义，任务定义的修改也不会影响已经产生的执行记录。

## 5.3 生命周期

```
draft ──部署/激活──> pending ──投递──> running ──┬──> succeeded
                                                ├──> failed
                                                ├──> cancelled
                                                └──> awaiting_confirm ──人放行──> succeeded
                                                                      └──人驳回──> failed
```

## 5.4 状态说明

| 状态 | 含义 | 进入方式 |
|---|---|---|
| `draft` | 已创建，未发布。看门狗不扫描此状态 | 计划部署时预建 |
| `pending` | 待激活或待投递 | 计划激活、定时任务待到期 |
| `running` | 执行中 | 投递成功 |
| `succeeded` | 执行成功 | 执行方调用结案方法 |
| `failed` | 执行失败 | 执行方调用结案方法，或看门狗判定 |
| `cancelled` | 已取消 | 计划取消或人工作废 |
| `awaiting_confirm` | 已交付，等待人放行 | `needs_confirm=1` 时执行成功 |

当前库中执行记录以 `succeeded` 为主（约 6400 条），另有 `draft` 约 730 条、`cancelled` 约 480 条、`failed` 约 76 条。`draft` 数量较多属于正常现象：计划部署后未激活的步骤会停留在此状态。

## 5.5 需要人放行

部分任务在执行成功时不直接置为 `succeeded`，而是停在 `awaiting_confirm`，等人确认后才推进。

| 属性 | 值 | 含义 |
|---|---|---|
| `needs_confirm` | 0 | 不需要放行 |
| | 1 | 待放行。执行成功落 `awaiting_confirm` |
| | 2 | 已放行 |
| | 3 | 已驳回 |

放行与驳回通过 `agx_confirm_task(task_run_uuid=..., action="approve"|"reject")` 执行。

`awaiting_confirm` 状态不计入超时。为了让确认有时间进行，当 `needs_confirm=1` 且派发时没有显式指定超时，系统会把超时提升到至少 86400 秒（1 天）。

需要注意 `reply_mode=confirm` 与 `needs_confirm` 是两件事：前者是任务模板上声明的回执方式，后者是这一次执行是否需要人放行。前者影响回执，后者影响状态流转。

## 5.6 超时

超时决定看门狗从什么时候开始介入。最终取值的顺序：

| 顺序 | 来源 | 说明 |
|---|---|---|
| 1 | 派发时传入的 `timeout_seconds` | 以传入值为准 |
| 2 | `needs_confirm=1` 且未传入 | 至少 86400 秒 |
| 3 | `task_type=plan` 且未传入 | 读 `plan_timeout`（默认 3600 秒） |
| 4 | 其余情况 | 读 `task_timeout`（默认 3600 秒） |

超时可以在执行过程中调整：

```python
agx_update_execution(
    caller_agent_id="agx_core",
    task_run_uuid="run_xxx",
    updates={"timeout_seconds": 10800},
)
```

规则是只允许延长。缩短会立即触发唤醒档位，因此仅管理员与全局角色可以缩短。取值范围 60 秒到 86400 秒。

## 5.7 看门狗

看门狗是独立进程，不是守护进程的一个函数。它按固定周期扫描执行记录，按四档逐级介入。

| 档 | 触发条件 | 动作 | 配置 |
|---|---|---|---|
| 唤醒 | 已运行到超时的 70%、80%、90% | 各提醒一次，不重复 | `watchdog_wake_tiers` |
| 告警 | 已超过 `timeout_seconds` | 写 `alert_level`、`alert_count`，发告警 | `watchdog_max_retries` |
| 重发 | 判定消息未送达 | 重新投递，有次数上限 | `send_resend_after` 600 秒，最多 2 次 |
| 人工催办 | 以上都用完仍无进展 | 由人手动推进 | `agx_nudge_execution` |

判定细节：

| 参数 | 默认值 | 作用 |
|---|---|---|
| `watchdog_wake_grace` | 120 秒 | 同一档唤醒的间隔下限 |
| `watchdog_grace_period` | 1800 秒 | 告警前的宽限期 |
| `watchdog_max_retries` | 3 | 告警次数上限 |
| `watchdog_host_heartbeat_timeout` | 90 秒 | 主机心跳超时判定 |

`alert_level` 的含义：

| 值 | 含义 |
|---|---|
| 0 | 无提醒 |
| 1 | 已唤醒 |
| 2 | 已告警 |
| 3 | 已升级给人 |
| 9 | 已收口 |

唤醒次数与告警次数分别记录在 `wake_count` 与 `alert_count`，用于判断是否已用尽自动手段。

### 5.7.1 started_at 的判断限制

`started_at` 采用懒写：只有在确认消息真正投递到执行方时才写入。因此：

| 现象 | 含义 |
|---|---|
| `started_at` 为空，`run_status=running` | 已发送，尚未确认开始执行 |
| `started_at` 非空 | 已确认投递成功 |

判断一次执行是否真的跑过，应看 `finished_at` 与 `summary`，不能只看 `started_at`。

## 5.8 回执与签收

回执用于回答「对方处理了吗、读了吗」。它与执行结果不是一回事。

### 5.8.1 needs_reply

| 值 | 含义 |
|---|---|
| 0 | 不需要回复 |
| 1 | 需要回复 |
| 2 | 回复中 |
| 3 | 已回复 |

派发时若不显式指定，系统按任务的 `reply_mode` 推导：`result` 与 `confirm` 置 1，`silent` 置 0。`force_no_reply=True` 的优先级最高。

### 5.8.2 签收

回执发出后，接收方读取并签收，才算闭环。

| 字段 | 说明 |
|---|---|
| `is_reply_to` | 该记录是回执时，指向原执行的 `task_run_uuid` |
| `reply_sent_at` | 回执发出时刻 |
| `reply_delivered_at` | 回执送达确认时刻 |
| `reply_read` | 是否已签收，0 未签收，1 已签收 |
| `reply_read_at` | 首次签收时刻 |
| `reply_read_last_at` | 最近一次签收时刻 |
| `ack_state` | 聚合状态：`none` / `sent` / `delivered` / `read` / `failed` |

`reply_read_at` 只记录首次签收时间，重复签收只刷新 `reply_read_last_at`。这样「回执发出多久后被读」的统计口径不会被重复签收抹掉。

签收使用 `agx_ack_receipt(caller_agent_id=..., task_run_uuid=...)`，只能签收发给自己的回执。签收动作会把 `needs_reply` 推进到 3，看门狗据此认定事项处理完毕。

未签收的回执会重发：`receipt_resend_after` 默认 600 秒，最多重发 2 次，即含首次共 3 次。

### 5.8.3 三种结束方式的区别

| 动作 | 作用对象 | 效果 |
|---|---|---|
| 结案 `agx_finish_task` | 本人的执行 | 写状态、结果、总结，释放会话 |
| 签收 `agx_ack_receipt` | 收到的回执 | 标记已读，关闭回执跟踪 |
| 催办 `agx_nudge_execution` | 卡住的执行 | 不建执行、不改状态，只推一把 |

## 5.9 执行现场

一次执行保存三类内容：

| 字段 | 说明 |
|---|---|
| `exec_instruction` | 本次执行交给 Agent 的完整指令 |
| `summary` | 执行方写回的人类可读结论 |
| `result_data` | 结构化结果，通常为 JSON |
| `exec_name` | 本次执行的短标题，一句话说明这一次在做什么 |

`exec_name` 是实例级描述，区别于任务的 `description`（类型级描述）。列表中出现多条同名任务时，靠它区分。

<details>
<summary>agx_task_execution 主要字段</summary>

| 字段 | 说明 |
|---|---|
| `task_run_uuid` | 本次执行唯一 ID |
| `task_id` | 关联的任务 |
| `agent_id` | 执行方 |
| `send_agent_id` | 发起方 |
| `send_session_id` | 发起方会话，用于回执归位 |
| `host_id` / `project_id` | 执行主机与所属项目 |
| `plan_id` | 所属计划，非计划任务为空 |
| `prev_task` / `next_task` | 前置与后置执行，用于串行链 |
| `parent_run_uuid` | 父执行，用于子任务追踪 |
| `session_id` | 执行落在哪个会话 |
| `run_status` | 状态 |
| `dispatched_at` | 真正发往执行方的时刻，重发判定基线 |
| `started_at` | 开始时间，懒写，可能为空 |
| `finished_at` | 完成时间 |
| `scheduled_at` | 期待运行时间，到期后由看门狗激活 |
| `timeout_seconds` | 超时时间 |
| `needs_reply` | 回执需求 0–3 |
| `needs_confirm` | 人放行 0–3 |
| `alert_level` / `alert_count` / `wake_count` | 提醒等级与次数 |
| `alerted_at` / `alert_reason` / `alert_target` | 最近一次提醒的时间、原因、收件人 |
| `quick_cmd_id` / `quick_cmd_params` | 由哪条快捷指令派发，及本次参数 |
| `total_tokens` / `input_tokens` / `output_tokens` | token 消耗 |
| `diag_info` | 诊断信息 |

</details>

## 5.10 执行操作

| 方法 | 用途 |
|---|---|
| `agx_dispatch_task` | 创建执行并立即投递 |
| `agx_schedule_task` | 只创建执行记录，状态为 `pending`，不投递 |
| `agx_activate_task` | 把 `pending` 的执行激活并投递 |
| `agx_get_execution` | 查询执行详情，含前置执行上下文与子执行列表 |
| `agx_finish_task` | 结案，写状态、结果、总结 |
| `agx_update_execution` | 更新执行属性，按身份分级授权 |
| `agx_continue_task` | 在原会话基础上续接，不新建会话 |
| `agx_confirm_task` | 对 `awaiting_confirm` 的执行放行或驳回 |
| `agx_nudge_execution` | 人工催办，不建执行、不改状态 |
| `agx_ack_receipt` | 签收回执 |
| `agx_ack_notice` | 签收通知事件 |
| `agx_task_events` | 查看某次执行的唤醒、告警、回执、签收时间线 |
| `agx_running_tasks` | 查看当前运行中的执行及已运行时长 |

`agx_update_execution` 的写入权限按字段区分：

| 字段类别 | 可写入的身份 |
|---|---|
| `timeout_seconds`、`exec_instruction`、`alert_note`、`scheduled_at`、`x_desc` | 执行方、发起方、项目管理员、全局角色 |
| `session_id`、`send_agent_id`、`prev_task`、`next_task`、`plan_id`、`exec_name`、`reply_read` | 执行方、项目管理员、全局角色 |

发起方可以修改执行方式与补充结论，不能修改派给谁、挂在哪条链上。

## 5.11 不适用的情况

- **只是说一句话、不需要留痕**：使用 `agx_send_text`，不产生执行记录。
- **为了显示进度而重复派发同一条任务**：会造成多条执行并行，结果相互覆盖。需要推进时使用 `agx_nudge_execution`。
- **手工修改 `run_status`**：该字段只能由结案方法变更，手工修改后的状态看门狗不识别。
- **用 `started_at` 判断是否执行过**：该字段懒写，可能为空。判断依据应为 `finished_at` 与 `summary`。
- **长时间依靠人工观察长任务**：设置合理的 `timeout_seconds`，由看门狗分四档介入。人工催办是最后一道。
