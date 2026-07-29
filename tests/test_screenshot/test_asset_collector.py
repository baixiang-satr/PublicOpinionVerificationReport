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

    # Image index follows the candidate position, so failed downloads leave
    # gaps instead of shifting later file names.
    assert sorted(path.name for path in result.files) == ["001_01.png", "001_03.png"]
    assert all(path.is_file() for path in result.files)
    assert sorted(url for url, _index in fetcher.calls) == [
        failed_url,
        "https://example.test/one.png",
        "https://example.test/two.png",
    ]
    assert sorted(index for _url, index in fetcher.calls) == [1, 2, 3]
    assert [error.code for error in result.errors] == ["IMAGE_HTTP_ERROR"]
    assert "secret" not in result.errors[0].message


@pytest.mark.asyncio
async def test_failed_download_falls_back_to_element_screenshot(tmp_path: Path) -> None:
    class FallbackPage:
        async def evaluate(self, script: str, _url: str | None = None) -> bool:
            return True

        def locator(self, _selector: str) -> "FallbackLocator":
            return FallbackLocator()

    class FallbackLocator:
        @property
        def first(self) -> "FallbackLocator":
            return self

        async def screenshot(self, path: str, **_kwargs: object) -> None:
            Path(path).write_bytes(b"png")

    failed_url = "https://example.test/protected.avif"
    fetcher = StubFetcher(failed_url)
    collector = AssetCollector(TaskConfig(), fetcher=fetcher)

    result = await collector.collect(
        FallbackPage(),
        [failed_url],
        evidence_id=2,
        output_dir=tmp_path,
    )

    assert [path.name for path in result.files] == ["002_01_element.png"]
    assert result.errors == ()
