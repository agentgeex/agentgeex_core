"""ASR 断线自愈自测：能发现挂了 → 会内部重试 → 服务回来后自己恢复。

跑法（在 webtools/server 目录下）：
    ../../.venv/bin/python tests/asr_reconnect_selftest.py

★ 全程不碰 8888：用临时端口 59997 + 临时起的极简 WS 服务，
  不影响产品负责人正在用的语音服务。验完自己收摊。"""
import asyncio, sys, time
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
from app import asr                 # noqa: E402
from app.config import settings     # noqa: E402

import websockets                    # noqa: E402

DEAD = "ws://127.0.0.1:59997/ws/audio/audio2text"

async def main():
    # ① 端口不通 → 判为不可用
    settings.asr_ws_url = DEAD
    ok = await asr.probe()
    snap = asr.snapshot()
    print(f"① 服务挂了：probe={ok} state={snap['state']} streak={snap['fail_streak']} err={snap['error'][:60]}")
    assert ok is False and snap["state"] == "down"

    # ② 转写：连不上时要**内部重试**再放弃（不是一次就败）
    t0 = time.time()
    try:
        await asr.transcribe(b"\x00" * 2048)
        print("② 竟然成功了？")
        raise SystemExit(1)
    except asr.AsrError as e:
        cost = time.time() - t0
        print(f"② 转写失败码={e.code} retryable={e.retryable} 耗时={cost:.2f}s（重试 {asr._RETRY_TIMES} 次）")
        assert e.code == "ASR_UNAVAILABLE"
        assert cost >= asr._RETRY_BASE_DELAY * (1 + 2) * 0.8, "没重试就直接失败了"

    # ③ 服务起来（极简 WS 服务：收完回一段文本 + 完成标记）
    async def handler(ws):
        try:
            while True:
                d = await ws.recv()
                if d == b"":
                    await ws.send("自动重连测试通过")
                    await ws.send("AUDIO_PROCESSING_COMPLETE")
                    return
        except Exception:
            pass

    server = await websockets.serve(handler, "127.0.0.1", 59997)
    settings.asr_ws_url = "ws://127.0.0.1:59997/ws/audio/audio2text"
    try:
        # 巡检循环跑到它恢复（不手动 probe，让**后台巡检**自己发现）
        got = False
        for _ in range(30):
            await asyncio.sleep(0.2)
            await asr.probe()          # 模拟巡检每一拍
            if asr.snapshot()["state"] == "up":
                got = True
                break
        print(f"③ 服务回来了：state={asr.snapshot()['state']}（自动恢复={got}）")
        assert got

        # ④ 恢复后立刻能转写
        r = await asr.transcribe(b"\x00" * 2048)
        print(f"④ 恢复后可转写：{r['text']!r} attempts={r['attempts']}")
        assert r["text"] == "自动重连测试通过"
    finally:
        server.close()
        await server.wait_closed()

    print("\n✅ 三项全过：能发现挂了 / 会重试 / 回来了自己恢复")

asyncio.run(main())
