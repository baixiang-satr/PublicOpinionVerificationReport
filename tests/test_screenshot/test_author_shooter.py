from pathlib import Path

import pytest

from src.config.settings import TaskConfig
from src.screenshot.author_evidence import identity_verdict
from src.screenshot.author_shooter import (
    AuthorShooter,
    AuthorScreenshotError,
)


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
        TaskConfig(page_stabilize_milliseconds=0),
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
