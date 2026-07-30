"""截图会话冒烟测试：Edge 内核 + 登录态 + 工具条 SPA 自愈（真实浏览器，自动断言）。

断言：
1. 会话浏览器打开抖音视频页且页面非空（Edge 内核渲染）
2. 工具条宿主 `__poir-shot-host` 已注入
3. 模拟 SPA 跳转个人页后工具条仍然存在（自愈轮询生效）
4. 登录态 cookie 在会话内可见

运行方式：
    .venv\\Scripts\\python.exe tools\\smoke_capture_session.py
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.auth.store import AuthProfileStore
from src.config.settings import TaskConfig, default_auth_store_dir
from src.screenshot.capture_session import CaptureSession

URL = "https://www.douyin.com/video/7667987870472788815"


async def main() -> int:
    store = AuthProfileStore(default_auth_store_dir())
    state = store.load_state("douyin", include_inactive=True)
    session = CaptureSession(TaskConfig(), store)
    checks: list[tuple[str, bool]] = []
    try:
        context = await session.context_for("douyin", state)
        page = await session.browse_page_for("douyin", context)
        await page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(4_000)

        body_len = await page.evaluate("() => (document.body?.innerText||'').length")
        checks.append(("页面渲染非空", body_len > 100))

        host = await page.evaluate(
            "() => Boolean(document.getElementById('__poir-shot-host'))"
        )
        checks.append(("工具条已注入", bool(host)))

        # 模拟 SPA 客户端路由跳转个人页（不触发整页重载）
        await page.evaluate(
            "() => { const a = document.querySelector("
            "  'a[href*=\"/user/MS4\"]');"
            "  if (a) { a.removeAttribute('target'); a.click(); } }"
        )
        await page.wait_for_timeout(2_500)
        host_after = await page.evaluate(
            "() => Boolean(document.getElementById('__poir-shot-host'))"
        )
        checks.append(("SPA 跳转后工具条存活（自愈）", bool(host_after)))

        cookies = await context.cookies("https://www.douyin.com")
        names = {c["name"] for c in cookies}
        checks.append(("登录态 cookie 在场", bool({"sessionid", "sessionid_ss"} & names)))
    finally:
        await session.close()

    print("\n冒烟结果：")
    ok = True
    for name, passed in checks:
        ok = ok and passed
        print(f"  {'✅' if passed else '❌'} {name}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
