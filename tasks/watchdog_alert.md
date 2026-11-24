---
reply_mode: confirm
---

# 看门狗报警处理 — Watchdog Alert

## 概述

由看门狗（`agx_watchdog`）发出的任务执行异常报警。看门狗发现某个 execution 超时或异常后，
通过 `agx_dispatch_task` 将报警投递到你（原始任务的发起者）的队列中。

**你是最关心这个任务的人，也是最适合判断它到底死没死的人。**

## ⚠️ 先看 alert_type —— 有两类报警

报警的 `exec_instruction` 是 JSON，第一件事看 `alert_type`，**两类处理方式完全不同**：

| alert_type | 含义 | 去哪 |
|---|---|---|
| `watchdog` | 某个 execution 超时/异常 | 本文档 Step 1 ~ Step 7 |
| `host_offline` | **主机失联**（多主机保活） | 跳下方「主机失联报警」章节 |

```json
// 主机失联报警长这样
{
  "alert_type": "host_offline",
  "target_host_id": "test_srv_a",
  "target_host_alias": "test_srv_a",
  "offline_seconds": 200,
  "reason": "主机失联（200s 无心跳，阈值 90s）",
  "detected_by": "test_host_mac",
  "escalate": false
}
```

## 流程总览

```
收到报警 → Step 1 解析报警参数
         → Step 2 现场调查 (agx_get_execution → diag_info)
         → Step 3 判断真死/假死
         → Step 4 真死：标记失败
         → Step 5 假死/无问题：回滚 run_status 为 succeeded
         → Step 6 假死处理（无法确认状态）
         → Step 7 结案
```

## 处理流程

### Step 1: 解析报警内容

从 `agx_get_execution(task_run_uuid=<本 execution 的 task_run_uuid>)` 中读取 `exec_instruction` 字段，它是 JSON：

```json
{
  "alert_type": "watchdog",
  "target_run_uuid": "run_xxx",
  "target_task_id": "task_xxx",
  "reason": "超时（已运行 345s，限制 900s）",
  "elapsed_seconds": 345,
  "timeout_seconds": 900
}
```

**关键字段说明：**
- `target_run_uuid`：出现问题的 execution ID，后续用 `agx_get_execution` 查它的详情
- `target_task_id`：原始 execution 的 task_id（可用于重试）
- `reason`：看门狗判断的报警原因

### Step 2: 现场调查

**3.1 读诊断快照：**
```
agx_get_execution(caller_agent_id=<你的 agent_id>, task_run_uuid=<target_run_uuid>)
```
- `run_status`：仍是 `running` → 继续诊断；`succeeded`/`failed` → 假死，任务已自行完成
- **`diag_info`**：看门狗采集的诊断快照，结构如下：

```json
{
  "platform": "openclaw",
  "captured_at": "2026-05-26 19:00:00",
  "reason": "超时（900s）",
  "elapsed_seconds": 950,
  "timeout_seconds": 900,
  "log_tail": "[最后30行日志]",
  "template_file": "/Users/xxx/.agx/docs/internal/watchdog-openclaw.md",
  "live": {
    // 平台相关 — 参照 platform 补充文件
  }
}
```

**3.2 读平台补充文件（如有）：**
如果 `diag_info.template_file` 非空，**直接用它给出的绝对路径**调 `read` 工具打开它，
理解 `live` 字段的当前平台含义。

> 该字段是**绝对路径**（如 `/Users/xxx/.agx/docs/internal/watchdog-openclaw.md`），
> 不受你的 workspace 沙箱限制，直接 read 即可。
> 若为空串，说明该平台没有补充文档，跳过此步。

**3.3 读 Agent 最后输出：**
- 优先看 `diag_info.live.gateway_session.last_messages` — Agent 最后几条消息，直接展示 Agent 在做什么
- `diag_info.log_tail` 可作为补充 — 看门狗采集的日志尾部 30 行

### Step 3: 判断真死假死

以 `diag_info.live.gateway_session.status` 为核心，结合 `last_messages` 诊断：

| 场景 | gateway_session.status | 证据 | 判断 | 动作 |
|------|----------------------|------|------|------|
| execution 已经是 succeeded/failed | — | run_status 非 running | **假死** | 跳到 Step 4 回滚 |
| 🔴 超时 + task_type=plan + session 仍在运行 | `running` | plan 类任务本身执行时间长 | **假死**（正常慢） | 跳到 Step 3.1 延长超时 |
| session 活跃且输出有进展 | `running` | last_messages 内容不重复，推理继续 | **假死**（慢） | 通知人类，跳到 Step 4 回滚 |
| session 活跃但循环输出 | `running` | last_messages 最后几条内容相同 | **真死**（死循环） | 跳到 Step 4 |
| session 已完成但未 finish | `done` | last_messages 显示正常完成 | **假死**（忘调 finish） | 跳到 Step 4 回滚 |
| session 已完成但有异常 | `done` | last_messages 显示错误/异常 | **真死**（异常退出） | 跳到 Step 4 |
| session 已断开 | `not_found` | session 不存在 | **真死**（进程崩溃/退出） | 跳到 Step 4 |
| 无 diag_info 或 live 为空 | — | `agx_get_execution` 返回无现场信息 | **真死**（执行中断） | 检查下方「无现场信息」指引 → 跳到 Step 4 |

### Step 3.1: 🔴 延长超时（plan 类任务）

如果 target execution 的 **task_type 为 `plan`**，且 `diag_info.reason` 为超时，但 `gateway_session.status` 为 `running`（session 仍在运行）：

```
agx_get_task(task_id=target_task_id)  # 确认 task_type='plan'
```

确认后，延长超时时间。估算公式：
- 查询 `agx_plan_query(plan_id=target_execution的plan_id)` 获取剩余 pending step 数量
- `新超时 = 当前 elapsed × 2` 或 `剩余 step 数 × 300s`，取较大值

```
agx_update_execution(
    task_run_uuid=<target_run_uuid>,
    updates={"timeout_seconds": 新超时值}
)
```

然后在本 execution 的 `x_desc` 备注：`"看门狗报警 — plan 类任务超时 — 已延长至 {新超时}s"`

>  这步只对 plan 类任务生效。普通超时仍然走假死判断逻辑。

**无现场信息的处理：**

如果 `agx_get_execution` 返回的 `diag_info` 为空，说明任务已中断且没有留下现场。两种可能：
1. **Agent 已完成但没及时 finish** — 检查 `result_data`、`summary` 字段，如有内容说明任务实际完成了 → 跳到 Step 4 回滚
2. **任务确实中断了** — `result_data` 也为空 → 以下字段可帮你推断：
   - `finished_at`：有值说明任务曾经结束过
   - `ws_delivery_status`：`delivered` / `timeout` / `error` 说明送达层的结果
   - `started_at`：结合当前时间可算任务卡了多久

确认中断后，标记 target execution 为 failed：

```
agx_finish_task(
    task_run_uuid=<target_run_uuid>,
    status="failed",
    summary="看门狗报警 — 无现场信息 — 任务已中断（started_at={时间}，finished_at={值}，ws_delivery_status={值}）"
)
```

上游（人类或 PM）收到 failed 回执后自行决定是否重新 dispatch。

### Step 4: 处理真死

#### 5.1 失败分类

| 失败类型 | 日志特征 | 处理 |
|---------|---------|------|
| API 临时故障 | `429` / `503` / `rate limit` | 标记 failed + 通知人类 |
| 网络临时故障 | `connection refused` / `timeout` / `DNS` | 标记 failed + 通知人类 |
| OOM / SIGKILL | 进程消失 + 日志干净截断 | 标记 failed + 通知人类 |
| 部署/注册失败 | 自检步骤报错 | 标记 failed + 通知人类 |
| 代码 Bug | `Traceback` / `AttributeError` / `ImportError` | 标记 failed + 通知人类 |
| 认证过期 | `401` / `token expired` / `unauthorized` | 标记 failed + 通知人类 |
| 磁盘不足 | `No space left` / `disk quota` | 标记 failed + 通知人类 |

#### 5.2 标记失败

```
agx_finish_task(
    task_run_uuid=<target_run_uuid>,
    status="failed",
    summary="看门狗报警 — {失败类型} — {日志关键信息}"
)
```

summary 必须写清失败原因和日志关键信息，方便人类或上游 PM 决定是否重试。

### Step 5: 回滚 run_status（假死 / 无问题）

**当确认被报警的 execution 实际上没问题时（假死），必须主动把 run_status 回滚。**

看门狗不会自动修改 target execution 的状态，这项工作由你完成：

```
agx_finish_task(
    task_run_uuid=<target_run_uuid>,
    status="succeeded",
    summary="看门狗报警 — 经诊断任务已自行完成 — 回滚状态为 succeeded"
)
```

**为什么必须做这一步？**
- 测试或边界场景下，run_status 被手动改为 `running` 后看门狗触发报警
- 实际任务早已完成（run_status 与实际不符）
- 不回滚会导致 run_status 永远停留在异常状态

### Step 6: 假死处理（无法确认状态）

当无法确定真死假死，但 target execution 仍在 `running` 时：

无需 `agx_finish_task`（任务可能还在跑）。更新 `x_desc` 备注即可。

### Step 7: 结案

```
agx_finish_task(
    task_run_uuid=<本报警的 task_run_uuid>,
    status="succeeded",
    summary="已分析 target={target_run_uuid}，判断：{真死/假死}，处理：{...}"
)
```

## 异常场景

| 异常 | 处理 |
|------|------|
| 查不到 target execution | 可能已被处理或不存在，succeed 结案 |
| 无法读日志 | 标记 failed，summary 注明 |
| 自己分析不清 | 通知人类，summary 写清已知信息 |

---

## 主机失联报警（alert_type = host_offline）

> 2026-10-25 多主机改造新增。这是**网络级**报警，不是单个任务的问题。

### 背景

一台机器一只看门狗。每只狗都巡检**全网**主机心跳（`agx_host_inventory.last_heartbeat`），
因为核心机自己挂了只能靠别的机器发现。

多只狗会同时发现同一台主机失联，靠 Redis 分布式锁 `agx:lock:host_alert:{host_id}`
保证**只有一个狗发报警**（锁 TTL = 报警冷却期，默认 10 分钟）。所以收到这条报警时，
可以确定：**当前网络上没有第二只在重复报同一台主机**。

### 处理流程

#### Step H1: 确认真实状态

```sql
SELECT host_id, host_alias, endpoint_ip, presence_status,
       last_heartbeat, TIMESTAMPDIFF(SECOND, last_heartbeat, NOW()) AS idle_sec
FROM agx_host_inventory WHERE host_id = '{target_host_id}'
```

判断：

| 现象 | 结论 |
|---|---|
| `idle_sec` 已回到阈值内（<90s） | **误报**（网络抖动后已恢复）→ 直接结案 |
| `idle_sec` 仍超阈值 | 真实失联 → 继续 Step H2 |

#### Step H2: 判定影响面

查该主机上的 agent 和运行中的任务：

```sql
SELECT agent_id, agent_role FROM agx_agent_registry
WHERE host_id = '{target_host_id}' AND active_status = 1;

SELECT task_run_uuid, agent_id, started_at, exec_instruction
FROM agx_task_execution
WHERE host_id = '{target_host_id}' AND run_status = 'running';
```

#### Step H3: 处置原则（**重要**）

**失联主机上的 running 任务：一律不动。**

理由：机器失联 ≠ 任务死了。可能只是网络抖动、断电重启、或临时断网。
误杀（标 failed）的代价远高于等待——任务活过来会自己结案。

唯一例外：`escalate=true`（失联超过 1 小时）→ **通知人类**，由人决定是否清理。

#### Step H4: 结案

```python
agx_finish_task(
    task_run_uuid="<本 execution>",
    status="succeeded",
    summary="主机 {target_host_id} 失联：idle={offline_seconds}s，"
            f"影响 agent {N} 个、running 任务 {M} 个。"
            f"按策略不处置（等其恢复）{'；已升级人工' if escalate else ''}"
)
```

### 禁止事项

- ❌ 不要批量把失联主机上的任务标记 `failed` —— 会误杀真在跑的任务
- ❌ 不要尝试通过 WS 唤醒失联主机的 agent —— 连不上，纯浪费时间
- ❌ 不要删除 `agx:lock:host_alert:{host_id}` —— 删锁会导致下一轮重复报警
