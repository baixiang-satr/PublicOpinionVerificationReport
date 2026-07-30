"""抖音修复验证：两条真实失败 URL 的分层诊断 + 完整爬取实测。

分层诊断（快速定位哪一层有效，避免每次烧满硬超时）：
1. 短链预解析：v.douyin.com → 规范 video URL（含 aweme_id）
2. API 兜底：iesdouyin iteminfo / share 页 SSR JSON
3. 完整爬取管线：CrawlEngine（导航→提取→截图→个人页→抖音号回填）

运行方式（需本机网络；登录态自动从本机加密库读取）：
    .venv\\Scripts\\python.exe tools\\test_douyin_fix.py                # 无头
    .venv\\Scripts\\python.exe tools\\test_douyin_fix.py --headed       # 有头（可人工过验证）
    .venv\\Scripts\\python.exe tools\\test_douyin_fix.py --precheck-only

退出码：两条 URL 全部拿到 正文+昵称+内容页截图 为 0，否则 1。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.settings import TaskConfig, default_auth_store_dir
from src.crawler.engine import CrawlEngine
from src.domain.models import RecordStatus, UrlTask

URLS = (
    "https://v.douyin.com/erOICsACek8/",
    "https://v.douyin.com/6OYduQ_wgKk/",
)


def _config(headed: bool, edge: bool = False) -> TaskConfig:
    return TaskConfig(
        max_concurrency=1,
        page_timeout_seconds=45,
        page_processing_timeout_seconds=180.0,
        max_retries=1,
        min_host_interval_seconds=1.0,
        page_stabilize_milliseconds=2000,
        headless=not headed,
        browser_channel="msedge" if edge else None,
        auth_store_dir=default_auth_store_dir(),
        enable_stealth=True,
        enable_extra_stealth=True,
        enable_platform_fallbacks=True,
        enable_headed_fallback=False,  # 本脚本手动控制有头/无头
        ocr_enabled=False,  # 诊断提速；OCR 链路已由单测覆盖
    )


async def precheck(config: TaskConfig) -> None:
    """短链解析 + API 兜底快速验证（复用浏览器会话与登录态）。"""

    from playwright.async_api import async_playwright

    from src.auth.store import AuthProfileStore
    from src.crawler.api_assist import douyin_aweme_detail, douyin_aweme_id
    from src.crawler.share_links import resolve_share_link
    from src.screenshot.browser_options import (
        browser_context_options,
        browser_launch_options,
    )

    store = AuthProfileStore(config.auth_store_dir)
    try:
        state = store.load_state("douyin", include_inactive=True)
    except Exception:
        state = None
    profile = store.profile_for("douyin")
    print(f"登录态: {'已加载 (' + profile.status.value + ')' if state else '无（游客模式）'}")

    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(**browser_launch_options(config))
    try:
        context = await browser.new_context(**browser_context_options(config, state))
        page = await context.new_page()
        for url in URLS:
            print(f"\n  {url}")
            resolved = await resolve_share_link(page, url)
            print(f"    短链解析: {resolved or '失败'}")
            aweme_id = douyin_aweme_id(resolved or url)
            print(f"    aweme_id: {aweme_id or '未解析'}")
            if not aweme_id:
                continue
            detail = await douyin_aweme_detail(page, aweme_id)
            if not detail:
                print("    API 兜底: 未取到")
                continue
            author = detail.get("author") if isinstance(detail.get("author"), dict) else {}
            print(f"    API 兜底: desc={str(detail.get('desc'))[:40]!r}")
            print(
                f"      nickname={author.get('nickname')!r}"
                f" unique_id={author.get('unique_id')!r}"
                f" short_id={author.get('short_id')!r}"
            )
    finally:
        await browser.close()
        await playwright.stop()


async def crawl(config: TaskConfig, output_dir: Path) -> bool:
    """完整管线实测两条 URL，打印字段级结果。"""

    engine = CrawlEngine(config)
    tasks = [UrlTask(index + 1, url, url) for index, url in enumerate(URLS)]
    started = time.time()
    results = await engine.run(tasks, output_dir)
    elapsed = time.time() - started

    print(f"\n爬取完成，用时 {elapsed:.1f}s")
    all_ok = True
    for result in results:
        page = result.page
        assets = result.assets
        ok = (
            result.status == RecordStatus.ASSETS_READY
            and bool(page.content_text)
            and bool(page.author_name)
            and assets.page_screenshot is not None
        )
        all_ok = all_ok and ok
        icon = "✅" if ok else "❌"
        print(f"\n  {icon} #{result.task.evidence_id} {result.task.original_url}")
        print(f"    状态: {result.status.value} | final_url: {page.final_url}")
        if page.redirect_chain:
            print(f"    跳转链: {' -> '.join(page.redirect_chain)}")
        print(f"    标题/正文: {str(page.title)[:30]!r} / {len(page.content_text or '')} 字")
        print(
            f"    作者: {page.author_name!r} | 账号: {page.author_id!r}"
            f"{'（昵称兜底）' if page.author_id_is_fallback else ''}"
        )
        print(f"    发布时间: {page.published_at}")
        print(
            f"    截图: 内容页={'有' if assets.page_screenshot else '无'}"
            f" 个人页={'有' if assets.author_screenshot else '无'}"
        )
        for error in result.errors[:3]:
            print(f"    错误 [{error.code}]: {error.message[:100]}")
    return all_ok


async def main() -> int:
    headed = "--headed" in sys.argv
    edge = "--edge" in sys.argv
    precheck_only = "--precheck-only" in sys.argv
    config = _config(headed, edge)
    output_dir = (
        Path(__file__).resolve().parents[1]
        / "output"
        / ("test-douyin-headed" if headed else "test-douyin-headless")
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print(
        "  抖音修复验证 "
        f"({'有头' if headed else '无头'}{' + Edge' if edge else ''} 模式)"
    )
    print("=" * 70)
    print("\n[1/2] 分层预检（短链解析 + API 兜底）")
    await precheck(config)
    if precheck_only:
        return 0
    print("\n[2/2] 完整爬取管线")
    ok = await crawl(config, output_dir)
    print("\n" + ("全部通过 ✅" if ok else "仍有记录未达标 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
