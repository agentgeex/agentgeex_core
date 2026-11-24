---
reply_mode: result
---
# 任务：图书结构与标准制定 (book_plan_structure)

## 概述
图书创作流程的起点。策划编辑在理解全书目标和读者画像后，输出一套完整、可执行的写作标准。后续所有 writer 和 reviewer 的工作以此为唯一依据。

## 输入要求
`instruction` 中应包含：
- `book_title`: 书名
- `target_reader`: 目标读者描述
- `core_idea`: 本书想解决的核心问题
- `chapter_outline`: 章节目录（可选，没有则由策划根据素材自行推导）
- `material_paths`: 原始素材文件路径列表

## 产出

| 文件 | 说明 |
|------|------|
| `standards/writing_standard.md` | 写作标准：小节结构模板、字数、代码规范、语气要求 |
| `standards/glossary.md` | 术语统一表：专有名词 → 标准译法/写法 |

### writing_standard.md 结构
```markdown
# 写作标准 — {book_title}

## 小节结构模板
- 开头（~200字）：接住读者，说明本节要解决的问题
- 核心内容（~1500-3000字）：概念解释 + 代码/案例 + 分析
- 结尾（~100字）：总结 + 预告下节

## 字数规范
- 单小节 2000-4000 字
- 代码块不超过 40 行
- 单段不超过 200 字

## 代码规范
- 语言：{Python/JS/...}
- 注释语言：中文
- 必须有可运行的完整示例

## 语气要求
- 平实、专业，不卖弄
- "先接住读者再解释概念"
- 每个技术判断附证据
```

### glossary.md 结构
```markdown
# 术语表 — {book_title}

| 原文/英文 | 标准译法 | 备注 |
|----------|---------|------|
| transformer | Transformer | 首字母大写，不翻译 |
| prompt | 提示词 | 不是"提示" |
| embedding | 嵌入向量 | 简称"嵌入"也可 |
```

## 执行约束
- 输出文件写入项目的 `standards/` 目录
- 不确定的术语标注 `[待定]`
- 完成后 agx_finish_task，result 写文件路径列表
