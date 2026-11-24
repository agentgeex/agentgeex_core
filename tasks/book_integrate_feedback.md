---
reply_mode: result
---

# 任务：读者反馈整合 (book_integrate_feedback)

## 概述
审校官收集多个读者试读员的阅读笔记（`_beta.md`）、结合自己的审校报告（`_review.md`），整合为统一的修改建议。不是简单汇总——审校官需要判断哪些反馈真正需要采纳，哪些是视角差异，哪些可以解释后忽略。

## 输入
`instruction` 包含整合报文：

```
整合任务：{文件名}

审校报告：
{drafts/{chXX}/{文件名}_vN_review.md 的 🔴🟡 项摘要}

读者反馈列表：
- drafts/{chXX}/{文件名}_vN_beta.md          ← reader_1 的阅读笔记
- drafts/{chXX}/{文件名}_vN_beta_dev.md      ← reader_2（初级开发者视角）
- drafts/{chXX}/{文件名}_vN_beta_senior.md   ← reader_3（资深开发者视角）
（至少 1 份，可能多份）

---

本书介绍：
{book_intro}

standards/glossary.md — 术语统一表（如有）
standards/writing_standard.md — 写作标准（如有）
```

## 整合流程


### Step 0: 获取书稿根目录

调用 `agx_get_project_config` 读取项目 `x_desc`，从 `## 书稿信息` 中提取 **书稿根目录** 的绝对路径。
本文中所有 `workspace/` 路径都相对于书稿根目录，不可使用 Agent 自身的工作目录。
### Step 1: 收集所有反馈源
1. 读审校报告摘要（🔴🟡 项）
2. 逐个读读者阅读笔记（`_beta.md` 文件）
3. 如有 glossary / writing_standard，读之作为判断基准

### Step 2: 五维分类

将 feedback 按维度分类：

| 维度 | 来源示例 |
|------|---------|
| **术语** | reviewer 说术语不一致，reader 说某个词看不懂 |
| **内容** | reviewer 指出代码错误，reader 说某个概念理解不了 |
| **风格** | reviewer 说导语太长，reader 说某段读着累 |
| **衔接** | reviewer 说缺钩子，reader 说不知道和下节什么关系 |
| **体验** | reader 说"第3段很爽"、"第5段想跳过"——这是纯 reader 视角 |

### Step 3: 去重 + 判断

| 多条反馈指向同一问题 | → 合并为一条，注明"N人同提"，优先级升高 |
| reviewer 和 reader 同时指出 | → 优先级至少 🟡，很可能 🔴 |
| 仅一个 reader 提出 | → reviewer 独立判断 |
| 纯观点/偏好差异 | → 🟢 可忽略，写明原因 |
| 不确定该不该改 | → ❓ 标记 `[待主编确认]` |

### Step 4: 输出整合报告

写入 `workspace/drafts/{chXX}/{文件名}_vN_review_integration.md`：

```markdown
# 反馈整合报告 — {文件名}

**整合范围**: {文件名}_vN.md 的审校报告 + {M} 份读者阅读笔记
**审校报告**: _vN_review.md → 总评 {通过/有条件通过/不通过}，🔴{X} 🟡{Y} 🟢{Z}
**读者笔记**: reader_1, reader_2, ...（共 {M} 份）
**整合人**: book_reviewer
**整合时间**: {ISO 时间}

---

## 🔴 必须修改

| # | 问题 | 来源 | 说明 |
|---|------|------|------|
| 1 | L42 "transformer" → "Transformer" | reviewer | 术语不规范 |
| 2 | L78 代码缺 import | reviewer + reader_1（"代码跑不起来"） | 两人同提 |
| 3 | L156 "推理时间"概念未解释 | reader_1, reader_2 | 两人都没看懂 |

## 🟡 建议修改

| # | 问题 | 来源 | 说明 |
|---|------|------|------|
| 1 | 开头超出导语字数（350字） | reviewer | 压缩到200-500字 |
| 2 | L90 技术细节过密，需加一个类比 | reader_2 | 信息密度太高 |
| 3 | 结尾缺少对下一节的钩子 | reviewer | 衔接断层 |

## 🟢 可忽略

| # | 反馈 | 来源 | 忽略原因 |
|---|------|------|---------|
| 1 | "建议把代码换成 Go" | reader_3 | 视角差异：本书用 Python 是既定技术栈 |
| 2 | "开头太温和，建议直接上干货" | reader_1 | 导语铺垫是 H2 节结构要求 |

## ❓ 待主编确认

| # | 问题 | 来源 | 说明 |
|---|------|------|------|
| 1 | L200 "6层架构"是否要引用书中后续章节编号 | reviewer | 后续章号可能变化 |

---

## 决策统计
- 🔴 必须改：{N} 项
- 🟡 建议改：{M} 项
- 🟢 忽略：{K} 项
- ❓ 待确认：{P} 项
```

### Step 5: 结案
```
agx_finish_task(
  task_run_uuid={当前 run_uuid},
  status="succeeded",
  result={整合报告文件路径},
  summary="{文件名}：整合 {M} 份读者反馈 + 审校报告，🔴{N} 🟡{M} 🟢{K} ❓{P}"
)
```

## 执行约束
- 必须标注每条结论的来源（reviewer / reader_X / "两人同提"）
- 两人以上同提 → 必须出现在 🔴 或 🟡，不能忽略
- 不确定的标注 `[待主编确认]`，不擅自决定
- 整合报告放在同目录：`drafts/{chXX}/{文件名}_vN_review_integration.md`
- 如果只有一个 reader，也要走整合流程（reader + reviewer 对照权重判断）
