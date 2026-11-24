---
name: "md-section-splitter"
description: "按 heading 层级拆分 Markdown 文件，支持 flat/tree 两种模式，--toc 提取章节目录，输出 TOC.txt 精简目录，支持无损合成"
---

# md-section-splitter

按 heading 层级将 Markdown 文件拆分为独立 `.md` 片段，用于 AI Agent 逐节编辑后再无损合成。

## 三种操作模式

### 1. 提取目录（只读）— `--toc`

```
uv run python scripts/split_by_h2.py chapter.md --toc
uv run python scripts/split_by_h2.py chapter.md --toc --level 2   # 只看 H2
```

打印编号树，如：
```
01  第1章 什么是大模型
  01_01  1.1 理解大模型
    01_01_01  1.1.1 大模型的摸样
```

### 2. 拆分文件 — `--deep`

```
uv run python scripts/split_by_h2.py chapter.md output_dir/ --deep
uv run python scripts/split_by_h2.py chapter.md output_dir/ --deep --level 4
```

拆分产出：
```
output_dir/
├── TOC.txt                              ← 533B 精简文本目录
├── manifest.json                        ← 完整 JSON（含 toc 数组 + chapter 字段）
├── 01_01_00_理解大模型.md              ← H2 导语
│   ├── 01_01_01_大模型的摸样.md        ← H3 子节
│   └── ...
```

### 3. 合成

```
uv run python scripts/split_by_h2.py --merge manifest.json output.md
```

## 命名规范

| 后缀 | 含义 |
|------|------|
| `_00_` | 该层级导语（标题到第一个子标题之间的正文） |
| `_01/02/...` | 子节序号（从标题编号提取） |

```
01_01_00_理解大模型.md
01_01_01_大模型的摸样.md
01_01_02_大模型的编年史（为什么会是这个样子）.md
01_01_03_大模型的三个纬度.md
```

## 核心规则

1. `00` = 导语，H1 章标题 + 前言归入第一个 H2 的 `_00`
2. 代码块内 `##`/`###` 不触发拆分（code fence 追踪）
3. YAML frontmatter 只保留在第一个 section
4. 原文保真，round-trip `diff` = 零
5. 中文标点 `（）《》·` 保留在文件名中
6. 章节号识别：`1.1`、`第1章` → 文件前缀

## 输出文件

| 文件 | 格式 | 用途 |
|------|------|------|
| `TOC.txt` | 纯文本树 | Agent 快速理解结构，极省 token |
| `manifest.json` | JSON | 完整元数据（chapter, toc[], sections[]） |

## 依赖

- Python 3.8+
- 标准库 only（零第三方依赖）
