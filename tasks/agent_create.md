---
reply_mode: result
---

# Agent 创建 — agent_create

## 概述
**创建**一个新的 Agent（区别于 `agent_clone` 的"克隆"语义）。
**这是对外公开的入口** —— 其他项目管理员（PM）可通过本 task 请求 agentfather 创建 Agent。

> ★ **2026-10-13 恢复 + 改造**：`agent_create` 曾因「直写 DB、不加载模板 → 空壳 Agent（BUG-A1）」
> 被废除。现**恢复为「入口 task」**，但其**执行体已收敛到 `agx_create_agent_pipeline`**
> —— 即：本 task 的说明书指向同一个方法，**不再走 `agx_upsert_agent` 直写路径**，
> 因此**不会复活 BUG-A1**。

## 与 agent_clone 的关系
| task | 语义 | 执行体（同一个） |
|------|------|-----------------|
| `agent_clone` | 克隆（从模板继承） | `agx_create_agent_pipeline` |
| `agent_create` | 创建（对外公开入口） | `agx_create_agent_pipeline` |

**二者是"两个行为、同一实现"**：PM 心智里"我要克隆一个" vs "我要造一个新的"是分开的，
所以保留两个入口；但底层都汇到 `agx_create_agent_pipeline`，不再两套路径分叉。

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

方法内部：① 建 plan（克隆/创建 → 部署）→ ② deploy → ③ dispatch `plan_execute` 给
`agx_core` 在独立会话激活并全权推进。**发起方止步于方法返回（异步，发完即回）。**

## 模板约定
| 角色 | 默认模板 |
|------|-----------|
| project_manager | **`super_pm`** |
| worker | **`agent_template`**（有专属模板则用专属，如 `book_writer`） |

## 幂等原则（最高优先级）
- 目标 `new_agent_id` 已存在且 active → **succeed** 返回已有信息，绝不 failed。

## 关键约束
- ★ **本 task 是 `agx_agentfather` 的职责**：仅 agentfather + PM（可请求建/克隆本项目 worker）
  + 全局角色可触发（方法内已按此校验）。
- **对外公开**：其他项目管理员（PM）想创建 Agent → dispatch 本 task 给 `agx_agentfather`。
- **绝不直调 `agx_upsert_agent`**（那是"已运行 Agent 融入登记"的路径，不是创建入口）。
- 完整流程见 `agents/agx_agentfather/AGENTS.md` 的《能力》小节。
