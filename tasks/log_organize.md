---
reply_mode: result
---

# 日志整理任务 — Log Organize

## 概述
**把项目组内所有 Agent 的工作现场（`execution.summary`）聚合、过滤、成文的汇总层任务。**

这是「分发模式」闭环的最后一环：
```
worker / PM 干活  →  execution.summary（素材层，天然存在）
                          ↓
               ★ 本任务（汇总层）★
                          ↓
           docs/agent_docs/daily/YYYY-MM-DD.md（成文/对外唯一窗口）
```

**定性（务必理解，防止退化成"顺手做的日志功能"）：**
日志不是可选的附加功能，而是**分发模式的必要回路** —— 工作离开主会话后，
PM 只能靠汇总 `execution.summary` 才能重新获得全局视野。
本任务是该回路的执行载体。

## 核心职责（四步）
1. **收集**：拉取指定时间窗内、本项目组所有 Agent（含 PM 自己）的 `execution.summary`
2. **过滤**：剔除低信息量条目（看门狗提示、回执流水、链路确认等合规但无内容的 summary）
3. **分组**：按主轴（时间）+ 切片（plan / agent / 会话）组织
4. **成文**：输出人类可读的当日（或指定窗口）日志

## ★ 权限约束（本任务仅限 PM）
- **只有 `role = project_manager` 的 Agent（项目管理员）可调用本任务**
- 非 PM（worker 等）调用 → **直接拒绝**，不降级、不代理
- 依据：日志汇总发生在 PM 层（见 AGENTS.md 职责分层：worker 只产 summary，不写日志）
- 因此调用方的 `project_id` 即汇总范围，**不做 `scope=self` 降级**

---

## Step 1: 解析参数

从 `exec_instruction` 解析参数（JSON）：
```json
{
  "date": "2026-09-14",
  "project_id": "proj_xxx",
  "window": ["2026-09-14 00:00:00", "2026-09-14 23:59:59"],
  "scope": "project",
  "filter_mode": "auto",
  "output": "daily"
}
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `date` | 当天 | 目标日期（用于 daily 文件命名与默认时间窗） |
| `project_id` | 调用方自身绑定项目 | 汇总范围（**必须是自身所属项目组**；仅 PM 可指定） |
| `window` | `[date 00:00:00, date 23:59:59]` | 时间窗（可跨天，用于补录） |
| `scope` | `project` | `project`（本项目组）/ `self`（仅自己） |
| `filter_mode` | `auto` | `auto`（自动过滤低信息量）/ `none`（全量） |
| `output` | `daily` | `daily`（写入 daily 文件）/ `report`（仅回写 summary） |

**约束：范围校验**
- **前置**：调用方必须是 PM（`role = project_manager`），否则直接拒绝（见上方权限约束）
- `project_id` 留空时自动取调用方自身 `project_id`
- **汇总范围 = 调用方所属项目组，不跨项目**（权限 + 信息边界；全平台汇总应由 `agx_core` 承担）

---

## Step 2: 收集素材（execution.summary）

**事实来源 = `agx_task_execution` 表**（MySQL），按 `project_id` + `started_at`/`finished_at` 落在时间窗内筛选。

```
agx_running_tasks 不适用（只查 running）；本步骤用 MySQL 只读查询：
-- 推荐：LEFT JOIN agx_task_base 以在 SQL 层排除 R0/R5
SELECT e.task_run_uuid, e.agent_id, e.task_id, e.session_id, e.exec_name,
       e.run_status, e.send_agent_id, e.started_at, e.finished_at,
       e.duration_seconds, e.plan_id, e.prev_task, e.summary
FROM agx_task_execution e
LEFT JOIN agx_task_base t ON e.task_id = t.task_id
WHERE e.project_id = '<project_id>'
  AND e.finished_at >= '<window_start>'
  AND e.finished_at <= '<window_end>'
  AND e.run_status IN ('succeeded', 'failed')
  AND e.summary IS NOT NULL AND e.summary <> ''
  AND (t.task_labs IS NULL OR t.task_labs NOT IN ('log_organize', 'watchdog_alert'))
ORDER BY e.finished_at ASC
```

> **R0/R5 在 SQL 层就排除**（`LEFT JOIN agx_task_base` 取 `task_labs`）：
> 上述 `AND (t.task_labs IS NULL OR t.task_labs NOT IN ('log_organize','watchdog_alert'))`
> 即把「本任务自身」（R5）与「看门狗提示」（R0）在取数阶段就剔除。
> 若因权限/字段原因无法 join，则在内存过滤阶段用 R0/R5 补充拦截（结果等价）。

> ⚠️ **时间字段选择**：以 `finished_at`（现场结束时刻）归窗，而非
> `session_last_active_at`（那是 Redis 缓存的会话最后活跃时间，会被后续执行刷新，
> 用它会把不同快照的时间搞混 —— 09-14 实测踩坑）。

辅助：如需按 agent / 会话再看细节，可配合
`agx_list_project_agents(project_id)`、`agx_project_sessions(project_id)`。

---

## Step 3: 过滤低信息量条目（技术核心）

**问题实测**：09-13 PM 名下 10 条 execution 中，6 条（60%）是 `agx_core` 的
「第 N 轮回执确认，链路正常」类流水 —— 合规、有 summary，但信息量极低。
不过滤 → daily 里 60% 是废话。此外，看门狗的超时提示、过期提醒等也是
高频低信息量来源，同样不参与统计。

**过滤规则（`filter_mode=auto`）：**

| 规则 | 判据 | 处理 |
|------|------|------|
| **R0 看门狗提示** | 来自看门狗的消息（task 来源匹配 `watchdog_alert` / 看门狗相关 task，如超时提醒、过期提醒） | **不参与统计**，归并计数，不展开 |
| R1 回执流水归并 | summary 含「回执确认 / 链路正常 / 第 N 轮」等，且多条同质 | 合并为一句「N 条回执流水（已归并）」 |
| R2 极短 summary | summary 长度 < **40 字**且无实质结论 | 计入"其他"计数，不展开 |
| R3 测试/验证类 | `exec_name` 含「验证 / 测试」**且 summary 本身不含决策/产出结论**（判据独立可得，不依赖“后续是否覆盖”） | 归入"过程性验证"计数，不展开 |
| R4 重复内容 | 与已收集条目高度相似 | 去重 |
| **R5 自引用排除** | `task_labs = 'log_organize'`（本任务自身产生的 execution，含 PM 跑的那条） | **不纳入素材**，不进统计、不入日志 |

> **R0 判定方式**：优先按 **task 来源**（`task_labs` / `task_id` 匹配 `watchdog_alert`，或 `send_agent_id` 为看门狗）识别，比按 summary 文本匹配更稳。
>
> **R3 收窄说明（09-14）**：原判据含「且已被后续结论覆盖」—— 该条件**不可机械判定**（需读未来 execution，且“覆盖”无客观标准），执行时只能靠猜，易造成假阴/假阳。**已收窄为**：仅看本条 summary 自身是否含决策/产出结论。有结论的验证（如“验证通过，方案定 A”）按正常条目保留；纯过程性验证（“跑了一遍，无异常”）归并计数。
>
> **R5 为何必要（自包含悖论）**：`log_organize` 由 PM 自己执行，其产出的 `execution.summary` 同属"本项目组所有 Agent 的 execution"。若纳入素材，则每次汇总都会记录“上次汇总了日志”→ **自我引用套娃**，日志越滚越胖。故本任务自身的 execution **一律不纳入**（与 R0 同类：把"元操作"从素材中剔除）。

**保留优先级**（信息量从高到低）：
1. 决策类（含"定了什么/决策/方案"）
2. 踩坑类（含"问题/原因/解决"）
3. 工作摘要类（含"完成/产出/commit"）
4. 快照笔记类（`agx_note_main_session` 产出，主会话现场）
5. 回执流水类（最低，归并计数）

> 过滤是**工程细节**，目标是可读性，不改变机制成立性。
> 若无法判断，**宁可保留**（保留原文 + 归并，不静默丢弃）—— 日志漏记的代价 > 冗余。

---

## Step 4: 分组与成文

**主轴 = 时间**（daily 天然是时间窗）；**切片 = plan / agent**（两者正交，配合信息最全）。

输出 markdown 模板：

```markdown
# YYYY-MM-DD 工作日志（<项目名>）

> 汇总范围：<project_id> 项目组，共 N 个 Agent
> 时间窗：YYYY-MM-DD HH:MM ~ HH:MM
> 素材：X 条 execution.summary（过滤后 Y 条）

## 一、重要决策
- [HH:MM] {agent}: {决策内容及理由}

## 二、踩坑与解决
- [HH:MM] {agent}: {问题} → {原因} → {解决}

## 三、工作产出
- [HH:MM] {agent}: {工作内容概要}（{commit/产出}）

## 四、主会话快照（决策现场）
- [HH:MM] {快照要点}

## 五、计划进展
- {plan_id / 名称}: {已完成 steps}/{总数}

## 六、过程性记录（已归并）
- 回执流水 {N} 条；验证/测试 {M} 条

## 统计
- 涉及 Agent：[agent_a, agent_b, ...]
- execution 总数：X（过滤前）→ Y（过滤后）
- 时间窗：YYYY-MM-DD HH:MM ~ HH:MM
```

---

## Step 5: 落地输出

**`output=daily`（默认）：**
写入 `docs/agent_docs/daily/YYYY-MM-DD.md`（**项目对外日志唯一权威窗口**）。
- 文件已存在 → **追加/合并**，不覆盖（当天多次汇总时）
- 头部标题规范：`# YYYY-MM-DD 工作日志（<agent_id>）`

### ★ 追加写入规则（17:00 后新增，防章节号打架）

当天多次汇总时，daily 会**逐轮追加**。历史分节**原样保留，绝不重排/重编号**，但须遵守以下规则：

**规则 A（编号续接）：**
追加前**先扫描文件内已存在的编号小节标题**（形如 `## 一、` `## 二、` …），
取当前**最大编号 N**，本次追加的编号小节**从 N+1 起续编**。
- 中文数字序列：`一 二 三 四 五 六 七 八 九 十 十一 十二 …`（超十后用阿拉伯数字继续）
- **禁止**因新增小节而回头改历史小节的编号（那会破坏"追加不覆盖"承诺）
- 若文件为空/无编号小节 → 从 `一、` 起

**规则 B（非编号小节允许重复，但须带限定语）：**
`统计` / `待办` / `执行说明` 等**不需要唯一编号**的小节，允许在同一天出现多次，
但**每一处必须带限定语**标明当时口径，避免读者误以为是并列的唯一小节。
- 写法：`## 当日统计（截至 21:55）` / `## 待办（更新至 19:45）` —— 括号内写时间或口径
- **禁止**出现两个语义相同、又都无限定语的同名小节

**规则 C（追加分隔）：**
每轮追加的起始，用一条**分节线 + 轮次说明**（引用块）与上文隔开，例如：
```
---

## 下午追加：<主题> —— <时间区间>

> 本节由 `log_organize`（`output=daily`）追加写入。当天多次汇总采用**追加/合并**策略，保留上文，不覆盖。
> 汇总范围：<project_id> 项目组，共 N 个 Agent
> 时间窗：YYYY-MM-DD 00:00 ~ 23:59
> 素材：本轮 X 条 execution.summary（过滤后 Y 条）
```

> **为什么是「续编号」而非「全局重编号」**：重编号需改写历史分节、破坏追加承诺，且依赖解析整个文件重排，脆弱易错；续编号只往后接、机械可判定、历史零改动。两者信息量等价，故选续编号。

**`output=report`：**
不写文件，仅把成文结果回写到 `agx_finish_task` 的 `summary`。

---

## 结束流程

```
agx_finish_task(
    task_run_uuid=<本次 run_uuid>,
    status="succeeded",
    result=json.dumps({"file": "<daily 路径>", "counts": {...}}),   # 结构化统计
    summary=<人类可读的成文日志 / 或摘要>
)
```

失败时 `status="failed"`，`summary` 说明原因（如素材为空、时间窗无数据）。

---

## 触发方式（三种都支持，不锁死）

| 方式 | 说明 | 阶段 |
|------|------|------|
| 手动 dispatch | PM 直接 dispatch 一条，验证机制 | ✅ **当前采用** |
| `agx_schedule_task` 定时 | 每天固定时间自动跑（独立会话） | ⏸ 暂不着急（排后面） |
| 看门狗 | 未来由看门狗定时触发 | 远期 |

> 建议本任务使用独立会话（`dedicated_session`），支持与其他任务并发、上下文隔离。

---

## 适用场景
- PM 每天定时汇总本项目组日志（daily）
- 项目复盘：按 plan 切片看一个计划的完整闭环
- 补录：指定 `window` 回填某天/某时段的日志

## 不适用（边界）
- ❌ 跨项目 / 全平台汇总 → 应由 `agx_core` 承担
- ❌ 单个 execution 的详细展开 → 用 `agx_get_execution` 直接查
- ❌ worker 自己调用做"个人日志" → worker 不写日志，只写 summary（见 AGENTS.md 职责分层）

---

## 设计定稿（老冯 2026-09-14 12:58 拍板）
1. ✅ **task 名称**：`log_organize`（与 09-13 已存在会话名对齐）
2. ✅ **触发方式**：当前**手动 dispatch 验证**；定时**暂不着急**（排后面）
3. ✅ **过滤阈值**：R2 极短 summary 阈值为 **40 字**
4. ✅ **输出落地**：daily **追加/合并** 分节策略采用（当天多次汇总时合并，不覆盖）
5. ✅ **权限**：**仅 PM 可用**（`role=project_manager`），非 PM 直接拒绝
6. ✅ **过滤新增**：**看门狗提示类（超时/过期提醒）不参与统计**（R0）

## 说明书打磨（2026-09-14 18:47）
7. ✅ **R3 收窄**：删去不可机械判定的「且已被后续结论覆盖」—— 改为只看本条 summary 自身是否含结论（判据独立可得）
8. ✅ **R5 自引用排除（自包含悖论）**：`log_organize` 自身产生的 execution 不纳入素材（防自我引用套娃）；与 R0 同类：元操作不算现场

## 追加写入规则（2026-09-15 09:45）
9. ✅ **编号续接（规则 A）**：追加前扫描已有最大编号 N，新小节从 N+1 续编；**禁止重排历史小节编号**（不破坏"追加不覆盖"）
10. ✅ **非编号小节限定语（规则 B）**：`统计`/`待办` 等同名小节允许重复，但每处必须带限定语（如 `## 待办（更新至 19:45）`）
11. ✅ **追加分隔（规则 C）**：每轮追加用「分节线 + 轮次说明引用块」与上文隔开
