---
reply_mode: confirm
---

# Agent 部署任务 — Agent Deploy

## 概述
调用 `agx_deploy_agent` MCP 工具，将 Agent 配置从 DB 物化到本机文件系统并注册到 OpenClaw。

**一条命令搞定：校验 → 目录创建 → 文件写入 → OpenClaw 注册 → 验证 → 结案。**

## 执行流程

### Step 1: 解析指令
从 `instruction` 中解析参数：

| 参数 | 必填 | 格式 | 说明 |
|------|------|------|------|
| `agent_id` | ✅ | `agent_id=xxx` | 要部署的 Agent ID |
| `force` | ❌ | `force=true` | 强制覆盖，默认 false |
| `task_run_uuid` | ✅ | 见下 | **你本次执行的执行编号**（部署回调的关联键，**必传**） |

示例: `"部署 agent_id=pt2_pm"` 或 `"部署 agent_id=pt2_pm force=true"`

### Step 1.5: 取本次执行的 task_run_uuid（★ 必做）

你本次执行编号就在 `exec_instruction` 顶部【会话上下文】块的「你本次执行的执行编号(task_run_uuid)是：run_xxx」一行 —— **原样取出**。

> ⚠️ **它决定部署完成回调（agx_deploy_callback）投递到哪个会话。漏传 = 回调丢失 = 本执行永久卡 running（BUG-A2）。绝不自行编造、绝不留空。**

### Step 2: 调用 agx_deploy_agent
```
agx_deploy_agent(
    caller_agent_id=<本机 agx_host_id>,
    agent_id=<目标 agent_id>,
    force=<force>,
    task_run_uuid=<Step 1.5 取到的本次执行编号: run_xxx>
)
```
### Step 4: 部署结果处理 

agx_deploy_agent调用完成后不需要做任何处理，就地等待。ws_client 注册完成后自动发送回调到本session，消息格式为：

```json
{
  "type": "agx_deploy_callback",
  "status": "succeeded",
  "run_uuid": "run_task_xxx",
  "agent_id": "pt2_pm",
  "agent_name": "pt2_pm",
  "result": { "ok": true, "agentId": "pt2_pm", ... }
}
```

收到后执行：

1. `agx_update_agent(agent_id=agent_id, updates={"deploy_status": "deployed"})` — 标记已部署
2. `agx_finish_task(task_run_uuid=run_uuid, status="succeeded", summary="部署成功: {agent_name}")` — 结案

> ℹ️ **若 agx_deploy_agent 返回 `status=failed` 且 error 含「无法确定部署回调关联 ID」** —— 说明你漏传了 `task_run_uuid`（或该 caller 当前无 running execution）。此时物化文件已被自动回滚，按 Step 1.5 取到编号后**重试**即可，不要静默结案。
