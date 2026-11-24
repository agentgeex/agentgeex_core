# Contributing to AgentGeeX

**English** · [简体中文](CONTRIBUTING.zh-CN.md)

Thanks for taking the time. This document describes how work actually gets done in
this repository, not how it is usually described in templates.

**Read section 1 before you write code.** It will save you a wasted PR.

---

## 1. What we merge, and what we don't

### We merge

- **Bug fixes with a reproduction.** "It crashed" is not a report. "Ran
  `./agx.sh start` on a clean machine, daemon exited with `X`, here is the log
  line" is.
- **Fixes to something that is objectively wrong** — a wrong number in the docs,
  a column missing from `agx.sql`, a task template that references a file that
  does not exist. These do not need a design discussion; open the PR.
- **New task templates** (`tasks/<name>.md` + the matching `agx_struct.json`
  entry) that encode a workflow someone actually runs. See section 6.
- **Platform adapters** for another agent harness, as long as they follow the
  existing bridge contract and do not modify the upstream harness.
- **Documentation that fixes a lie.** If a doc says something that is not true,
  deleting that sentence is a welcome PR.

### We do not merge

- **Formatting-only or "cleanup" PRs** that touch many files. They conflict with
  everything in flight and cost more to review than they are worth.
- **Dependency bumps without a reason.** Version drift between machines is a
  real class of bug here — see `docs/development/environment-parity.md`.
- **Refactors that are not tied to a bug or a shipped feature.** Propose it in an
  issue first, with the failure mode it prevents.
- **New abstractions with one caller.** We prefer three similar lines over a
  premature shared helper.
- **PRs that reformat a file while changing one line in it.** Two commits, or
  one minimal diff.
- **Anything that requires sending this project's users' data to a third party.**
  Self-hosting is the product.

If you are unsure which side a change falls on, open an issue with the diff
sketch before writing the whole thing.

---

## 2. Development environment

Requirements:

- **Python 3.12+** (`pyproject.toml` declares `requires-python = ">=3.12"`).
- **uv** — dependency resolution and the virtualenv both go through it.
- **MySQL 8.x** — the development baseline is 8.0; the schema is `utf8mb4`
  throughout.
- **Redis** — the kernel has no in-memory fallback mode.
- **Node.js 20+** only if you touch `webtools/` (`webtools/client/package.json`
  declares `engines.node >= 20`).

Set up:

```bash
git clone <repo-url> agentgeex
cd agentgeex
uv sync
```

**The lock file is the authority.** `uv.lock` pins every dependency, including
transitive ones.

- ✅ `uv sync` — installs exactly what the lock says.
- ❌ `uv pip install <pkg>` — re-resolves and installs the newest version,
  bypassing the lock. Two machines then behave differently from the same commit,
  and it is nearly impossible to see why after the fact. This has already caused
  a production-only failure once (`docs/development/environment-parity.md`).
- If you genuinely need a new dependency: add it to `pyproject.toml`, run
  `uv lock`, and commit **both** files in the same PR.

Optional extras (audio transcription, which pulls a large ML stack) are opt-in:

```bash
uv sync --extra audio
```

The core kernel — MCP server, daemon, watchdog — does not import numpy or torch.

---

## 3. Before you commit

Run these in order. All four are cheap; none are optional.

### 3.1 Syntax check

Every Python file you touched must compile:

```bash
python3 -m py_compile path/to/changed_file.py
```

A file that fails `py_compile` cannot be imported, so it cannot be tested. This
is the cheapest gate we have and it catches the most embarrassing mistakes
(unclosed strings, stray characters from a partial edit).

### 3.2 Lint

```bash
uv run ruff check .
uv run ruff format --check .
```

Configuration lives in `pyproject.toml`. Do not reformat unrelated files to make
lint pass.

### 3.3 Schema consistency

```bash
python3 scripts/verify_schema.py
```

This checks that **`agx.sql` matches the live MySQL schema.** `agx.sql` is the
only schema a fresh install has — `install.py` imports it into an empty database.
If the file drifts from what the code needs, every new install breaks while the
developer's machine keeps working. That is the worst kind of bug, because it is
invisible until someone else installs.

The rule the script enforces: **the file is the truth, the database is the
product.**

Options:

```bash
python3 scripts/verify_schema.py            # human report, exit 1 on drift
python3 scripts/verify_schema.py --json     # machine readable
python3 scripts/verify_schema.py --strict   # also compare defaults and comments
```

### 3.4 Task catalogue consistency

```bash
python3 scripts/verify_tasks.py
```

This checks the three-way agreement between `agx_struct.json`, `tasks/*.md`, and
task names referenced from code. Three kinds of drift are possible, and all of
them only surface on a customer machine:

1. In `agx_struct.json` but no file → the task is installed with a short
   description instead of its full manual.
2. A file exists but is not in `agx_struct.json` → the task will never be
   installed. It is an orphan.
3. Code loads a task by name that has no file → a hard error at runtime.

Run it before every PR that touches `tasks/` or `agx_struct.json`.

> Both scripts require **Python 3.10+** and say so explicitly if run on an older
> interpreter. They are deliberately dependency-free, so they also work when the
> virtualenv is broken.

### 3.5 Tests (only when you have a running kernel)

`test/` is an **end-to-end** suite: it drives the MCP server over `mcporter` and
asserts against a live MySQL/Redis kernel. It is not a unit suite you can run in
a bare checkout.

```bash
./agx.sh start          # kernel up
uv run pytest test/ -q
```

If you only changed a pure function, `py_compile` plus `ruff` is enough; say so
in the PR so the reviewer knows what was and was not exercised.

---

## 4. Commits

Format:

```
<type>(<scope>): <subject>
```

- **type**: `feat` `fix` `docs` `refactor` `perf` `test` `chore`
- **scope**: the area touched — `install`, `mcp`, `watchdog`, `tasks`, `docs`,
  `uib`, `scripts`, `dsh`, `openclaw`
- **subject**: what changed, in the imperative, lowercase, no trailing period.
  Say what was wrong and what it is now — not "update file".

Examples from this repository's history:

```
fix(install): 装完「安装完成」，但唯一接口 MCP 网关（31890）从来没被启动
fix(scripts): 3.10 以下直接说清楚，别让人对着无关 TypeError 猜
docs(readme): 工具数 100 → 96（数字必须可核，此前写错）
chore(tasks): 任务清单对齐 —— 28 条 struct 无缺件，3 个孤儿归档
```

Message bodies may be written in Chinese or English. Subjects are fine in either;
what matters is that a reader six months later understands *what was broken*.

### Hard rules about git commands

Use `-m` or `-F <file>` to write the message:

```bash
git commit -m "fix(watchdog): ..."
git commit -F /tmp/commit-msg.txt
```

**Never run these in this repository:**

| Command | Why it is forbidden here |
|---|---|
| `git add .` | Stages every untracked file, including local configs, credential files and scratch output. Multiple tracked files must not enter a commit by accident. |
| `git add -A` | Same problem, plus deletions. |
| `git reset --hard` | Silently destroys uncommitted work. There is no undo. |
| `git checkout -- <file>` | Same, one file at a time. |

Stage **explicitly, path by path**:

```bash
git add agx_mcp/mcp_server.py tasks/my_task.md
git diff --staged          # read what you are about to commit
git commit -m "..."
```

If you find yourself wanting `reset --hard`, stop and ask in the issue instead.
In practice the answer is almost always `git stash`.

---

## 5. Schema changes (DDL)

**`agx.sql` is committed. The database is not.**

If you change a table — add a column, change a default, add an index — the DDL
must land as a file change in the same PR:

1. Apply the change to your local database.
2. Update `agx.sql` to match.
3. Run `python3 scripts/verify_schema.py` and get a clean exit.
4. Commit `agx.sql` with the code that needs it.

A change that exists only in your local MySQL is a change that does not exist.
The next person to install from scratch will get a schema that cannot run the
code they just pulled.

For changes that need one-off data migration, say so in the PR body and describe
how to run it. Do not put migration logic in `install.py` without discussing it
first.

---

## 6. Task templates

A task template has **three landing points**. Missing one is the most common
mistake in this repository, because nothing fails loudly when you miss it.

| # | What | Where |
|---|---|---|
| 1 | The manual | `tasks/<task_name>.md` |
| 2 | The publish manifest | `agx_struct.json` → `tasks` section |
| 3 | The stored row | `agx_task_base` (written by `install.py` at install time) |

`agx_struct.json` is what `install.py` walks to decide which tasks exist on a
customer machine. A markdown file that is not listed there is never installed.

After editing, run:

```bash
python3 scripts/verify_tasks.py
```

and include the output in your PR body.

---

## 7. Pull requests

A PR body must answer three questions:

1. **What was wrong?** The failure mode, not the file list. If it is a bug,
   include how to reproduce it.
2. **What changed?** One or two sentences. Link the lines if they are not
   obvious.
3. **How was it verified?** Paste the commands you ran and their result:
   `py_compile`, `ruff`, `verify_schema.py`, `verify_tasks.py`, `pytest`, or a
   manual reproduction.

Checklist:

- [ ] `python3 -m py_compile` passes on every changed Python file
- [ ] `uv run ruff check .` passes
- [ ] `python3 scripts/verify_schema.py` exits 0 (if DDL or `agx.sql` changed)
- [ ] `python3 scripts/verify_tasks.py` exits 0 (if `tasks/` or `agx_struct.json` changed)
- [ ] `uv.lock` updated in the same commit as any `pyproject.toml` change
- [ ] No credentials, tokens, local configs, or logs in the diff
- [ ] No unrelated formatting churn

Keep PRs small. A 40-line PR gets reviewed the same day; a 400-line PR sits
until someone has an uninterrupted hour.

---

## 8. Issues

Before opening an issue, **search existing issues** — open and closed. Most
"it doesn't start" reports have already been answered.

A useful issue contains:

- What you ran (exact command, exact version or commit)
- What you expected
- What happened (the actual output, not a paraphrase)
- The platform (OS, Python, MySQL, Redis versions)
- Whether a clean machine reproduces it

Please do not use the issue tracker for security reports. See `SECURITY.md`.

---

## 9. Review

- One maintainer approval is required to merge.
- Reviewers may ask you to split a PR. That is not a rejection; it is usually the
  fastest path to getting it merged.
- If a PR goes quiet for more than two weeks, comment on it. Nothing is being
  ignored on purpose — it just fell below the waterline.
- Disagreement about design is fine and expected. Disagreement about facts is
  settled by running the thing.

---

## 10. License

By contributing, you agree that your contributions are licensed under the same
license as this repository. See `LICENSE`.
