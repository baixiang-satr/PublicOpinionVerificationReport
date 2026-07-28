"""Capture an optional author home page in the source page's browser context."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from src.config.settings import TaskConfig
from src.crawler.navigation import navigate_page, stabilize_rendered_page
from src.screenshot.page_shooter import PageShooter, PageScreenshotError
from src.tools.page_access import inspect_page_access, wait_for_manual_access


PROFILE_SELECTORS = (
    "[class*='profile-header']",
    "[class*='user-info']",
    "[class*='author-info']",
    "[class*='profile']",
    "[class*='nickname']",
    "main h1",
    "main h2",
)


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
        *,
        expected_author_name: str | None = None,
        expected_author_id: str | None = None,
    ) -> Path:
        _raise_if_cancelled(cancel_event)
        if urlsplit(author_url).scheme.lower() not in {"http", "https"}:
            raise AuthorScreenshotError("AUTHOR_URL_INVALID", "作者主页不是 HTTP(S) URL")

        author_page = None
        try:
            author_page = await source_page.context.new_page()
            response, _partial_navigation_error = await navigate_page(
                author_page,
                author_url,
                self._config.page_timeout_seconds * 1000,
                cancel_event,
            )
            _raise_if_cancelled(cancel_event)
            status = int(response.status) if response is not None else None
            if status is not None and status >= 400:
                raise AuthorScreenshotError(
                    "AUTHOR_HTTP_ERROR",
                    f"作者主页返回 HTTP {status}",
                )
            await stabilize_rendered_page(
                author_page,
                self._config.page_stabilize_milliseconds,
            )
            await _wait_for_author_surface(author_page)
            _raise_if_cancelled(cancel_event)
            final_url = str(getattr(author_page, "url", "") or author_url)
            barrier = await inspect_page_access(author_page, final_url, author_url)
            if (
                barrier is not None
                and barrier.manual_recoverable
                and not self._config.headless
                and self._config.manual_intervention_timeout_seconds
            ):
                barrier = await wait_for_manual_access(
                    author_page,
                    final_url,
                    author_url,
                    self._config.manual_intervention_timeout_seconds,
                    cancel_event=cancel_event,
                )
                await stabilize_rendered_page(author_page, 0)
            if barrier is not None:
                raise AuthorScreenshotError(
                    "AUTHOR_ACCESS_RESTRICTED",
                    f"作者主页不可访问：{barrier.message}",
                )
            if await _is_access_restricted(author_page):
                raise AuthorScreenshotError(
                    "AUTHOR_ACCESS_RESTRICTED",
                    "作者主页要求登录或访问验证",
                )
            if not await _has_author_content(author_page):
                raise AuthorScreenshotError(
                    "AUTHOR_CONTENT_NOT_READY",
                    "作者主页未渲染出可见的账号资料或实质内容",
                )
            await _validate_author_identity(
                author_page,
                expected_author_name,
                expected_author_id,
            )
            return await self._shooter.capture_named(
                author_page,
                f"{evidence_id:03d}主页",
                output_dir,
                cancel_event,
                focus_selectors=PROFILE_SELECTORS,
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
    if not hasattr(page, "evaluate"):
        return False
    try:
        has_profile_surface = bool(
            await page.evaluate(
                """() => Boolean(document.querySelector(
                    'h1, main h2, [class*="profile"], [class*="user-info"], '
                    + '[class*="author-info"], [class*="avatar"], '
                    + '[class*="display-name"], [class*="user-name"], '
                    + '[class*="author-name"], [class*="nickname"]'
                ))"""
            )
        )
    except Exception:
        return False
    if has_profile_surface:
        return False
    # Very small pages with no recognizable profile surface are usually an
    # error shell. Longer sparse pages remain eligible for evidence capture.
    return len(compact_body) < 20


async def _wait_for_author_surface(page: Any) -> None:
    if not hasattr(page, "locator"):
        return
    try:
        await page.locator(
            "h1, main h2, [class*='profile'], [class*='user-info'], "
            "[class*='author-info'], [class*='avatar'], [class*='nickname']"
        ).first.wait_for(state="visible", timeout=3_000)
    except Exception:
        pass


async def _validate_author_identity(
    page: Any,
    expected_name: str | None,
    expected_id: str | None,
) -> None:
    if not expected_name and not expected_id:
        return
    if not hasattr(page, "evaluate"):
        return
    try:
        identity = await page.evaluate(
            """() => {
                const selectors = [
                  '[class*="profile-header"] [class*="name"]',
                  '[class*="user-info"] [class*="name"]',
                  '[class*="author-info"] [class*="name"]',
                  '[class*="nickname"]',
                  'main h1',
                  'main h2'
                ];
                let name = '';
                for (const selector of selectors) {
                  const element = document.querySelector(selector);
                  const text = (element?.innerText || element?.textContent || '').trim();
                  if (text && text.length <= 100) { name = text; break; }
                }
                return {
                  name,
                  body: (document.body?.innerText || '').slice(0, 5000)
                };
            }"""
        )
    except Exception:
        return
    detected = _identity_key(str(identity.get("name") or ""))
    expected = _identity_key(expected_name or "")
    body = _identity_key(str(identity.get("body") or ""))
    identifier = _identity_key(expected_id or "")
    if identifier and identifier in body:
        return
    # A detected profile-header name is stronger evidence than arbitrary body
    # text.  Navigation bars, recommendations and sidebars commonly repeat
    # other publisher names; accepting a body-only match when the header names
    # somebody else produced false author screenshots in the real URL batch.
    if expected and detected:
        if expected in detected or detected in expected:
            return
        raise AuthorScreenshotError(
            "AUTHOR_IDENTITY_MISMATCH",
            (
                f"作者主页身份不一致：正文作者为 {expected_name!r}，"
                f"主页显示为 {identity.get('name')!r}"
            ),
        )
    if expected and expected in body:
        return


def _identity_key(value: str) -> str:
    return "".join(
        character.casefold()
        for character in value
        if character.isalnum()
    )


async def _has_author_content(page: Any) -> bool:
    if not hasattr(page, "locator"):
        return True
    try:
        title = (await page.title()).strip()
        body = (await page.locator("body").inner_text(timeout=1_500)).strip()
    except Exception:
        return False
    if hasattr(page, "evaluate"):
        try:
            has_profile_surface = bool(
                await page.evaluate(
                    """() => Boolean(document.querySelector(
                        'h1, main h2, [class*="profile"], [class*="user-info"], '
                        + '[class*="author-info"], [class*="avatar"], '
                        + '[class*="display-name"], [class*="user-name"], '
                        + '[class*="author-name"], [class*="nickname"]'
                    ))"""
                )
            )
            if has_profile_surface:
                return True
        except Exception:
            pass
    return bool(title) and len("".join(body.split())) >= 80
