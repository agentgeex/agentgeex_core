---
reply_mode: result
---

# 任务：公告/日志定期提炼并归档 (Notice Summarize)

## 目标
对指定项目的有效公告进行提炼，生成结构化摘要，然后将已提炼的原始公告归档（active_status→0）。

## 为什么需要提炼？
- 防止公告表无限膨胀、降低查询成本
- 零散日志 → 结构化知识，新成员快速上手
- 保留审计线索（原始公告仍在，仅标记 inactive）

---

### Step 1: 确认范围和阈值

从 `exec_instruction` 解析参数：
```json
{"project_id": "proj_xxx", "days_back": 7, "min_count": 20}
```
| 参数 | 默认 | 说明 |
|------|------|------|
| project_id | 必填 | 要提炼的项目 |
| days_back | 7 | 只提炼最近 N 天 |
| min_count | 20 | 不足此数不提炼 |

---

### Step 2: 查询有效公告

```
agx_query_notices(project_id=<project_id>, active_status="1", limit=500)
```
- `total < min_count` → 输出 "公告数不足（X < min_count），暂不提炼"，结束
- 否则继续

---

### Step 3: 分类提炼

按 `notice_type` 分组，生成摘要公告：

```markdown
# 项目 XX 工作周报（YYYY-MM-DD ~ YYYY-MM-DD）

## 踩坑记录（N条 error）
- [日期] {agent}: {问题} → {原因} → {解决}

## 重要决策（N条 decision）
- [日期] {agent}: {决策内容及理由}

## 工作摘要（N条 log）
- {agent}: {工作内容概要}

## 配置变更（N条 config）
- [日期] {agent}: {变更内容}

## 交接事项（N条 handoff）
- {agent}: {待办/注意事项}

## 统计数据
- 总公告: X 条（已归档）
- 涉及 Agent: [agent_a, agent_b, ...]
- 时间段: YYYY-MM-DD ~ YYYY-MM-DD
```

---

### Step 4: 写入摘要 & 归档原公告

1. **写入摘要公告：**
```
agx_write_notice(
    project_id=<project_id>,
    notice_type="decision",
    title="[提炼] 项目周报 YYYY-MM-DD",
    content=<上一步的摘要 markdown>,
    visibility="project"
)
```
记录返回的 `notice_id`，后续可用于追溯。

2. **归档原始公告（MySQL 批量更新）：**
```sql
UPDATE agx_agent_notice 
SET active_status = 0 
WHERE project_id = '<project_id>' 
  AND active_status = 1 
  AND created_at >= '<days_back 天的日期>'
  AND notice_id != '<上一步的摘要 notice_id>'
```
用 `exec` 执行 mysql 命令完成批量归档。

---

### Step 5: 输出报告

```
agx_write_notice(
    notice_type="log",
    title="[归档完成] 项目 XX 公告提炼",
    content="已提炼 {X} 条公告为 1 条摘要（note_xxx），{X} 条原始公告已归档。"
)
```


[146 more lines in file. Use offset=78 to continue.]