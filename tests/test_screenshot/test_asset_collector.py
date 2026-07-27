from pathlib import Path

import pytest

from src.config.settings import TaskConfig
from src.crawler.fetcher import AssetFetchError
from src.screenshot.asset_collector import AssetCollector


class StubFetcher:
    def __init__(self, failed_url: str) -> None:
        self.failed_url = failed_url
        self.calls: list[tuple[str, int]] = []

    async def fetch(
        self,
        page: object,
        url: str,
        output_dir: Path,
        evidence_id: int,
        image_index: int,
        **_options: object,
    ) -> Path:
        del page
        self.calls.append((url, image_index))
        if url == self.failed_url:
            raise AssetFetchError("IMAGE_HTTP_ERROR", "图片返回 HTTP 404")
        path = output_dir / f"{evidence_id:03d}_{image_index:02d}.png"
        path.write_bytes(b"png")
        return path


@pytest.mark.asyncio
async def test_collector_deduplicates_limits_and_returns_only_real_files(tmp_path: Path) -> None:
    failed_url = "https://example.test/missing.png?secret=1"
    fetcher = StubFetcher(failed_url)
    collector = AssetCollector(
        TaskConfig(max_images_per_record=3),
        fetcher=fetcher,
    )

    result = await collector.collect(
        object(),
        [
            "https://example.test/one.png",
            failed_url,
            "https://example.test/one.png",
            "https://example.test/two.png",
            "https://example.test/not-attempted.png",
        ],
        evidence_id=1,
        output_dir=tmp_path,
    )

    assert [path.name for path in result.files] == ["001_01.png", "001_02.png"]
    assert all(path.is_file() for path in result.files)
    assert [url for url, _index in fetcher.calls] == [
        "https://example.test/one.png",
        failed_url,
        "https://example.test/two.png",
    ]
    assert [index for _url, index in fetcher.calls] == [1, 2, 2]
    assert [error.code for error in result.errors] == ["IMAGE_HTTP_ERROR"]
    assert "secret" not in result.errors[0].message
