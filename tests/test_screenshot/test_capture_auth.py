import pytest

from src.crawler.platform_catalog import find_platform
from src.screenshot.capture_auth import GuestCaptureError, require_authenticated_capture


class _Context:
    def __init__(self, cookies: list[dict[str, str]]) -> None:
        self.cookies = cookies

    async def storage_state(self, **_kwargs: object) -> dict[str, object]:
        return {"cookies": self.cookies, "origins": []}


class _Page:
    def __init__(self, url: str, cookies: list[dict[str, str]]) -> None:
        self.url = url
        self.context = _Context(cookies)


@pytest.mark.asyncio
async def test_douyin_mobile_share_is_rejected_even_with_desktop_cookie() -> None:
    definition = find_platform("https://www.douyin.com/video/123")
    assert definition is not None
    page = _Page(
        "https://www.iesdouyin.com/share/video/123",
        [{"name": "sessionid", "value": "not-inspected"}],
    )

    with pytest.raises(GuestCaptureError, match="mobile-share"):
        await require_authenticated_capture(page, definition)


@pytest.mark.asyncio
async def test_douyin_desktop_without_account_cookie_is_rejected() -> None:
    definition = find_platform("https://www.douyin.com/video/123")
    assert definition is not None
    page = _Page(
        "https://www.douyin.com/video/123",
        [{"name": "ttwid", "value": "anonymous-device"}],
    )

    with pytest.raises(GuestCaptureError, match="session evidence is missing"):
        await require_authenticated_capture(page, definition)
