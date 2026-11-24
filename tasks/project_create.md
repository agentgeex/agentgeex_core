---
reply_mode: result
---

# 项目创建 — project_create

## 概述
在 AgentGeeX 网络中创建新项目。项目是任务、Agent 和算力的最高级容器。

> ★ **2026-10-13 改造**：建项目已收敛为**一次方法调用** —— 执行者只需调用
> **`agx_create_project_pipeline`**（声明式 plan 编排），它会自动完成「建项目 →
> 克隆 PM → 部署 PM → 建 Worker → 部署 Worker → 绑定验收」全链。
> **不再需要手工一环环 dispatch。**

## 执行来源
**完整执行流程参见 Agent 的 AGENTS.md §能力：project_create。**
（位于 `agents/agx_projectmother/AGENTS.md`）

## 怎么做（一句话）
调用 **`agx_create_project_pipeline`**，入参 `instruction` = JSON：

```json
{
  "project_name": "项目名(必填)",
  "description": "项目描述(必填)",
  "project_abbr": "项目简称(必填,全小写字母)",
  "platform": "openclaw(可选,默认openclaw)",
  "source_dir": "源码目录(可选)",
  "worker": [
    {"agent_id": "xxx_worker", "agent_name": "表意名", "template_agent_id": "模板", "role": "worker"}
  ]
}
```

方法内部：① 建 plan（固定链）→ ② deploy → ③ dispatch `plan_execute` 给 `agx_core`
在独立会话激活并全权推进。**发起方止步于方法返回**（本方法异步，发完即回，不阻塞）。

## 关键约束
- ★ **本方法仅 `agx_projectmother` 可调用**（+ 全局角色 workshop_manager / host_manager）。
  其他 Agent 无权调 `agx_create_project_pipeline` —— 要建项目必须先 dispatch 给 projectmother。
- **建项目是 projectmother 的专属职责**：别人（含 PM）想建项目 → dispatch 本 task 给 projectmother，
  由 projectmother 调用 `agx_create_project_pipeline`。
- 创建 Agent 环节由管线自动 dispatch 给 `agx_agentfather`（不自己调 `agx_clone_agent`）。
- 批量创建时必须逐条 dispatch，每次只创建 1 个 Agent（管线内已按 worker 列表逐条编排）。
- 超时降级 = 升级到 agx_core 或人类，绝不自干。
