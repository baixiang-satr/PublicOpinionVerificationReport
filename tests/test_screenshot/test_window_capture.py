"""window_capture：标题 token 定位窗口、标签激活兜底、前台恢复与落盘。"""

from pathlib import Path

import pytest
from PIL import Image

from src.config.settings import TaskConfig
from src.screenshot import window_capture


class FakeWindowPage:
    def __init__(self, title: str = "原页面标题") -> None:
        self._title = title
        self.titles: list[str] = []
        self.front_calls = 0

    async def title(self) -> str:
        return self._title

    async def evaluate(self, script: str, *args: object) -> object:
        if "document.title = t" in script and args:
            self.titles.append(str(args[0]))
        return None

    async def bring_to_front(self) -> None:
        self.front_calls += 1


def _frame(width: int = 800, height: int = 600) -> Image.Image:
    image = Image.new("RGB", (width, height), "#202225")
    for x in range(0, width, 40):
        image.paste("#c8c9cc", (x, 0, min(width, x + 20), height))
    return image


@pytest.fixture
def window_os(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    state: dict[str, object] = {
        "tokens": [],
        "printed": [],
        "restored": [],
    }

    def _record_find(token: str) -> int | None:
        state["tokens"].append(token)  # type: ignore[union-attr]
        return 4321

    def _record_print(hwnd: int) -> Image.Image:
        state["printed"].append(hwnd)  # type: ignore[union-attr]
        return _frame()

    monkeypatch.setattr(window_capture, "_find_window_by_title", _record_find)
    monkeypatch.setattr(window_capture, "_print_window", _record_print)
    monkeypatch.setattr(window_capture, "FIND_WINDOW_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(window_capture, "TAB_STRIP_REPAINT_SECONDS", 0)
    monkeypatch.setattr(window_capture, "_foreground_window", lambda: 999)
    monkeypatch.setattr(
        window_capture,
        "_restore_foreground",
        lambda hwnd: state["restored"].append(hwnd),  # type: ignore[union-attr]
    )
    return state


@pytest.mark.asyncio
async def test_capture_tags_window_restores_title_and_saves_jpeg(
    tmp_path: Path,
    window_os: dict[str, object],
) -> None:
    page = FakeWindowPage()
    output = tmp_path / "001.jpg"

    await window_capture.capture_browser_window(
        page,
        output,
        TaskConfig(screenshot_format="jpeg"),
    )

    assert output.read_bytes().startswith(b"\xff\xd8")  # JPEG SOI
    tokens = window_os["tokens"]
    assert tokens and str(tokens[0]).startswith("__POIR_CAPTURE_")
    assert page.titles == [str(tokens[0]), "原页面标题"]
    assert window_os["printed"] == [4321]
    assert page.front_calls == 0  # 标签已激活时不打扰前台
    assert window_os["restored"] == []


@pytest.mark.asyncio
async def test_capture_saves_png_when_configured(
    tmp_path: Path,
    window_os: dict[str, object],
) -> None:
    output = tmp_path / "002.png"

    await window_capture.capture_browser_window(
        FakeWindowPage(),
        output,
        TaskConfig(screenshot_format="png"),
    )

    assert output.read_bytes().startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_inactive_tab_is_activated_and_foreground_restored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    window_os: dict[str, object],
) -> None:
    page = FakeWindowPage()

    def _find_until_active(token: str) -> int | None:
        state_tokens = window_os["tokens"]
        state_tokens.append(token)  # type: ignore[union-attr]
        return 4321 if page.front_calls else None

    monkeypatch.setattr(window_capture, "_find_window_by_title", _find_until_active)

    await window_capture.capture_browser_window(
        page,
        tmp_path / "003.jpg",
        TaskConfig(screenshot_format="jpeg"),
    )

    assert page.front_calls == 1
    assert window_os["printed"] == [4321]
    assert window_os["restored"] == [999]
    assert page.titles[-1] == "原页面标题"


@pytest.mark.asyncio
async def test_missing_window_raises_and_restores_title(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    window_os: dict[str, object],
) -> None:
    monkeypatch.setattr(window_capture, "_find_window_by_title", lambda token: None)
    page = FakeWindowPage()

    with pytest.raises(window_capture.WindowCaptureError, match="Unable to locate"):
        await window_capture.capture_browser_window(
            page,
            tmp_path / "004.jpg",
            TaskConfig(),
        )

    assert page.front_calls == 1
    assert window_os["printed"] == []
    assert page.titles[-1] == "原页面标题"
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_print_failure_propagates_and_restores_title(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    window_os: dict[str, object],
) -> None:
    def _fail(hwnd: int) -> Image.Image:
        raise window_capture.WindowCaptureError("PrintWindow failed")

    monkeypatch.setattr(window_capture, "_print_window", _fail)
    page = FakeWindowPage()

    with pytest.raises(window_capture.WindowCaptureError, match="PrintWindow failed"):
        await window_capture.capture_browser_window(
            page,
            tmp_path / "005.jpg",
            TaskConfig(),
        )

    assert page.titles[-1] == "原页面标题"
    assert not list(tmp_path.iterdir())
