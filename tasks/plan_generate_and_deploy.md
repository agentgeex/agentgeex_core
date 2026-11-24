---
reply_mode: result
task_type: plan
---

# 任务：Plan 生成与部署 (plan_generate_and_deploy)

## 概述

你是 PM（Project Manager）。收到高层指令后，在 **独立 session** 中完成 plan JSON 的生成与部署。

**关键设计理念**：
- 本 task 设置了 `dedicated_session=1`，每次执行都在**全新的独立 session** 中
- 主 session 只需 dispatch 本 task，不参与 JSON 生成过程
- 避免主 session 上下文膨胀导致 token 超限

## 执行流程

### Step 0: 读取协议

读取文件 `{work_dir}/docs/internal/project-management.md`
> 注意：调用 `agx_get_global_config(caller_agent_id=<你的 AGX_AGENT_ID>)` 获取 `work_dir`

从 PM 协议理解 JSON Plan schema 和部署接口。

### Step 1: 读取领域 task（如有）

如果 instruction 中指定了 `domain_task`（如 `book_project_orchestrate`），先用 `agx_get_task_by_name` 读取该领域 task 的完整内容，严格按照其中的管线规则生成 plan。

如果 instruction 中没有指定领域 task，则按通用项目管理规则编排。

### Step 2: 查团队能力

从 instruction 中提取涉及的 agent 列表，或从领域 task 中获取标准团队，然后：

```
agx_list_agent_tasks(agent_id="xxx")
```

获取每个 Agent 的 task_id 和 task_name，供 plan 中的 `task_id` 引用。

### Step 2.5: 🔴 判定是否需要层级计划

**拆解目标前，先自问：这个目标是否过于庞大，单层计划无法承载？**

判断标准（满足任一即应使用层级计划）：

| 信号 | 说明 |
|------|------|
| 目标包含多个独立子目标，每个子目标本身需要多步流水线 | 如"创作10章"，每章都需要"拆分→写作→审校"三个步骤 |
| 单层拆解会导致 15+ 个 step | 平铺式拆解 -> 上下文溢出 -> 丢失信息 |
| 已有验证过的子管线模式 | 如每章创作流水线已验证，复用即可 |

层级计划 vs 单层计划：

```
❌ 单层平铺（step 过多，难以管理）：
  Plan_A
  ├── ch01_拆分 ├── ch01_写导语 ├── ch01_写1.1 ├── ch01_写1.2 ├── ch01_审校
  ├── ch02_拆分 ├── ch02_写导语 ├── ch02_写2.1 ├── ch02_写2.2 ├── ch02_审校
  └── ... (200+ steps)

✅ 层级计划（子管线复用，清晰可控）：
  Plan_A（父计划 — hub）
  ├── ch01: plan_execute → 生成并执行 Plan_ch01（拆写审）
  ├── ch02: plan_execute → 生成并执行 Plan_ch02（拆写审）
  └── ch03: plan_execute → 生成并执行 Plan_ch03（拆写审）
```

层级计划的 step 使用 `plan_execute` task（`task_type='plan'`），详细写法见 PM 协议 §2.5 模式六。

▶ **如果判定需要层级计划 → Step 3 的拆解按层级方案进行。**

### Step 3: 拆解目标为步骤链

根据 instruction 中的高层目标（如"撰写第1-3章初稿"），拆解为有序步骤。

**拓扑模式**（详见 PM 协议）：
- **串行**：`prev_task` — 看门狗等前置完成后激活
- **扇出并行**：`parent_run_uuid` + `children` — 父完成后子并行激活

> 🔴 **互斥规则：`prev_task` 和 `parent_run_uuid` 不能同时设在一个 step 上。**
> - 子任务（有 `parent_run_uuid`）= 父的分支，依赖父激活
> - 串行任务（有 `prev_task`）= 独立的链，等前一个完成
> - 一个 step 要么是某人的孩子，要么在某人的后面，不能两者都是。
- **扇入汇聚**：多个步骤设同一个 `next_task`

### Step 4: 预生成 run_uuid

```
agx_generate_uuid(prefix="run", infix="split_ch01")
agx_generate_uuid(prefix="run", infix="write_010100")
...
```
>注意：agx_generate_uuid 支持批量生成，如果需要生成多个run_uuid，可以一次性批量生成，避免重复调用

### Step 5: 🔴 确定 send_session_id

**每次收到 task 消息，消息体中都包含 `task_run_uuid`。**

部署 plan 时，把收到的 `task_run_uuid` 传给 `agx_plan_deploy`：

```
agx_plan_deploy(plan_id=xxx, task_run_uuid=收到的task_run_uuid)
```

`_deploy_executions` 内部通过 `task_run_uuid` 查 execution 记录获取实际 session_id。
查不到则留空，走默认路由。

如果在对话中直接发起 plan deploy（非 task 消息触发），没有 `task_run_uuid` 则不传该参数，留空走默认。

### Step 6: 拼装 JSON plan

🔴 **如果 plan 较大（10+ step），必须使用文件模式，避免 prompt 溢出：**

**文件模式 Step 5a：分批写入 plan 文件**

```bash
# 初始化
python3 tools/json_ops.py write /tmp/plan_output.json \
  --data '{"plan_id":"plan_xxx_...","project_id":"proj_xxx","description":"...","steps":[]}'

# 逐 step 追加（用 stdin 传 JSON 避免空格问题）
echo '{"step_name":"step1","task_id":"task_xxx","instruction":"...","timeout_seconds":600}' | \
  python3 tools/json_ops.py append /tmp/plan_output.json --path '$.steps'
# ... 继续追加

# 最后校验
python3 tools/json_ops.py validate /tmp/plan_output.json
```

**Step 5b：创建 + 部署**

```
agx_plan_create(plan_json=读取 /tmp/plan_output.json 的内容)
agx_plan_deploy(plan_id=返回的 plan_id, task_run_uuid=收到的task_run_uuid)
```

### Step 7: 创建 + 部署

> 链式调用确保 plan 先创建为 draft，再部署 execution。如果 plan 已有错误可单独取消，不影响 execution。

**小 plan（< 10 step）可直接在 prompt 中拼装 JSON：**

按 PM 协议 schema 组装。注意：
- `plan_id` 要有意义的前缀（如 `plan_write_ch01_xxxxx`）
- `project_id` 从 `agx_get_global_config` 获取
- 每个 step 的 `instruction` 要写清具体做什么
- `timeout_seconds` 按任务类型设置合理值
- 写作类任务设 `needs_reply=1`

### Step 7: 创建 + 部署

```
agx_plan_create(plan_json=组装好的 JSON)
agx_plan_deploy(plan_id=返回的 plan_id, task_run_uuid=收到的task_run_uuid)
```

### Step 8: 激活（可选）

如果 instruction 要求立即执行：

```
agx_activate_plan(plan_id=生成的 plan_id)
```

### Step 9: 回写结果

```
agx_finish_task(
  status="succeeded",
  summary="plan_{xx} 已部署并激活，共 {N} 个 step，入口: run_xxx"
)
```

## 输入格式

`instruction` 建议为结构化文本：

```
目标：撰写第1-3章的初稿
领域 task：book_project_orchestrate
项目 ID：proj_xxx
书稿根目录：/path/to/book
选项：部署后即刻激活
```

## 超时设置

dispatch 本 task 时建议 `timeout_seconds=1800`（30分钟），因为涉及：
- 读取多个 task 文件
- 生成 run_uuid
- 拼装较大 JSON
- 调用 deploy

## 职责边界

| ✅ 本 task 做 | ❌ 本 task 不做 |
|-------------|---------------|
| 读 PM 协议 + 领域 task | 不手动调度（不调 schedule_task / activate_task） |
| 拆解目标为步骤链 | 不跟踪执行进度 |
| 生成完整 JSON plan | 不代替领域 Agent 干活 |
| 调用 agx_plan_deploy | 不创建新的 task 定义 |

## 与领域 task 的关系

本 task 是**纯编排引擎**，领域 task（如 `book_project_orchestrate`）提供管线规则。

调用链：
```
主 session（super_pm 主循环）
  → dispatch plan_generate_and_deploy (dedicated_session=1)
    → 独立 session 读取领域 task 内容
    → 按领域规则生成 JSON
    → agx_plan_deploy(send_session_id=自己的 session)
    → 激活 → 回写结果
```
