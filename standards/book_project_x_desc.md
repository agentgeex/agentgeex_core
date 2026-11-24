# 《{{book_name}}》 项目描述

> 注意：”图书编写项目铁律“ 是你必须遵守的规则


## 图书编写项目铁律（注意:这是必须遵守的规则，请先阅读本规则）
由于图书创作过程具有特殊性，因此图书创作项目相关创作和工作计划**必须**通过`book_project_orchestrate`这个task进行创建，这是专门为图书编写路程而特别优化的task，通过该task可以创建符合图书创作特性的工作计划。

## 图书定位
{{book_desc}}

## 项目路径
- **书稿目录**：`{{source_dir}}`
- **临时目录**：`{{source_dir}}/workspace`

## 书稿信息

- **书稿根目录**: {source_dir}
- **章源文件**: {书稿根目录}/ch{XX}_*.md
- **工作区**: {书稿根目录}/workspace/
- **草稿目录**: {书稿根目录}/workspace/drafts/{chXX}/
- **进度文件**: {书稿根目录}/workspace/status.json
- **质量规范**: {书稿根目录}/workspace/standards/

所有 task 说明书中的相对路径（如 workspace/drafts/）都相对于书稿根目录。
Agent 工作目录与书稿目录不同，读写书稿文件时必须使用绝对路径。


## 临时目录使用规则

1. **`workspace/` 是管线运行时的工作区**。所有创作过程中产生的中间文件、计划文件、版本文件均放在此目录，不得污染项目源目录。
2. **目录结构约定**：
   ```
   workspace/
   ├── standards/              ← 写作标准 + 术语表
   │   ├── writing_standard.md
   │   └── glossary.md
   ├── split/                  ← 章节物理拆分后的原始切片
   │   └── chXX/
   │       ├── TOC.txt         ← 本章目录树
   │       └── {小节}.md       ← 拆分後的小节文件
   ├── drafts/                 ← 创作中的草稿（含版本号）
   │   └── chXX/
   │       ├── {小节}_v1.md    ← 初版
   │       ├── {小节}_v1_review.md      ← 审校报告
   │       ├── {小节}_v1_beta.md        ← 读者笔记
   │       ├── {小节}_v1_review_integration.md ← 反馈整合
   │       ├── {小节}_v2.md    ← 修订版
   │       └── status.json     ← 本章各节状态追踪
   ├── merge/                  ← 合并後的定稿
   └── plans/                  ← 编排生成的 plan JSON
   ```
3. **版本号递增**：`_v1.md` → `_v2.md` → `_v3.md`，最多 3 轮修订。
4. **每章的 status.json 放在 `workspace/drafts/{chXX}/status.json`**，不是 workspace 根目录。
5. **`.md` 文件是源文件**，不是临时文件。临时目录中的 `.md` 产物在合并完成前不要手动删除。

## 创作团队

> ⚠️ **下表为模板 Agent 名称，不能直接用于部署或调用。**
> 实际部署后的 agent_id 格式为 `{模板名}_{随机后缀}`（如 `book_writer_xxxxxx`），
> 必须通过 `agx_list_project_agents(project_id=...)` 获取实际 agent_id。

| 模板名 | 角色 | 职责 |
|-------|------|------|
| `book_planner` | 图书策划编辑 | 定标准、拆章、分发、管路决策、合并 |
| `book_writer` | 图书写作师 | 撰写小节、修订（基于审校反馈） |
| `book_reviewer` | 图书审校官 | 多维审校（术语/内容/风格/衔接/体验）+ 整合读者反馈 |
| `book_beta_reader` | 图书读者试读员 | 沉浸式阅读 + 盲点发现 + 阅读体验反馈 |
| `super_pm` | 项目管理员 | 收高层指令、读 PM 协议、编排 JSON plan、plan_deploy |

## 标准管线

```
筹备: plan_structure（产出写作标准 + 术语表）

逐章（ch01→ch02→...）:
  agx_book_orchestrate_chapter（split → deploy 一步到位）
    → writer*N (并行)
    → manage_quality (决策中枢)
      → review + beta (并行)
      → integrate_feedback
      → 定稿 / 修订循环 (≤3轮)
    → merge_chapter
    → 下一章
```

## 关键约束

1. **修订循环最多 3 轮**。超过 3 轮仍未通过，write notice 通知人工介入。
2. **Writer 只能提 `[审校异议]`，不能否决**。Reviewer 必须逐一确认或驳回。
3. **Review 和 Beta_read 可并行**，互不依赖。
4. **`_00` 导语必须最先分发**，writer 需要导语建立本节上下文。
5. **每章独立**。ch02 的 split 可以在 ch01 审校期间就开始。
6. **代码示例必须可运行**（yes-md 铁律），技术声明必须有引用来源。
7. **AI 味检测是强制项**（humanize-chinese），但 AI 味本身不影响通过/不通过判定，只标注不扣分。
8. **Beta_reader 必须做盲点预设**（doc-coauthoring Stage 3），带着问题读。

## Task 清单

> ⚠️ **下表为模板 Agent 名称，不能直接用于部署或调用。**
> 实际部署后的 agent_id 格式为 `{模板名}_{随机后缀}`（如 `book_writer_xxxxxx`），
> 必须通过 `agx_list_project_agents(project_id=...)` 获取实际 agent_id。

### book_planner（5 个 task）
| task_name | task_labs | 说明 |
|-----------|-----------|------|
| general | general | 兜底消息处理 |
| watchdog_alert | watchdog_alert | 故障恢复 |
| 图书结构与标准制定 | book_plan_structure | 产出写作标准 + 术语表 |
| book_manage_quality | book_manage_quality | 质量决策中枢 |
| book_merge_chapter | book_merge_chapter | 全章定稿合并 |

### book_writer（4 个 task）
| task_name | task_labs | 说明 |
|-----------|-----------|------|
| general | general | 兜底 |
| watchdog_alert | watchdog_alert | 故障恢复 |
| 小节写作 | book_write_section | 创作/改写小节 |
| 小节修订 | book_revise_section | 基于审校反馈修订 |

### book_reviewer（4 个 task）
| task_name | task_labs | 说明 |
|-----------|-----------|------|
| general | general | 兜底 |
| watchdog_alert | watchdog_alert | 故障恢复 |
| 书稿审校 | book_review_content | 五维审校 + yes-md + humanize-chinese |
| 读者反馈整合 | book_integrate_feedback | 整合多读者反馈为统一修订建议 |

### book_beta_reader（4 个 task）
| task_name | task_labs | 说明 |
|-----------|-----------|------|
| general | general | 兜底 |
| watchdog_alert | watchdog_alert | 故障恢复 |
| 图书读者试读员 | book_beta_reader | 默认任务（来自 agent 注册） |
| book_beta_read | book_beta_read | 沉浸阅读 + 盲点验证 |

### super_pm（3 个 task）
| task_name | task_labs | 说明 |
|-----------|-----------|------|
| general | general | 兜底 |
| watchdog_alert | watchdog_alert | 故障恢复 |
| book_project_orchestrate | book_project_orchestrate | 图书项目编排 |
