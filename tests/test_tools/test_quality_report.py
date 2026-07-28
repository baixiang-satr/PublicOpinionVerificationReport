import csv
import json
from pathlib import Path

from src.domain.models import (
    PageData,
    RecordResult,
    RecordStatus,
    TaskError,
    UrlTask,
)
from src.tools.quality_report import write_quality_artifacts


def _record(
    evidence_id: int,
    status: RecordStatus,
    *,
    partial: bool = False,
) -> RecordResult:
    errors = (
        [
            TaskError(
                "export_validation",
                "PARTIAL_FIELDS_MISSING",
                "缺少 author_id",
            )
        ]
        if partial
        else []
    )
    return RecordResult(
        task=UrlTask(
            evidence_id,
            f"https://www.zhihu.com/question/{evidence_id}",
            f"https://www.zhihu.com/question/{evidence_id}",
        ),
        status=status,
        page=PageData(
            final_url=f"https://www.zhihu.com/question/{evidence_id}",
            title="标题" if status == RecordStatus.EXPORTED else None,
            content_text="正文" if status == RecordStatus.EXPORTED else None,
        ),
        errors=errors,
    )


def test_quality_report_counts_platforms_and_writes_manual_queue(
    tmp_path: Path,
) -> None:
    records = [
        _record(1, RecordStatus.EXPORTED),
        _record(2, RecordStatus.EXPORTED, partial=True),
        _record(3, RecordStatus.NEEDS_REVIEW),
    ]
    records[2].errors.append(
        TaskError("access", "LOGIN_REQUIRED", "需要登录")
    )

    artifacts = write_quality_artifacts(
        records,
        tmp_path,
        job_id="job-1",
        label="质量测试",
    )

    summary = json.loads(artifacts.summary_path.read_text(encoding="utf-8"))
    assert summary["totals"]["input_records"] == 3
    assert summary["totals"]["successful_records"] == 2
    assert summary["totals"]["manual_entry_records"] == 2
    assert summary["platforms"][0]["total"] == 3
    assert summary["error_counts"] == {
        "LOGIN_REQUIRED": 1,
        "PARTIAL_FIELDS_MISSING": 1,
    }
    with artifacts.manual_entry_path.open(
        encoding="utf-8-sig",
        newline="",
    ) as stream:
        rows = list(csv.DictReader(stream))
    assert [row["证据编号"] for row in rows] == ["002", "003"]
    assert "管理平台登录态" in rows[1]["建议处理"]
    assert artifacts.report_path.is_file()
