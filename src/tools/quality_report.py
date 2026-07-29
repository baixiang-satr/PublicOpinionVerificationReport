"""Per-job quality statistics and a manual-entry queue outside the delivery ZIP."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from src.crawler.platform_router import PlatformRouter
from src.domain.models import RecordResult, RecordStatus
from src.tools.crawl_tracker import get_failure_advice
from src.utils.time_utils import DEFAULT_TIMEZONE


_SUCCESS_STATUSES = {
    RecordStatus.EXPORTED,
    RecordStatus.ASSETS_READY,
    RecordStatus.READY_FOR_EXPORT,
}
_TRACKED_FIELDS = (
    "title",
    "content_text",
    "author_name",
    "author_id",
    "account_uin",
    "store_name",
    "published_at",
)


@dataclass(frozen=True)
class QualityArtifacts:
    report_path: Path
    summary_path: Path
    manual_entry_path: Path


def write_quality_artifacts(
    records: list[RecordResult],
    job_dir: Path,
    *,
    job_id: str,
    label: str,
    rejected_count: int = 0,
    router: PlatformRouter | None = None,
    author_decisions: list[Any] | None = None,
    author_audit_entries: list[dict[str, Any]] | None = None,
) -> QualityArtifacts:
    """Write UTF-8 audit files without changing the fixed template ZIP."""

    destination = Path(job_dir)
    destination.mkdir(parents=True, exist_ok=True)
    router = router or PlatformRouter()
    rows = [_record_row(record, router) for record in records]
    platform_stats = _platform_statistics(rows)
    error_counts = _count_values(
        code
        for row in rows
        for code in row["error_codes"]
    )
    source_counts = _count_values(
        source.value
        for record in records
        for source in record.page.field_sources.values()
    )
    status_counts = _count_values(record.status.value for record in records)
    manual_rows = [
        row
        for row in rows
        if row["needs_manual_entry"]
    ]
    summary: dict[str, Any] = {
        "schema_version": 1,
        "job_id": job_id,
        "label": label,
        "generated_at": datetime.now(DEFAULT_TIMEZONE).isoformat(),
        "totals": {
            "input_records": len(records),
            "successful_records": sum(
                record.status in _SUCCESS_STATUSES for record in records
            ),
            "manual_entry_records": len(manual_rows),
            "records_with_page_screenshot": sum(
                record.assets.page_screenshot is not None for record in records
            ),
            "records_with_author_screenshot": sum(
                record.assets.author_screenshot is not None for record in records
            ),
            "author_profile_eligible": sum(
                row["author_profile_eligible"] for row in rows
            ),
            "author_profile_url_found": sum(
                row["author_profile_url_found"] for row in rows
            ),
            "author_profile_screenshot_valid": sum(
                row["has_author_screenshot"] for row in rows
            ),
            "records_with_title": sum(bool(record.page.title) for record in records),
            "records_with_nickname": sum(
                bool(record.page.author_name or record.page.store_name)
                for record in records
            ),
            "records_with_published_at": sum(
                record.page.published_at is not None for record in records
            ),
            "records_with_content": sum(
                bool(record.page.content_text) for record in records
            ),
            "rejected_input_values": rejected_count,
        },
        "status_counts": status_counts,
        "error_counts": error_counts,
        "extraction_source_counts": source_counts,
        "platforms": platform_stats,
        "author_evidence": _author_evidence_summary(
            author_decisions or [],
            author_audit_entries or [],
        ),
    }

    summary_path = destination / "quality_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manual_entry_path = destination / "pending_manual_entry.csv"
    _write_manual_csv(manual_entry_path, manual_rows)
    report_path = destination / "quality_report.md"
    report_path.write_text(
        _markdown_report(summary, manual_entry_path.name),
        encoding="utf-8",
    )
    return QualityArtifacts(report_path, summary_path, manual_entry_path)


def _record_row(
    record: RecordResult,
    router: PlatformRouter,
) -> dict[str, Any]:
    definition = router.definition_for(record.task.normalized_url)
    route = record.route
    error_codes = list(dict.fromkeys(error.code for error in record.errors))
    error_messages = list(dict.fromkeys(error.message for error in record.errors))
    partial = "PARTIAL_FIELDS_MISSING" in error_codes
    successful = record.status in _SUCCESS_STATUSES
    author_failure_codes = [
        code
        for code in error_codes
        if code.startswith("AUTHOR_")
    ]
    return {
        "evidence_id": record.task.evidence_id,
        "platform_key": definition.key if definition else "unmapped",
        "platform": (
            route.platform_value
            if route is not None
            else definition.platform_value
            if definition is not None
            else "未匹配"
        ),
        "sheet_name": (
            route.sheet_name
            if route is not None
            else definition.sheet_name
            if definition is not None
            else ""
        ),
        "original_url": record.task.original_url,
        "final_url": record.page.final_url or "",
        "status": record.status.value,
        "successful": successful,
        "needs_manual_entry": not successful or partial,
        "has_page_screenshot": record.assets.page_screenshot is not None,
        "has_author_screenshot": record.assets.author_screenshot is not None,
        "author_profile_eligible": bool(
            record.page.author_url
            or record.assets.author_screenshot
        ),
        "author_profile_url_found": bool(record.page.author_url),
        "author_profile_failure_code": (
            author_failure_codes[0] if author_failure_codes else ""
        ),
        "content_kind": record.page.content_kind.value,
        "ocr_status": record.page.ocr_status.value,
        "ocr_image_count": record.page.ocr_image_count,
        "ocr_text_image_count": record.page.ocr_text_image_count,
        "original_content_chars": record.page.original_content_chars,
        "exported_content_chars": record.page.exported_content_chars,
        "missing_fields": [
            field
            for field in _TRACKED_FIELDS
            if not getattr(record.page, field)
        ],
        "field_rejection_notes": list(
            dict.fromkeys(record.page.field_rejection_notes)
        )[:5],
        "filled_fields": sum(
            bool(getattr(record.page, field))
            for field in _TRACKED_FIELDS
        ),
        "tracked_fields": len(_TRACKED_FIELDS),
        "error_codes": error_codes,
        "error_messages": error_messages,
    }


def _platform_statistics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row["platform_key"], []).append(row)
    statistics: list[dict[str, Any]] = []
    for key, group in sorted(groups.items()):
        total = len(group)
        successful = sum(row["successful"] for row in group)
        field_slots = sum(row["tracked_fields"] for row in group)
        filled_fields = sum(row["filled_fields"] for row in group)
        statistics.append(
            {
                "platform_key": key,
                "platform": group[0]["platform"],
                "sheet_name": group[0]["sheet_name"],
                "total": total,
                "successful": successful,
                "needs_manual_entry": sum(
                    row["needs_manual_entry"] for row in group
                ),
                "with_page_screenshot": sum(
                    row["has_page_screenshot"] for row in group
                ),
                "success_rate": round(successful / total, 4) if total else 0,
                "field_coverage": (
                    round(filled_fields / field_slots, 4)
                    if field_slots
                    else 0
                ),
            }
        )
    return statistics


def _write_manual_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = (
        "证据编号",
        "平台",
        "工作表",
        "原始URL",
        "最终URL",
        "状态",
        "错误码",
        "错误说明",
        "建议处理",
        "缺失字段",
        "字段拒绝原因",
        "主截图拒绝原因",
        "主页截图拒绝原因",
        "建议在登录态恢复后重试",
        "推荐补录顺序",
        "内容类型",
        "OCR状态",
        "主页截图状态",
        "是否可自动重试",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            codes = row["error_codes"]
            messages = row["error_messages"]
            advice = get_failure_advice(codes[0]) if codes else "请人工核对并补录。"
            page_screenshot_rejection = next(
                (
                    code
                    for code in codes
                    if code in {"PAGE_SCREENSHOT_FAILED", "CONTENT_SCREENSHOT_FAILED"}
                ),
                "",
            )
            writer.writerow(
                {
                    "证据编号": f"{row['evidence_id']:03d}",
                    "平台": row["platform"],
                    "工作表": row["sheet_name"],
                    "原始URL": row["original_url"],
                    "最终URL": row["final_url"],
                    "状态": row["status"],
                    "错误码": "; ".join(codes),
                    "错误说明": "; ".join(messages),
                    "建议处理": advice.replace("\n", " "),
                    "缺失字段": "; ".join(row["missing_fields"]),
                    "字段拒绝原因": (
                        "；".join(row["field_rejection_notes"])
                        if row["field_rejection_notes"]
                        else (
                            "平台未提供可信候选"
                            if row["missing_fields"]
                            else ""
                        )
                    ),
                    "主截图拒绝原因": page_screenshot_rejection,
                    "主页截图拒绝原因": row["author_profile_failure_code"],
                    "建议在登录态恢复后重试": (
                        "是"
                        if any(
                            code
                            in {
                                "LOGIN_REQUIRED",
                                "HTTP_401",
                                "PLATFORM_AUTH_PAUSED",
                                "CAPTCHA_REQUIRED",
                                "ACCESS_CHALLENGE",
                                "HTTP_403",
                                "AUTHOR_ACCESS_RESTRICTED",
                            }
                            for code in codes
                        )
                        else "否"
                    ),
                    "推荐补录顺序": _manual_entry_priority(row),
                    "内容类型": row["content_kind"],
                    "OCR状态": row["ocr_status"],
                    "主页截图状态": (
                        "已生成"
                        if row["has_author_screenshot"]
                        else (
                            f"未生成（{row['author_profile_failure_code']}）"
                            if row["author_profile_failure_code"]
                            else "未生成"
                        )
                    ),
                    "是否可自动重试": (
                        "是"
                        if any(
                            code
                            in {
                                "OCR_TIMEOUT",
                                "OCR_FAILED",
                                "OPTIONAL_ENRICHMENT_TIMEOUT",
                                "PAGE_PROCESSING_TIMEOUT",
                                "PAGE_SCREENSHOT_FAILED",
                            }
                            for code in codes
                        )
                        else "否"
                    ),
                }
            )


def _manual_entry_priority(row: dict[str, Any]) -> str:
    """Suggested manual-entry order: nearly-complete records finish fastest."""

    missing = len(row["missing_fields"])
    if row["status"] == "failed":
        return "靠后（主结果未取到，先核对 URL/登录态）"
    if missing <= 2:
        return "优先（仅缺少数字段）"
    if missing <= 4:
        return "普通"
    return "靠后"


def _markdown_report(summary: dict[str, Any], manual_file: str) -> str:
    totals = summary["totals"]
    lines = [
        "# 抓取质量报告",
        "",
        f"- 任务：{summary['label']}",
        f"- 任务 ID：`{summary['job_id']}`",
        f"- 输入记录：{totals['input_records']}",
        f"- 成功记录：{totals['successful_records']}",
        f"- 待人工补录：{totals['manual_entry_records']}",
        f"- 有正文截图：{totals['records_with_page_screenshot']}",
        f"- 有个人主页截图：{totals['records_with_author_screenshot']}",
        (
            "- 个人主页候选/发现 URL/有效截图："
            f"{totals['author_profile_eligible']}/"
            f"{totals['author_profile_url_found']}/"
            f"{totals['author_profile_screenshot_valid']}"
        ),
        f"- 有标题：{totals['records_with_title']}",
        f"- 有昵称/店铺名：{totals['records_with_nickname']}",
        f"- 有发布时间：{totals['records_with_published_at']}",
        f"- 有信息内容：{totals['records_with_content']}",
        f"- 待补录清单：`{manual_file}`",
        "",
        "## 平台统计",
        "",
        "| 平台 | 工作表 | 总数 | 成功 | 待补录 | 有截图 | 成功率 | 字段覆盖率 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summary["platforms"]:
        lines.append(
            "| {platform} | {sheet_name} | {total} | {successful} | "
            "{needs_manual_entry} | {with_page_screenshot} | {success_rate:.1%} | "
            "{field_coverage:.1%} |".format(**item)
        )
    lines.extend(
        [
            "",
            "## 错误码统计",
            "",
            "| 错误码 | 数量 |",
            "|---|---:|",
        ]
    )
    for code, count in summary["error_counts"].items():
        lines.append(f"| `{code}` | {count} |")
    if not summary["error_counts"]:
        lines.append("| 无 | 0 |")
    author = summary.get("author_evidence") or {}
    if author:
        lines.extend(
            [
                "",
                "## 个人主页证据验收",
                "",
                f"- 候选主页：{author.get('author_url_candidates', 0)}",
                f"- 已打开主页：{author.get('author_pages_opened', 0)}",
                f"- 身份校验通过：{author.get('author_identity_validated', 0)}",
                f"- 干净可交付截图：{author.get('author_clean_screenshots', 0)}",
            ]
        )
        rejections = author.get("author_rejection_codes") or {}
        if rejections:
            lines.extend(["", "| 拒绝码 | 数量 |", "|---|---:|"])
            for code, count in rejections.items():
                lines.append(f"| `{code}` | {count} |")
        removals = author.get("author_staging_audit_removals") or []
        if removals:
            lines.extend(["", "### ZIP 前审计移除的主页附件", ""])
            for entry in removals:
                lines.append(
                    f"- `{entry.get('file')}`（{entry.get('rejection_code')}）"
                )
    return "\n".join(lines) + "\n"


def _author_evidence_summary(
    decisions: list[Any],
    audit_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Structured author-home evidence counters from persisted decisions."""

    rejection_counts = _count_values(
        getattr(decision, "rejection_code", None) or "UNKNOWN"
        for decision in decisions
        if not getattr(decision, "accepted", False)
    )
    return {
        "author_url_candidates": len(decisions),
        "author_pages_opened": sum(
            getattr(decision, "access_state", "unknown") != "unknown"
            for decision in decisions
        ),
        "author_identity_validated": sum(
            getattr(decision, "identity_state", "unverified") == "verified"
            for decision in decisions
        ),
        "author_clean_screenshots": sum(
            bool(getattr(decision, "accepted", False))
            and getattr(decision, "overlay_state", "unknown") in {"clear", "dismissed"}
            for decision in decisions
        ),
        "author_rejection_codes": rejection_counts,
        "author_staging_audit_removals": audit_entries,
    }


def _count_values(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items()))
