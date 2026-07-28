import asyncio
from pathlib import Path

import pytest

from src.domain.models import AssetSet, PageData, RecordResult, UrlTask
from src.screenshot.author_asset import capture_author_home_asset


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
