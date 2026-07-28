"""Capture an optional author home page in the source page's browser context."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from src.config.settings import TaskConfig
from src.screenshot.page_shooter import PageShooter, PageScreenshotError


class AuthorScreenshotError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AuthorShooter:
    def __init__(self, config: TaskConfig, shooter: PageShooter | None = None) -> None:
        self._config = config
        self._shooter = shooter or PageShooter(config)

    async def capture(
        self,
        source_page: Any,
        author_url: str,
        evidence_id: int,
        output_dir: Path,
        cancel_event: asyncio.Event | None = None,
    ) -> Path:
        _raise_if_cancelled(cancel_event)
        if urlsplit(author_url).scheme.lower() not in {"http", "https"}:
            raise AuthorScreenshotError("AUTHOR_URL_INVALID", "作者主页不是 HTTP(S) URL")

        author_page = None
        try:
            author_page = await source_page.context.new_page()
            response = await author_page.goto(
                author_url,
                wait_until="domcontentloaded",
                timeout=self._config.page_timeout_seconds * 1000,
            )
            _raise_if_cancelled(cancel_event)
            status = int(response.status) if response is not None else None
            if status is not None and status >= 400:
                raise AuthorScreenshotError(
                    "AUTHOR_HTTP_ERROR",
                    f"作者主页返回 HTTP {status}",
                )
            if self._config.page_stabilize_milliseconds:
                await author_page.wait_for_timeout(self._config.page_stabilize_milliseconds)
            _raise_if_cancelled(cancel_event)
            if await _is_access_restricted(author_page):
                raise AuthorScreenshotError(
                    "AUTHOR_ACCESS_RESTRICTED",
                    "作者主页要求登录或访问验证",
                )
            return await self._shooter.capture_named(
                author_page,
                f"{evidence_id:03d}主页",
                output_dir,
                cancel_event,
            )
        except AuthorScreenshotError:
            raise
        except PageScreenshotError as error:
            raise AuthorScreenshotError("AUTHOR_SCREENSHOT_FAILED", str(error)) from error
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = "AUTHOR_TIMEOUT" if "timeout" in str(error).lower() else "AUTHOR_NAVIGATION_FAILED"
            raise AuthorScreenshotError(code, f"作者主页访问失败：{error}") from error
        finally:
            if author_page is not None:
                try:
                    await author_page.close()
                except Exception:
                    pass


def _raise_if_cancelled(cancel_event: asyncio.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise asyncio.CancelledError


async def _is_access_restricted(page: Any) -> bool:
    if not hasattr(page, "locator"):
        return False
    try:
        title = (await page.title()).strip()
        body = (await page.locator("body").inner_text(timeout=1_500)).strip()
    except Exception:
        return False
    normalized = f"{title}\n{body[:5_000]}".casefold()
    exact_titles = {"登录", "安全验证", "访问验证", "sign in", "log in"}
    markers = (
        "登录后查看",
        "请先登录",
        "扫码登录",
        "安全验证",
        "访问验证",
        "验证码",
        "sign in to continue",
        "log in to continue",
        "verify you are human",
        "参数错误",
        "页面不存在",
        "用户不存在",
        "账号不存在",
        "主页不存在",
        "内容已失效",
        "访问出错",
    )
    if title.casefold() in exact_titles or any(marker in normalized for marker in markers):
        return True

    compact_body = "".join(body.split())
    if len(compact_body) >= 120 or not hasattr(page, "evaluate"):
        return False
    try:
        has_profile_surface = bool(
            await page.evaluate(
                """() => Boolean(document.querySelector(
                    'h1, main h2, [class*="profile"], [class*="user-info"], [class*="author-info"]'
                ))"""
            )
        )
    except Exception:
        return False
    return not has_profile_surface
