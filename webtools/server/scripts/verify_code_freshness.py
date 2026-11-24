"""MCP 代码新旧判据（code_freshness）分支验证 —— 老冯 2026-10-29

为什么单独测：
    这个判据是「弹窗里唯一可信的部分」（mcp_version 是写死的常量，不会随时间变），
    所以它的每条分支都必须能被证明，包括「判不出来」的那三条。

    只把模块内的 _KERNEL_FILE / _PID_FILE 指到 /tmp 临时文件，
    不碰仓库任何真实文件的 mtime。

跑法（在 webtools/server 下）：
    ../../.venv/bin/python scripts/verify_code_freshness.py
"""
import os
import pathlib
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.routers import mcp as M

ok, bad = [], []
def chk(name, cond, extra=""):
    (ok if cond else bad).append(name)
    print(("✅ " if cond else "❌ ") + name + ("  — " + extra if extra else ""))

real_k, real_p = M._KERNEL_FILE, M._PID_FILE

# ① stale=True：代码 mtime 在未来（模拟「代码比进程新」）
tmp_k = pathlib.Path("/tmp/_cf_kernel.py")
tmp_k.write_text("#")
future = time.time() + 3600
os.utime(tmp_k, (future, future))
M._KERNEL_FILE = tmp_k
r = M.code_freshness()
chk("代码比进程新 → stale=True", r["stale"] is True, r["verdict"])

# ② stale=False：代码 mtime 在过去
past = time.time() - 86400
os.utime(tmp_k, (past, past))
r = M.code_freshness()
chk("代码比进程旧 → stale=False", r["stale"] is False, r["verdict"])

# ③④ 走 pid 文件分支
#    ★ 2026-11-05 22:28：这两条原来直接换 _PID_FILE 就以为会走 pidfile 分支 —— 不成立。
#      现在 MCP 已迁到共享网关（:31890 有真实监听），`_listener_pids(port)` 先命中，
#      pidfile 分支永远到不了，于是「换掉 pid 文件」测的是同一个真实进程 → 断言假失败。
#      要测那条分支，必须**同时**把端口监听屏蔽掉（模拟"端口上没人，只能读 pid 文件"）。
_real_listener = M._listener_pids
M._listener_pids = lambda port: []          # 模拟端口无监听

M._PID_FILE = pathlib.Path("/tmp/_cf_nonexistent.pid")
if M._PID_FILE.exists():
    M._PID_FILE.unlink()
r = M.code_freshness()
chk("pid 文件缺失 → stale=None", r["stale"] is None, r["verdict"])

bad_p = pathlib.Path("/tmp/_cf_bad.pid")
bad_p.write_text("not-a-pid")
M._PID_FILE = bad_p
r = M.code_freshness()
chk("pid 非法 → stale=None", r["stale"] is None, r["verdict"])

M._listener_pids = _real_listener          # 还原，避免影响后续 ⑤

# ⑤ 内核文件缺失 → stale=None
M._KERNEL_FILE = pathlib.Path("/tmp/_cf_no_kernel.py")
r = M.code_freshness()
chk("内核文件缺失 → stale=None", r["stale"] is None, r["verdict"])

# ⑥ 新判据（2026-11-05）：仓库内文件走 git —— 切分支只改 mtime 不改内容，不该被当旧码
M._KERNEL_FILE = real_k
r = M.code_freshness()
chk("仓库内文件 → 判据来源是 git（不是裸 mtime）",
    r["kernel_change_source"] in ("git-commit", "mtime(dirty)"),
    f'source={r["kernel_change_source"]} change_at={r["kernel_change_at"]} mtime={r["kernel_mtime"]}')
chk("切分支把 mtime 顶新也不会误判成旧码",
    not (r["kernel_mtime"] and r["kernel_mtime"] != r["kernel_change_at"] and r["stale"] is True),
    f'stale={r["stale"]}')

M._KERNEL_FILE, M._PID_FILE = real_k, real_p
print(f"\n通过 {len(ok)} / 失败 {len(bad)}")
sys.exit(1 if bad else 0)
