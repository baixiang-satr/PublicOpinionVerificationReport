"""Real-browser smoke check for the one-time interactive-login reveal path."""

from __future__ import annotations

import asyncio

from playwright.async_api import async_playwright

from src.auth.window_visibility import reveal_window_once, stage_window_offscreen


async def main() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            **stage_window_offscreen(
                {"headless": False, "channel": "msedge"},
                width=1100,
                height=720,
            )
        )
        try:
            context = await browser.new_context(viewport={"width": 1100, "height": 720})
            page = await context.new_page()
            session = await context.new_cdp_session(page)
            before = await session.send("Browser.getWindowForTarget")
            await page.set_content(
                "<title>登录态窗口稳定性测试</title>"
                "<main><h1>登录页面已就绪</h1><p>窗口仅在内容完成后显示。</p></main>"
            )
            revealed = await reveal_window_once(page, width=1100, height=720)
            after = await session.send("Browser.getWindowForTarget")
            assert revealed is True
            assert before["bounds"].get("left", 0) < -10000
            assert after["bounds"].get("left") == 40
            assert after["bounds"].get("top") == 40
            assert before["windowId"] == after["windowId"]
            print(
                "PASS: same browser window staged off-screen and revealed once; "
                f"windowId={after['windowId']}"
            )
            await session.detach()
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
