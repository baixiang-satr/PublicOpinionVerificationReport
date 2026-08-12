from pathlib import Path

import pytest
from PIL import Image

from src.config.settings import TaskConfig
from src.crawler.platform_catalog import find_platform
from src.screenshot import page_shooter as page_shooter_module
from src.screenshot.page_shooter import PageScreenshotError, PageShooter


class FakeScreenshotPage:
    def __init__(
        self,
        *,
        width: int = 1_440,
        document_width: int | None = None,
        height: int = 900,
        focus_x: int = 0,
    ) -> None:
        self.width = width
        self.document_width = document_width or width
        self.height = height
        self.focus_x = focus_x
        self.wait_scripts: list[str] = []
        self.wait_options: list[dict[str, object]] = []
        self.wait_timeouts: list[int] = []
        self.horizontal_scrolls: list[int] = []
        self.css_alignments: list[dict[str, object]] = []

    async def wait_for_function(self, script: str, **options: object) -> None:
        self.wait_scripts.append(script)
        self.wait_options.append(options)

    async def wait_for_timeout(self, milliseconds: int) -> None:
        self.wait_timeouts.append(milliseconds)

    async def evaluate(self, script: str, *args: object) -> object:
        if "documentWidth" in script and "viewportWidth" in script:
            return {
                "viewportWidth": self.width,
                "documentWidth": self.document_width,
                "height": self.height,
                "focusX": self.focus_x,
                "scrollX": 0,
                "needsHorizontalAlignment": self.focus_x > 0,
            }
        if "window.scrollTo(left, top)" in script and args:
            self.horizontal_scrolls.append(int(args[0]))
        if "data-por-capture-aligned" in script and args:
            self.css_alignments.append(dict(args[0]))
        return None


class EmptyScreenshotPage(FakeScreenshotPage):
    async def wait_for_function(self, script: str, **_options: object) -> None:
        self.wait_scripts.append(script)
        raise TimeoutError("content did not render")

    async def evaluate(self, script: str, *args: object) -> object:
        if "const visibleText" in script:
            return False
        return await super().evaluate(script, *args)


class UnmeasurablePage(FakeScreenshotPage):
    """Geometry scripts return nothing: alignment can never succeed."""

    async def evaluate(self, script: str, *args: object) -> object:
        return None


def _striped_window(width: int = 1_440, height: int = 1_020) -> Image.Image:
    image = Image.new("RGB", (width, height), "#f4f5f6")
    for x in range(0, width, 32):
        image.paste("#2f6f9f", (x, 0, min(width, x + 16), height))
    return image


def _save_like_window_capture(output_path: Path, config: TaskConfig) -> None:
    image = _striped_window()
    try:
        if config.screenshot_format == "jpeg":
            image.convert("RGB").save(
                str(output_path),
                format="JPEG",
                quality=config.screenshot_jpeg_quality,
            )
        else:
            image.save(str(output_path), format="PNG")
    finally:
        image.close()


@pytest.fixture
def window_shot(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the OS-level window capture with a deterministic striped image."""

    calls: list[str] = []

    async def _capture(page: object, output_path: Path, config: TaskConfig) -> None:
        calls.append("shot")
        _save_like_window_capture(output_path, config)

    monkeypatch.setattr(page_shooter_module, "capture_browser_window", _capture)
    return calls


@pytest.mark.asyncio
async def test_capture_grabs_browser_window_with_address_bar(
    tmp_path: Path,
    window_shot: list[str],
) -> None:
    page = FakeScreenshotPage()
    config = TaskConfig(screenshot_format="jpeg", screenshot_jpeg_quality=90)

    path = await PageShooter(config).capture(page, 1, tmp_path)

    assert path.name == "001.jpg"
    assert window_shot == ["shot"]
    with Image.open(path) as image:
        assert image.format == "JPEG"
        assert image.size == (1_440, 1_020)


@pytest.mark.asyncio
async def test_capture_saves_png_when_configured(
    tmp_path: Path,
    window_shot: list[str],
) -> None:
    page = FakeScreenshotPage()

    path = await PageShooter(TaskConfig(screenshot_format="png")).capture(
        page,
        2,
        tmp_path,
    )

    assert path.name == "002.png"
    assert path.read_bytes().startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_normal_page_waits_for_platform_content(
    tmp_path: Path,
    window_shot: list[str],
) -> None:
    page = FakeScreenshotPage(height=2_000)
    definition = find_platform("https://item.jd.com/100.html")
    assert definition is not None

    await PageShooter(TaskConfig(screenshot_format="jpeg")).capture(
        page,
        2,
        tmp_path,
        definition=definition,
    )

    assert window_shot == ["shot"]
    assert any(".sku-name" in script for script in page.wait_scripts)
    assert any(
        int(options.get("timeout", 0)) >= 8_000
        for options in page.wait_options
    )
    assert any(
        "video.readyState >= 2" in script and "video.poster" not in script
        for script in page.wait_scripts
    )
    assert any(
        int(options.get("timeout", 0)) >= 6_000
        for options in page.wait_options
    )
    assert any(milliseconds >= 500 for milliseconds in page.wait_timeouts)


@pytest.mark.asyncio
async def test_douyin_video_is_aligned_before_window_capture(
    tmp_path: Path,
    window_shot: list[str],
) -> None:
    page = FakeScreenshotPage(
        document_width=4_000,
        height=1_100,
        focus_x=1_180,
    )
    page.url = "https://www.douyin.com/video/7667987870472788815"
    definition = find_platform(page.url)
    assert definition is not None

    await PageShooter(TaskConfig(screenshot_format="jpeg")).capture(
        page,
        2,
        tmp_path,
        definition=definition,
    )

    assert page.horizontal_scrolls == [1_180]
    assert window_shot == ["shot"]


@pytest.mark.asyncio
async def test_horizontal_overflow_is_aligned_before_capture(
    tmp_path: Path,
    window_shot: list[str],
) -> None:
    page = FakeScreenshotPage(
        width=1_440,
        document_width=4_000,
        height=1_100,
        focus_x=1_180,
    )

    await PageShooter(TaskConfig()).capture(page, 3, tmp_path)

    assert page.horizontal_scrolls == [1_180]
    assert window_shot == ["shot"]


@pytest.mark.asyncio
async def test_css_offset_does_not_mutate_target_site_transforms(
    tmp_path: Path,
    window_shot: list[str],
) -> None:
    page = FakeScreenshotPage(width=1_440, document_width=1_440, focus_x=0)

    async def dimensions(script: str, *args: object) -> object:
        if "documentWidth" in script and "viewportWidth" in script:
            return {
                "viewportWidth": 1_440,
                "documentWidth": 1_440,
                "height": 1_100,
                "focusX": 0,
                "scrollX": 0,
                "needsHorizontalAlignment": True,
                "focusSelector": "main",
                "focusIndex": 0,
                "desiredLeft": 96,
            }
        return await FakeScreenshotPage.evaluate(page, script, *args)

    page.evaluate = dimensions  # type: ignore[method-assign]
    await PageShooter(TaskConfig()).capture(page, 4, tmp_path)

    assert page.css_alignments == []


@pytest.mark.asyncio
async def test_unframed_content_raises_when_alignment_required(
    tmp_path: Path,
    window_shot: list[str],
) -> None:
    page = UnmeasurablePage()

    with pytest.raises(PageScreenshotError, match="could not be framed"):
        await PageShooter(TaskConfig()).capture_named(
            page,
            "005",
            tmp_path,
            focus_selectors=("main",),
        )

    assert window_shot == []
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_unframed_content_falls_back_to_viewport_when_allowed(
    tmp_path: Path,
    window_shot: list[str],
) -> None:
    page = UnmeasurablePage()

    path = await PageShooter(TaskConfig()).capture_named(
        page,
        "006",
        tmp_path,
        focus_selectors=("main",),
        require_alignment=False,
    )

    assert path.name == "006.jpg"
    assert window_shot == ["shot"]


@pytest.mark.asyncio
async def test_screenshot_rejects_page_without_visible_content(
    tmp_path: Path,
    window_shot: list[str],
) -> None:
    page = EmptyScreenshotPage()

    with pytest.raises(PageScreenshotError, match="did not become visibly rendered"):
        await PageShooter(TaskConfig()).capture(page, 7, tmp_path)

    assert window_shot == []
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_screenshot_rejects_near_uniform_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _blank(page: object, output_path: Path, config: TaskConfig) -> None:
        image = Image.new("RGB", (1_440, 1_020), "#f4f5f6")
        try:
            image.convert("RGB").save(str(output_path), format="JPEG", quality=90)
        finally:
            image.close()

    monkeypatch.setattr(page_shooter_module, "capture_browser_window", _blank)
    page = FakeScreenshotPage()

    with pytest.raises(PageScreenshotError, match="blank or near-uniform"):
        await PageShooter(TaskConfig()).capture(page, 8, tmp_path)

    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_capture_failure_leaves_no_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(page: object, output_path: Path, config: TaskConfig) -> None:
        raise RuntimeError("grab failed")

    monkeypatch.setattr(page_shooter_module, "capture_browser_window", _boom)
    page = FakeScreenshotPage()

    with pytest.raises(PageScreenshotError, match="Unable to capture screenshot"):
        await PageShooter(TaskConfig()).capture(page, 9, tmp_path)

    assert not list(tmp_path.iterdir())
