"""apply_edit 前置校验测试：非法编辑当场拒绝，合法编辑保证导出生效。

离线（tmp 目录、假许可证、无浏览器/外网/Cookie）。校验与导出共用同一
解析器/允许值（src/services/manual_edit.py），这里验证桥接层行为。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.config.settings import AppConfig, TaskConfig, TemplateConfig
from src.domain.models import (
    RecordResult,
    RecordStatus,
    RouteDecision,
    UrlTask,
)
from src.license.models import LicenseInfo, LicenseStatus
from src.services.checkpoint_store import CheckpointStore
import src.webui.bridge as bridge_module
from src.webui.bridge import WebUIBridge
from src.webui.runner import EventSink


class _AlwaysValidLicense:
    """测试替身：永远视为已激活（本模块不测试许可证逻辑本身）。"""

    def status(self) -> LicenseInfo:
        return LicenseInfo(
            activated=True,
            status=LicenseStatus.VALID,
            message="",
            machine_code="TEST",
        )


@pytest.fixture(autouse=True)
def _always_licensed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bridge_module, "default_license_manager", _AlwaysValidLicense)


def _record(eid: int, url: str, sheet: str, status: RecordStatus) -> RecordResult:
    return RecordResult(
        task=UrlTask(eid, url, url),
        status=status,
        route=RouteDecision(sheet_name=sheet, platform_value="", text_type="正文"),
    )


def _bridge_with_job(tmp_path: Path) -> WebUIBridge:
    records = [_record(1, "https://example.com/a", "微博博客", RecordStatus.NEEDS_REVIEW)]
    job_dir = tmp_path / "output" / "job-test"
    job_dir.mkdir(parents=True)
    tasks = tuple(record.task for record in records)
    store = CheckpointStore(job_dir / "job_checkpoint.json", job_id="job-test", tasks=tasks)
    store.update_many(records)
    store.save()
    config = AppConfig(
        template=TemplateConfig(output_dir=tmp_path / "output"),
        task=TaskConfig(auth_store_dir=tmp_path / "auth"),
    )
    bridge = WebUIBridge(config, EventSink())
    ok, message = bridge.jobs.open_session(job_dir)
    assert ok, message
    return bridge


def test_apply_edit_rejects_unparsable_published_at(tmp_path: Path) -> None:
    """非法发布时间当场拒绝（此前保存成功但导出静默保留原值）。"""

    bridge = _bridge_with_job(tmp_path)

    result = bridge.apply_edit(1, "published_at", "2026-13-45")

    assert result["ok"] is False
    assert "发布时间" in result["message"]
    override = bridge.jobs.session.get_override(1)
    assert override is None or "published_at" not in override.values


def test_apply_edit_rejects_text_type_outside_sheet_values(tmp_path: Path) -> None:
    bridge = _bridge_with_job(tmp_path)

    result = bridge.apply_edit(1, "text_type", "不存在类型")

    assert result["ok"] is False
    assert "正文" in result["message"]  # 错误消息列出允许值


def test_apply_edit_accepts_chinese_date(tmp_path: Path) -> None:
    bridge = _bridge_with_job(tmp_path)

    result = bridge.apply_edit(1, "published_at", "2026年9月1日")

    assert result["ok"] is True


def test_apply_edit_empty_value_is_explicit_clear(tmp_path: Path) -> None:
    """清空单元格保存为空串人工值（导出空单元格，不回落爬取值）。"""

    bridge = _bridge_with_job(tmp_path)
    assert bridge.apply_edit(1, "author_name", "小明")["ok"] is True

    result = bridge.apply_edit(1, "author_name", "")

    assert result["ok"] is True
    override = bridge.jobs.session.get_override(1)
    assert override is not None
    assert override.values["author_name"] == ""


def test_apply_edit_unknown_record_rejected(tmp_path: Path) -> None:
    bridge = _bridge_with_job(tmp_path)

    assert bridge.apply_edit(999, "author_name", "x")["ok"] is False
