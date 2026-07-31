"""临时调试：定位抖音视频页“厂牌排名规则”污染节点的来源。

用法: .venv\\Scripts\\python.exe tools\\debug_douyin_payloads.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.auth.store import AuthProfileStore
from src.config.settings import TaskConfig, default_auth_store_dir
from src.crawler.platforms.douyin import _RENDER_DATA_SCRIPT, _aweme_detail
from src.crawler.platforms.payload_search import iter_mappings
from src.crawler.structured_data import candidate_scope, url_content_ids
from src.screenshot.browser_options import (
    browser_context_options,
    browser_launch_options,
)

URLS = (
    "https://www.douyin.com/video/7667987870472788815",
    "https://www.douyin.com/video/7667886625339225445",
)


def _node_preview(node: dict) -> str:
    keys = [k for k in ("aweme_id", "id", "desc", "title", "content", "nickname", "createTime") if k in node]
    parts = []
    for key in keys:
        value = str(node.get(key))[:60].replace("\n", " ")
        parts.append(f"{key}={value!r}")
    return ", ".join(parts)


def _walk(payload, needle: str, source: str, url_ids) -> None:
    for node in iter_mappings(payload, max_nodes=4000):
        haystack = json.dumps(node, ensure_ascii=False)[:200000]
        if needle not in haystack:
            continue
        scope = candidate_scope(node, url_ids)
        # 只打印直接含关键词的最小节点，避免整棵树重复刷屏
        direct = any(
            isinstance(v, str) and needle in v for v in node.values()
        )
        if direct:
            print(f"      [直接命中] scope={scope} keys={sorted(node.keys())[:18]}")
            print(f"        {_node_preview(node)}")


async def main() -> None:
    config = TaskConfig(
        headless=True,
        page_timeout_seconds=45,
        page_stabilize_milliseconds=2500,
        auth_store_dir=default_auth_store_dir(),
        enable_stealth=True,
        enable_extra_stealth=True,
    )
    store = AuthProfileStore(config.auth_store_dir)
    state = store.load_state("douyin", include_inactive=True)
    print(f"登录态: {'已加载' if state else '无'}")

    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(**browser_launch_options(config))
    try:
        context = await browser.new_context(**browser_context_options(config, state))
        for url in URLS:
            print(f"\n=== {url}")
            url_ids = url_content_ids(url)
            print(f"  url_ids: {sorted(url_ids)}")
            payloads: list[tuple[str, object]] = []

            def listener(response):
                async def read():
                    try:
                        ct = str(response.headers.get("content-type") or "")
                        if "json" not in ct.casefold():
                            return
                        body = await response.body()
                        if not body or len(body) > 2_000_000:
                            return
                        payloads.append((response.url, json.loads(body.decode("utf-8", "replace"))))
                    except Exception:
                        pass
                asyncio.create_task(read())

            page = await context.new_page()
            page.on("response", listener)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(6000)
                render_data = await page.evaluate(_RENDER_DATA_SCRIPT)
                render = json.loads(render_data) if render_data else None
                print(f"  RENDER_DATA: {'有' if render else '无'} | XHR JSON 数: {len(payloads)}")
                await asyncio.sleep(1.5)  # 等在途 body 读取

                wanted = url.split("/")[-1]
                print(f"  -- 在 RENDER_DATA 找 aweme detail (wanted={wanted}):")
                detail = _aweme_detail(render, wanted_id=wanted) if render else None
                if detail is None:
                    print("      未找到匹配节点")
                else:
                    print(f"      desc={str(detail.get('desc'))[:80]!r}")
                    print(f"      createTime={detail.get('createTime')!r}")
                # 不管找没找到，全量扫“厂牌”
                print("  -- 全量扫描含「厂牌」的节点:")
                if render:
                    _walk(render, "厂牌", "RENDER_DATA", url_ids)
                for payload_url, payload in payloads:
                    before = None
                    _walk(payload, "厂牌", payload_url, url_ids)
                print("  -- 各 XHR 响应概览:")
                for payload_url, payload in payloads:
                    detail2 = _aweme_detail(payload, wanted_id=wanted)
                    mark = "★含目标detail" if detail2 else ""
                    has_factory = "厂牌" in json.dumps(payload, ensure_ascii=False)[:500000]
                    print(f"      {payload_url[:110]} {'[含厂牌]' if has_factory else ''} {mark}")
                    if detail2 is not None:
                        print(f"        -> desc={str(detail2.get('desc'))[:80]!r}")
            finally:
                await page.close()
    finally:
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
