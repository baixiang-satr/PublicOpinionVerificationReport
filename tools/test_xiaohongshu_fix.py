"""小红书笔记专项爬取与截图验收工具。

默认使用本次修复的真实分享 URL。完整验收只访问笔记一次，避免重复
请求触发平台频控；也可单独运行轻量预检：

1. ``--precheck-only`` 读取 ``window.__INITIAL_STATE__`` 并核对笔记 ID；
2. 默认运行项目完整 ``CrawlEngine``，输出字段和证据截图。

运行示例::

    .venv\\Scripts\\python.exe -X utf8 tools\\test_xiaohongshu_fix.py
    .venv\\Scripts\\python.exe -X utf8 tools\\test_xiaohongshu_fix.py --headless
    .venv\\Scripts\\python.exe -X utf8 tools\\test_xiaohongshu_fix.py --precheck-only

工具只读取公开页面，不自动登录、不处理验证码，也不绕过访问控制。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.settings import TaskConfig, default_auth_store_dir
from src.crawler.engine import CrawlEngine
from src.crawler.platforms.extract_helpers import evaluate_json
from src.crawler.platforms.xiaohongshu import (
    _INITIAL_STATE_NOTE_SCRIPT,
    xiaohongshu_note_id,
)
from src.domain.models import RecordResult, RecordStatus, UrlTask
from src.utils.time_utils import DEFAULT_TIMEZONE

TEST_URL = (
    "https://www.xiaohongshu.com/explore/6a5dd45a0000000001033293"
    "?app_platform=android&ignoreEngage=true&app_version=9.40.0"
    "&share_from_user_hidden=true&xsec_source=app_share&type=normal"
    "&xsec_token=CBoSyprSxvgShPap2rA5gp76m1HlBGxpVlahX4ktT2P50%3D"
    "&author_share=1&xhsshare=WeixinSession"
    "&shareRedId=ODhGNjc8Ok02NzUyOTgwNjY1OTc0SjpM"
    "&apptime=1785429056"
    "&share_id=42f32ac8f4564f0e8ea591ce49d68df5"
    "&share_channel=wechat&code=xFGpxP88mK"
)
EXPECTED_NOTE_ID = "6a5dd45a0000000001033293"
EXPECTED_TITLE = "买不到 kimi 订阅的别伤心，买到了也 429"
EXPECTED_CONTENT = "买的199年订阅，用了三个多月的kimi"
EXPECTED_AUTHOR = "靠窗那一狗"
EXPECTED_PUBLISHED_AT = "2026-07-20 15:55:06"


def _config(
    headed: bool,
    edge: bool = False,
    *,
    guest: bool = False,
) -> TaskConfig:
    return TaskConfig(
        max_concurrency=1,
        page_timeout_seconds=45,
        page_processing_timeout_seconds=180.0,
        max_retries=1,
        min_host_interval_seconds=1.0,
        page_stabilize_milliseconds=2_000,
        screenshot_format="jpeg",
        full_page_screenshot=True,
        headless=not headed,
        browser_channel="msedge" if edge else None,
        auth_store_dir=None if guest else default_auth_store_dir(),
        # Xiaohongshu's current public SSR page is most reliable with the
        # browser's native fingerprint.  The shared stealth scripts are
        # useful for some platforms but trigger 300012 on this route.
        enable_stealth=False,
        enable_extra_stealth=False,
        enable_platform_fallbacks=True,
        enable_headed_fallback=False,
        capture_network_json=True,
        ocr_enabled=False,
    )


async def precheck(config: TaskConfig, url: str) -> bool:
    """Verify that the rendered page exposes the URL-matched note state."""

    from playwright.async_api import async_playwright

    from src.auth.store import AuthProfileStore
    from src.crawler.navigation import stabilize_rendered_page
    from src.screenshot.browser_options import (
        browser_context_options,
        browser_launch_options,
    )

    store = (
        AuthProfileStore(config.auth_store_dir)
        if config.auth_store_dir is not None
        else None
    )
    try:
        state = (
            store.load_state("xiaohongshu", include_inactive=True)
            if store is not None
            else None
        )
    except Exception:  # noqa: BLE001 - a corrupt optional auth state means guest mode
        state = None
    profile = store.profile_for("xiaohongshu") if store is not None else None
    state_label = (
        f"已加载（{profile.status.value}）"
        if state is not None and profile is not None
        else "无（游客模式）"
    )
    print(f"登录态: {state_label}")

    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(**browser_launch_options(config))
    try:
        context = await browser.new_context(
            **browser_context_options(config, state)
        )
        page = await context.new_page()
        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=config.page_timeout_seconds * 1_000,
        )
        await stabilize_rendered_page(
            page,
            config.page_stabilize_milliseconds,
        )
        note = await evaluate_json(page, _INITIAL_STATE_NOTE_SCRIPT)
        expected_id = xiaohongshu_note_id(url)
        actual_id = (
            str(note.get("noteId") or note.get("note_id") or note.get("id") or "")
            if isinstance(note, dict)
            else ""
        )
        print(f"HTTP: {response.status if response is not None else '未知'}")
        print(f"笔记 ID: {actual_id or '未提取'}")
        if not isinstance(note, dict):
            print("预检失败：未读取到当前 URL 对应的笔记状态。")
            return False
        user = note.get("user") if isinstance(note.get("user"), dict) else {}
        print(f"标题: {note.get('title') or '未提取'}")
        print(f"作者: {user.get('nickname') or user.get('name') or '未提取'}")
        print(f"正文: {str(note.get('desc') or '')[:120]}")
        return bool(expected_id and actual_id == expected_id)
    finally:
        await browser.close()
        await playwright.stop()


async def crawl(
    config: TaskConfig,
    url: str,
    output_dir: Path,
) -> tuple[bool, RecordResult]:
    """Run the project's complete extraction and evidence pipeline."""

    engine = CrawlEngine(config)
    started = time.monotonic()
    results = await engine.run([UrlTask(1, url, url)], output_dir)
    elapsed = time.monotonic() - started
    if not results:
        raise RuntimeError("爬虫没有返回记录")
    result = results[0]
    page = result.page
    page_shot_ok = bool(
        result.assets.page_screenshot
        and Path(result.assets.page_screenshot).is_file()
        and Path(result.assets.page_screenshot).stat().st_size > 1_024
    )
    author_shot_ok = bool(
        result.assets.author_screenshot
        and Path(result.assets.author_screenshot).is_file()
        and Path(result.assets.author_screenshot).stat().st_size > 1_024
    )
    published = (
        page.published_at.strftime("%Y-%m-%d %H:%M:%S")
        if page.published_at
        else ""
    )
    is_reference = xiaohongshu_note_id(url) == EXPECTED_NOTE_ID
    fields_ok = bool(page.title and page.content_text and page.author_name)
    if is_reference:
        fields_ok = (
            page.title == EXPECTED_TITLE
            and EXPECTED_CONTENT in (page.content_text or "")
            and page.author_name == EXPECTED_AUTHOR
            and published == EXPECTED_PUBLISHED_AT
        )
    ok = (
        result.status == RecordStatus.ASSETS_READY
        and fields_ok
        and page_shot_ok
    )

    print(f"\n爬取完成，用时 {elapsed:.1f}s")
    print(f"状态: {result.status.value}")
    print(f"最终 URL: {_public_url(page.final_url or url)}")
    print(f"标题: {page.title or '未提取'}")
    print(f"正文: {(page.content_text or '')[:300] or '未提取'}")
    print(f"作者: {page.author_name or '未提取'}")
    print(f"账号 ID: {page.author_id or '未提取'}")
    print(f"发布时间: {published or '未提取'}")
    print(
        "截图: "
        f"内容页={'成功' if page_shot_ok else '失败'}，"
        f"作者主页={'成功' if author_shot_ok else '未取得（不阻断主记录）'}"
    )
    if result.assets.page_screenshot:
        print(f"内容页截图: {Path(result.assets.page_screenshot).resolve()}")
    if result.assets.author_screenshot:
        print(f"作者主页截图: {Path(result.assets.author_screenshot).resolve()}")
    for error in result.errors:
        print(f"提醒 [{error.code}]: {error.message}")

    summary = {
        "ok": ok,
        "status": result.status.value,
        "url": _public_url(page.final_url or url),
        "note_id": xiaohongshu_note_id(page.final_url or url),
        "title": page.title,
        "content_text": page.content_text,
        "author_name": page.author_name,
        "author_id": page.author_id,
        "published_at": published or None,
        "page_screenshot": (
            str(Path(result.assets.page_screenshot).resolve())
            if result.assets.page_screenshot
            else None
        ),
        "author_screenshot": (
            str(Path(result.assets.author_screenshot).resolve())
            if result.assets.author_screenshot
            else None
        ),
        "errors": [
            {
                "stage": error.stage,
                "code": error.code,
                "message": error.message,
            }
            for error in result.errors
        ],
    }
    report_path = output_dir / "xiaohongshu_result.json"
    report_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"结构化结果: {report_path.resolve()}")
    return ok, result


def _public_url(url: str) -> str:
    """Drop share/auth query parameters from console and JSON diagnostics."""

    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="小红书笔记字段与截图专项验收工具",
    )
    parser.add_argument("--url", default=TEST_URL, help="待验收的小红书笔记 URL")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="改用无头 Chromium；小红书可能返回安全限制，默认使用可视 Edge",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--edge",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--use-saved-login",
        action="store_true",
        help="显式加载本机小红书登录态；默认游客优先，避免过期状态污染公开分享页",
    )
    parser.add_argument(
        "--precheck-only",
        action="store_true",
        help="只验证页面内嵌笔记状态，不运行完整截图流程",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="指定输出目录；默认按当前时间创建独立目录",
    )
    return parser.parse_args()


async def main() -> int:
    args = _arguments()
    headed = bool(args.headed) or not bool(args.headless)
    edge = bool(args.edge) or headed
    config = _config(
        headed,
        edge,
        guest=not bool(args.use_saved_login),
    )
    mode = "headed" if headed else "headless"
    output_dir = args.output_dir or (
        Path(__file__).resolve().parents[1]
        / "output"
        / (
            "test-xiaohongshu-"
            f"{mode}-{datetime.now(DEFAULT_TIMEZONE).strftime('%Y%m%d-%H%M%S')}"
        )
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 72)
    print(f"小红书专项验收（{mode}{' + Edge' if edge else ''}）")
    print(f"URL: {_public_url(args.url)}")
    print("=" * 72)

    if args.precheck_only:
        print("\n目标笔记状态预检")
        return 0 if await precheck(config, args.url) else 1

    print("\n完整爬取与截图")
    crawl_ok, _result = await crawl(config, args.url, output_dir)
    print("\n" + ("验收通过 ✓" if crawl_ok else "验收未通过 ✗"))
    return 0 if crawl_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
