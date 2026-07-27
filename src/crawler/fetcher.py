"""Download public image assets through the active Playwright browser session."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


_MIME_BY_EXTENSION = {
    "jpg": {"image/jpeg", "image/jpg", "image/pjpeg"},
    "png": {"image/png", "image/x-png"},
    "gif": {"image/gif"},
    "webp": {"image/webp"},
    "bmp": {"image/bmp", "image/x-ms-bmp"},
}


class AssetFetchError(RuntimeError):
    """A bounded, user-reportable image download failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ImageFetcher:
    """Fetch and validate one raster image before atomically publishing it."""

    async def fetch(
        self,
        page: Any,
        url: str,
        output_dir: Path,
        evidence_id: int,
        image_index: int,
        max_bytes: int,
        timeout_seconds: int,
        cancel_event: asyncio.Event | None = None,
    ) -> Path:
        _raise_if_cancelled(cancel_event)
        if urlsplit(url).scheme.lower() not in {"http", "https"}:
            raise AssetFetchError("IMAGE_URL_INVALID", "图片地址不是 HTTP(S) URL")

        response = None
        output_root = Path(output_dir).resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        try:
            response = await page.context.request.get(
                url,
                timeout=timeout_seconds * 1000,
                fail_on_status_code=False,
            )
            _raise_if_cancelled(cancel_event)
            status = int(response.status)
            if status < 200 or status >= 300:
                raise AssetFetchError("IMAGE_HTTP_ERROR", f"图片返回 HTTP {status}")

            content_type = _content_type(response.headers)
            if not content_type.startswith("image/"):
                raise AssetFetchError("IMAGE_MIME_INVALID", f"图片响应类型无效：{content_type or '缺失'}")

            declared_length = _content_length(response.headers)
            if declared_length is not None and declared_length > max_bytes:
                raise AssetFetchError("IMAGE_TOO_LARGE", f"图片超过 {max_bytes} 字节限制")

            body = await response.body()
            _raise_if_cancelled(cancel_event)
            if not body:
                raise AssetFetchError("IMAGE_EMPTY", "图片响应为空")
            if len(body) > max_bytes:
                raise AssetFetchError("IMAGE_TOO_LARGE", f"图片超过 {max_bytes} 字节限制")

            extension = detect_image_extension(body)
            if extension is None:
                raise AssetFetchError("IMAGE_FORMAT_INVALID", "响应内容不是支持的栅格图片")
            if content_type not in _MIME_BY_EXTENSION[extension]:
                raise AssetFetchError(
                    "IMAGE_MIME_MISMATCH",
                    f"图片响应类型 {content_type} 与文件内容不一致",
                )

            output_path = output_root / f"{evidence_id:03d}_{image_index:02d}.{extension}"
            temporary_path = output_path.with_suffix(f"{output_path.suffix}.part")
            write_task = asyncio.create_task(
                asyncio.to_thread(_atomic_write, temporary_path, output_path, body)
            )
            try:
                await asyncio.shield(write_task)
                _raise_if_cancelled(cancel_event)
            except asyncio.CancelledError:
                await write_task
                temporary_path.unlink(missing_ok=True)
                output_path.unlink(missing_ok=True)
                raise
            except BaseException:
                temporary_path.unlink(missing_ok=True)
                output_path.unlink(missing_ok=True)
                raise
            return output_path
        except AssetFetchError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise AssetFetchError("IMAGE_DOWNLOAD_FAILED", f"图片下载失败：{error}") from error
        finally:
            if response is not None:
                try:
                    await response.dispose()
                except Exception:
                    pass


def detect_image_extension(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"BM"):
        return "bmp"
    return None


def _content_type(headers: dict[str, str]) -> str:
    return _header(headers, "content-type").split(";", 1)[0].strip().lower()


def _content_length(headers: dict[str, str]) -> int | None:
    raw_value = _header(headers, "content-length")
    if not raw_value:
        return None
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return None
    return max(0, value)


def _header(headers: dict[str, str], name: str) -> str:
    return next(
        (str(value) for key, value in headers.items() if str(key).lower() == name),
        "",
    )


def _atomic_write(temporary_path: Path, output_path: Path, data: bytes) -> None:
    temporary_path.write_bytes(data)
    os.replace(temporary_path, output_path)


def _raise_if_cancelled(cancel_event: asyncio.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise asyncio.CancelledError
