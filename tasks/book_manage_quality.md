---
reply_mode: result
---

# 任务：质量控制与流程判定 (book_manage_quality)

## 概述
策划编辑的核心品控任务。收集中间产出（writer 的 draft、reviewer 的审校报告、reader 的阅读笔记），判定每一节的质量状态，返回决策结果。由上层 plan 根据决策结果触发下一步动作。

**这是整个创作管线唯一的质量决策点。**

## 输入
`instruction` 包含任务回执信息：

```
质量检查：{文件名}

当前阶段：{write_done / review_done / beta_done / integration_done}

产出物路径：
- draft: drafts/{chXX}/{文件名}_vN.md         （必存在）
- review: drafts/{chXX}/{文件名}_vN_review.md  （如已完成审校）
- beta: drafts/{chXX}/{文件名}_vN_beta.md      （如已完成试读）
- integration: drafts/{chXX}/{文件名}_vN_review_integration.md （如已完成整合）
```

## 判定流程

### Step 0: 获取书稿根目录

调用 `agx_get_project_config` 读取项目 `x_desc`，从 `## 书稿信息` 中提取 **书稿根目录** 的绝对路径。
本文中所有 `workspace/` 路径都相对于书稿根目录，不可使用 Agent 自身的工作目录。

### Step 1: 读当前状态
读所有已产出的文件（draft、review、beta、integration 中存在的）。

### Step 2: 决策矩阵

根据当前阶段判定下一步动作：

#### 阶段：write_done（刚写完）

→ 下一步动作：`review_and_beta`
含义：扇出 `book_review_content`（审校）和 `book_beta_read`（试读）两个并行任务。

#### 阶段：review_done + beta_done（审校和试读已完成）

→ 下一步动作：`integrate`
含义：触发 `book_integrate_feedback`（整合反馈），即使只有一个 reader 也要过整合流程。

#### 阶段：integration_done（反馈整合完成）

→ 读整合报告，统计 🔴 🟡 数量：

| 整合结果 | 下一步动作 |
|---------|-----------|
| 🔴 = 0 且 🟡 = 0 | `finalize` — 直接定稿 |
| 🔴 = 0 且 🟡 ≤ 3 | `revise_light` — 建议修订，改后直接定稿，不走重审 |
| 🔴 > 0 或 🟡 > 3 | `revise_heavy` — 强制修订，改后回到审校 |

### Step 3: 定稿

当一节满足定稿条件：
1. 在 `workspace/status.json` 中更新该节状态为 `final`
2. 如果本章所有节都为 `final`，返回下一步动作：`merge_chapter`

### Step 4: 完成

`agx_finish_task(status="succeeded")`，在 `summary` 中返回：
- 当前阶段
- 下一步动作（`review_and_beta` / `integrate` / `finalize` / `revise_light` / `revise_heavy` / `merge_chapter`）
- 相关的节文件和状态

> 本 task 不下发任何 `agx_dispatch_task`。所有后续任务由上层 plan 根据返回的下一步动作执行。

## 完整状态机（参考，具体编排由 plan 管理）

```
write_done → review_and_beta（扇出 review + beta_read）
  review_done + beta_done → integrate（整合反馈）
    integration_done:
      🔴=0,🟡=0 → finalize
      🔴=0,🟡≤3 → revise_light → finalize
      🔴>0 或 🟡>3 → revise_heavy → 重审
  所有节 final → merge_chapter
```

## 异常处理

| 场景 | 处理 |
|------|------|
| 只收到 review 回执，beta 迟迟不回 | `agx_finish_task(summary="waiting_beta")` |
| 修订循环超过 3 轮 | `agx_finish_task(summary="revision_limit_reached")`，需人工介入 |
| writer 在修订中提了审校异议 | 标记为需 reviewer 复核，返回 `needs_reviewer_judgment` |
| 某文件找不到 | `agx_finish_task(status="failed")` |

## 执行约束
- 本 task 只做质量判定，不触发下游任务
- 不产生用户可见文件（只更新 status.json）
- 修订循环最多 3 轮标记，超过后返回 `revision_limit_reached`
