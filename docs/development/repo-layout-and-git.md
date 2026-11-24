# Agent Directory Layout and Git-Onboarding Guide

> Document purpose: **a reference for future AgentGeeX users (and for Agents running inside the system)**
> First practice: 2026-10-25, on the two experiment machines `10.0.0.12` (test_srv_a) and `10.0.0.13` (test_host_srv)
> Related code: `install.py` → `agent_home_rel_dir()` / `resolve_agents_dir_name()` / `resolve_default_workdir()`

---

## 0. In One Sentence

**Make the repository structure equal the on-disk structure.**

Wherever a directory lands on disk, it sits at the same place in the repository — no path mapping, no symlinks, no copying. Once that holds, everything under `host/` (including an Agent's own workspace files, memory and logs) can be committed, pushed and pulled like any ordinary directory.

---

## 1. What the Directory Tree Looks Like

```
~/.agx/
├── agx_agents/                        ← both the on-disk root and the git repository root
│   ├── .git/
│   ├── agentgeex_agent/               ← globally unique agent (flat)
│   │   └── workspace/
│   ├── agx_core/
│   │   └── workspace/
│   ├── ...(other ordinary agents, also flat)
│   └── host/                          ← one-per-host agents all live here
│       ├── agx_host_srv_a/
│       │   └── workspace/
│       └── agx_host_srv/
│           └── workspace/
```

### 1.1 Why `host/` Needs Its Own Level

The two kinds of agent have **different namespace semantics**:

| Kind | Semantics | Count | Landing point |
|------|------|------|------|
| Ordinary agent | Globally unique (`agentgeex_agent` exists only once on the whole network) | one | `agx_agents/{agent_id}/` |
| `per_host_unique` agent | **One per host** (`agx_host_<hostname>`, N copies for N machines) | N | `agx_agents/host/{agent_id}/` |

Mixing them at one level would squeeze `agx_host_srv_a` / `agx_host_srv` / `agx_host_xxx` in with business agents, and looking at the directory you could not tell which one is "one per machine". Splitting them into `host/` makes it obvious at a glance, and **in git it becomes naturally possible to check out selectively per machine** (see §3).

### 1.2 Only `agx_host` Goes Under `host/`

The decision is driven by the `per_host_unique` field in `agx_struct.json`:

```json
{ "agent_name": "agx_host", "agent_id": "agx_host", "per_host_unique": true }
```

Right now **`agx_host` is the only one**. If another "one per host" agent is added in the future, just mark it `per_host_unique: true` in `agx_struct.json`; its landing point is handled uniformly by `agent_home_rel_dir()` and nothing else needs changing.

### 1.3 A host agent's ID suffix = the host name

The `test_srv_a` in `agx_host_srv_a` is not random hex; it is the **sanitized host name**:

```
host_id: test_srv_a     →  agent_id: agx_host_srv_a
host_id: test_host_srv  →  agent_id: agx_host_srv
```

Sanitization rules: lowercase, replace anything not matching `[a-z0-9_]` with `_`, strip leading and trailing underscores.

> **Why not random hex**: the ID is meant to be read by humans. A random number does not tell you which machine it is, and when an Agent drifts you must identify the machine by name.

---

## 2. Two Landing Schemes, Old and New (backward compatibility)

The project supports both directory naming schemes and **trusts whatever actually exists on disk** — no forced migration:

| | Old structure | New structure |
|---|---|---|
| Default root | `~/.agx` | `~/.agx` |
| agents directory name | `openclaw_agents` | `agx_agents` |
| host-type landing point | `openclaw_agents/agx_host/` (**bare directory, no `host/`**) | `agx_agents/host/agx_host_<hostname>/` |

### Resolution order (`resolve_default_workdir()`)

```
~/.agx exists      → use ~/.agx
else ~/.agx exists → use ~/.agx
else               → ~/.agx (new-install default)
```

### Resolution order (`resolve_agents_dir_name()`)

```
<workdir>/agx_agents exists      → agx_agents
<workdir>/agx_agents exists → agx_agents
workdir is .agx (the only case)  → agx_agents
anything else                    → agx_agents
```

> **Why the old structure does not get a `host/` level**: on `test_host_mac`, `agx_host` has historically lived in the bare `openclaw_agents/agx_host/` directory, and refactoring it is expensive (the openclaw registration path, systemd units and historical data would all have to move with it), so it is deliberately left alone. **Only the new structure `agx_agents` gets the `host/` subdirectory.**

---

## 3. Git Onboarding: How `host/` Gets Into the Repository

This is the core of the document. **Git is the user's own business; the AgentGeeX project does not touch git.**

### 3.1 The project does exactly one thing: put directories in the right place

The responsibility boundary of `install.py` (this is a hard constraint):

> **The project only owns directory layout** — `host`-type agents go into `host/`, other agents stay outside it.
> **git belongs entirely to the user** — no `git init`, no sparse configuration, no remote handling, no commits.

The single choke point in code:

```python
# install.py:159
def agent_home_rel_dir(agent_name, unique_id="", agents_dir_name="") -> str:
    aid = unique_id or agent_name
    _is_new = (agents_dir_name or AGENTS_DIR_NEW) == AGENTS_DIR_NEW
    if agent_name == "agx_host" and _is_new:
        return f"{HOST_SUBDIR}/{aid}"     # HOST_SUBDIR = "host"
    return aid
```

Every place that needs to compute an agent's landing point must call it; **nobody may concatenate `agents_dir / agent_dir_name` by hand** (historically 4 places bypassed it, which caused host agents to land on the wrong level; fixed on 2026-10-25 in `970d246`).

### 3.2 The user side: make the repository land in that same directory

> ⚠️ **Key prerequisite**: `git clone` can only clone into a directory that **does not exist or is empty**. But `~/.agx/agx_agents` already contains agents installed by AgentGeeX — **so clone cannot be used**.

**The correct approach** (create the repository inside the existing landing directory):

```bash
cd ~/.agx/agx_agents

# 1. Create the repository (not clone)
git init
git remote add origin git@gitee.com:<your account>/<your repo>.git

# 2. Configure the sparse-checkout whitelist first, then fetch/checkout
#    (order matters: set the whitelist first so checkout only pulls what you want)
```

### 3.3 How to Write the `sparse-checkout` Whitelist

Suppose this machine only wants "its own host plus the business agents it needs":

```bash
cat > .git/info/sparse-checkout <<'EOF'
/host/agx_host_srv_a/
/agentgeex_agent/
/agx_core/
/agx_agentfather/
/agx_projectmother/
/xplane_toliss_dev/
EOF
```

Then:

```bash
git fetch origin
git checkout -b main origin/main
```

**Result**: only `agx_host_srv_a` appears under `host/`; other machines' hosts are never pulled down locally.

### 3.4 ⚠️ Three pitfalls you must know (all measured the hard way)

#### Pitfall 1: `sparse-checkout set --no-cone` causes a "full checkout" on old git

`git 2.25.1` (shipped with Ubuntu 20.04) **does not support the `--no-cone` option**. The flag is not rejected; it is treated as a **directory name** and written as the first line of the sparse-checkout file:

```
--no-cone          ← became a line of content, not an option
/*
```

`/*` matches every top-level directory → **all 87 directories checked out in full**, which makes the whole configuration pointless.

✅ **What to do**: skip the `set` subcommand and write the whitelist **directly, one `/dir/` per line** (i.e. the cone-mode whitelist); git 2.25 is fully compatible with that.

#### Pitfall 2: `git clean -fd` deletes a `host/` that has not been committed yet

If `host/agx_host_xxx` is already in the sparse whitelist but **the directory does not exist in the repository yet** (never committed/pushed), `git clean -fd` treats it as an "extra file" and deletes it.

✅ **What to do**:
```bash
git clean -nd      # preview first! see what it intends to delete
git clean -fd      # execute only after confirming
```

> In this practice session it really was deleted once; it was recovered from a `host_backup_*.tgz` taken beforehand, and every file was checked one by one — 8/8 intact.

#### Pitfall 3: `git clone` into a non-empty directory fails → **do not move the repository somewhere else because of it**

This is the easiest place to go off track. The correct reaction to a failed `git clone` is "switch to `git init` + `remote add`" (§3.2), **not** "create another directory to serve as the repository".

Once you create another directory you get exactly this split:

```
~/.agx/agx_agent/     ← the repo was created here (only the few directories sparse pulled)
~/.agx/agx_agents/    ← the system actually lands here, but it is not the repo
    host/agx_host_xxx/    ← lands here and can never enter the repo
```

✅ **What to do**: insist that the repository root = the landing root.

---

## 4. What `host/` Looks Like Once Onboarded

```
$ cd ~/.agx/agx_agents
$ git ls-files host/
host/agx_host_srv_a/workspace/AGENTS.md
host/agx_host_srv_a/workspace/IDENTITY.md
host/agx_host_srv_a/workspace/SOUL.md
host/agx_host_srv_a/workspace/USER.md
...
```

Note that **`git ls-files` shows the full index** (every file the remote has, including other machines' hosts). To see **what is actually checked out locally**, look at the disk:

```bash
ls host/                              # only this machine's copy
find host -type f | wc -l
cat .git/info/sparse-checkout | grep host
```

Measured results on the two experiment machines:

| | 10.0.0.12 | 10.0.0.13 |
|---|---|---|
| host directory checked out on disk | `agx_host_srv_a` | `agx_host_srv` |
| host file count on disk | 4 | 8 |
| were other machines' hosts pulled down | ❌ no | ❌ no |
| hosts in the remote repository | both present (12 files) | same as left |

### 4.1 An Agent's own files go into the repository too

`AGENTS.md`, `IDENTITY.md`, `SOUL.md`, `USER.md` and `memory/` under `host/agx_host_xxx/workspace/` are all ordinary files — once the directory is in the repository they can be `git add` / `commit` / `push`ed normally.

**This is the biggest value of onboarding `host/`**: an Agent's memory and persona configuration no longer live only on one machine's disk; they can enter version control, sync across machines, and be rolled back.

---

## 5. Multi-Instance Deployment: Several Paths Pointing at One Entity (optional)

> This section describes an **optional** deployment shape. The default shape (repository = landing root, see §0) requires nothing at all.
> It applies only when "one machine runs several openclaw instances" or "the instance-side directory and the version-control directory must be separated".

### 5.1 First, See How Today's Sharing Is Actually Done

Many people assume "sharing" needs some special mechanism; in fact the overwhelming majority of cases are **one checkout, shared naturally**:

| Machine | Layout | How sharing works | Any copying |
|---|---|---|---|
| Mac | `~/.agx/agx_agents/` = full checkout (88 agent directories) | one checkout root; each agent is a subdirectory of it | none |
| 10.0.0.12 | `~/.agx/agx_agents/` = sparse checkout (86 whitelist entries) | one version-control repository + sparse whitelist selecting directories | none |
| 10.0.0.12 second instance | `~/.agx/agx_agents/<agent>/workspace` → symlink → the tree above | path mapping | none |

**Conclusion: as long as every instance's workspace lands inside the same checkout, there is no "cloning multiple copies".**
The only case that really duplicates is when some instance's `work_dir` points at another directory and the deployment writes a physical copy there.

### 5.2 ★ Direction Decides Success or Failure

A symlink has two possible directions, one works and one does not — this is the easiest thing to get wrong in this whole document:

| Approach | What git sees | Verdict |
|---|---|---|
| Symlink **inside** the repository pointing outside it | a `120000` text blob | ❌ does not work (this is the case described in §7's misconceptions) |
| Symlink **outside** the repository pointing at an entity inside it | a real file | ✅ this is what this chapter does |

One-line criterion: **the entity must be inside the repository; the symlink can only be outside it**.

### 5.3 How To Do It (three steps)

```bash
# 1. Create the directory on the instance side and symlink it to the entity in the version-control tree
mkdir -p ~/.agx/agx_agents/<agent_id>
ln -sfn ~/.agx/agx_agents/host/<agent_id>/workspace \
        ~/.agx/agx_agents/<agent_id>/workspace

# 2. Change that instance's own configuration (both places, do not change only one)
#    ~/.openclaw-<profile>/openclaw.json
#    agents.entries.<agent_id>.workspace
#    agents.defaults.workspace

# 3. Restart that instance
systemctl --user restart openclaw-<profile>.service
```

From then on, day-to-day work is only `git pull` / `git commit` / `git push` in the repository directory — the instance side sees the changes because it is the same path.

### 5.4 ★ Hard Constraint: openclaw Identifies Workspaces by realpath

You must know this, otherwise you will hit an error that looks like "symlinks are not supported".

```
Error: workspace path alias points to a different current target
```

The mechanism (measured plus reading the source):

- a workspace identity = `sha256(real path realpath)`, not the literal path
- the same path registers **two aliases**: the literal path and the real path, both pointing at the same identity
- the error is raised only when "the store already has an old identity for this path, and the current real path differs from the old one"

So the rule is:

| Situation | Need to clear state? |
|---|---|
| Symlink points at the **same** real directory (only the literal path changed) | **No**, it just works |
| Symlink points at **another** real directory (a different entity) | **Must clear**, otherwise you get the error above |

When clearing is needed (stop the instance before touching the store):

```bash
systemctl --user stop openclaw-<profile>.service
cp ~/.openclaw-<profile>/state/openclaw.sqlite /tmp/state.bak-$(date +%s)
# table workspace_path_aliases: delete the old rows for this workspace's literal path
systemctl --user start openclaw-<profile>.service
```

From this follows a deployment discipline: **either create the symlink from the start, or do not create it at all**. Landing a physical copy first and converting it to a symlink later amounts to changing the real target, and state must be cleared once.

### 5.5 Verification (check all four; none can be skipped)

| # | What to verify | How | Expectation |
|---|---|---|---|
| 1 | the instance accepts the path | after restart, watch the log for workspace errors | no `workspace path alias` error |
| 2 | it reads the copy in the repository | have the agent report `readlink <workspace>` / `pwd -P` / the first line of `AGENTS.md` | `pwd -P` resolves to the repository entity path, and the first line matches the repository file |
| 3 | writes pass through to the repository | have the agent write a file in the workspace, then `ls` the repository entity directory | the file shows up in the repository |
| 4 | **version control is not truncated** | have the agent run `git rev-parse --show-toplevel` in the workspace | it lands on the **repository root** (not the workspace itself) |

> ⚠️ Item 4 was added at wrap-up on 2026-11-04, because items 1/2/3 can all be green and still miss it.
> At the time, test_srv_a2's workspace contained an empty repository created on 2026-11-04 at 10:48 (`.git`, 0 commits, no remote).
> The consequence: `pwd -P` was correct, file reads and writes were normal, skills were normal, but `git rev-parse --show-toplevel`
> stopped right at its own feet — meaning **no git operation inside that instance could reach the central repository**.
> `pwd -P` can never detect this deviation: the symlink is fine; what is wrong is that the symlink target has yet another repository nested inside it.
>
> Criterion: `git -C <workspace> rev-parse --show-toplevel` must equal the repository root.
> Not equal = there is a nested `.git`; handle it with `mv <workspace>/.git /tmp/xxx.bak` (confirm it is an empty repository before moving it).

**A dry run that does not touch production state** (verify a path is accepted before changing the config):

```bash
OPENCLAW_CONFIG_PATH=<production config> OPENCLAW_STATE_DIR=/tmp/probe-state \
  openclaw agent --local --agent <agent_id> -m "report readlink and the first line of AGENTS.md"
```

This borrows the production credentials to run a real turn, but the state is brand new and isolated — no keys copied, no production store polluted.

### 5.6 Recommended Topology: Put the Checkout in a Third-Party Directory (verified on 10.0.0.12)

If the goal is "the instance can be installed and removed at will without affecting the repository", put the repository checkout somewhere unrelated to the openclaw installation directory:

```
~/repos/agents_central/                                   ← third party: the single entity (git checkout)
├── .git/
└── host/agx_host_srv_a/workspace/

~/.agx/agx_agents                      → ~/repos/agents_central                        (compatibility symlink, see below)
~/.agx/agx_agents/<agent>/workspace → ~/repos/agents_central/host/<agent>/workspace   (one symlink per instance)
```

**Why the checkout belongs in a third party**: deleting an instance = deleting a symlink + uninstalling openclaw, and the repository is untouched; drifting/rebuilding = creating the symlink again in the other direction, with no repository migration. The lifecycles of repository and instance are completely decoupled.

**Why the compatibility symlink at `~/.agx/agx_agents` still has to stay**: `assistant_files_root_rel_template = "agx_agents/{agent_id}/workspace/assistants"` in `agx_config.json` is resolved **relative to `work_dir`**
(see `agx_mcp/agx_core.py:_resolve_assistant_rel_template`). Delete the old path entirely and the assistant file root points at a non-existent
path, which then gets created on the spot by `makedirs` — leaving behind an extra set of empty directories. Keeping one deletable symlink is the least trouble.

**Removal / drift runbook** (three steps, none touch the repository):

```bash
# 1. Delete that instance's symlink
rm ~/.agx/agx_agents/<agent_id>/workspace
# 2. Uninstall the instance
systemctl --user disable --now openclaw-<profile>.service
# 3. The repository is untouched; a new instance just recreates the symlink
```

Measured on 10.0.0.12 on 2026-11-04 (two instances pointing at the same third-party repository):

| Instance | Result | Key evidence |
|---|---|---|
| main instance `agx_host_srv_a` (18789) | ✅ succeeded, first try | `pwd -P` = `/home/<user>/repos/agents_central/host/...`, `git rev-parse --show-toplevel` = `/home/<user>/repos/agents_central` |
| second instance `agx_host_srv_a2` (19789) | ⚠️ failed first → ✅ succeeded after clearing state | failure cause in §5.7 |

### 5.7 Executable Recipe for Clearing Workspace State

```bash
# 1. Stop the instance first (the store is in use; do not edit it directly)
systemctl --user stop openclaw-<profile>.service
cp ~/.openclaw-<profile>/state/openclaw.sqlite /tmp/state.bak-$(date +%s)

# 2. Delete the old alias rows for this workspace (both the literal path and the real path are in the table)
node --experimental-sqlite -e '
const {DatabaseSync}=require("node:sqlite");
const d=new DatabaseSync(process.env.HOME+"/.openclaw-<profile>/state/openclaw.sqlite");
const r=d.prepare("DELETE FROM workspace_path_aliases WHERE alias_path IN (?,?)")
         .run("<literal path>","<old real path>");
console.log("deleted:", r.changes);'

# 3. Start the instance
systemctl --user start openclaw-<profile>.service
```

**Tooling limitation** (measured the hard way): `workspace_path_aliases` uses a SQLite `STRICT` table.
On a machine without the `sqlite3` CLI, or with only Python 3.8, both report
`malformed database schema (...) near "STRICT"`. Node 22's built-in `node:sqlite` (requires `--experimental-sqlite`) is the most reliable option.

**★ Silent trap: there are two kinds of path change, one errors and one does not — the second is nastier**

| Case | Behavior | Consequence |
|---|---|---|
| the same **literal path** is repointed at a new entity | hard error `workspace path alias points to a different current target` | at least you know something broke |
| switching to a literal path that was **never registered** (like the main instance in this round) | **no error, works normally** | it is treated as a brand-new workspace; the old workspace's state (memory index, session mounts, etc.) is **not inherited** |

So acceptance cannot be limited to "no error appeared"; you must also check **whether the state came along** (§5.5 item 2).

### 5.8 Why This Is Not Written Into `install.py`

| Reason | Explanation |
|---|---|
| Not required for installation | install aims at "get the system running within ten minutes"; symlinks are an optional deployment optimization |
| It would widen the failure surface | Windows without developer mode, cross-drive letters and container mounts can all degrade a symlink into a plain file |
| The decision belongs to the deployer | Which layout to use is the user's call; the project should not decide it for them |

So: **the document provides the recipe, and `install.py` stays a single tree landing directly.**

---

## 6. Acceptance Checklist

After deploying to a new machine, or after reworking one, confirm every item in this table:

| # | Check | Command | Expectation |
|---|---|---|---|
| 1 | landing point correct | `ls ~/.agx/agx_agents/host/` | only this machine's `agx_host_<hostname>` |
| 2 | it is a git repository | `git -C ~/.agx/agx_agents rev-parse --short HEAD` | output present, no error |
| 3 | working tree clean | `git -C ~/.agx/agx_agents status --porcelain` | empty |
| 4 | this machine's host is tracked | `git -C ~/.agx/agx_agents ls-files host/ \| grep <this machine>` | files listed |
| 5 | untracked files = 0 | `git -C ~/.agx/agx_agents status --porcelain host/` | empty |
| 6 | sparse whitelist contains only this machine's host | `grep host .git/info/sparse-checkout` | only `/host/agx_host_<this machine>/` |
| 7 | openclaw registration path correct | check the workspace path in `~/.openclaw/openclaw.json` | points at `agx_agents/host/agx_host_<this machine>/workspace` |
| 8 | messages flow both ways | dispatch a task to the host agent and check the receipt | `succeeded`, and hostname matches host_id |

Item 8 is the most important — **the directory being right does not mean the link works**. You must actually dispatch work to verify it, and check that the `hostname` in the receipt matches the `host_id` of the dispatch record (to prevent a task from running on the wrong host).

---

## 7. Common Misconceptions

| Misconception | Reality |
|---|---|
| "The repository and the landing directory must be two different directories" | No, and they should not be. The same directory is the least trouble |
| "Symlink the host into the repository" | ❌ in git it is only a `120000` text blob; on Windows without developer mode it degrades into a plain file. Note: **the reverse direction works** — the entity lives in the repository and a symlink outside it points at it (see §5) |
| "Copy one in at deploy time with code" | ❌ two copies to maintain, and consistency cannot be guaranteed |
| "Compute relative paths with a mapping function" | Not needed — make the structure itself consistent, zero mapping |
| "`sparse-checkout` can check directories out somewhere else" | ❌ it only selects **which** directories are checked out; it cannot change **where** they land |
| "`git mv` cannot change the level of an already-committed directory" | It can; `git mv a b` preserves history |
| "Just throw a `git init` into `install.py`" | ❌ the project does not touch git; the user's repository structure is the user's decision |

---

## 8. Where the Code Lives

| Location | Role |
|---|---|
| `install.py:119-123` | `DEFAULT_WORKDIR_NEW=".agx"` / `DEFAULT_WORKDIR_LEGACY=".agx"` / `AGENTS_DIR_NEW="agx_agents"` / `AGENTS_DIR_LEGACY="openclaw_agents"` / `HOST_SUBDIR="host"` |
| `install.py:126-138` | `resolve_default_workdir()` — workdir priority |
| `install.py:141-156` | `resolve_agents_dir_name()` — old/new directory-name decision |
| `install.py:159-175` | `agent_home_rel_dir()` — **the single source of truth for landing points** |
| `install.py:178-189` | `_sanitize_host_suffix()` — host name → ID suffix |
| `agx_struct.json` | `per_host_unique` declares which agents are "one per host" |
| `scripts/dsh_register_agents.py` | ⚠️ known defect: `discover_agents()` only scans one level, so agents under `host/` are missed (to be fixed) |

---

## 9. Change Log

| Date | Change |
|---|---|
| 2026-10-25 | The new structure `agx_agents` introduces the `host/` subdirectory; the 4 places in `install.py` that bypassed `agent_home_rel_dir()` are consolidated (`970d246`) |
| 2026-10-25 | The ID suffix for `per_host_unique` agents changes from random hex to the host name (`_sanitize_host_suffix`) |
| 2026-10-25 | Rebuild verification completed on the two experiment machines 10.0.0.12 / 10.0.0.13: identical HEAD, each host onboarded, message path working |
| 2026-11-04 | Added §5 "Multi-instance deployment: several paths pointing at one entity": symlink direction, the `workspace_path_aliases` hard constraint, verification methods; explicitly not going into `install.py` |
| 2026-11-04 | The second instance on 10.0.0.12 (`test_srv_a2`, 19789) measured end to end: instance side `~/.agx/openclaw_agents/agx_host_srv_a2/workspace` → symlink → `~/.agx/agx_agents/host/agx_host_srv_a2/workspace`, a real turn passed (read AGENTS.md / 23 skills / write-through) |
| 2026-11-04 | Added §5.6/§5.7: recommend putting the checkout in a third-party directory (`~/repos/agents_central`) + one symlink per instance; measured passing with two instances on 10.0.0.12 pointing at the same third-party repository; added an executable recipe for "clearing workspace state" and the silent trap |
| 2026-11-04 | §5.5 acceptance gains item 4: you must run `git rev-parse --show-toplevel` to confirm it lands on the repository root — a nested empty `.git` makes `pwd -P` fully green while version control is truncated (measured on the test_srv_a2 instance) |
