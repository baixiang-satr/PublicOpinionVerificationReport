from pathlib import Path

import pytest

from src.config.settings import TaskConfig
from src.screenshot.author_evidence import identity_verdict
from src.screenshot.author_identity import best_header_name
from src.screenshot.author_shooter import (
    AuthorShooter,
    AuthorScreenshotError,
)
from src.screenshot.page_shooter import PageScreenshotError


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status


class FakeAuthorPage:
    def __init__(self, status: int) -> None:
        self.status = status
        self.closed = False
        self.goto_url: str | None = None

    async def goto(self, url: str, **_options: object) -> FakeResponse:
        self.goto_url = url
        return FakeResponse(self.status)

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


class FakeBodyLocator:
    async def inner_text(self, **_options: object) -> str:
        return "请先登录后查看该作者主页"


class RestrictedAuthorPage(FakeAuthorPage):
    async def title(self) -> str:
        return "登录"

    def locator(self, _selector: str) -> FakeBodyLocator:
        return FakeBodyLocator()


class ErrorBodyLocator:
    async def inner_text(self, **_options: object) -> str:
        return "参数错误"


class ErrorAuthorPage(FakeAuthorPage):
    async def title(self) -> str:
        return "提示"

    def locator(self, _selector: str) -> ErrorBodyLocator:
        return ErrorBodyLocator()


class SparseBodyLocator:
    async def inner_text(self, **_options: object) -> str:
        return "页面正在加载，请稍候。" * 4


class SparseAuthorPage(FakeAuthorPage):
    async def title(self) -> str:
        return "用户主页"

    def locator(self, _selector: str) -> SparseBodyLocator:
        return SparseBodyLocator()


class FakeContext:
    def __init__(self, author_page: FakeAuthorPage) -> None:
        self.author_page = author_page

    async def new_page(self) -> FakeAuthorPage:
        return self.author_page


class FakeSourcePage:
    def __init__(self, author_page: FakeAuthorPage) -> None:
        self.context = FakeContext(author_page)


class StubPageShooter:
    async def capture_named(
        self,
        _page: object,
        file_stem: str,
        output_dir: Path,
        _cancel_event: object,
        **_options: object,
    ) -> Path:
        path = output_dir / f"{file_stem}.png"
        path.write_bytes(b"png")
        return path


class AlignmentFailingShooter:
    """聚焦对齐恒失败、仅 require_alignment=False 可拍的桩。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def capture_named(
        self,
        _page: object,
        file_stem: str,
        output_dir: Path,
        _cancel_event: object,
        **options: object,
    ) -> Path:
        self.calls.append(options)
        if options.get("require_alignment", True):
            raise PageScreenshotError(
                "Target content could not be framed completely in the viewport."
            )
        path = output_dir / f"{file_stem}.png"
        path.write_bytes(b"png")
        return path


class SignalsAuthorPage(FakeAuthorPage):
    """提供主页信号的假页面：走身份核验后的正常截图分支。"""

    async def evaluate(self, script: str, *_args: object):
        if "headerNames" in script or "headerName" in script:
            return {
                "headerName": "作者",
                "headerNames": ["作者"],
                "headerId": "",
                "headerIdSource": "",
                "title": "作者的主页",
                "body": "作者的主页内容",
                "hasProfileSurface": True,
            }
        return None


@pytest.mark.asyncio
async def test_author_shooter_falls_back_to_viewport_after_alignment_failure(
    tmp_path: Path,
) -> None:
    author_page = SignalsAuthorPage(200)
    stub = AlignmentFailingShooter()
    shooter = AuthorShooter(
        TaskConfig(page_stabilize_milliseconds=0, screenshot_format="png"),
        shooter=stub,
    )

    path = await shooter.capture(
        FakeSourcePage(author_page),
        "https://example.test/author/42",
        5,
        tmp_path,
        expected_author_name="作者",
    )

    assert path.name == "005主页.png"
    assert len(stub.calls) == 2
    assert stub.calls[0].get("focus_selectors")
    assert stub.calls[1].get("require_alignment") is False


@pytest.mark.asyncio
async def test_author_shooter_reuses_context_and_closes_page(tmp_path: Path) -> None:
    author_page = FakeAuthorPage(200)
    shooter = AuthorShooter(
        TaskConfig(page_stabilize_milliseconds=0, screenshot_format="png"),
        shooter=StubPageShooter(),
    )

    path = await shooter.capture(
        FakeSourcePage(author_page),
        "https://example.test/author/42",
        3,
        tmp_path,
    )

    assert path.name == "003主页.png"
    assert author_page.goto_url == "https://example.test/author/42"
    assert author_page.closed


@pytest.mark.asyncio
async def test_author_shooter_http_failure_is_reportable_and_closes_page(tmp_path: Path) -> None:
    author_page = FakeAuthorPage(403)
    shooter = AuthorShooter(
        TaskConfig(page_stabilize_milliseconds=0),
        shooter=StubPageShooter(),
    )

    with pytest.raises(AuthorScreenshotError) as caught:
        await shooter.capture(
            FakeSourcePage(author_page),
            "https://example.test/author/42",
            1,
            tmp_path,
        )

    assert caught.value.code == "AUTHOR_HTTP_ERROR"
    assert author_page.closed


@pytest.mark.asyncio
async def test_author_shooter_rejects_login_wall_without_creating_evidence(tmp_path: Path) -> None:
    author_page = RestrictedAuthorPage(200)
    shooter = AuthorShooter(
        TaskConfig(headless=True, page_stabilize_milliseconds=0),
        shooter=StubPageShooter(),
    )

    with pytest.raises(AuthorScreenshotError) as caught:
        await shooter.capture(
            FakeSourcePage(author_page),
            "https://example.test/login",
            1,
            tmp_path,
        )

    assert caught.value.code == "AUTHOR_ACCESS_RESTRICTED"
    assert author_page.closed
    # No image evidence is produced, but the rejection decision is persisted
    # for the pre-ZIP audit and quality report.
    assert not list(tmp_path.glob("*.png"))
    decision_files = list(tmp_path.glob("*.decision.json"))
    assert len(decision_files) == 1


@pytest.mark.asyncio
async def test_author_shooter_rejects_platform_error_page(tmp_path: Path) -> None:
    author_page = ErrorAuthorPage(200)
    shooter = AuthorShooter(
        TaskConfig(page_stabilize_milliseconds=0),
        shooter=StubPageShooter(),
    )

    with pytest.raises(AuthorScreenshotError) as caught:
        await shooter.capture(
            FakeSourcePage(author_page),
            "https://example.test/author/bad",
            1,
            tmp_path,
        )

    assert caught.value.code == "AUTHOR_ACCESS_RESTRICTED"
    assert author_page.closed
    # No image evidence is produced, but the rejection decision is persisted
    # for the pre-ZIP audit and quality report.
    assert not list(tmp_path.glob("*.png"))
    decision_files = list(tmp_path.glob("*.decision.json"))
    assert len(decision_files) == 1


@pytest.mark.asyncio
async def test_author_shooter_rejects_page_without_rendered_profile_content(
    tmp_path: Path,
) -> None:
    author_page = SparseAuthorPage(200)
    shooter = AuthorShooter(
        TaskConfig(page_stabilize_milliseconds=0),
        shooter=StubPageShooter(),
    )

    with pytest.raises(AuthorScreenshotError) as caught:
        await shooter.capture(
            FakeSourcePage(author_page),
            "https://example.test/author/loading",
            1,
            tmp_path,
        )

    assert caught.value.code == "AUTHOR_CONTENT_NOT_READY"
    assert author_page.closed
    assert not list(tmp_path.glob("*.png"))
    decision_files = list(tmp_path.glob("*.decision.json"))
    assert len(decision_files) == 1


def test_author_identity_mismatch_is_rejected() -> None:
    state, rejection = identity_verdict(
        expected_name="正文作者",
        expected_id="author-123",
        detected_name="完全不同的账号",
        detected_id=None,
        body_text="完全不同的账号 作品列表 粉丝 简介",
    )

    assert state == "mismatch"
    assert rejection == "AUTHOR_IDENTITY_MISMATCH"


def test_author_identity_does_not_accept_name_from_navigation() -> None:
    # Regression for evidence 047/048: the header names 新华社 while the
    # navigation bar repeats the expected author 网易; must not pass.
    state, rejection = identity_verdict(
        expected_name="网易",
        expected_id=None,
        detected_name="新华社",
        detected_id=None,
        body_text="网易首页 新闻 体育 新华社 作品列表 粉丝 简介",
    )

    assert state == "mismatch"
    assert rejection == "AUTHOR_IDENTITY_MISMATCH"


def test_profile_header_selection_prefers_expected_author_over_city_switcher() -> None:
    signals = {
        "headerName": "北京",
        "headerNames": ["北京", "新华社", "推荐账号"],
    }

    assert best_header_name(signals, "新华社") == "新华社"


def test_profile_header_selection_uses_scoped_title_over_viewer_city() -> None:
    signals = {
        "headerName": "邵阳",
        "headerNames": ["邵阳"],
        "title": "钰然成长记的头条主页 - 今日头条",
        "body": "钰然成长记 3.1万获赞 2584粉丝 12关注",
    }

    assert best_header_name(signals, "钰然成长记") == "钰然成长记"


def test_profile_header_selection_rejects_title_only_shell() -> None:
    signals = {
        "headerName": "邵阳",
        "headerNames": ["邵阳"],
        "title": "钰然成长记的头条主页 - 今日头条",
        "body": "关注 推荐 视频 财经 科技 热点",
    }

    assert best_header_name(signals, "钰然成长记") == "邵阳"
