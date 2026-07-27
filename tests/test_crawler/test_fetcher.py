from pathlib import Path

import pytest

from src.crawler.fetcher import AssetFetchError, ImageFetcher, detect_image_extension


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"valid-png-payload"


class FakeResponse:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self._body = body
        self.disposed = False

    async def body(self) -> bytes:
        return self._body

    async def dispose(self) -> None:
        self.disposed = True


class FakeRequestSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def get(self, url: str, **options: object) -> FakeResponse:
        self.calls.append((url, options))
        return self.response


class FakePage:
    def __init__(self, response: FakeResponse) -> None:
        request = FakeRequestSession(response)
        self.context = type("Context", (), {"request": request})()


@pytest.mark.asyncio
async def test_fetcher_writes_valid_image_with_generated_safe_name(tmp_path: Path) -> None:
    response = FakeResponse(
        200,
        {"content-type": "image/png", "content-length": str(len(PNG_BYTES))},
        PNG_BYTES,
    )

    path = await ImageFetcher().fetch(
        FakePage(response),
        "https://example.test/path/untrusted-name.exe?token=secret",
        tmp_path,
        evidence_id=7,
        image_index=2,
        max_bytes=1024,
        timeout_seconds=3,
    )

    assert path == tmp_path / "007_02.png"
    assert path.read_bytes() == PNG_BYTES
    assert response.disposed
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "body", "max_bytes", "code"),
    [
        ({"content-type": "text/html"}, b"<html>login</html>", 1024, "IMAGE_MIME_INVALID"),
        ({"content-type": "image/jpeg"}, PNG_BYTES, 1024, "IMAGE_MIME_MISMATCH"),
        ({"content-type": "image/png", "content-length": "4096"}, PNG_BYTES, 100, "IMAGE_TOO_LARGE"),
        ({"content-type": "image/png"}, b"not-an-image", 1024, "IMAGE_FORMAT_INVALID"),
    ],
)
async def test_fetcher_rejects_untrusted_responses_without_leaving_files(
    tmp_path: Path,
    headers: dict[str, str],
    body: bytes,
    max_bytes: int,
    code: str,
) -> None:
    response = FakeResponse(200, headers, body)

    with pytest.raises(AssetFetchError) as caught:
        await ImageFetcher().fetch(
            FakePage(response),
            "https://example.test/image",
            tmp_path,
            evidence_id=1,
            image_index=1,
            max_bytes=max_bytes,
            timeout_seconds=3,
        )

    assert caught.value.code == code
    assert response.disposed
    assert not list(tmp_path.iterdir())


def test_detect_image_extension_supports_allowed_raster_formats() -> None:
    assert detect_image_extension(b"\xff\xd8\xffpayload") == "jpg"
    assert detect_image_extension(b"GIF89apayload") == "gif"
    assert detect_image_extension(b"RIFFxxxxWEBPpayload") == "webp"
    assert detect_image_extension(b"BMpayload") == "bmp"
    assert detect_image_extension(b"<svg></svg>") is None
