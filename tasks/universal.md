---
reply_mode: confirm
---

# 万能任务 - Universal Task

## 概述
通用任务模板，与 general task 的核心区别在于：**本任务在独立会话（dedicated session）中执行**。当 Agent 需要并发执行、定时执行、或上下文隔离的任意任务时，使用本任务。

## 核心机制
- 本 task 无固定功能，具体任务内容通过 `exec_instruction` 动态传入
- `dedicated_session=1`，每次 dispatch 自动创建独立会话
- 多个 universal task 可并行执行，互不干扰
- 任务的详细说明可通过 `agx_update_execution` 写入 execution 的 `x_desc` 字段

## 与 general task 的区别

| 特性 | general task | universal task |
|------|-------------|----------------|
| 会话模式 | 主 session（共享上下文） | 独立 session（上下文隔离） |
| dedicated_session | 0 | 1 |
| 并发支持 | 不支持（共享 session 会冲突） | 支持（每个 execution 独立 session） |
| 定时执行 | 不适合 | 适合（独立 session 互不干扰） |
| 适用场景 | 简单消息处理、通知响应 | 并发任务、定时任务、需要隔离的复杂任务 |

## 执行规范

### 1. 启动流程
收到 universal task 消息后，Agent 应：
1. 通过 `agx_get_execution(task_run_uuid)` 读取本次 execution 详情
2. 从 `exec_instruction` 获取执行指令
3. 从 `x_desc` 获取任务详细说明（如有）
4. 在当前独立 session 中执行任务

### 2. 执行原则
- `exec_instruction` 是本次执行的核心指令，必须遵照执行
- `x_desc` 是任务的补充说明和背景上下文（如有），用于辅助理解指令
- 如 `x_desc` 为空，直接按 `exec_instruction` 执行

### 3. 结束流程
- 执行完毕后通过 `agx_finish_task(task_run_uuid, status, result, summary)` 回写结果
- `status`: succeeded 或 failed
- `result`: 执行产生的数据（JSON 字符串）
- `summary`: 对本次执行的人类可读总结
- 如遇到无法执行的情况，status 设为 failed 并在 summary 中说明原因

## 输入规范
`exec_instruction` 参数应包含：
- `action`: 要执行的动作描述
- `params`: 动作所需的参数（可选）
- `output_format`: 期望的输出格式（可选，默认 JSON）

## 输出规范
执行结果以 JSON 格式返回，包含：
- `status`: succeeded / failed
- `result`: 执行产生的数据
- `summary`: 对人类可读的执行总结

## 适用场景
- 需要并发执行的多个独立任务
- 定时调度任务（配合 `agx_schedule_task`）
- 需要上下文隔离的复杂任务
- 临时性、一次性的定制任务
- 跨项目协作任务

## 自派通道（Agent 给自己派活）
Agent 可通过 `agx_self_dispatch` 给自己派一条 universal 任务，无需 PM 代劳：
- 用于「给自己排后续动作」（如跑完 A 接着做 B）、自派临时巡检/分析/快照
- `agent_id` 硬编码 = 调用方自身，无参数可指定他人（与「指挥他人」的 dispatch 严格区分）
- 每次调用落独立会话 `agent:{aid}:universal:{run_xxx}`，可并发
- 这是 worker 等无 L5 角色的调度出口：给他人发 = 指挥（L5）；给自己发 = 自我管理（L3）
