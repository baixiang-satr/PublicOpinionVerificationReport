"""临时调试 2：检查 aweme/detail XHR 响应真实结构。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.auth.store import AuthProfileStore
from src.config.settings import TaskConfig, default_auth_store_dir
from src.screenshot.browser_options import (
    browser_context_options,
    browser_launch_options,
)

URLS = (
    "https://www.douyin.com/video/7667987870472788815",
    "https://www.douyin.com/video/7667886625339225445",
)


async def main() -> None:
    config = TaskConfig(
        headless=True,
        page_timeout_seconds=45,
        auth_store_dir=default_auth_store_dir(),
        enable_stealth=True,
        enable_extra_stealth=True,
    )
    store = AuthProfileStore(config.auth_store_dir)
    state = store.load_state("douyin", include_inactive=True)

    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(**browser_launch_options(config))
    try:
        context = await browser.new_context(**browser_context_options(config, state))
        for url in URLS:
            print(f"\n=== {url}")
            hits: list[tuple[str, object]] = []

            def listener(response):
                if "/aweme/detail" not in response.url and "/aweme/related" not in response.url:
                    return

                async def read():
                    try:
                        body = await response.body()
                        if body and len(body) <= 4_000_000:
                            hits.append((response.url, json.loads(body.decode("utf-8", "replace"))))
                    except Exception:
                        pass

                asyncio.create_task(read())

            page = await context.new_page()
            page.on("response", listener)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(7000)
                await asyncio.sleep(1)
                for resp_url, payload in hits:
                    kind = "detail" if "/aweme/detail" in resp_url else "related"
                    print(f"  [{kind}] {resp_url[:120]}")
                    if isinstance(payload, dict):
                        print(f"    top keys: {sorted(payload.keys())[:12]}")
                        print(f"    status_code={payload.get('status_code')!r} status_msg={str(payload.get('status_msg'))[:60]!r}")
                        detail = payload.get("aweme_detail")
                        if detail is None:
                            print("    aweme_detail: None")
                        elif isinstance(detail, dict):
                            print(f"    aweme_detail.aweme_id={detail.get('aweme_id')!r}")
                            print(f"    aweme_detail.desc={str(detail.get('desc'))[:100]!r}")
                            print(f"    aweme_detail.createTime={detail.get('createTime')!r}")
                            author = detail.get("author") or {}
                            if isinstance(author, dict):
                                print(f"    author.nickname={author.get('nickname')!r} unique_id={author.get('unique_id')!r}")
                        items = payload.get("aweme_list") or payload.get("items")
                        if isinstance(items, list) and items:
                            first = items[0] if isinstance(items[0], dict) else {}
                            print(f"    aweme_list[0].aweme_id={first.get('aweme_id')!r} desc={str(first.get('desc'))[:60]!r}")
            finally:
                await page.close()
    finally:
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
