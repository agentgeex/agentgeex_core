---
reply_mode: result
---

# 任务：章节合并 (book_merge_chapter)

## 概述
一章所有小节通过质量检查（`status.json` 中全为 `final`）后，将 `drafts/` 下的最终版稿本合并为章的完整 `.md` 文件。合并用 shell 脚本拼接，不由大模型输出全文。

## 输入
`instruction` 中包含：

```
合并任务：第 {N} 章

计划 ID：{plan_id}

章节源文件：
{chapter_path}（如 ch01_xxx.md）

定稿目录：
workspace/drafts/{chXX}/

status.json：
workspace/status.json
```

## 合并流程

### Step 0: 获取书稿根目录

调用 `agx_get_project_config` 读取项目 `x_desc`，从 `## 书稿信息` 中提取 **书稿根目录** 的绝对路径。
本文中所有 `workspace/` 路径都相对于书稿根目录，不可使用 Agent 自身的工作目录。

### Step 1: 收集定稿文件

1. 读 `workspace/status.json` → 找出所有标记为 `final` 的节
2. 对每个 `final` 的节，找出最新版 `_vN.md`（取最大 N）
3. 按 `manifest.json`（`split/` 下的）确定 H2→H3 层级顺序
4. 拼接顺序：H1 标题 → 第一个 H2 导语 → 该 H2 的子节 → 下一个 H2 导语 → ...

### Step 2: 用 shell 拼接文件

每节文件用 `cat` 追加到合并文件，不要在模型回复里输出全文：

```bash
# 1. 备份原文件
cp {chapter_path} {chapter_path}.bak.{date}

# 2. 提取 H1 标题（从源文件的第一行 H1）
head -1 {chapter_path} > workspace/merged_ch{XX}.md

# 3. 按顺序拼接每个定稿小节文件
cat workspace/drafts/{chXX}/01_01_00_v{N}.md     >> workspace/merged_ch{XX}.md
echo ""                                                    >> workspace/merged_ch{XX}.md
cat workspace/drafts/{chXX}/01_01_01_v{N}.md     >> workspace/merged_ch{XX}.md
echo ""                                                    >> workspace/merged_ch{XX}.md
cat workspace/drafts/{chXX}/01_01_02_v{N}.md     >> workspace/merged_ch{XX}.md
echo ""                                                    >> workspace/merged_ch{XX}.md
# ... 按 manifest.json 的顺序继续
```

> 每个小节文件本身就是完整的 Markdown 片段（含 H2/H3 标题 + 正文），直接 cat 拼接即可，不需要做字符串级替换。

### Step 3: 清理过程痕迹

遍历拼接后的文件，移除过程注释：
- 删除含 `[审校异议]` 的行
- 删除 `<!-- 修订记录:` ... `-->` 注释块
- 删除 `_vN` 版本号引用（如果出现在正文中）

```bash
sed -i '' '/\[审校异议\]/d' workspace/merged_ch{XX}.md
sed -i '' '/<!-- 修订记录:/,/-->/d' workspace/merged_ch{XX}.md
```

### Step 4: 覆盖写回源文件

```bash
mv workspace/merged_ch{XX}.md {chapter_path}
```

### Step 5: 结案
```
agx_finish_task(
  task_run_uuid={当前 run_uuid},
  status="succeeded",
  result={chapter_path},
  summary="第 {N} 章合并完成：{K} 节 → {chapter_path}"
)
```

## 执行约束
- 必须确认所有节都是 `final` 状态才合并（读 status.json）
- 合并前备份原源文件（`cp {chapter_path} {chapter_path}.bak.{date}`）
- 采用 shell 文件拼接（cat），不要让大模型在回复中输出合并后的全文
- 合并不改文件格式：保持原 .md 结构，H1→H2→H3 层级完好
- 如果某节在 status.json 中标了 `final` 但找不到 draft，报错不合并
