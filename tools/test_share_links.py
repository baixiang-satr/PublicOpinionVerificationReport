"""23 条社交媒体分享链接验收实跑：全管线抓取 + 字段 dump + 人工核验清单。

前置条件：用户已在桌面工具「管理平台登录态」中完成各平台登录。
运行方式:
    .venv/Scripts/python.exe tools/test_share_links.py
    .venv/Scripts/python.exe tools/test_share_links.py --input tests\\test_input\\social_share_links.csv

产出:
    output/share-link-acceptance/acceptance_report.md    逐链接结果 + 人工核验清单
    output/share-link-acceptance/acceptance_report.json  机器可读明细
    output/<job_id>/                                     任务产物（template.zip、质量报告、截图）

完整成功口径（与验收约定一致）：
    页面可访问（状态 assets_ready/ready_for_export/exported）
    + 标题非空 + 正文非空 + 作者非空 + 有效内容页截图（>1KB）
达标线：完整成功 ≥ 85%（23 条 ⇒ 至少 20 条）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from src.config.settings import AppConfig
from src.crawler.platform_catalog import find_platform
from src.domain.models import RecordResult, RecordStatus
from src.domain.template_schema import SHEET_LAYOUTS
from src.services.models import JobRequest
from src.services.task_runner import TaskRunner

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "tests" / "test_input" / "social_share_links.csv"
REPORT_DIR = PROJECT_ROOT / "output" / "share-link-acceptance"
PASS_LINE = 0.85
MIN_SCREENSHOT_BYTES = 1024
SUCCESS_STATUSES = {
    RecordStatus.ASSETS_READY,
    RecordStatus.READY_FOR_EXPORT,
    RecordStatus.EXPORTED,
}

# 与 CSV 行序一致的预期平台 key（路由回归可见性；None 表示不校验）。
EXPECTED_PLATFORMS: tuple[str, ...] = (
    "wechat_official",
    "wechat_video",
    "wechat_video",
    "xiaohongshu",
    "xiaohongshu",
    "xiaohongshu",
    "weibo",
    "weibo",
    "weibo",
    "douyin",
    "douyin",
    "douyin",
    "toutiao",
    "toutiao",
    "toutiao",
    "bilibili",
    "bilibili",
    "bilibili",
    "ixigua",
    "ixigua",
    "ixigua",
    "baijiahao",
    "baijiahao",
)


def _title_required(record: RecordResult) -> bool:
    """仅当目标工作表有标题列（如公众号表）时才强制标题非空。"""

    if record.route is None:
        return True
    layout = SHEET_LAYOUTS.get(record.route.sheet_name)
    return bool(layout and "title" in layout.field_columns)


def _record_complete_success(record: RecordResult) -> bool:
    """按验收口径判定单条记录是否完整成功。"""
    if record.status not in SUCCESS_STATUSES:
        return False
    page = record.page
    if not (page.content_text and page.author_name):
        return False
    if _title_required(record) and not page.title:
        return False
    shot = record.assets.page_screenshot
    return bool(shot and shot.exists() and shot.stat().st_size > MIN_SCREENSHOT_BYTES)


def _tick(ok: bool) -> str:
    return "✅" if ok else "❌"


def _preview(text: str | None, limit: int = 300) -> str:
    if not text:
        return ""
    compact = " ".join(text.split())
    return compact[:limit] + ("…" if len(compact) > limit else "")


def _analyze(records: tuple[RecordResult, ...]) -> list[dict]:
    rows: list[dict] = []
    for record in records:
        page = record.page
        url = record.task.original_url or record.task.normalized_url
        expected = (
            EXPECTED_PLATFORMS[record.task.evidence_id - 1]
            if 0 < record.task.evidence_id <= len(EXPECTED_PLATFORMS)
            else None
        )
        detected = find_platform(url)
        shot = record.assets.page_screenshot
        shot_size = (
            shot.stat().st_size if shot and shot.exists() else 0
        )
        author_shot = record.assets.author_screenshot
        rows.append(
            {
                "eid": record.task.evidence_id,
                "url": url,
                "expected_platform": expected,
                "detected_platform": detected.key if detected else None,
                "route_ok": expected is None or (detected and detected.key == expected),
                "status": record.status.value,
                "http_status": page.status_code,
                "final_url": page.final_url,
                "title": page.title,
                "author_name": page.author_name,
                "author_id": page.author_id,
                "published_at_raw": page.published_at_raw,
                "published_at": (
                    page.published_at.strftime("%Y-%m-%d %H:%M:%S")
                    if page.published_at
                    else None
                ),
                "content_chars": len(page.content_text or ""),
                "content_preview": _preview(page.content_text),
                "field_sources": {k: v.value for k, v in page.field_sources.items()},
                "has_title": bool(page.title),
                "has_content": bool(page.content_text),
                "has_author": bool(page.author_name),
                "screenshot_path": str(shot) if shot else None,
                "screenshot_size": shot_size,
                "author_screenshot_path": str(author_shot) if author_shot else None,
                "elapsed_seconds": record.elapsed_seconds,
                "errors": [
                    {"stage": e.stage, "code": e.code, "message": e.message[:160]}
                    for e in record.errors
                ],
                "complete_success": _record_complete_success(record),
            }
        )
    return rows


def _write_reports(rows: list[dict], job_dir: Path, label: str) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / "acceptance_report.json"
    md_path = REPORT_DIR / "acceptance_report.md"

    total = len(rows)
    success = sum(1 for r in rows if r["complete_success"])
    rate = (success / total) if total else 0.0
    verdict = "PASS ✅" if rate >= PASS_LINE else "FAIL ❌"

    json_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "label": label,
                "job_dir": str(job_dir),
                "total": total,
                "complete_success": success,
                "success_rate": round(rate, 4),
                "pass_line": PASS_LINE,
                "verdict": verdict,
                "records": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    lines: list[str] = []
    lines.append("# 分享链接爬取验收报告")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"- 任务目录：`{job_dir}`")
    lines.append(f"- 完整成功：**{success}/{total} = {rate:.1%}**（达标线 {PASS_LINE:.0%}）→ **{verdict}**")
    lines.append("")
    lines.append("## 逐链接结果")
    lines.append("")
    lines.append("| # | 预期平台 | 路由 | 状态 | HTTP | 标题 | 正文 | 作者 | 截图 | 完整成功 |")
    lines.append("|---|---------|------|------|------|------|------|------|------|---------|")
    for r in rows:
        route = "✅" if r["route_ok"] else f"❌({r['detected_platform']})"
        lines.append(
            f"| {r['eid']} | {r['expected_platform']} | {route} | {r['status']} "
            f"| {r['http_status'] or '-'} | {_tick(r['has_title'])} "
            f"| {_tick(r['has_content'])}({_safe(r['content_chars'])}) "
            f"| {_tick(r['has_author'])} | {_tick(r['screenshot_size'] > MIN_SCREENSHOT_BYTES)}"
            f"({r['screenshot_size'] // 1024}KB) | {_tick(r['complete_success'])} |"
        )
    lines.append("")
    lines.append("## 人工核验清单（对照截图逐条打勾）")
    lines.append("")
    for r in rows:
        lines.append(f"### [{r['eid']:03d}] {r['expected_platform']}")
        lines.append("")
        lines.append(f"- URL：{r['url']}")
        lines.append(f"- 标题：{r['title'] or '（空）'}")
        lines.append(f"- 作者：{r['author_name'] or '（空）'}（ID：{r['author_id'] or '-'}）")
        lines.append(f"- 发布时间：{r['published_at'] or r['published_at_raw'] or '（空）'}")
        lines.append(f"- 正文（{r['content_chars']} 字）：{r['content_preview'] or '（空）'}")
        lines.append(f"- 截图：`{r['screenshot_path'] or '（无）'}`")
        if r["errors"]:
            err = r["errors"][0]
            lines.append(f"- 错误：[{err['code']}] {err['message']}")
        lines.append("- 核验：标题正确 [ ]　作者正确 [ ]　正文一致 [ ]　截图有效 [ ]")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, json_path


def _safe(value: object) -> str:
    return str(value)


async def main(input_path: Path, label: str) -> int:
    # from_environment 与桌面端一致，自动复用用户本机加密登录态。
    config = AppConfig.from_environment(PROJECT_ROOT)
    runner = TaskRunner(config)
    result = await runner.run(JobRequest(input_path=input_path, label=label))

    rows = _analyze(result.records)
    md_path, json_path = _write_reports(rows, result.job_dir, label)

    total = len(rows)
    success = sum(1 for r in rows if r["complete_success"])
    rate = (success / total) if total else 0.0
    print(f"job_id={result.job_id}")
    print(f"archive={result.archive_path}")
    print(f"完整成功: {success}/{total} = {rate:.1%}（达标线 {PASS_LINE:.0%}）")
    for r in rows:
        flag = "✅" if r["complete_success"] else "❌"
        print(
            f"  [{r['eid']:03d}] {flag} {r['expected_platform']:<16} "
            f"{r['status']:<16} 标题{_tick(r['has_title'])} "
            f"正文{_tick(r['has_content'])} 作者{_tick(r['has_author'])} "
            f"截图{r['screenshot_size'] // 1024}KB"
        )
    print(f"报告: {md_path}")
    print(f"明细: {json_path}")
    return 0 if rate >= PASS_LINE else 1


if __name__ == "__main__":
    # 中文 Windows 控制台默认 GBK，帮助文本与 emoji 状态符需 UTF-8 输出，
    # 必须先于 argparse 解析（--help 会直接打印 docstring 中的 ⇒）。
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--label", default="分享链接验收")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.input.resolve(), args.label)))
