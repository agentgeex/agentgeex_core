---
reply_mode: result
---

# 项目审计任务 — Project Audit

## 概述
对 AgentGeeX 网络中的所有项目进行全面审计，检查每个项目的健康状态、资源使用情况和活跃度。

## 使用场景
- 定期巡检（由 HEARTBEAT 触发）
- 人类要求查看项目大盘
- 发现异常前的前置检查

## 审计维度

### 1. 项目基本状态
- 查询 `agx_list_by_network(network_id, "project")` 获取所有项目
- 检查 `active_status` — 是否有项目异常停用？

### 2. Agent 绑定情况
- 对每个活跃项目，调用 `agx_list_project_agents(project_id)`
- 检查：
  - 项目是否有 Agent 绑定？（空项目告警）
  - Agent 的 `active_status` 是否都正常？
  - 是否有 Agent 绑定了多个项目？（资源争用预警）

### 3. 运行任务
- 调用 `agx_running_tasks(project_id)` 检查各项目是否有正在执行的任务
- 标记长期无任务的非活跃项目

## 输出格式
```
 项目审计报告
─────────────────────────────────────
总项目数: {total}
活跃项目: {active}
非活跃项目: {inactive}
空项目（无 Agent 绑定）: {empty}

 详表:
| 项目名 | ID | 状态 | Agent 数 | 运行中任务 | 备注 |
|--------|-----|------|----------|-----------|------|
| ...    | ... |    | 3        | 1         |      |
| ...    | ... |   | 0        | 0         | 空项目 |

 告警项:
- {project_name}: 无 Agent 绑定
- {project_name}: 超过 7 天无活跃任务
```
