"""整页长图截图离线测试：平台集合、预算编码与扩窗抓取编排。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from src.config.settings import TaskConfig
from src.screenshot import long_capture
from src.screenshot.long_capture import (
    LONG_PAGE_PLATFORMS,
    LongCaptureError,
    expand_and_capture,
    is_long_page_platform,
)


# ── 平台集合 ──


@pytest.mark.parametrize(
    "key",
    (
        "wechat_official",
        "baijiahao",
        "toutiao",
        "netease_news",
        "sohu_news",
        "ifeng_news",
        "weibo",
    ),
)
def test_text_platforms_use_long_capture(key: str) -> None:
    assert is_long_page_platform(key) is True
    assert key in LONG_PAGE_PLATFORMS


@pytest.mark.parametrize(
    "key",
    (
        "douyin",
        "kuaishou",
        "bilibili",
        "xiaohongshu",
        "wechat_video",
        "tieba",
        "zhihu",
        "taobao",
        "hupu",
        "",
        None,
    ),
)
def test_video_and_other_platforms_keep_viewport_capture(key: str | None) -> None:
    assert is_long_page_platform(key) is False


# ── 预算编码 ──


def _striped_image(width: int = 800, height: int = 2_000) -> Image.Image:
    image = Image.new("RGB", (width, height), "#f4f5f6")
    for x in range(0, width, 16):
        image.paste("#2f6f9f", (x, 0, min(width, x + 8), height))
    return image


def test_budget_encode_real_jpeg_fits(tmp_path: Path) -> None:
    output = tmp_path / "001.jpg"
    image = _striped_image()
    try:
        long_capture._save_with_budget(image, output, TaskConfig())
    finally:
        image.close()
    assert output.is_file()
    assert output.stat().st_size <= 1_000_000
    with Image.open(output) as decoded:
        assert decoded.format == "JPEG"


def test_budget_ladder_walks_quality_before_scaling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[tuple[int, int]] = []

    def _fake_encode(image: Image.Image, quality: int) -> bytes:
        attempts.append((image.width, quality))
        # 400_000 基数：质量 q 的字节量 = 400_000 × width/800 × q/100。
        size = round(400_000 * (image.width / 800) * (quality / 100))
        return b"x" * size

    monkeypatch.setattr(long_capture, "_encode_jpeg", _fake_encode)
    monkeypatch.setattr(long_capture, "_SCALE_LADDER", (1.0, 0.5))
    output = tmp_path / "001.jpg"
    image = _striped_image()
    try:
        long_capture._save_with_budget(
            image,
            output,
            TaskConfig(long_screenshot_max_bytes=100_000),
        )
    finally:
        image.close()
    # 全尺寸各质量档都超预算；半宽档 2000q，q50=100_000 恰好达标。
    # （质量阶梯 82 起每次 −8：82/74/66/58/50/42，42 是 ≥40 下限的末档。）
    assert attempts[0] == (800, 82)
    assert (400, 82) in attempts
    assert attempts[-1] == (400, 50)
    assert len(output.read_bytes()) == 100_000


def test_budget_overflow_writes_smallest_encoding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_encode(image: Image.Image, quality: int) -> bytes:
        size = round(10_000_000 * (image.width / 800) * (quality / 100))
        return b"x" * size

    monkeypatch.setattr(long_capture, "_encode_jpeg", _fake_encode)
    monkeypatch.setattr(long_capture, "_SCALE_LADDER", (1.0, 0.5))
    output = tmp_path / "001.jpg"
    image = _striped_image()
    try:
        long_capture._save_with_budget(
            image,
            output,
            TaskConfig(long_screenshot_max_bytes=100_000),
        )
    finally:
        image.close()
    # 所有档位都超预算：落最小一档（半宽 × 阶梯末档 q42 = 2_100_000）。
    assert len(output.read_bytes()) == 2_100_000


# ── 扩窗抓取编排 ──


class _FakeLongPage:
    """Scriptable page double for the expand-and-capture flow."""

    def __init__(self, *, doc_height: int = 5_000, viewport: int = 900) -> None:
        self.doc_height = doc_height
        self.inner_height = viewport
        self.scroll_steps = 0
        self.topped = False
        self.fail_measure = False

    async def evaluate(self, script: str, arg: object = None) -> object:
        if "scrollTo(0, next)" in script:
            self.scroll_steps += 1
            return self.scroll_steps >= 2
        if "window.scrollTo(0, 0)" in script:
            self.topped = True
            return None
        if "document.images" in script:
            return 0
        if "scrollHeight" in script:
            if self.fail_measure:
                raise RuntimeError("js context gone")
            return self.doc_height
        if "window.innerHeight" in script:
            return self.inner_height
        return None

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


@pytest.fixture
def window_os_double(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    state: dict[str, object] = {"resized": [], "restored": [], "printed": []}

    def _rect(_hwnd: int) -> tuple[int, int, int, int]:
        return (0, 0, 1456, 988)

    def _set_geometry(
        hwnd: int,
        rect: tuple[int, int, int, int],
        new_height: int,
        *,
        page: _FakeLongPage | None = None,
    ) -> None:
        state["resized"].append(new_height)  # type: ignore[union-attr]
        if page is not None:
            page.inner_height = new_height - (988 - 900)

    monkeypatch.setattr(long_capture, "_window_rect", _rect)
    monkeypatch.setattr(long_capture, "_SCROLL_PAUSE_MS", 0)
    monkeypatch.setattr(long_capture, "_SETTLE_POLL_SECONDS", 0)
    return state


def _bind_geometry(
    monkeypatch: pytest.MonkeyPatch,
    state: dict[str, object],
    page: _FakeLongPage,
) -> None:
    def _set_geometry(
        _hwnd: int,
        rect: tuple[int, int, int, int],
        new_height: int,
    ) -> None:
        state["resized"].append(new_height)  # type: ignore[union-attr]
        page.inner_height = new_height - (988 - 900)

    def _restore(_hwnd: int, rect: tuple[int, int, int, int]) -> None:
        state["restored"].append(rect)  # type: ignore[union-attr]
        page.inner_height = 900

    monkeypatch.setattr(long_capture, "_set_window_geometry", _set_geometry)
    monkeypatch.setattr(long_capture, "_restore_window", _restore)


def _printed_frame(_hwnd: int) -> Image.Image:
    return _striped_image(1_456, 3_000)


@pytest.mark.asyncio
async def test_expand_capture_resizes_restores_and_saves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    window_os_double: dict[str, object],
) -> None:
    page = _FakeLongPage(doc_height=5_000)
    _bind_geometry(monkeypatch, window_os_double, page)
    monkeypatch.setattr(long_capture, "_print_window", _printed_frame)
    output = tmp_path / "001.jpg"

    await expand_and_capture(page, 4321, output, TaskConfig())

    # chrome = 988 − 900 = 88 → 88 + 5_000 = 5_088。
    assert window_os_double["resized"] == [5_088]
    assert window_os_double["restored"] == [(0, 0, 1456, 988)]
    assert page.inner_height == 900
    assert page.scroll_steps >= 1 and page.topped is True
    assert output.is_file() and output.stat().st_size > 0


@pytest.mark.asyncio
async def test_expand_capture_caps_document_height(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    window_os_double: dict[str, object],
) -> None:
    page = _FakeLongPage(doc_height=99_000)
    _bind_geometry(monkeypatch, window_os_double, page)
    monkeypatch.setattr(long_capture, "_print_window", _printed_frame)

    await expand_and_capture(page, 4321, tmp_path / "001.jpg", TaskConfig())

    assert window_os_double["resized"] == [88 + 20_000]


@pytest.mark.asyncio
async def test_expand_capture_restores_geometry_when_measure_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    window_os_double: dict[str, object],
) -> None:
    page = _FakeLongPage()
    page.fail_measure = True
    _bind_geometry(monkeypatch, window_os_double, page)

    with pytest.raises(LongCaptureError, match="document height"):
        await expand_and_capture(page, 4321, tmp_path / "001.jpg", TaskConfig())

    assert window_os_double["resized"] == []
    assert window_os_double["restored"] == [(0, 0, 1456, 988)]


@pytest.mark.asyncio
async def test_expand_capture_proceeds_when_viewport_never_settles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    window_os_double: dict[str, object],
) -> None:
    page = _FakeLongPage(doc_height=5_000)  # inner_height 恒 900：重排未达标

    def _restore(_hwnd: int, rect: tuple[int, int, int, int]) -> None:
        window_os_double["restored"].append(rect)  # type: ignore[union-attr]

    monkeypatch.setattr(long_capture, "_set_window_geometry", lambda *a: None)
    monkeypatch.setattr(long_capture, "_restore_window", _restore)
    monkeypatch.setattr(long_capture, "_print_window", _printed_frame)
    monkeypatch.setattr(long_capture, "_SETTLE_TIMEOUT_SECONDS", 0)

    await expand_and_capture(page, 4321, tmp_path / "001.jpg", TaskConfig())

    assert window_os_double["restored"] == [(0, 0, 1456, 988)]

