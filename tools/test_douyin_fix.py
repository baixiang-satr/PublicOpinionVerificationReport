"""抖音修复验证：两条真实失败 URL 的分层诊断 + 完整爬取实测。

分层诊断（快速定位哪一层有效，避免每次烧满硬超时）：
1. 短链预解析：v.douyin.com → 规范 video URL（含 aweme_id）
2. API 兜底：iesdouyin iteminfo / share 页 SSR JSON
3. 完整爬取管线：CrawlEngine（导航→提取→截图→个人页→抖音号回填）

运行方式（需本机网络；登录态自动从本机加密库读取）：
    .venv\\Scripts\\python.exe tools\\test_douyin_fix.py                # 无头
    .venv\\Scripts\\python.exe tools\\test_douyin_fix.py --headed       # 有头（可人工过验证）
    .venv\\Scripts\\python.exe tools\\test_douyin_fix.py --precheck-only

退出码：两条 URL 全部拿到正确正文、昵称、内容页截图和个人页截图，
且第二条发布时间为 2026-07-29 17:56:00 时为 0，否则 1。
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
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
EXPECTED = {
    1: {
        "content": "准备 开战了",
        "published_at": None,
    },
    2: {
        "content": "道路千万条，安全第一条",
        "published_at": "2026-07-29 17:56:00",
    },
}


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


async def precheck(
    config: TaskConfig,
    evidence_ids: tuple[int, ...] = (1, 2),
) -> None:
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
        for evidence_id in evidence_ids:
            url = URLS[evidence_id - 1]
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


async def crawl(
    config: TaskConfig,
    output_dir: Path,
    evidence_ids: tuple[int, ...] = (1, 2),
) -> bool:
    """完整管线实测两条 URL，打印字段级结果。"""

    engine = CrawlEngine(config)
    tasks = [
        UrlTask(evidence_id, URLS[evidence_id - 1], URLS[evidence_id - 1])
        for evidence_id in evidence_ids
    ]
    started = time.time()
    results = await engine.run(tasks, output_dir)
    elapsed = time.time() - started

    print(f"\n爬取完成，用时 {elapsed:.1f}s")
    all_ok = True
    for result in results:
        page = result.page
        assets = result.assets
        expected = EXPECTED[result.task.evidence_id]
        content = page.content_text or ""
        content_ok = expected["content"] in content and "厂牌榜单值" not in content
        published = (
            page.published_at.strftime("%Y-%m-%d %H:%M:%S")
            if page.published_at
            else ""
        )
        time_ok = (
            expected["published_at"] is None
            or published == expected["published_at"]
        )
        page_shot_ok = bool(
            assets.page_screenshot
            and Path(assets.page_screenshot).is_file()
        )
        author_shot_ok = bool(
            assets.author_screenshot
            and Path(assets.author_screenshot).is_file()
        )
        ok = (
            result.status == RecordStatus.ASSETS_READY
            and content_ok
            and time_ok
            and bool(page.author_name)
            and page_shot_ok
            and author_shot_ok
        )
        all_ok = all_ok and ok
        icon = "✅" if ok else "❌"
        print(f"\n  {icon} #{result.task.evidence_id} {result.task.original_url}")
        print(f"    状态: {result.status.value} | final_url: {page.final_url}")
        if page.redirect_chain:
            print(f"    跳转链: {' -> '.join(page.redirect_chain)}")
        print(f"    标题/正文: {str(page.title)[:30]!r} / {content!r}")
        print(
            f"    作者: {page.author_name!r} | 账号: {page.author_id!r}"
            f"{'（昵称兜底）' if page.author_id_is_fallback else ''}"
        )
        print(f"    发布时间: {published or '无'}")
        print(
            f"    截图: 内容页={'有' if page_shot_ok else '无'}"
            f" 个人页={'有' if author_shot_ok else '无'}"
        )
        for error in result.errors[:3]:
            print(f"    错误 [{error.code}]: {error.message[:100]}")
    return all_ok


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="两条真实抖音视频的专项验收工具")
    parser.add_argument("--headed", action="store_true", help="使用有头浏览器")
    parser.add_argument("--edge", action="store_true", help="使用本机 Edge")
    parser.add_argument(
        "--precheck-only",
        action="store_true",
        help="只检查短链解析与公共 API 兜底",
    )
    parser.add_argument(
        "--skip-precheck",
        action="store_true",
        help="直接执行完整爬取，减少对抖音的重复访问",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="指定截图输出目录；默认每次创建独立时间戳目录",
    )
    parser.add_argument(
        "--only",
        type=int,
        choices=(1, 2),
        help="只验收第 1 或第 2 条真实短链",
    )
    return parser.parse_args()


async def main() -> int:
    args = _arguments()
    headed = bool(args.headed)
    edge = bool(args.edge)
    precheck_only = bool(args.precheck_only)
    evidence_ids = (int(args.only),) if args.only else (1, 2)
    config = _config(headed, edge)
    output_dir = args.output_dir or (
        Path(__file__).resolve().parents[1]
        / "output"
        / (
            f"test-douyin-{'headed' if headed else 'headless'}-"
            f"{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print(
        "  抖音修复验证 "
        f"({'有头' if headed else '无头'}{' + Edge' if edge else ''} 模式)"
    )
    print("=" * 70)
    if not args.skip_precheck:
        print("\n[1/2] 分层预检（短链解析 + API 兜底）")
        await precheck(config, evidence_ids)
    if precheck_only:
        return 0
    print("\n[2/2] 完整爬取管线")
    ok = await crawl(config, output_dir, evidence_ids)
    print("\n" + ("全部通过 ✅" if ok else "仍有记录未达标 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
