---
reply_mode: confirm
---

# Agent 配置任务 — Agent Configure

## 概述
更新现有 Agent 的配置。支持修改模型、角色、系统提示词、绑定主机等。

## 前置条件
- 目标 Agent 在 `agx_agent_registry` 中已注册且 active
- 目标 Agent **不能**是 `agx_agentfather` 自己（不自改原则）

## 执行流程

### Step 1: 确认参数
- `agent_id` — 目标 Agent ID
- 需要修改的字段（支持：model, agent_name, agent_role, host_id, system_prompt, active_status）

### Step 2: 验证
- 调用 `agx_get_agent(agent_id)` 确认目标存在且 active
- 如果目标是 `agx_agentfather` → 拒绝，提示"不自改原则，请通过 daemon 或人类修改"

### Step 3: 更新
调用 `agx_update_agent(agent_id, updates)`
- `updates` 包含需要修改的字段和值

### Step 4: 通知
```json
{
  "status": "updated",
  "agent_id": "agt_xxxx",
  "changes": ["model: deepseek-chat → gpt-4o"],
  "updated_at": "2026-05-11T17:00:00+08:00"
}
```

## 支持的更新字段

| 字段 | 说明 | 示例值 |
|------|------|--------|
| agent_name | 改名 | "agx_new_name" |
| agent_role | 变更角色 | "workshop_manager" |
| host_id | 迁移到别的主机 | "new_host" |
| base_model | 换模型 | "gpt-4o" |
| system_prompt | 更新系统提示词 | "你是一个..." |
| active_status | 启用/停用 | "0"（停用）|
