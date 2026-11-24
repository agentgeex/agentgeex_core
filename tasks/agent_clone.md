---
reply_mode: result
---

# Agent 克隆 — agent_clone

## 概述
从现有 Agent 克隆新 Agent，继承原型配置，用于服务不同项目。
**这是创建 Agent 的入口之一（另一入口 `agent_create`）。二者都收敛到同一实现：`agx_create_agent_pipeline`。**

> ★ **2026-10-13 改造**：建/克隆 Agent 已收敛为**一次方法调用** ——
> 执行者只需调用 **`agx_create_agent_pipeline`**（声明式 plan 编排），它会自动完成
> 「克隆 → 部署（→ 可选配置）」全链。**不再需要手工一环环 dispatch：clone → deploy。**

## 模板约定（先记牢）
Agent 体系只有两类（外加固定的系统四个 `agx_core` / `agx_host` / `agx_agentfather` / `agx_projectmother`）：

| 角色 | 克隆源模板 |
|------|-----------|
| project_manager | **`super_pm`** |
| worker | **`agent_template`**（有专属模板则用专属，如 `book_writer`） |

## 幂等原则（最高优先级）
克隆前检查目标 `new_agent_id` 是否已存在：
- 已存在且 active → **succeed** 返回已有信息
- **永不因"已存在"而 `agx_finish_task(status="failed")`**
（`agx_create_agent_pipeline` 的克隆 step 已内置幂等）

## 怎么做（一句话）
调用 **`agx_create_agent_pipeline`**，入参 `instruction` = JSON：

```json
{
  "new_agent_id": "新agent_id(可选,全小写;不填→默认 {template}_{hex(6)};填→校验+去重)",
  "agent_name": "表意名(必填)",
  "target_project_id": "目标项目(必填)",
  "host_id": "主机(可选,默认调用方 host)",
  "template_agent_id": "模板(可选,默认 PM→super_pm / 其余→agent_template)",
  "agent_role": "worker|project_manager(可选,决定默认模板)",
  "agent_type": "openclaw(可选)",
  "configure": {"base_model": "...", "system_prompt": "..."}
}
```

方法内部：① 建 plan（克隆 → 部署）→ ② deploy → ③ dispatch `plan_execute` 给
`agx_core` 在独立会话激活并全权推进。**发起方止步于方法返回（异步，发完即回）。**

克隆后继承模板的全部能力（role/model/labs/prompt/**全部 `*_md`**），不继承笔记
（notebook/self/heartbeat）。共享 task 和领域 task 自动创建；身份占位符
（`{{AGENT_NAME}}` 等）就地渲染为新实例值。

## 关键约束
- ★ **本方法是 `agx_agentfather` 的职责**：仅 `agx_agentfather` + `project_manager`（PM，
  可请求建/克隆**本项目**的 worker）+ 全局角色可调用 `agx_create_agent_pipeline`。
- **对外公开**：其他项目管理员（PM）想建/克隆 Agent → dispatch 本 task（或 `agent_create`）
  给 `agx_agentfather`，由 agentfather 调用 `agx_create_agent_pipeline`。
- PM 只能创建自己项目的 worker；跨项目/系统级 Agent 由 agentfather 判断。
- 完整流程见 `agents/agx_agentfather/AGENTS.md` 的《能力：Agent 克隆》小节。
