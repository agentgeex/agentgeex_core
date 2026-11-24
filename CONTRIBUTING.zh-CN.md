# 参与 AgentGeeX 开发

[English](CONTRIBUTING.md) · **简体中文**

谢谢愿意花时间。这份文档讲的是**这个仓库里事情实际怎么做**，不是模板话术。

**动笔之前先看第 1 节。** 它能帮你避免一次白做的 PR。

---

## 1. 我们收什么，不收什么

### 会合并的

- **带复现步骤的 bug 修复。** "它崩了"不算报告。"在干净机器上跑
  `./agx.sh start`，daemon 退出并打印 `X`，日志这一行是……"才算。
- **修客观错误** —— 文档里写错的数字、`agx.sql` 里缺的列、引用了不存在文件
  的任务模板。这类不需要先讨论设计，直接提 PR。
- **新的任务模板**（`tasks/<name>.md` + 对应的 `agx_struct.json` 条目），
  前提是有人真的在跑这个流程。见第 6 节。
- **新的平台适配器** —— 接另一个 agent harness，只要遵守现有桥接契约、
  并且**不改上游 harness 的源码**。
- **修掉谎话的文档。** 文档说了不成立的话，删掉那句话就是受欢迎的 PR。

### 不会合并的

- **纯格式化 / 纯"整理"的 PR**，一改一大片。它会和在飞的每一件事冲突，
  审起来的成本大于收益。
- **没有理由的依赖升级。** 机器之间的版本漂移在本项目是一类真实故障，
  见 `docs/development/environment-parity.md`。
- **和 bug 或已交付功能无关的重构。** 先开 issue，写清它防住了哪种故障。
- **只有一个调用者的抽象。** 宁可三行相似的代码，也不要一个过早的公共函数。
- **一个文件里"只改一行、但顺手把整篇重排了"的 PR。** 要么拆两个提交，
  要么只留最小 diff。
- **任何要求把本项目用户的数据发给第三方的改动。** 自托管就是这个项目的产品本身。

拿不准属于哪一类，就先开 issue 附上改动草图，别先写完再问。

---

## 2. 开发环境

前置要求：

- **Python 3.12+**（`pyproject.toml` 声明 `requires-python = ">=3.12"`）。
- **uv** —— 依赖解析和虚拟环境都走它。
- **MySQL 8.x** —— 开发基线是 8.0；全库使用 `utf8mb4`。
- **Redis** —— 内核没有内存降级模式。
- **Node.js 20+** —— 只有改 `webtools/` 时才需要
  （`webtools/client/package.json` 声明 `engines.node >= 20`）。

初始化：

```bash
git clone <repo-url> agentgeex
cd agentgeex

# 国内网络建议指定镜像源，依赖下载会快很多（不设也能装，只是慢一些）
export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

uv sync
```

镜像源对本目录下后续所有 `uv` 命令（含 `uv sync --extra audio`）都生效，不必重复指定。

**锁文件是权威。** `uv.lock` 固定了每一个依赖，包括间接依赖。

- ✅ `uv sync` —— 严格按锁安装。
- ❌ `uv pip install <pkg>` —— 会重新解析并装最新版，绕过锁。于是同一份
  commit 在两台机器上行为不同，而且事后极难看出原因。本项目已经因此踩过一次
  只在生产暴露的故障（`docs/development/environment-parity.md`）。
- 确实需要新依赖时：写进 `pyproject.toml`，跑 `uv lock`，**两个文件同一个 PR 提交**。

可选扩展（语音转写，会拉一个很大的 ML 依赖栈）：

```bash
uv sync --extra audio
```

内核（MCP server、daemon、watchdog）不 import numpy 或 torch。

---

## 3. 提交之前

按顺序跑完这四步。都不贵，都不能省。

### 3.1 语法检查

改动过的每个 Python 文件都必须能编译：

```bash
python3 -m py_compile path/to/changed_file.py
```

`py_compile` 过不去的文件连 import 都不行，也就谈不上测。这是最便宜的一道闸，
也挡住最难堪的错误（字符串没闭合、局部替换残留的乱码字符）。

### 3.2 Lint

```bash
uv run ruff check .
uv run ruff format --check .
```

配置在 `pyproject.toml`。**不要为了过 lint 去重排无关文件。**

### 3.3 Schema 一致性

```bash
python3 scripts/verify_schema.py
```

它校验 **`agx.sql` 与真实 MySQL 库是否一致。** `agx.sql` 是全新安装唯一拥有的
schema —— `install.py` 把它导进一个空库，那就是客户的起点。文件一旦和代码需要
的东西脱节，每个新安装都会坏，而开发者本机一直好好的。这是最坏的一类 bug，
因为它在别人安装之前完全不可见。

脚本执行的规则是：**文件是真理，库是产品。**

可选参数：

```bash
python3 scripts/verify_schema.py            # 人类可读报告，有漂移则 exit 1
python3 scripts/verify_schema.py --json     # 机器可读
python3 scripts/verify_schema.py --strict   # 连默认值、注释一起比
```

### 3.4 任务清单一致性

```bash
python3 scripts/verify_tasks.py
```

它校验三方一致：`agx_struct.json` ↔ `tasks/*.md` ↔ 代码里引用的任务名。
漂移有三种，且**都只在客户机器上才暴露**：

1. `agx_struct.json` 里有、文件没有 → 安装时写成一句短描述，而不是完整说明书。
2. 文件有、`agx_struct.json` 里没有 → 这个任务永远不会被安装，是个孤儿。
3. 代码按名字读、文件没有 → 运行时直接报错。

凡是要动 `tasks/` 或 `agx_struct.json` 的 PR，提交前必须跑它。

> 两个脚本都要求 **Python 3.10+**，低于这个版本会明确告诉你。它们刻意不依赖
> 任何第三方包，所以在虚拟环境坏掉的时候也照样能用。

### 3.5 测试（只有内核在跑时才需要）

`test/` 是**端到端**套件：通过 `mcporter` 驱动真实的 MCP server，对着活的
MySQL/Redis 内核断言。它不是能在裸 checkout 里跑的单元测试。

```bash
./agx.sh start          # 内核起来
uv run pytest test/ -q
```

只改了纯函数的话，`py_compile` 加 `ruff` 就够了；在 PR 里说明一句，让审查者知道
哪些跑过、哪些没跑。

---

## 4. 提交信息

格式：

```
<type>(<scope>): <subject>
```

- **type**：`feat` `fix` `docs` `refactor` `perf` `test` `chore`
- **scope**：改动的区域 —— `install` `mcp` `watchdog` `tasks` `docs`
  `uib` `scripts` `dsh` `openclaw`
- **subject**：改了什么，祈使句、小写、结尾不加句号。要说清**原来错在哪、现在是什么**，
  不是"更新文件"。

本仓库历史里的真实例子：

```
fix(install): install says "done" but the only MCP gateway (31890) was never started
fix(scripts): below 3.10, say so -- do not leave people guessing from an unrelated TypeError
docs(readme): tool count 100 -> 96 (numbers must be verifiable; the old value was wrong)
chore(tasks): align the task inventory -- 28 struct entries present, 3 orphans archived
```

提交信息一律用英文（subject 与正文都是）。理由是：这个仓库对外公开，英文历史任何人
clone 下来都读得懂。关键是半年后的读者能看懂**当时坏在哪里**。

### 关于 git 命令的硬规矩

提交信息只用 `-m` 或 `-F <file>`：

```bash
git commit -m "fix(watchdog): ..."
git commit -F /tmp/commit-msg.txt
```

**在本仓库里永不执行以下命令：**

| 命令 | 为什么在这里禁用 |
|---|---|
| `git add .` | 会把所有未跟踪文件一起暂存 —— 本地配置、凭证文件、临时产物。本仓库同时存在多个并行开发线，不该进本次提交的文件绝不能"顺手"进去。 |
| `git add -A` | 同样的问题，还多带上删除。 |
| `git reset --hard` | 静默销毁未提交的工作，没有撤销。 |
| `git checkout -- <file>` | 同上，只是按单个文件来。 |

**逐路径显式暂存：**

```bash
git add agx_mcp/mcp_server.py tasks/my_task.md
git diff --staged          # 看清你即将提交什么
git commit -m "..."
```

如果你发现自己在想用 `reset --hard`，停下，在 issue 里问一句。实践上答案几乎
总是 `git stash`。

---

## 5. Schema 变更（DDL）

**`agx.sql` 进 git，数据库不进。**

改表 —— 加列、改默认值、加索引 —— DDL 必须在同一个 PR 里作为文件改动落地：

1. 先把改动应用到本地库。
2. 同步改 `agx.sql`。
3. 跑 `python3 scripts/verify_schema.py`，拿到干净退出。
4. 把 `agx.sql` 和需要它的代码一起提交。

只存在于你本地 MySQL 的改动，等于不存在。下一个从零安装的人，会拿到一个跑不了
他刚拉下来的代码的 schema。

需要一次性数据迁移的变更，在 PR 正文里说明，并写清怎么跑。**不要未经讨论就把
迁移逻辑塞进 `install.py`。**

---

## 6. 任务模板

一个任务模板有**三个落点**。漏一个，是本仓库最常见的错误 —— 因为漏了不会有任何
东西响亮地报错。

| # | 是什么 | 在哪 |
|---|---|---|
| 1 | 说明书 | `tasks/<task_name>.md` |
| 2 | 发布清单 | `agx_struct.json` → `tasks` 段 |
| 3 | 落库的行 | `agx_task_base`（安装时由 `install.py` 写入） |

`install.py` 遍历 `agx_struct.json` 来决定客户机器上存在哪些任务。一个没被列进去
的 markdown 文件，永远不会被安装。

改完跑：

```bash
python3 scripts/verify_tasks.py
```

把输出贴进 PR 正文。

---

## 7. Pull Request

PR 正文必须回答三个问题：

1. **原来错在哪？** 说故障现象，不是文件清单。是 bug 就写怎么复现。
2. **改了什么？** 一两句话。不明显的地方给出代码位置。
3. **怎么验证的？** 把你跑过的命令和结果贴上来：`py_compile`、`ruff`、
   `verify_schema.py`、`verify_tasks.py`、`pytest`，或者手工复现步骤。

检查清单：

- [ ] 每个改动的 Python 文件都过了 `python3 -m py_compile`
- [ ] `uv run ruff check .` 通过
- [ ] `python3 scripts/verify_schema.py` 退出码 0（若改了 DDL 或 `agx.sql`）
- [ ] `python3 scripts/verify_tasks.py` 退出码 0（若改了 `tasks/` 或 `agx_struct.json`）
- [ ] `pyproject.toml` 一改，`uv.lock` 就在同一个提交里跟着改
- [ ] diff 里没有凭证、token、本地配置、日志
- [ ] 没有无关的格式化噪音

PR 请保持小。40 行的 PR 当天就能审；400 行的会一直挂着，直到有人能腾出一整小时。

---

## 8. Issue

开 issue 之前**先搜已有 issue** —— 开着的和已关闭的都要搜。大多数"起不来"的报告
都已经回答过了。

一份有用的 issue 包含：

- 你跑了什么（准确的命令、准确的版本或 commit）
- 你期望什么
- 实际发生了什么（原始输出，不要转述）
- 平台信息（操作系统、Python、MySQL、Redis 版本）
- 在干净机器上能否复现

**安全问题不要用 issue 跟踪器。** 见 `SECURITY.md`。

---

## 9. 审查

- 合并需要一位维护者批准。
- 审查者可能请你拆 PR。这不是拒绝，通常是让它最快被合并的路径。
- PR 静置超过两周就留言催一下。没有什么是被故意忽略的，只是沉到水面以下了。
- 设计上有分歧是正常且预期的。**事实上有分歧，跑一遍就定了。**

---

## 10. 许可证

提交贡献，即表示你同意你的贡献以本仓库相同的许可证授权。见 `LICENSE`。
