import asyncio
from pathlib import Path

import pytest

from src.domain.models import (
    AssetSet,
    ExtractionSource,
    PageData,
    RecordResult,
    UrlTask,
)
from src.screenshot.author_asset import _backfill_author_id, capture_author_home_asset


class UnexpectedShooter:
    async def capture(self, *_args: object) -> Path:
        raise AssertionError("direct profile input should reuse the primary screenshot")


@pytest.mark.asyncio
async def test_direct_profile_page_reuses_primary_evidence_as_home_attachment(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "054.jpg"
    primary.write_bytes(b"profile screenshot")
    profile_url = (
        "https://h5-ol.sns.sohu.com/hy-super-h5/share/profile/abc"
        "?sf_hy=wechat"
    )
    result = RecordResult(
        task=UrlTask(54, profile_url, profile_url),
        page=PageData(final_url=profile_url, author_url=profile_url),
        assets=AssetSet(page_screenshot=primary),
    )

    asset, error = await capture_author_home_asset(
        UnexpectedShooter(),
        object(),
        result,
        tmp_path,
        asyncio.Event(),
    )

    assert error is None
    assert asset == tmp_path / "054主页.jpg"
    assert asset.read_bytes() == primary.read_bytes()


def _fallback_result(author_id: str, *, fallback: bool) -> RecordResult:
    page = PageData(
        final_url="https://www.douyin.com/video/123",
        author_id=author_id,
        author_id_is_fallback=fallback,
    )
    return RecordResult(
        task=UrlTask(7, "https://www.douyin.com/video/123", "https://www.douyin.com/video/123"),
        page=page,
    )


def test_backfill_author_id_replaces_nickname_fallback() -> None:
    """已核验主页上抓到的抖音号要替换昵称兜底。"""

    result = _fallback_result("昵称", fallback=True)

    _backfill_author_id(result, "real_id_123")

    assert result.page.author_id == "real_id_123"
    assert result.page.author_id_is_fallback is False
    assert result.page.field_sources["author_id"] == ExtractionSource.PLATFORM_DOM


def test_backfill_author_id_keeps_verified_existing_id() -> None:
    result = _fallback_result("already", fallback=False)

    _backfill_author_id(result, "other")

    assert result.page.author_id == "already"


def test_backfill_author_id_ignores_empty_detected() -> None:
    result = _fallback_result("昵称", fallback=True)

    _backfill_author_id(result, None)

    assert result.page.author_id == "昵称"
    assert result.page.author_id_is_fallback is True


def test_backfill_author_id_upgrade_replaces_internal_uid_with_douyin_hao() -> None:
    """个人页「抖音号」标签来源可升级 JSON 内部 uid（账号列要的是可见账号）。"""

    result = _fallback_result("7541599308718883897", fallback=False)

    _backfill_author_id(result, "83699722623", upgrade=True)

    assert result.page.author_id == "83699722623"
    assert result.page.author_id_is_fallback is False
