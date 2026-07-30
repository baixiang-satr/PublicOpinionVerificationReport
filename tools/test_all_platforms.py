"""全面测试所有14个目标网站的爬取能力。

测试每个平台3个真实URL，评估：
1. 页面能否正常访问（非403/404/登录墙）
2. 能否提取必要字段（标题/正文/作者等）
3. 能否生成有效截图

运行方式:
    cd d:\\project\\PublicOpinionVerificationReport
    d:/project/PublicOpinionVerificationReport/.venv/Scripts/python.exe tools/test_all_platforms.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# 确保能从项目根目录导入
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.settings import TaskConfig
from src.crawler.engine import CrawlEngine
from src.domain.models import RecordStatus, UrlTask

# ─── 每个平台3条测试URL（不使用CSV中的失效URL）──────────────
# 使用真实内容 URL，优先选高概率仍在线的热门内容；数据表见独立模块。

from tools.platform_test_urls import PLATFORM_URLS

# 测试配置: 少量并发，足够超时，启用stealth
TEST_CONFIG = TaskConfig(
    max_concurrency=1,  # 单并发，避免触发反爬
    page_timeout_seconds=45,
    page_processing_timeout_seconds=180.0,
    max_retries=0,
    min_host_interval_seconds=1.0,
    page_stabilize_milliseconds=2000,
    screenshot_format="jpeg",
    headless=True,
    enable_stealth=True,
    enable_extra_stealth=True,
    full_page_screenshot=True,
    max_full_page_screenshot_height=4096,
    capture_network_json=True,
    enable_platform_fallbacks=True,
)


async def test_single_url(
    engine: CrawlEngine,
    platform: str,
    url: str,
    idx: int,
    output_dir: Path,
) -> dict:
    """测试单个URL，返回详细结果。"""
    result_info = {
        "platform": platform,
        "url": url,
        "index": idx + 1,
        "status": "unknown",
        "status_code": None,
        "final_url": None,
        "has_title": False,
        "has_content": False,
        "has_author": False,
        "has_screenshot": False,
        "errors": [],
        "record_status": None,
        "duration_seconds": None,
    }

    tasks = [UrlTask(idx + 1, url, url)]
    start_time = time.time()

    try:
        results = await engine.run(tasks, output_dir)
        if results:
            result = results[0]
            result_info["record_status"] = (
                result.status.value if result.status else None
            )
            result_info["duration_seconds"] = round(time.time() - start_time, 1)

            page = result.page
            if page:
                result_info["status_code"] = page.status_code
                result_info["final_url"] = page.final_url
                result_info["has_title"] = bool(page.title)
                result_info["has_content"] = bool(page.content_text)
                result_info["has_author"] = bool(page.author_name)

            if result.assets and result.assets.page_screenshot:
                shot_path = result.assets.page_screenshot
                if shot_path.exists() and shot_path.stat().st_size > 1024:
                    result_info["has_screenshot"] = True
                    result_info["screenshot_path"] = str(shot_path)
                    result_info["screenshot_size"] = shot_path.stat().st_size

            if result.errors:
                for e in result.errors:
                    result_info["errors"].append(
                        {
                            "stage": e.stage,
                            "code": e.code,
                            "message": e.message[:120],
                        }
                    )

            if result.status == RecordStatus.ASSETS_READY:
                result_info["status"] = "✅ 完整成功"
            elif result.status == RecordStatus.NEEDS_REVIEW:
                result_info["status"] = "⚠️ 部分成功"
            elif result.status == RecordStatus.FAILED:
                result_info["status"] = "❌ 失败"
            else:
                result_info["status"] = f"🔷 {result.status.value}"
        else:
            result_info["status"] = "❌ 无返回结果"
    except Exception as ex:
        result_info["status"] = "💥 异常"
        result_info["errors"].append(
            {"stage": "runtime", "code": "EXCEPTION", "message": str(ex)[:120]}
        )
        result_info["duration_seconds"] = round(time.time() - start_time, 1)

    return result_info


def print_result(result: dict) -> None:
    """打印单个测试结果。"""
    icon = result["status"]
    print(f"\n  [{result['index']}/3] {icon}")
    print(f"    URL: {result['url'][:100]}")
    print(f"    状态: {result['status']}", end="")
    if result["status_code"]:
        print(f" | HTTP {result['status_code']}", end="")
    if result["record_status"]:
        print(f" | {result['record_status']}", end="")
    if result["duration_seconds"]:
        print(f" | {result['duration_seconds']}s", end="")
    print()

    fields = []
    if result["has_title"]:
        fields.append("标题✅")
    else:
        fields.append("标题❌")
    if result["has_content"]:
        fields.append("正文✅")
    else:
        fields.append("正文❌")
    if result["has_author"]:
        fields.append("作者✅")
    else:
        fields.append("作者❌")
    if result["has_screenshot"]:
        fields.append(
            f"截图✅({result.get('screenshot_size', 0)//1024}KB)"
        )
    else:
        fields.append("截图❌")

    print(f"    字段提取: {' '.join(fields)}")

    if result["errors"]:
        for e in result["errors"][:2]:
            print(f"    错误 [{e['code']}]: {e['message'][:80]}")


def final_url_domain(final_url: str | None) -> str:
    """从URL提取域名用于展示。"""
    if not final_url:
        return "N/A"
    from urllib.parse import urlsplit
    parts = urlsplit(final_url)
    return parts.hostname or "N/A"


async def main() -> None:
    print("=" * 72)
    print("  舆情验证报告工具 — 全平台爬取能力测试")
    print(f"  测试日期: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  共 {len(PLATFORM_URLS)} 个平台, 每个平台 3 条 URL = {len(PLATFORM_URLS)*3} 次测试")
    print("=" * 72)

    output_base = Path(__file__).resolve().parents[1] / "output" / "test-all-platforms"
    output_base.mkdir(parents=True, exist_ok=True)

    engine = CrawlEngine(TEST_CONFIG)
    all_results: list[dict] = []
    platform_summaries: list[dict] = []

    try:
        for platform_name, sheet_name, urls in PLATFORM_URLS:
            print(f"\n{'─'*72}")
            print(f"  📱 平台: {platform_name} (→ {sheet_name})")
            print(f"{'─'*72}")

            platform_output = output_base / platform_name
            platform_output.mkdir(parents=True, exist_ok=True)

            url_results = []
            for idx, url in enumerate(urls):
                print(f"\n  >>> 测试 #{idx+1}")
                result = await test_single_url(
                    engine, platform_name, url, idx, platform_output
                )
                print_result(result)
                url_results.append(result)
                all_results.append(result)

            # 汇总此平台结果
            success_count = sum(
                1
                for r in url_results
                if r["status"] == "✅ 完整成功"
            )
            partial_count = sum(
                1
                for r in url_results
                if r["status"] == "⚠️ 部分成功"
            )
            fail_count = sum(
                1
                for r in url_results
                if r["status"] in ("❌ 失败", "💥 异常")
            )
            has_screenshot_count = sum(1 for r in url_results if r["has_screenshot"])
            has_title_count = sum(1 for r in url_results if r["has_title"])
            has_content_count = sum(1 for r in url_results if r["has_content"])
            has_author_count = sum(1 for r in url_results if r["has_author"])

            # 判定整体等级
            if success_count == 3 and has_screenshot_count == 3:
                grade = "✅ 完全可爬"
            elif success_count >= 1 or (partial_count >= 1 and has_screenshot_count >= 1):
                grade = "⚠️ 部分可爬"
            elif success_count == 0 and partial_count == 0:
                grade = "❌ 不可爬"
            else:
                grade = "⚠️ 部分可爬"

            platform_summaries.append(
                {
                    "platform": platform_name,
                    "sheet": sheet_name,
                    "grade": grade,
                    "success": success_count,
                    "partial": partial_count,
                    "fail": fail_count,
                    "has_screenshot": has_screenshot_count,
                    "has_title": has_title_count,
                    "has_content": has_content_count,
                    "has_author": has_author_count,
                    "url_results": url_results,
                }
            )

            print(f"\n  📊 平台汇总: {grade}  "
                  f"完整{success_count}/3 部分{partial_count}/3 失败{fail_count}/3  "
                  f"截图{has_screenshot_count}/3")

        # ── 打印最终汇总 ──
        print(f"\n\n{'='*72}")
        print("  📊 最终汇总")
        print(f"{'='*72}")
        print(f"{'平台':12s} {'评级':20s} {'完整':>6s} {'部分':>6s} {'失败':>6s} {'截图':>6s} {'标题':>6s} {'正文':>6s} {'作者':>6s}")
        print("-" * 72)

        fully_ok = 0
        partial_ok = 0
        not_ok = 0
        total_shot = 0
        total_title = 0
        total_content = 0

        for ps in platform_summaries:
            print(
                f"{ps['platform']:12s} {ps['grade']:20s} "
                f"{ps['success']:>6d} {ps['partial']:>6d} {ps['fail']:>6d} "
                f"{ps['has_screenshot']:>6d} {ps['has_title']:>6d} "
                f"{ps['has_content']:>6d} {ps['has_author']:>6d}"
            )
            if ps["grade"] == "✅ 完全可爬":
                fully_ok += 1
            elif ps["grade"] == "❌ 不可爬":
                not_ok += 1
            else:
                partial_ok += 1
            total_shot += ps["has_screenshot"]
            total_title += ps["has_title"]
            total_content += ps["has_content"]

        print("-" * 72)
        print(f"{'总计':12s} {'':20s} "
              f"{sum(ps['success'] for ps in platform_summaries):>6d} "
              f"{sum(ps['partial'] for ps in platform_summaries):>6d} "
              f"{sum(ps['fail'] for ps in platform_summaries):>6d} "
              f"{total_shot:>6d} {total_title:>6d} {total_content:>6d} "
              f"{sum(ps['has_author'] for ps in platform_summaries):>6d}")
        print(f"\n  平台统计: ✅完全可爬 {fully_ok}/14  ⚠️部分可爬 {partial_ok}/14  ❌不可爬 {not_ok}/14")
        print(f"  字段统计: 截图 {total_shot}/{len(PLATFORM_URLS)*3}  "
              f"标题 {total_title}/{len(PLATFORM_URLS)*3}  "
              f"正文 {total_content}/{len(PLATFORM_URLS)*3}")

        # ── 保存详细JSON报告 ──
        report = {
            "test_date": datetime.now().isoformat(),
            "total_platforms": len(PLATFORM_URLS),
            "total_urls": len(PLATFORM_URLS) * 3,
            "platforms": [
                {
                    "platform": ps["platform"],
                    "sheet": ps["sheet"],
                    "grade": ps["grade"],
                    "stats": {
                        "success": ps["success"],
                        "partial": ps["partial"],
                        "fail": ps["fail"],
                        "screenshot": ps["has_screenshot"],
                        "title": ps["has_title"],
                        "content": ps["has_content"],
                        "author": ps["has_author"],
                    },
                    "urls": [
                        {
                            "url": r["url"],
                            "status": r["status"],
                            "http_code": r["status_code"],
                            "has_screenshot": r["has_screenshot"],
                            "has_title": r["has_title"],
                            "has_content": r["has_content"],
                            "has_author": r["has_author"],
                            "errors": r["errors"],
                            "duration": r["duration_seconds"],
                        }
                        for r in ps["url_results"]
                    ],
                }
                for ps in platform_summaries
            ],
        }

        report_path = output_base / "test_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n  详细报告已保存: {report_path}")

        # 简要Markdown报告
        md_path = output_base / "test_report.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# 全平台爬取能力测试报告\n\n")
            f.write(f"**测试日期**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"| 平台 | 评级 | 完整 | 部分 | 失败 | 截图 | 标题 | 正文 | 作者 |\n")
            f.write(f"|------|------|------|------|------|------|------|------|------|\n")
            for ps in platform_summaries:
                f.write(
                    f"| {ps['platform']} | {ps['grade']} | "
                    f"{ps['success']}/3 | {ps['partial']}/3 | {ps['fail']}/3 | "
                    f"{ps['has_screenshot']}/3 | {ps['has_title']}/3 | "
                    f"{ps['has_content']}/3 | {ps['has_author']}/3 |\n"
                )
            f.write(f"\n## 详细测试结果\n\n")
            for ps in platform_summaries:
                f.write(f"### {ps['platform']} ({ps['sheet']}) — {ps['grade']}\n\n")
                for r in ps["url_results"]:
                    fields = []
                    fields.append("📸" if r["has_screenshot"] else "📷X")
                    fields.append("📝" if r["has_title"] else "TX")
                    fields.append("📄" if r["has_content"] else "CX")
                    fields.append("👤" if r["has_author"] else "AX")
                    f.write(f"- **#{r['index']}** {r['status']} | "
                           f"{''.join(fields)} | HTTP {r['status_code'] or 'N/A'} | "
                           f"{r['duration_seconds'] or '?'}s\n")
                    f.write(f"  `{r['url'][:100]}`\n")
                    if r["errors"]:
                        for e in r["errors"][:3]:
                            f.write(f"  - [{e['code']}] {e['message'][:100]}\n")
                    f.write("\n")

        print(f"  简要报告已保存: {md_path}")

    finally:
        await engine._browser_pool.close()


if __name__ == "__main__":
    asyncio.run(main())
