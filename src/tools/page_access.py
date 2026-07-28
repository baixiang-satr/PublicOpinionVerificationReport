"""Detect access barriers and support bounded, user-driven recovery."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from src.domain.models import RecordStatus


class AccessKind(StrEnum):
    LOGIN = "login"
    CAPTCHA = "captcha"
    ACCESS_RESTRICTED = "access_restricted"
    JAVASCRIPT_REQUIRED = "javascript_required"
    API_RESPONSE = "api_response"
    CONTENT_UNAVAILABLE = "content_unavailable"
    REDIRECTED_HOME = "redirected_home"
    EMPTY_RENDERED_PAGE = "empty_rendered_page"


@dataclass(frozen=True)
class AccessBarrier:
    kind: AccessKind
    code: str
    message: str
    status: RecordStatus
    manual_recoverable: bool = False
    retryable: bool = False


# ── 验证码 URL 特征 ────────────────────────────────────────────────────
_CAPTCHA_URL_MARKERS = (
    "wappass.baidu.com/static/captcha",
    "/captcha",
    "/punish",
    "x5sec",
    "/safeverify",
    "/security-check",
    "geetest",
    "slide.captcha",
    "hcaptcha",
    "recaptcha",
    "turnstile",
)

# ── 登录 URL 特征 ──────────────────────────────────────────────────────
_LOGIN_URL_MARKERS = (
    "/login",
    "/signin",
    "/passport",
    "/visitor",
    "passport.",
    "login.",
    "account.",
    "/auth/",
)

# ── 风控/限制 URL 特征 ─────────────────────────────────────────────────
_RESTRICTED_URL_MARKERS = (
    "risk.jd.com",
    "/risk",
    "/challenge",
    "/anti-bot",
    "/block",
    "/deny",
    "/verify",
)

# ── 验证码文本特征 ─────────────────────────────────────────────────────
_CAPTCHA_TEXT_MARKERS = (
    "请输入验证码",
    "图形验证码",
    "滑动验证",
    "拖动滑块",
    "完成安全验证",
    "verify you are human",
    "captcha",
    "滑块拼图",
    "点击按钮进行验证",
    "安全验证",
    "人机验证",
    "拖动下方滑块完成拼图",
    "请完成安全验证",
    "滑动拼图验证",
)

# ── 登录文本特征 ───────────────────────────────────────────────────────
_LOGIN_TEXT_MARKERS = (
    "请先登录",
    "登录后查看",
    "扫码登录",
    "账号登录",
    "微博访客",
    "sign in to continue",
    "log in to continue",
    "使用微信扫码登录",
    "登录即代表同意",
    "手机号登录",
    "验证码登录",
)

# ── 风控文本特征 ───────────────────────────────────────────────────────
_RESTRICTED_TEXT_MARKERS = (
    "访问异常",
    "请求异常",
    "访问过于频繁",
    "存在安全风险",
    "操作存在风险",
    "unusual traffic",
    "access denied",
    "请求太快了",
    "操作频率过高",
    "休息一下",
    "前方拥挤",
    "刷新试试",
    "检测到异常访问",
    "访问被拒绝",
    "你的访问受到限制",
    "账号存在风险",
    "操作过于频繁",
)

# ── JavaScript 文本特征 ────────────────────────────────────────────────
_JAVASCRIPT_TEXT_MARKERS = (
    "需要允许该网站执行 javascript",
    "请启用 javascript",
    "enable javascript",
    "javascript is required",
)

# ── 内容不可用文本特征 ─────────────────────────────────────────────────
_UNAVAILABLE_TEXT_MARKERS = (
    "笔记不存在",
    "内容不存在",
    "页面不存在",
    "视频不存在",
    "文章不存在",
    "内容已删除",
    "内容已下线",
    "页面已失效",
    "404 not found",
    "该内容已被删除",
    "页面不存在或已删除",
    "内容找不到了",
    "该内容暂时无法查看",
)
_HOME_PATHS = {"", "/", "/index.html", "/home", "/home/"}


def inspect_http_response(status_code: int | None) -> AccessBarrier | None:
    """Classify HTTP failures using the same vocabulary as rendered barriers."""

    if status_code in {401, 403}:
        return AccessBarrier(
            AccessKind.ACCESS_RESTRICTED,
            f"HTTP_{status_code}",
            f"页面返回 HTTP {status_code}；请确认访问权限或提供合法登录态。",
            RecordStatus.NEEDS_REVIEW,
        )
    if status_code == 405:
        return AccessBarrier(
            AccessKind.ACCESS_RESTRICTED,
            "HTTP_405_ACCESS_RESTRICTED",
            "页面拒绝当前访问方式（HTTP 405）；请核对真实内容 URL。",
            RecordStatus.NEEDS_REVIEW,
        )
    if status_code == 404:
        return AccessBarrier(
            AccessKind.CONTENT_UNAVAILABLE,
            "CONTENT_NOT_FOUND",
            "页面返回 HTTP 404，内容可能不存在、已删除或 URL 无效。",
            RecordStatus.FAILED,
        )
    if status_code == 429:
        return AccessBarrier(
            AccessKind.ACCESS_RESTRICTED,
            "HTTP_429",
            "页面返回 HTTP 429",
            RecordStatus.NEEDS_REVIEW,
            retryable=True,
        )
    if status_code is not None and status_code >= 500:
        return AccessBarrier(
            AccessKind.ACCESS_RESTRICTED,
            "HTTP_5XX",
            f"页面返回 HTTP {status_code}",
            RecordStatus.FAILED,
            retryable=True,
        )
    if status_code is not None and status_code >= 400:
        return AccessBarrier(
            AccessKind.ACCESS_RESTRICTED,
            f"HTTP_{status_code}",
            f"页面返回 HTTP {status_code}",
            RecordStatus.FAILED,
        )
    return None


async def inspect_page_access(
    page: Any,
    final_url: str,
    original_url: str | None = None,
) -> AccessBarrier | None:
    """Return a strong access/content barrier signal without bypassing it."""

    lowered_url = final_url.lower()
    if any(marker in lowered_url for marker in _CAPTCHA_URL_MARKERS):
        return _captcha_barrier()
    if any(marker in lowered_url for marker in _LOGIN_URL_MARKERS):
        return _login_barrier()
    if any(marker in lowered_url for marker in _RESTRICTED_URL_MARKERS):
        return _restricted_barrier()
    if _redirected_to_home(original_url, final_url):
        return AccessBarrier(
            AccessKind.REDIRECTED_HOME,
            "CONTENT_REDIRECTED_TO_HOME",
            "内容链接被重定向到平台首页；请提供仍然有效的真实内容 URL。",
            RecordStatus.NEEDS_REVIEW,
        )

    title, body = await _read_page_snapshot(page)
    normalized = f"{title}\n{body}".strip().lower()
    if (
        any(marker in normalized for marker in _CAPTCHA_TEXT_MARKERS)
        and _looks_like_barrier_only_page(title, body, AccessKind.CAPTCHA)
    ):
        return _captcha_barrier()
    if (
        any(marker in normalized for marker in _LOGIN_TEXT_MARKERS)
        and _looks_like_barrier_only_page(title, body, AccessKind.LOGIN)
    ):
        return _login_barrier()
    if (
        any(marker in normalized for marker in _RESTRICTED_TEXT_MARKERS)
        and _looks_like_barrier_only_page(title, body, AccessKind.ACCESS_RESTRICTED)
    ):
        return _restricted_barrier()
    if any(marker in normalized for marker in _JAVASCRIPT_TEXT_MARKERS):
        return AccessBarrier(
            AccessKind.JAVASCRIPT_REQUIRED,
            "JAVASCRIPT_RENDER_BLOCKED",
            "页面要求 JavaScript 但正文未渲染；可增加稳定等待后重试。",
            RecordStatus.NEEDS_REVIEW,
        )
    if any(marker in normalized for marker in _UNAVAILABLE_TEXT_MARKERS):
        return AccessBarrier(
            AccessKind.CONTENT_UNAVAILABLE,
            "CONTENT_UNAVAILABLE",
            "平台明确提示内容不存在、已删除或已下线；请核对原始 URL。",
            RecordStatus.FAILED,
        )
    if _looks_like_json(body):
        return AccessBarrier(
            AccessKind.API_RESPONSE,
            "UNEXPECTED_API_RESPONSE",
            "页面返回 JSON/API 数据而不是可截图正文；请提供浏览器内容页 URL。",
            RecordStatus.NEEDS_REVIEW,
        )
    if hasattr(page, "locator") and len(normalized) < 12:
        return AccessBarrier(
            AccessKind.EMPTY_RENDERED_PAGE,
            "EMPTY_RENDERED_PAGE",
            "页面渲染后仍为空，可能需要登录态、更多等待或有效内容 URL。",
            RecordStatus.NEEDS_REVIEW,
        )
    return None


async def wait_for_manual_access(
    page: Any,
    final_url: str,
    original_url: str,
    timeout_seconds: int,
    poll_milliseconds: int = 1_000,
    cancel_event: asyncio.Event | None = None,
) -> AccessBarrier | None:
    """Wait for a user to finish a visible login or challenge, within a bound."""

    remaining = max(0, timeout_seconds * 1_000)
    barrier = await inspect_page_access(page, final_url, original_url)
    while barrier is not None and barrier.manual_recoverable and remaining > 0:
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError
        step = min(poll_milliseconds, remaining)
        await page.wait_for_timeout(step)
        remaining -= step
        final_url = str(page.url)
        barrier = await inspect_page_access(page, final_url, original_url)
    if cancel_event is not None and cancel_event.is_set():
        raise asyncio.CancelledError
    return barrier


def _captcha_barrier() -> AccessBarrier:
    return AccessBarrier(
        AccessKind.CAPTCHA,
        "CAPTCHA_REQUIRED",
        "页面要求人工验证码；请取消“后台运行浏览器”，在可视窗口完成验证后重试。",
        RecordStatus.NEEDS_REVIEW,
        manual_recoverable=True,
    )


def _login_barrier() -> AccessBarrier:
    return AccessBarrier(
        AccessKind.LOGIN,
        "LOGIN_REQUIRED",
        "页面要求登录；请选择您有权使用的登录态 JSON，或用可视浏览器人工登录。",
        RecordStatus.NEEDS_REVIEW,
        manual_recoverable=True,
    )


def _restricted_barrier() -> AccessBarrier:
    return AccessBarrier(
        AccessKind.ACCESS_RESTRICTED,
        "ACCESS_CHALLENGE",
        "平台显示访问风控页；请在可视窗口确认访问权限，无法恢复时稍后重试。",
        RecordStatus.NEEDS_REVIEW,
        manual_recoverable=True,
    )


def _redirected_to_home(original_url: str | None, final_url: str) -> bool:
    if not original_url:
        return False
    original = urlsplit(original_url)
    final = urlsplit(final_url)
    if original.path in _HOME_PATHS or final.path.lower() not in _HOME_PATHS:
        return False
    had_content_locator = original.path not in _HOME_PATHS or bool(original.query)
    return had_content_locator and bool(final.hostname)


async def _read_page_snapshot(page: Any) -> tuple[str, str]:
    if hasattr(page, "evaluate"):
        try:
            payload = await page.evaluate(
                """() => ({
                    title: document.title || "",
                    body: (document.body?.innerText || "").slice(0, 12000)
                })"""
            )
            if isinstance(payload, dict):
                return str(payload.get("title") or ""), str(payload.get("body") or "")
        except Exception:
            pass
    title = ""
    body = ""
    if hasattr(page, "title"):
        try:
            title = str(await page.title())
        except Exception:
            pass
    if hasattr(page, "locator"):
        try:
            body = str(await page.locator("body").inner_text(timeout=1_000))[:12_000]
        except Exception:
            pass
    return title, body


def _looks_like_json(body: str) -> bool:
    candidate = body.strip()
    if not candidate or candidate[0] not in "[{":
        return False
    try:
        return isinstance(json.loads(candidate), (dict, list))
    except (TypeError, ValueError, json.JSONDecodeError):
        return False


def _looks_like_barrier_only_page(title: str, body: str, kind: AccessKind) -> bool:
    """Avoid rejecting a real article merely because its chrome has a login prompt."""

    normalized_title = title.strip().casefold()
    exact_titles = {
        AccessKind.CAPTCHA: {
            "安全验证",
            "访问验证",
            "人机验证",
            "captcha",
            "verify you are human",
        },
        AccessKind.LOGIN: {
            "登录",
            "账号登录",
            "扫码登录",
            "sign in",
            "log in",
        },
        AccessKind.ACCESS_RESTRICTED: {
            "访问异常",
            "访问受限",
            "access denied",
        },
    }
    if normalized_title in exact_titles.get(kind, set()):
        return True

    # Substantial public pages commonly include a login modal or login text in
    # the header. Treat text markers as a barrier only when little other page
    # content rendered.
    visible_length = len("".join(body.split()))
    return visible_length < 800
