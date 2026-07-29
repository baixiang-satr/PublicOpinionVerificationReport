"""Generate a diff report between two crawl job directories.

Compares aggregate quality summaries and per-record checkpoint data so a
field-level regression always names the evidence ID and the reason.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


_FIELD_TOTALS = (
    ("records_with_title", "有标题"),
    ("records_with_nickname", "有昵称/店铺名"),
    ("records_with_published_at", "有发布时间"),
    ("records_with_content", "有信息内容"),
    ("records_with_page_screenshot", "有正文截图"),
    ("records_with_author_screenshot", "有个人主页截图"),
)

_BASELINES = {
    # 下一阶段验收基线（next_stage_development_plan 7.2）
    "records_with_title": 39,
    "records_with_nickname": 19,
    "records_with_published_at": 26,
    "records_with_content": 39,
}


def _load_summary(job_dir: Path) -> dict:
    return json.loads((job_dir / "quality_summary.json").read_text(encoding="utf-8"))


def _load_records(job_dir: Path) -> dict[int, dict]:
    path = job_dir / "job_checkpoint.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = {}
    for value in payload.get("records") or ():
        evidence_id = int(value.get("task", {}).get("evidence_id") or 0)
        if evidence_id:
            records[evidence_id] = value
    return records


def _record_fields(record: dict) -> dict[str, object]:
    page = record.get("page") or {}
    return {
        "status": record.get("status"),
        "has_title": bool(page.get("title")),
        "has_author": bool(page.get("author_name") or page.get("store_name")),
        "has_published": bool(page.get("published_at")),
        "has_content": bool(page.get("content_text") or page.get("content_summary")),
        "content_chars": int(page.get("original_content_chars") or 0),
        "author_screenshot": bool((record.get("assets") or {}).get("author_screenshot")),
        "page_screenshot": bool((record.get("assets") or {}).get("page_screenshot")),
        "error_codes": [error.get("code") for error in record.get("errors") or ()],
    }


def build_diff_report(baseline_dir: Path, current_dir: Path) -> str:
    baseline = _load_summary(baseline_dir)
    current = _load_summary(current_dir)
    baseline_records = _load_records(baseline_dir)
    current_records = _load_records(current_dir)

    lines = [
        f"# 差异报告：{current['job_id']} vs {baseline['job_id']}",
        "",
        f"- 基线：`{baseline_dir.name}`（{baseline.get('label', '')}）",
        f"- 本轮：`{current_dir.name}`（{current.get('label', '')}）",
        "",
        "## 1. 总量与字段基线",
        "",
        "| 指标 | 基线 | 本轮 | 变化 | 验收基线 | 结论 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    totals_b = baseline["totals"]
    totals_c = current["totals"]
    for key, label in _FIELD_TOTALS:
        before = int(totals_b.get(key, 0))
        after = int(totals_c.get(key, 0))
        floor = _BASELINES.get(key)
        verdict = ""
        if floor is not None:
            verdict = "✅ 达标" if after >= floor else "❌ 低于不可回退基线"
        lines.append(
            f"| {label} | {before} | {after} | {after - before:+d} | {floor or '—'} | {verdict} |"
        )
    lines.extend(
        [
            "",
            "## 2. 状态分布",
            "",
            "| 状态 | 基线 | 本轮 |",
            "|---|---:|---:|",
        ]
    )
    statuses = sorted(
        set(baseline.get("status_counts", {})) | set(current.get("status_counts", {}))
    )
    for status in statuses:
        lines.append(
            f"| `{status}` | {baseline.get('status_counts', {}).get(status, 0)} "
            f"| {current.get('status_counts', {}).get(status, 0)} |"
        )

    lines.extend(["", "## 3. 记录级字段变化", ""])
    regressions: list[str] = []
    improvements: list[str] = []
    for evidence_id in sorted(set(baseline_records) | set(current_records)):
        before = baseline_records.get(evidence_id)
        after = current_records.get(evidence_id)
        if before is None or after is None:
            continue
        before_f = _record_fields(before)
        after_f = _record_fields(after)
        for field, label in (
            ("has_title", "标题"),
            ("has_author", "昵称/店铺"),
            ("has_published", "发布时间"),
            ("has_content", "信息内容"),
        ):
            if before_f[field] and not after_f[field]:
                codes = ";".join(after_f["error_codes"]) or "无错误码"
                regressions.append(
                    f"- 证据 {evidence_id:03d}：{label} 丢失（状态 {after_f['status']}，{codes}）"
                )
            elif not before_f[field] and after_f[field]:
                improvements.append(f"- 证据 {evidence_id:03d}：新增 {label}")
        if before_f["author_screenshot"] and not after_f["author_screenshot"]:
            codes = ";".join(after_f["error_codes"]) or "无错误码"
            regressions.append(
                f"- 证据 {evidence_id:03d}：个人主页截图丢失（{codes}）"
            )
        elif not before_f["author_screenshot"] and after_f["author_screenshot"]:
            improvements.append(f"- 证据 {evidence_id:03d}：新增个人主页截图")
    if regressions:
        lines.extend(["### 回退（必须逐条说明）", "", *regressions, ""])
    else:
        lines.extend(["### 回退", "", "无字段回退。", ""])
    if improvements:
        lines.extend(["### 新增", "", *improvements, ""])

    lines.extend(["## 4. 错误码变化", "", "| 错误码 | 基线 | 本轮 |", "|---|---:|---:|"])
    error_codes = sorted(
        set(baseline.get("error_counts", {})) | set(current.get("error_counts", {}))
    )
    for code in error_codes:
        lines.append(
            f"| `{code}` | {baseline.get('error_counts', {}).get(code, 0)} "
            f"| {current.get('error_counts', {}).get(code, 0)} |"
        )
    author_b = (baseline.get("author_evidence") or {})
    author_c = (current.get("author_evidence") or {})
    if author_b or author_c:
        lines.extend(
            [
                "",
                "## 5. 个人主页证据验收",
                "",
                "| 指标 | 基线 | 本轮 |",
                "|---|---:|---:|",
            ]
        )
        for key in (
            "author_url_candidates",
            "author_pages_opened",
            "author_identity_validated",
            "author_clean_screenshots",
        ):
            lines.append(
                f"| {key} | {author_b.get(key, 0)} | {author_c.get(key, 0)} |"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path, help="基线任务目录")
    parser.add_argument("current", type=Path, help="本轮任务目录")
    parser.add_argument("--output", type=Path, default=None, help="差异报告输出路径")
    args = parser.parse_args()
    report = build_diff_report(args.baseline, args.current)
    destination = args.output or (args.current / "diff_report.md")
    destination.write_text(report, encoding="utf-8")
    print(f"diff_report={destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
