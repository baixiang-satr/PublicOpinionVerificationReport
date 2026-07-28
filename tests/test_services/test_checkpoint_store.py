from datetime import datetime
from pathlib import Path

import pytest

from src.domain.models import (
    AssetSet,
    ContentKind,
    ExtractionSource,
    OcrStatus,
    PageData,
    RecordResult,
    RecordStatus,
    RouteDecision,
    TaskError,
    UrlTask,
)
from src.services.checkpoint_store import CheckpointStore


def test_checkpoint_round_trips_record_state_atomically(tmp_path: Path) -> None:
    task = UrlTask(1, "https://example.test/post/1", "https://example.test/post/1")
    page = PageData(
        title="标题",
        content_text="完整正文",
        author_name="昵称",
        published_at=datetime(2026, 7, 28, 12, 30),
        field_sources={"title": ExtractionSource.EMBEDDED_JSON},
        field_confidences={"title": 0.94},
        ocr_status=OcrStatus.SUCCESS,
        content_kind=ContentKind.MIXED_TEXT_AND_IMAGE,
    )
    record = RecordResult(
        task,
        RecordStatus.ASSETS_READY,
        page=page,
        route=RouteDecision("微博博客", "新浪_新浪微博_博客贴吧", "正文"),
        assets=AssetSet(
            page_screenshot=tmp_path / "001.jpg",
            author_screenshot=tmp_path / "001主页.jpg",
        ),
        errors=[TaskError("ocr", "OCR_FAILED", "example", True)],
        attempt_count=2,
    )
    path = tmp_path / "job_checkpoint.json"
    store = CheckpointStore(path, job_id="job-1", tasks=(task,))

    store.update(record)
    loaded = CheckpointStore.load(path, expected_tasks=(task,))

    assert loaded.job_id == "job-1"
    assert len(loaded.records) == 1
    restored = loaded.records[0]
    assert restored.page.content_text == "完整正文"
    assert restored.page.field_sources["title"] == ExtractionSource.EMBEDDED_JSON
    assert restored.page.ocr_status == OcrStatus.SUCCESS
    assert restored.assets.author_screenshot == tmp_path / "001主页.jpg"
    assert restored.errors[0].retryable
    assert not path.with_suffix(".json.tmp").exists()


def test_checkpoint_rejects_a_different_input_set(tmp_path: Path) -> None:
    original = UrlTask(1, "https://example.test/1", "https://example.test/1")
    changed = UrlTask(1, "https://example.test/2", "https://example.test/2")
    path = tmp_path / "job_checkpoint.json"
    CheckpointStore(path, job_id="job-1", tasks=(original,)).save()

    with pytest.raises(ValueError, match="does not match"):
        CheckpointStore.load(path, expected_tasks=(changed,))
