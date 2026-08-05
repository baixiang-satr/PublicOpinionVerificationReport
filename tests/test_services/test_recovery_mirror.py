"""恢复镜像（recovery_mirror）与断点重建（ensure_checkpoint）测试。"""

from pathlib import Path

import pytest

from src.domain.models import (
    AssetSet,
    PageData,
    RecordResult,
    RecordStatus,
    RouteDecision,
    UrlTask,
)
from src.services import job_records, recovery_mirror
from src.services.checkpoint_store import CheckpointStore
from src.services.override_store import ManualOverrideStore
from src.services.retained_records import copy_retained_records
from src.services.review_session import ReviewSession


@pytest.fixture(autouse=True)
def _mirror_root(tmp_path: Path):
    recovery_mirror.enable(tmp_path / "recovery")
    yield
    recovery_mirror.disable()


def _record(evidence_id: int = 1) -> RecordResult:
    return RecordResult(
        UrlTask(evidence_id, f"https://example.test/p/{evidence_id}", f"https://example.test/p/{evidence_id}"),
        RecordStatus.EXPORTED,
        page=PageData(title="标题", content_text="正文"),
        route=RouteDecision("微博博客", "新浪_新浪微博_博客贴吧", "正文"),
    )


def test_mirror_disabled_by_default(tmp_path: Path) -> None:
    recovery_mirror.disable()
    source = tmp_path / "a.jpg"
    source.write_bytes(b"x")
    assert recovery_mirror.mirror_file("job-1", source) is None
    assert recovery_mirror.mirrored_asset("job-1", "a.jpg") is None


def test_checkpoint_update_mirrors_json_and_assets(tmp_path: Path) -> None:
    job_dir = tmp_path / "job-1"
    job_dir.mkdir()
    screenshot = job_dir / "001.jpg"
    screenshot.write_bytes(b"img")
    record = _record()
    record.assets = AssetSet(page_screenshot=screenshot)
    store = CheckpointStore(
        job_dir / "job_checkpoint.json",
        job_id="job-1",
        tasks=(record.task,),
    )

    store.update(record)

    assert recovery_mirror.mirrored_json("job-1", "job_checkpoint.json") is not None
    mirrored = recovery_mirror.mirrored_asset("job-1", "001.jpg")
    assert mirrored is not None
    assert mirrored.read_bytes() == b"img"


def test_mirror_record_assets_resolves_relative_manual_names(tmp_path: Path) -> None:
    job_dir = tmp_path / "job-2"
    manual_dir = job_dir / "manual_assets"
    manual_dir.mkdir(parents=True)
    (manual_dir / "002_author.jpg").write_bytes(b"manual")
    record = _record(2)
    record.assets = AssetSet(author_screenshot=Path("002_author.jpg"))

    recovery_mirror.mirror_record_assets("job-2", job_dir, record)

    assert recovery_mirror.mirrored_asset("job-2", "002_author.jpg") is not None


def test_ensure_checkpoint_restores_from_mirror(tmp_path: Path) -> None:
    job_dir = tmp_path / "job-3"
    job_dir.mkdir()
    record = _record()
    store = CheckpointStore(
        job_dir / "job_checkpoint.json",
        job_id="job-3",
        tasks=(record.task,),
    )
    store.update(record)
    original_bytes = (job_dir / "job_checkpoint.json").read_bytes()
    (job_dir / "job_checkpoint.json").unlink()

    restored = job_records.ensure_checkpoint(job_dir, [record])

    assert restored.is_file()
    assert restored.read_bytes() == original_bytes
    loaded = CheckpointStore.load(restored)
    assert loaded.job_id == "job-3"


def test_ensure_checkpoint_rebuilds_from_records_without_mirror(tmp_path: Path) -> None:
    job_dir = tmp_path / "job-4"
    job_dir.mkdir()

    path = job_records.ensure_checkpoint(job_dir, [_record()])

    assert path.is_file()
    loaded = CheckpointStore.load(path)
    assert loaded.job_id == "job-4"
    assert len(loaded.records) == 1


def test_review_session_slot_path_falls_back_to_mirror(tmp_path: Path) -> None:
    job_dir = tmp_path / "job-5"
    staging = job_dir / "staging" / "template"
    staging.mkdir(parents=True)
    crawled = staging / "001.jpg"
    crawled.write_bytes(b"img")
    record = _record()
    record.assets = AssetSet(page_screenshot=crawled)
    recovery_mirror.mirror_record_assets("job-5", job_dir, record)
    crawled.unlink()  # 外部清理

    session = ReviewSession.from_records(job_dir, [record])

    preview = session.content_screenshot_path(record)
    assert preview is not None
    assert preview.read_bytes() == b"img"


def test_copy_retained_records_uses_mirror_when_source_deleted(tmp_path: Path) -> None:
    job_dir = tmp_path / "job-6"
    staging = job_dir / "staging" / "template"
    staging.mkdir(parents=True)
    crawled = staging / "001.jpg"
    crawled.write_bytes(b"img")
    record = _record()
    record.assets = AssetSet(page_screenshot=crawled)
    recovery_mirror.mirror_record_assets("job-6", job_dir, record)
    crawled.unlink()
    target = tmp_path / "new-job" / "staging" / "template"
    target.mkdir(parents=True)

    copied = copy_retained_records((record,), target)

    assert len(copied) == 1
    assert copied[0].assets.page_screenshot is not None
    assert Path(copied[0].assets.page_screenshot).read_bytes() == b"img"


def test_override_store_restores_from_mirror(tmp_path: Path) -> None:
    job_dir = tmp_path / "job-7"
    job_dir.mkdir()
    store = ManualOverrideStore(job_dir)
    store.set_field(1, "content", "人工正文")
    assert (job_dir / "manual_overrides.json").is_file()
    (job_dir / "manual_overrides.json").unlink()

    reloaded = ManualOverrideStore(job_dir).load()

    override = reloaded.get(1)
    assert override is not None
    assert override.values.get("content") == "人工正文"
