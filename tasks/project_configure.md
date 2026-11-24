---
reply_mode: result
---

# 项目配置任务 — Project Configure

## 概述
更新现有项目的**运营属性**（名称、简称、Logo、描述、源码目录）。**不涉及启停** —— 启停是独立的快捷指令动作。

## 前置条件
- 目标项目在 `agx_project` 中已注册且 active
- **仅限运营字段**：`project_name` / `project_abbr` / `project_logo` / `x_desc` / `source_dir`
- 敏感字段（`active_status` 启停）**不在本任务范围**，由独立快捷指令 + 更高权限处理

## 执行流程

### Step 1: 确认参数
- `project_id` — 目标项目 ID
- 需要修改的字段（仅限运营字段白名单）

### Step 2: 验证
- 调用 `agx_get_project(project_id)` 确认目标存在且 active
- 若请求含 `active_status` 等非运营字段 → 拒绝，提示"启停请走独立快捷指令"

### Step 3: 更新
调用 `agx_update_project(project_id, updates)`
- `updates` 仅含运营字段

### Step 4: 通知
```json
{
  "status": "updated",
  "project_id": "proj_xxxx",
  "changes": ["project_name: 旧名 → 新名"],
  "updated_at": "2026-10-13T20:45:00+08:00"
}
```

## 支持的更新字段（运营字段白名单）

| 字段 | 说明 | 示例值 |
|------|------|--------|
| project_name | 项目名称 | "庄家分析V5" |
| project_abbr | 项目简称（全小写） | "div5" |
| project_logo | 项目 Logo（URL/路径） | "https://.../logo.png" |
| x_desc | 项目描述 | "面向 A 股..." |
| source_dir | 源码目录 | "/path/to/src" |

> ⚠️ **不含 `active_status`** —— 项目启停是独立动作，走专用快捷指令，不混入配置修改。
