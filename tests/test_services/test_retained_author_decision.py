from pathlib import Path
import shutil

from src.domain.models import AssetSet, RecordResult, RecordStatus, UrlTask
from src.screenshot.author_evidence import AuthorEvidenceDecision, write_decision
from src.services import recovery_mirror
from src.services.checkpoint_store import CheckpointStore
from src.services.models import JobRequest
from src.services.retained_records import copy_retained_records, prepare_retained_records


def test_reexport_copies_archived_author_decision_with_image(tmp_path: Path) -> None:
    source_job = tmp_path / "source-job"
    source_template = source_job / "staging" / "template"
    source_template.mkdir(parents=True)
    author = source_template / "002主页.jpg"
    author.write_bytes(b"image")
    decisions = source_job / "author_decisions"
    decisions.mkdir()
    write_decision(
        AuthorEvidenceDecision(
            candidate_url="https://space.bilibili.com/2",
            evidence_id=2,
            accepted=True,
            identity_state="verified",
        ),
        decisions,
    )
    record = RecordResult(
        task=UrlTask(2, "https://www.bilibili.com/video/2", "https://www.bilibili.com/video/2"),
        status=RecordStatus.EXPORTED,
        assets=AssetSet(author_screenshot=author),
    )
    destination = tmp_path / "new-job" / "staging" / "template"
    destination.mkdir(parents=True)

    copied = copy_retained_records((record,), destination)

    assert copied[0].assets.author_screenshot == destination / "002主页.jpg"
    assert (destination / "002主页.decision.json").is_file()


def test_reexport_retry_overrides_same_named_checkpoint_asset(tmp_path: Path) -> None:
    task = UrlTask(3, "https://www.douyin.com/video/3", "https://www.douyin.com/video/3")
    old_asset = tmp_path / "old" / "003.jpg"
    new_asset = tmp_path / "new" / "003.jpg"
    old_asset.parent.mkdir()
    new_asset.parent.mkdir()
    old_asset.write_bytes(b"old")
    new_asset.write_bytes(b"new")
    old = RecordResult(
        task=task,
        status=RecordStatus.EXPORTED,
        assets=AssetSet(page_screenshot=old_asset),
    )
    replacement = RecordResult(
        task=task,
        status=RecordStatus.ASSETS_READY,
        assets=AssetSet(page_screenshot=new_asset),
    )
    checkpoint = CheckpointStore(
        tmp_path / "source" / "job_checkpoint.json",
        job_id="source",
        tasks=(task,),
    )
    checkpoint.update(old)
    destination = tmp_path / "result" / "staging" / "template"
    destination.mkdir(parents=True)

    copied = prepare_retained_records(
        JobRequest(
            tasks=(task,),
            retained_records=(replacement,),
            resume_checkpoint_path=checkpoint.path,
            reexport_only=True,
        ),
        (task,),
        destination,
    )

    assert len(copied) == 1
    assert copied[0].assets.page_screenshot == destination / "003.jpg"
    assert (destination / "003.jpg").read_bytes() == b"new"


def test_reexport_finds_author_decision_by_image_name_when_ids_renumbered(tmp_path: Path) -> None:
    """zip 导入重排证据号后，sidecar 仍按图片文件名找回。"""

    source_job = tmp_path / "imported-job"
    source_template = source_job / "staging" / "template"
    source_template.mkdir(parents=True)
    author = source_template / "007主页.jpg"
    author.write_bytes(b"image")
    decisions = source_job / "author_decisions"
    decisions.mkdir()
    write_decision(
        AuthorEvidenceDecision(
            candidate_url="https://example.test/u/7",
            evidence_id=7,
            accepted=True,
            identity_state="verified",
        ),
        decisions,
    )
    # 导入重排：记录证据号 2，但图片名保持 007主页.jpg（审计正则只认图名）。
    record = RecordResult(
        task=UrlTask(2, "https://example.test/p/7", "https://example.test/p/7"),
        status=RecordStatus.EXPORTED,
        assets=AssetSet(author_screenshot=author),
    )
    destination = tmp_path / "new-job" / "staging" / "template"
    destination.mkdir(parents=True)

    copied = copy_retained_records((record,), destination)

    assert copied[0].assets.author_screenshot == destination / "007主页.jpg"
    assert (destination / "007主页.decision.json").is_file()
    assert not (destination / "002主页.decision.json").exists()


def test_reexport_recovers_author_decision_from_recovery_mirror(tmp_path: Path) -> None:
    """任务目录被外部清理后，主页截图与审计决策都可从恢复镜像救回。"""

    recovery_mirror.enable(tmp_path / "recovery")
    try:
        source_job = tmp_path / "ghost-job"
        source_template = source_job / "staging" / "template"
        source_template.mkdir(parents=True)
        author = source_template / "003主页.jpg"
        author.write_bytes(b"image")
        write_decision(
            AuthorEvidenceDecision(
                candidate_url="https://example.test/u/3",
                evidence_id=3,
                accepted=True,
                identity_state="verified",
            ),
            source_template,
        )
        record = RecordResult(
            task=UrlTask(3, "https://example.test/p/3", "https://example.test/p/3"),
            status=RecordStatus.EXPORTED,
            assets=AssetSet(author_screenshot=author),
        )
        recovery_mirror.mirror_record_assets("ghost-job", source_job, record)
        shutil.rmtree(source_job)  # 模拟外部清理

        destination = tmp_path / "new-job" / "staging" / "template"
        destination.mkdir(parents=True)
        copied = copy_retained_records((record,), destination)

        assert copied[0].assets.author_screenshot == destination / "003主页.jpg"
        assert (destination / "003主页.decision.json").is_file()
    finally:
        recovery_mirror.disable()
