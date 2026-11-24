# Agent Migration Runbook — moving an entire project group from one host to another

> Document type: an **operational runbook**, not a design document. Just follow it; every step has a verifiable gate.
> First practice run: 2026-11-02, the 5 Agents of project group `book_up_chain`: `test_host_mac` (macOS) → `test_host_srv` (Linux).
> Instance data is in §9; the pitfalls we hit are in §6 — **read §6 at least once before you start**.
> Scope: migrating a whole project group between hosts inside the same network (same MySQL + Redis).
> Out of scope: cross-network migration (the databases are not even co-located), moving a single Agent that still has tasks running, migrating session history across platforms.

---

## 0. Summary in one sentence

A migration is not "copying files" — it is making **five places agree about the same fact**:

```
① database  agx_agent_registry.host_id            ← which machine owns which agent (dispatch basis)
② cache     Redis agx:entity:agent:<id> host_id    ← what the runtime actually decides on
③ files     <agents_repo>/<id>/workspace/         ← travels with git (everything the agent owns)
④ state     ~/.openclaw/agents/<id>/              ← session store, not in git, easiest to forget
⑤ registry  ~/.openclaw/openclaw.json entries     ← whether the target host recognizes this agent
```

**Move ③ without ④ → the Agent starts up but has amnesia; change ① without ② → dispatches land on the old host; miss one of the five → the symptoms are "a task was dispatched but nobody picked it up" and "the agent exists but does not respond", with no error anywhere.**

The place that goes wrong is rarely the command — it is the **judgement**: which data travels with git, which stays on the source host, which must be backed up but not migrated. §1 nails this down first.

### 0.1 Stage order: register first, switch second, **verify first, delete last** (proven in the second migration, 2026-11-02)

The numbering below follows the first practice run (§5 change the database → §6 uninstall → §7 land on the target), but **the actual execution order should be this**:

```
pre-check → clean source repo → backup → manuscript in place
  → ⑤ register on the target host with `agents add`   (both hosts usable at once)
  → ① + ② change host_id and refresh the cache        (the switch point; the DB decides who gets work)
  → dispatch an acceptance probe to every member      ★ do this BEFORE deleting the source host
  → ⑥ source host: gateway stop + delete one by one   (business already proven to run on the new host)
  → source host: git checkout to restore the workspace
  → path rewrite → wrap-up
```

**Why this order is safer:**

| Change | First practice | Improved | Guarantee gained |
|---|---|---|---|
| ⑤ when to register | registered on the target only after changing the DB | register **before** changing the DB | during the switch window both hosts are registered and runnable; who gets work is decided solely by ① |
| when to verify | verified after deleting the source host | verify **before** deleting it | if the target host does not work, the source host is still there and rollback is just changing `host_id` back |
| precondition for deletion | deleted right after changing the DB | delete only when every probe is OK | there is never a window where "neither side is usable" |

★ **Verification must precede deletion** — this is the most important correction of the second migration. Verifying after deleting means you deleted your only way back.

★ The criterion for acceptance is not the text of the receipt, but **physical artifacts on the target host's disk**: have each agent write a probe file into its own workspace, then ssh in and `cat` it yourself — a receipt can be a paraphrase, a file cannot.

---

## 1. Three things to settle before you touch anything

### 1.1 Three kinds of data, three different fates

| Data | Location | Size (instance) | Migrate? | Reason |
|---|---|:---:|:---:|---|
| workspace (md docs, memory, notes, outputs) | `<agents_repo>/<id>/workspace/` | a few MB | **yes** (via git) | it is the Agent's memory and personality, and it is already in the repo |
| session history (session sqlite) | `~/.openclaw/agents/<id>/` | 104 / 28 / 41 / 44 / 60 MB | **no**, back it up only | tens of MB per agent, 300+ MB for five; value is lower than cost |
| openclaw registry entries | `~/.openclaw/openclaw.json` | a few KB | **do not copy**; rebuild on the target | what matters is the target host's own `agent-dir` path |

> **State the cost of "not migrating session history" plainly**: the Agent starts over on the new host and
> **does not know what it talked about before**.
> But its `memory/*.md`, `MEMORY.md`, `notes/` and `outputs/` are all in git and travel with it —
> so it is "does not remember the conversation", not "does not remember the conclusions". In practice that is plenty.
> The only purpose of backing up sessions is **rollback**, not migration.

### 1.2 What not to move

- **Source code / manuscripts / business data repos**: they have their own git and host ownership; sync them separately.
- **`*.sqlite` and `-wal`/`-shm`**: session databases were once committed into the agents repo by mistake (instance: 3 files); you must `git rm --cached` them and add them to `.gitignore` first, otherwise one migration drags hundreds of MB and historical dirty data into the repo.
- **Node caches, `node_modules`, `__pycache__`**: regenerate them on the target.

### 1.3 One trap that must never be handled with a blanket replacement

**The source path prefix is not the target path prefix.** It looks like swapping `/Users/<user>` for `/home/owner` will do, but it will be wrong:

```
source /Users/<user>/project/book/upper_chain     ← the upper_chain manuscript
target /home/owner/project/book/upper_chain        ← ✗ this path exists! but it is the repo of ANOTHER book
target /home/owner/project/upper_chain             ← ✓ this is the manuscript root you want
```

**"Same name, different thing" is more dangerous than "path does not exist"**: a missing path errors out immediately, an existing path silently reads and writes the wrong thing.
In the instance I replaced it wrongly at first and only caught it because of the gate "the target path should have 25 top-level entries and 312 tracked files".

**Rule**: for every absolute path you change, run `ls` on the target afterwards and check a characteristic (entry count / `git rev-parse HEAD` / byte size of a key file).

---

## 2. Stage 0 — pre-flight inventory (change nothing)

Run this first to turn "what has to move" into a definite table. If anything differs from expectations, **stop and ask; do not decide on your own**.

```sql
-- ownership, deploy status and role of the 5 agents
SELECT agent_id, agent_name, agent_role, host_id, deploy_status, project_id, active_status
FROM agx_agent_registry WHERE agent_id IN (...);

-- is anything running (if yes → wait for it to finish first)
SELECT task_run_uuid, agent_id, run_status, TIMESTAMPDIFF(MINUTE, started_at, NOW()) mins
FROM agx_task_execution WHERE run_status = 'running';

-- is anything queued (pending executions will be picked up on the new host after migration)
SELECT COUNT(*) FROM agx_task_execution WHERE agent_id IN (...) AND run_status = 'pending';

-- are there plans attached to them
SELECT plan_id, plan_status, host_id FROM agx_plan WHERE created_by IN (...);
```

On the target host:

```bash
# host identity (the host_id written into the DB must be this value)
openclaw status | head
hostname

# is the same version installed (a version mismatch gives you "works on the control host, not on the new one")
node -v && openclaw --version

# free disk: agents repo + per-agent state directory
df -h /home
```

On the source host:

```bash
# entry count (use it to check additions and removals before and after the migration)
python3 -c "import json;print(len(json.load(open('$HOME/.openclaw/openclaw.json'))['agents']['entries']))"

# ★ note: `agents` in openclaw.json is a dict and the real entries live under ['agents']['entries']
#   len(config['agents']) alone returns 3 (defaults/ownership/entries), not the entry count

# state directory size per agent
du -sh ~/.openclaw/agents/<id>
```

**Pre-flight gate**: `running = 0`, `pending = 0`, all `host_id` equal the source host, target free disk > 3× the agents repo size.

---

## 3. Stage 1 — make the repo clean first (source host)

Goal: guarantee that "what the target pulls == what is on the source disk". If the repo is dirty, every later file-count check is meaningless.

```bash
cd <agents_repo>

# 1) detach session databases committed by mistake (keep the files on disk)
git rm --cached <id>/agent/openclaw-agent.sqlite
git rm --cached <id>/agent/openclaw-agent.sqlite-wal
git rm --cached <id>/agent/openclaw-agent.sqlite-shm

# 2) add an ignore rule so it cannot happen again
printf 'openclaw-agent.sqlite*\n' >> .gitignore

# 3) commit the backlog of documentation changes in one go
git add -A && git commit -m "chore: commit pending workspace notes before migration"
git push origin main
```

**Gate**: `git status` is empty; `git rev-parse HEAD == git ls-remote origin main`.

> Instance: this step also committed 13 memory notes that had never been tracked — had we skipped it, those notes would have existed only on the source disk, invisible to the target, and the source would later be cleaned up, so **the memory would have been lost permanently**.

---

## 4. Stage 2 — backup (do it once, do it thoroughly)

```bash
BK=~/agx_backups/<project>_migrate_<YYYYMMDD>
mkdir -p $BK/{session_state,db,config}

# 1) session stores — ★ must use sqlite3 .backup, never cp
#    cp grabs a half-written file with an unmerged WAL; .backup uses sqlite's own online backup protocol
for a in <id1> <id2> ...; do
  mkdir -p $BK/session_state/$a
  for db in ~/.openclaw/agents/$a/*.sqlite; do
    [ -f "$db" ] && sqlite3 "$db" ".backup '$BK/session_state/$a/$(basename $db)'"
  done
done

# 2) database snapshot (registry rows and session rows before the migration)
#    registry_before.json         —— 5 rows including host_id; rollback restores from it
#     agx_session_rows.json       —— the project's session rows (instance: 561 rows)

# 3) ★ archive directories that are not sqlite —— searching only *.sqlite misses them
#    Measured (second migration, 2026-11-02): session-sqlite-import-archive/ holds 52
#    *.trajectory.jsonl.imported-* files, 44M, and contains no sqlite at all, so the loop above misses it.
#    Deleting the source state directory removes it too, and it is not in the backup → you find out when it is already gone.
for a in <id1> <id2> ...; do
  ARC=~/.openclaw/agents/$a/session-sqlite-import-archive
  [ -d "$ARC" ] && tar -czf "$BK/${a}_trajectory_archive.tar.gz" -C ~/.openclaw/agents/$a session-sqlite-import-archive
done

# 4) config snapshot
cp ~/.openclaw/openclaw.json $BK/config/openclaw_entries_before.json
```

> ★ **Gate on file count, not just on size**: `tar -tzf archive | grep -v '/$' | wc -l` must equal
> `find source_dir -type f | wc -l`, then compare md5 of every file (zero differences). `du -sh` alone lets missing files through.
> Measured: 44M compressed to 10M; only when all 52 file md5s match is the backup real.

**Gate**: `du -sh $BK` is in the same order of magnitude as the total source state directories (instance: 279M); `integrity_check` returns `ok` everywhere:

```python
python3 - <<'PY'
import sqlite3, glob
for f in glob.glob('**/*.sqlite', recursive=True):
    try:
        print(f, sqlite3.connect(f).execute('PRAGMA integrity_check').fetchone()[0])
    except Exception as e:
        print(f, 'ERR', e)
PY
```

> ⚠️ **The sqlite 3.20.1 shipped with macOS reports STRICT tables as `malformed database schema`**.
> Re-check with `python3`'s sqlite (3.53); do not "repair the database" just because the CLI says malformed — the tool is simply too old.

Also write a `RESTORE.md` stating what was backed up, how to restore it, and the rollback order. **When things go wrong people panic; do not count on thinking it through live.**

---

## 5. Stage 3 — change the database + refresh the cache (the service still runs on the source host)

### 5.1 Change ownership

```sql
UPDATE agx_agent_registry SET host_id='<target_host>' WHERE agent_id IN (...);
```

**Do not fill in `agent_root_dir` / `agent_workspace_dir`**. Instance: across the whole system these two columns are **NULL** for every deployed Agent, and the runtime derives them from `work_dir`. Filling them in creates an exception for yourself, and every later reader of those two columns needs a patch for the exception.

### 5.2 Refresh the cache (★ must go through an Agent that has permission)

`agx_cache_clear` requires level 7, which an ordinary agent (level ≤5) cannot call. Dispatch it to `agx_core`:

```
agx_dispatch_task(
  agent_id="agx_core",
  instruction="Clear only the cache of <entity_type> <entity_id> with confirm=True; do not clear any other entity.
               Re-read once afterwards and put the actual return value and whether any stale value remains into summary.",
  needs_reply=1)
```

### 5.3 Two-way verification (★ do not trust the report, query it yourself)

```python
# DB side
SELECT agent_id, host_id FROM agx_agent_registry WHERE agent_id IN (...);
# cache side
HGET agx:entity:agent:<id> host_id     # all 5 must equal the new host
```

**Gate**: 5 DB rows + 5 Redis entries, **identical in both places**, and equal to the target host's `hostname`.

> At this point the service is not down yet: the agent still runs on the source host, but the external "ownership" already points at the target. Keep this window as short as possible.

---

## 6. Stage 4 — uninstall on the source host (★ the only stage that can kill the service)

### 6.1 Iron rule: stop the gateway before deleting

`openclaw agents delete` is two steps internally (① delete the config entry ② delete the state directory), and the CLI waits only **10 seconds**.
If it exits on timeout with step ① done and step ② half-done, the next gateway start validates "the config says deleted, the state is still there" and **refuses to start**:

```
OpenClaw agent database is unavailable while agent <id> is deleted.
```

Instance cost: the gateway failed to come up 4 times between 16:06 and 16:12, `ws://127.0.0.1:18789` was fully down, and a cache rebuild was running at the same time → two faults stacked and the investigation went the wrong way for half an hour.

```bash
openclaw gateway stop                 # ① stop first, to avoid the 10-second timeout
openclaw agents delete <id> --force --json
# ② verify immediately, do not rush on to the next delete:
#    entry count -1
#    ~/.openclaw/agents/<id> is gone
openclaw gateway start
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18789/   # expect 200
```

**For batch deletes, go through this loop one agent at a time**; never fire 5 deletes in a row.

### 6.2 A guaranteed slap in the face: the workspace gets emptied

`agents delete` prunes that agent's workspace directory. Instance: after deleting 5 agents, `git status` showed **230 deletions**.

```bash
cd <agents_repo>
git checkout -- <id1> <id2> <id3> <id4> <id5>     # restore them all
git status --porcelain | wc -l                     # expect 0
```

**This is exactly why stage 1 must clean the repo and stage 2 must back it up** — with those two steps the prune is a false alarm; without them it is real data loss.

### 6.3 Incomplete deletion is normal; do not chase it

Instance: after deleting 5 agents, each left a `session-sqlite-import-archive/` behind (25M in total, 22M of it under `book_up_chain`).
Once you have confirmed those ids are gone from entries, just remove them (the backup holds complete originals):

```bash
rm -rf ~/.openclaw/agents/<id1> ~/.openclaw/agents/<id2> ...
```

> A common misunderstanding: `openclaw agents list` still listing an agent does not mean it can run —
> the criterion is always whether it is present in `agents.entries` of `openclaw.json`.

---

## 7. Stage 5 — land on the target host

### 7.1 Non-interactive ssh cannot find `openclaw`

```bash
export PATH=$HOME/.nvm/versions/node/v22.22.3/bin:$PATH
```

**Every remote command needs this** (or put it in the non-interactive branch of `~/.bashrc`). The symptom is `command not found: openclaw`, which is easily misread as "not installed".

### 7.2 Pull and verify

```bash
cd /home/owner/.agx/agx_agents
git pull --ff-only

# four comparisons (all must match)
git rev-parse --short HEAD                                  # == remote
git status --porcelain | wc -l                              # == 0
find . -path ./.git -prune -o -type f -print | wc -l         # == git ls-files | wc -l
git ls-files | wc -l
```

### 7.3 Per-directory alignment (key gate)

```bash
# run on both the source and the target, compare id by id
for a in <id1> ...; do echo "$a $(find $a -type f | wc -l)"; done
```

Instance: `87 / 37 / 35 / 33 / 38`; landing only counts as successful when **each one matches**.
**Equal totals are not enough** — one directory three files short and another three files over still adds up.

---

## 8. Stage 6 — register on the target host

```bash
openclaw agents add <id> \
  --workspace /home/owner/.agx/agx_agents/<id>/workspace \
  --agent-dir /home/owner/.openclaw/agents/<id>/agent \
  --non-interactive --json
```

**Gate** (all three):

```bash
python3 -c "import json;print(len(json.load(open('$HOME/.openclaw/openclaw.json'))['agents']['entries']))"
# expect: previous value +1
openclaw agents list | grep -A2 <id>        # it is listed and Workspace points at the right place
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18789/    # 200, and NO gateway restart is needed
```

### ★ Why not use `agx_deploy_agent`

`agx_deploy_agent` writes the **`agents_md` stored in the DB back into the workspace** during deployment.
The `AGENTS.md` you just pulled from git is the latest source of truth; the copy in the DB may be old — one deploy overwrites it, silently.

**Registration goes through `openclaw agents add` only; `agx_deploy_agent` is for "generate the files from the DB for the first time", not for "the files are already on the target".**

> Instance: after registration, `book_up_chain/workspace/IDENTITY.md` changed from `#` to `#\n- Name: book_up_chain`.
> This is written by OpenClaw identity normalization and is harmless — sync it back to the source and commit and both sides agree;
> leave it alone and the target's `git status` stays dirty forever.

---

## 9. Stage 7 — per-Agent acceptance (do not trust reports)

Dispatch one general task to each Agent asking for 7 measured items; **then ssh in and verify yourself**.

### 9.1 Probe instruction template

```
Which machine are you running on? Answer each item by actually measuring it, not from memory:
1) the host_id / work_dir / project_id / agent_role returned by agx_get_global_config
2) run hostname; does it match the host_id in your previous answer
3) pwd, plus how many *.md files are in that directory (list the file names)
4) create _migration_probe.txt in the workspace containing "<agent> migration acceptance <timestamp>", then cat it back
5) how many top-level entries are in the manuscript/business directory <path>; read any key file and report its byte size
6) call any read-only MCP method and report its return value
7) conclusion: which host this execution ran on; any failed items (say so plainly, do not embellish)
```

### 9.2 Verify it yourself (★ this step is not optional)

```bash
ssh <target> 'cd /home/owner/.agx/agx_agents
for a in <id1> ...; do printf "%-30s %s\n" "$a" "$(cat $a/workspace/_migration_probe.txt 2>/dev/null || echo MISSING)"; done
for a in <id1> ...; do printf "%-30s %s\n" "$a" "$(du -sh /home/owner/.openclaw/agents/$a | cut -f1)"; done'
```

**Criteria**:
- the probe file can be `cat`-ed and its content matches the timestamp (proof of write permission and of really being on the new host)
- `~/.openclaw/agents/<id>` is **newly created** on the target (2.8–3.7M range) → the session really started running there, not idling
- **remove the probe files after acceptance**, otherwise the target repo turns dirty

### 9.3 False alarm: `workspace/skills` is missing

Instance: 3 agents reported "the skills directory is gone, the migration lost data".
Checking the source host: **they had no skills directory before the migration either**; they had always used the global skills. Only `book_up_chain` had one private skill, and it had already travelled with git.

**To judge whether the migration damaged anything, the baseline is "yourself at the moment before the migration", not "the other agents".**
Using someone else's directory layout as the standard leads you down a path fixing a bug that does not exist.

---

## 10. Stage 8 — path rewrite (moving business paths)

Only needed when a business directory changed location on the target. **Do it last**: rewrite paths before acceptance and neither side is consistent, so when something breaks you cannot tell whether it was the migration or the path.

### 10.1 Database

```sql
UPDATE agx_project SET x_desc = REPLACE(x_desc, '<old_prefix>', '<new_root>') WHERE project_id='...';
UPDATE agx_project SET source_dir = '<new_root>' WHERE project_id='...';
```

Dump the old values into the backup directory before changing anything (`db/project_x_desc_before.json`).

> **Do not copy a blanket replacement**: `<old_prefix>` must be grepped out of x_desc as it actually appears (in the instance the whole string `/Users/<user>/project/book/upper_chain`),
> not the user prefix `/Users/<user>` — see §1.3.

### 10.2 workspace documents

```bash
files=$(grep -rl "<old_prefix>" <id1> <id2> ... 2>/dev/null)
for f in $files; do sed -i "s|<old_prefix>|<new_root>|g" "$f"; done
grep -rho "<old_prefix>" <id1> ... | wc -l      # expect 0
```

Instance: 17 files, 42 occurrences. Commit and push, then `git pull` on the other side to align.

### 10.3 Cache

`agx_project` is cached too; changing the DB without clearing the cache means the change accomplishes nothing. Again, dispatch a targeted clear to `agx_core` and require it to **report the actual return value after re-reading**.

**Gate** (all three layers green):

```
DB      zero occurrences of the old string in x_desc / source_dir
cache   re-reading agx_get_project_config returns the new string ×N, the old string ×0
files   grep -r for the old string = 0
```

### 10.4 Telling "should change" from "should not change"

`grep -r /Users/<user>` hit **58 occurrences** in the instance, and **not one of them should be changed**:

| Type | Example | Handling |
|---|---|---|
| old facts in historical diary entries | "09-29 need Lao Feng to provide the manuscript path (e.g. /Users/...)" | leave it; changing it falsifies history |
| "old → new" pairs in migration records | "old /Users/... → new /home/owner/..." | leave it; it belongs there |
| unrelated projects (another book) | `/Users/.../blockchain-practice-up1` | leave it; that is another project |

**The real criterion is not the grep count over memory, but the layer the Agent will act on**:

```bash
# ① task specifications (DB) — the agent really reads these to do its work
SELECT task_name, x_desc FROM agx_task_base WHERE agent_id='<id>' AND active_status=1;
# ② AGENTS.md — read on every startup
grep -c "<old_prefix>" <id>/workspace/AGENTS.md
```

If both are **0**, the migration is clean. **Using memory hit counts as a KPI means it is never "clean".**

---

## 11. Stage 9 — wrap-up

1. **Inform the team members**: have the PM record it in its own memory, and notify each worker to record it and check its paths. Require a report of "which file was written, how big, how many residual occurrences found".
2. **Do not take the PM's one-liner at face value**: "all workers notified" must be checked against the mtime and git status of the 4 workers' memory files.
3. **Align git on both sides**: commit on the target → push → `git pull --ff-only` on the source.
4. **Clean the leftovers**: probe files on the target, empty state shells on the source.
5. **Write the log** (`docs/agent_docs/daily/<date>.md`): date, old/new host, old/new path, acceptance conclusion, incidents, follow-ups.

---

## 12. Rollback — three layers, lightest first

| Layer | Scenario | Action | Data consequence |
|---|---|---|---|
| L1 revert ownership | the target will not come up, the source still exists | `UPDATE host_id` back to the source → rebuild the agent cache → restart the gateway | **no loss** (the source state directory is still there) |
| L2 restore state | the source state was deleted/damaged | restore from `$BK/session_state/<id>/` with `sqlite3 .backup` | loses sessions created after the migration; md memory intact |
| L3 restore files | files were pruned/damaged | `git checkout -- <id>` or `git reset --hard <pre-migration commit>` | loses documentation changes made after the migration |

**L1's precondition is "do not rush to delete the source state directory"**. In this instance I kept the 25M shells on the Mac until the target had passed every acceptance check and a plan had run through, and only then asked the user whether to clean them — that order cannot be reversed.

---

## 13. Incident archive (sorted by the cost of the pitfall)

### 13.1 gateway will not start (cost: 6 minutes fully down + half an hour of misdirected investigation)

- **Trigger**: `openclaw agents delete` timing out (10s) + a full cache rebuild in the same window.
- **Error**: `OpenClaw agent database is unavailable while agent <id> is deleted.`
- **Root cause**: the CLI's two steps are not atomic and it waits only 10 seconds; config deleted, state not cleared → the startup validation refuses. Concurrent heavy work slowed the config write.
- **Countermeasures**: ① `gateway stop` before deleting; ② never stack a second heavy job in the same window; ③ verify each delete when batching.

### 13.2 The path replacement was wrong yet "looked right" (cost: almost pointed the manuscript at another book)

- **Trigger**: a blanket replacement of the user prefix `/Users/<user>` → `/home/owner`.
- **Result**: `/home/owner/project/book/upper_chain` — the path exists, but it is the repo of **another book**.
- **Detected by**: the gate "the target directory should have 25 top-level entries / 312 tracked files".
- **Countermeasure**: see §1.3 — after replacing, always `ls` and check a characteristic.

### 13.3 False alarm about lost skills (cost: almost fixed a bug that did not exist)

See §9.3. **The baseline is always "yourself before the migration".**

### 13.4 The sqlite CLI reports a corrupt database (cost: thought the whole backup was ruined)

The sqlite 3.20.1 shipped with macOS cannot read STRICT tables → `malformed database schema`.
Re-checked with python sqlite 3.53 → all 5 databases `ok`. **A tool that is too old ≠ data that is broken.**

### 13.5 `agents delete` empties the workspace (cost: 230 deletions, all recovered through git)

See §6.2. **Without stage 1 (clean repo) + stage 2 (backup), this step is real data loss.**

### 13.6 A newly created plan gets dropped from the index by the watchdog (latent risk, never erupted)

The watchdog treats `draft` plans as "stale" and removes them from `agx:idx:plan:all`. **A normal activation (`agx_activate_plan`) puts the plan back with `zadd` through `_sync_entity`**; but if someone takes the shortcut of running `UPDATE agx_plan SET plan_status='activated'` directly, the plan is never in the index → the watchdog does not traverse it → not a single step runs, with no error at all.

**Criterion**: right after activation, check that `ZSCORE agx:idx:plan:all <plan_id>` is non-empty.
**Generalisation**: for any "change the status" operation, ask "who will repair the cache and the index".

### 13.7 Two hosts each have their own watchdog — that is by design, not a bug

The watchdog is **isolated per host**: it only scans tasks of agents under its own host_id, and plans are owned via `agx_plan.host_id`.
So after moving 5 agents to 10.0.0.13, their plans are scheduled by the 10.0.0.13 watchdog and the Mac watchdog skips them — **there is no double scheduling**.
Precondition: `agx_agent_registry.host_id` must actually be changed correctly, otherwise both watchdogs believe they own the work (or neither does).

---

## 14. Migration checklist (printable)

```
Pre-flight
[ ] running = 0, pending = 0
[ ] target hostname / node -v / openclaw --version / free disk
[ ] source entry count recorded
[ ] each agent's state directory size recorded

Repo
[ ] *.sqlite removed with git rm --cached and added to .gitignore
[ ] git status empty, HEAD == remote

Backup
[ ] 5 session_state copies (sqlite3 .backup, not cp)
[ ] registry_before.json / agx_session_rows.json / openclaw_entries_before.json
[ ] integrity_check all ok (re-checked with a recent sqlite)
[ ] RESTORE.md written

Database
[ ] host_id changed
[ ] agent_root_dir / agent_workspace_dir left empty
[ ] cache refreshed on target by an Agent with permission
[ ] 5 DB rows == 5 Redis entries == target hostname

Source uninstall
[ ] gateway stop first
[ ] delete one at a time, checking entries -1 + state gone each time
[ ] http=200 after gateway start
[ ] git checkout restores the pruned workspace, status = 0
[ ] leftover state directories cleaned

Target landing
[ ] export PATH (non-interactive ssh)
[ ] git pull --ff-only, HEAD == remote, status = 0
[ ] equal file totals AND equal per directory
[ ] agents add one by one (not agx_deploy_agent)
[ ] entries +N, visible in agents list, no gateway restart needed

Acceptance
[ ] 7 measured items per agent
[ ] I cat-ed the probe files myself
[ ] ~/.openclaw/agents/<id> newly created on the target with a plausible size
[ ] probes cleaned up
[ ] every reported "failed item" re-checked as a baseline difference or not

Paths
[ ] DB x_desc / source_dir
[ ] workspace grep replacement + ls characteristic check on the target
[ ] cache re-read verified
[ ] task specification x_desc + AGENTS.md = 0 occurrences of the old path

Wrap-up
[ ] PM + workers each recorded in memory (check mtime, not reports)
[ ] git aligned on both sides
[ ] daily log
```

---

## 15. Instance data (book_up_chain, 2026-11-02)

> Host names and user paths in this section are generalized; all numbers, timestamps and command output are verbatim from the original incident record.

| Item | Value |
|---|---|
| Project | `proj_a46ac4334886` (book_up_chain, book planning) |
| Members | `book_up_chain`(PM) / `up_chain_book_writer` / `up_chain_book_planner` / `up_chain_book_reviewer` / `up_chain_book_beta_reader` |
| Source → target | `test_host_mac` (macOS 14.5, arm64) → `test_host_srv` (Linux 5.15, 16 cores / 31G / disk 89% used, 50G free) |
| Session history (not migrated) | 104 + 28 + 41 + 44 + 60 = 277 MB |
| Backup | `~/agx_backups/book_up_chain_migrate_20261102/`, 279 MB |
| Pre-migration commit | `25418d6` |
| Source entries | 86 → 82 (5 deleted) |
| Target entries | 3 → 8 (5 added) |
| agents repo | 5440 tracked files = 5440 files on disk; 88 top-level directories; the 5 target directories 87/37/35/33/38 |
| Manuscript root | `/Users/<user>/project/book/upper_chain` → `/home/owner/project/upper_chain` (HEAD `0ea0eb3 v2`, 312 tracked files, 25 top-level entries) |
| Path rewrite | 17 files / 42 occurrences; commit `2b46103`; IDENTITY aligned in `67c8962` |
| Acceptance duration | 9–13 seconds per agent; 5/5 passed |
| First plan execution after migration | `plan_0f614c440fd4`, 77 steps in chain mode (1 quality gate → 6 parallel writing tracks → 3 review/revise/beta-read rounds per section → merge), **executed successfully** |

### Key path quick reference

```
source (macOS)
  /Users/<user>/.agx/openclaw_agents            agents repo (= target's /home/owner/.agx/agx_agents)
  /Users/<user>/.openclaw/agents/<id>/              state directory (session sqlite, not in git)
  /Users/<user>/.openclaw/openclaw.json             entries

target (Linux)
  /home/owner/.agx/agx_agents/<id>/workspace           workspace (git)
  /home/owner/.openclaw/agents/<id>/agent              agent dir
  /home/owner/.agx/scripts/agx_{daemon,watchdog,ws_client}.py
  /home/owner/.nvm/versions/node/v22.22.3/bin/openclaw

runtime identity source
  agx.env / config → work_dir(~/.agx) + host_id
  agx_get_global_config() → host_id / work_dir / project_id / agent_role
```

---

## 16. Known gaps (what can be improved next time)

1. **`agent_root_dir` / `agent_workspace_dir` are NULL across the whole system**, and the runtime derives them from `work_dir`. This manual records the status quo honestly; filling in a single row is not recommended.
2. **Not migrating session history** is a current decision, not a capability limit. If it is ever really needed, do a one-off "offline session-store merge" (take a copy with `.backup`, place it per agent on the target), but the 300MB-scale cost needs its own assessment.
3. **`openclaw agents delete` is not atomic** — that is upstream behaviour; the only mitigation is "stop the gateway first". If upstream ever fixes it, §6.1 can be simplified.
4. **The leftover `session-sqlite-import-archive/` after deletion** is incomplete upstream cleanup; wrap-up needs a manual `rm -rf`.
5. **Every gate in this document is an executable command** but none is scripted yet. If a third migration still means typing them by hand, turn the §14 checklist into `migrate_agent_group.sh`.
