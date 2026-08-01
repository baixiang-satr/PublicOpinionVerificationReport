"""Capture an optional author home page in the source page's browser context."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from src.config.settings import TaskConfig
from src.crawler.navigation import navigate_page, stabilize_rendered_page
from src.screenshot.author_evidence import (
    CAPTURABLE_PAGE_TYPES,
    PAGE_SIGNAL_SCRIPT,
    AuthorEvidenceDecision,
    ProfilePageType,
    classify_profile_page,
    dismiss_profile_overlays,
    identity_verdict,
    normalize_identity,
    write_decision,
)
from src.screenshot.page_shooter import (
    PageShooter,
    PageScreenshotError,
    hide_obstructive_login_overlays,
)
from src.tools.page_access import inspect_page_access, wait_for_manual_access


logger = logging.getLogger(__name__)

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
        candidate_source: str = "unknown",
        decision_sink: Callable[[AuthorEvidenceDecision], None] | None = None,
    ) -> Path:
        _raise_if_cancelled(cancel_event)
        decision = AuthorEvidenceDecision(
            candidate_url=author_url,
            evidence_id=evidence_id,
            candidate_source=candidate_source,
            expected_name=expected_author_name,
            expected_id=expected_author_id,
        )
        try:
            return await self._capture_with_decision(
                source_page,
                author_url,
                evidence_id,
                output_dir,
                cancel_event,
                decision,
            )
        finally:
            # Persist every decision, accepted or rejected, so the pre-ZIP
            # audit and the quality report work from facts, not log lines.
            try:
                await asyncio.to_thread(write_decision, decision, Path(output_dir))
            except Exception as error:
                logger.warning(
                    "Unable to persist author evidence decision for #%03d: %s",
                    evidence_id,
                    error,
                )
            if decision_sink is not None:
                try:
                    decision_sink(decision)
                except Exception:  # noqa: BLE001 — 回填失败不影响截图主流程
                    pass

    async def _capture_with_decision(
        self,
        source_page: Any,
        author_url: str,
        evidence_id: int,
        output_dir: Path,
        cancel_event: asyncio.Event | None,
        decision: AuthorEvidenceDecision,
    ) -> Path:
        if urlsplit(author_url).scheme.lower() not in {"http", "https"}:
            decision.rejection_code = "AUTHOR_URL_INVALID"
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
                decision.access_state = f"http_{status}"
                decision.rejection_code = "AUTHOR_HTTP_ERROR"
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
                decision.access_state = "restricted"
                decision.rejection_code = "AUTHOR_ACCESS_RESTRICTED"
                raise AuthorScreenshotError(
                    "AUTHOR_ACCESS_RESTRICTED",
                    f"作者主页不可访问：{barrier.message}",
                )
            if await _is_access_restricted(author_page):
                decision.access_state = "restricted"
                decision.rejection_code = "AUTHOR_ACCESS_RESTRICTED"
                raise AuthorScreenshotError(
                    "AUTHOR_ACCESS_RESTRICTED",
                    "作者主页要求登录或访问验证",
                )
            decision.access_state = "accessible"
            if not await _has_author_content(author_page):
                decision.page_type = ProfilePageType.DELETED_OR_EMPTY.value
                decision.rejection_code = "AUTHOR_CONTENT_NOT_READY"
                raise AuthorScreenshotError(
                    "AUTHOR_CONTENT_NOT_READY",
                    "作者主页未渲染出可见的账号资料或实质内容",
                )

            signals = await _page_signals(author_page)
            if not _signals_have_identity(signals):
                # SPA profile shells often paint the navigation and content
                # grid before the account header arrives.  Treating that
                # intermediate frame as a mismatch both drops valid evidence
                # and produces offset screenshots of an incomplete layout.
                await _wait_for_profile_identity(author_page)
                signals = await _page_signals(author_page)
            if not signals:
                # Pages without JS evaluation support (legacy/fake contexts)
                # keep the previous capture behavior; every real browser page
                # provides signals and goes through the full decision gate.
                path = await self._shooter.capture_named(
                    author_page,
                    f"{evidence_id:03d}主页",
                    output_dir,
                    cancel_event,
                    focus_selectors=PROFILE_SELECTORS,
                )
                decision.capture_region = "profile_container"
                decision.accepted = True
                decision.rejection_code = None
                return path
            decision.detected_name = _best_header_name(
                signals,
                decision.expected_name,
            )
            decision.detected_id = str(signals.get("headerId") or "") or None
            decision.detected_id_source = str(signals.get("headerIdSource") or "")
            title = str(signals.get("title") or "")
            body = str(signals.get("body") or "")
            decision.page_type = classify_profile_page(
                url=final_url,
                title=title,
                body_text=body,
                has_profile_surface=bool(signals.get("hasProfileSurface")),
                detected_name=decision.detected_name,
            ).value

            if decision.page_type == ProfilePageType.LOGIN_OR_CHALLENGE.value:
                decision.rejection_code = "AUTHOR_ACCESS_RESTRICTED"
                raise AuthorScreenshotError(
                    "AUTHOR_ACCESS_RESTRICTED",
                    "作者主页是登录页或访问验证页，不得作为证据",
                )
            if decision.page_type == ProfilePageType.DELETED_OR_EMPTY.value:
                decision.rejection_code = "AUTHOR_CONTENT_NOT_READY"
                raise AuthorScreenshotError(
                    "AUTHOR_CONTENT_NOT_READY",
                    "作者主页已删除或渲染后为空",
                )
            if (
                decision.page_type not in {item.value for item in CAPTURABLE_PAGE_TYPES}
                and decision.page_type != ProfilePageType.COMMENT_USER_PAGE.value
            ):
                decision.rejection_code = "AUTHOR_PAGE_TYPE_INVALID"
                raise AuthorScreenshotError(
                    "AUTHOR_PAGE_TYPE_INVALID",
                    f"候选页面类型为 {decision.page_type}，不是可接受的主页",
                )

            # A rendered profile may still carry a download/login modal over
            # the profile surface.  The normal page shooter already removes
            # only these large semantic overlays; do it before the evidence
            # gate too so a capturable profile is not rejected prematurely.
            await hide_obstructive_login_overlays(author_page)
            decision.overlay_state = await dismiss_profile_overlays(author_page)
            _raise_if_cancelled(cancel_event)
            if decision.overlay_state == "blocked":
                decision.rejection_code = "AUTHOR_OVERLAY_BLOCKED"
                raise AuthorScreenshotError(
                    "AUTHOR_OVERLAY_BLOCKED",
                    "作者主页弹窗或登录组件遮挡主体区域，按无截图处理",
                )

            identity_state, rejection = identity_verdict(
                expected_name=decision.expected_name,
                expected_id=decision.expected_id,
                detected_name=decision.detected_name,
                detected_id=decision.detected_id,
                body_text=body,
                page_url=final_url,
            )
            decision.identity_state = identity_state
            if rejection is not None:
                decision.rejection_code = rejection
                raise AuthorScreenshotError(
                    rejection,
                    (
                        f"作者主页身份校验未通过：正文作者为 "
                        f"{decision.expected_name or decision.expected_id!r}，"
                        f"主页头部显示为 {decision.detected_name!r}"
                    ),
                )
            # Comment-user pages are capturable only when identity is verified.
            if (
                decision.page_type == ProfilePageType.COMMENT_USER_PAGE.value
                and identity_state != "verified"
            ):
                decision.rejection_code = "AUTHOR_IDENTITY_UNVERIFIED"
                raise AuthorScreenshotError(
                    "AUTHOR_IDENTITY_UNVERIFIED",
                    "评论用户页无法确认就是正文作者，按无截图处理",
                )

            path = await self._shooter.capture_named(
                author_page,
                f"{evidence_id:03d}主页",
                output_dir,
                cancel_event,
                focus_selectors=PROFILE_SELECTORS,
            )
            decision.capture_region = "profile_container"
            decision.accepted = True
            decision.rejection_code = None
            return path
        except AuthorScreenshotError:
            raise
        except PageScreenshotError as error:
            decision.rejection_code = "AUTHOR_SCREENSHOT_FAILED"
            raise AuthorScreenshotError("AUTHOR_SCREENSHOT_FAILED", str(error)) from error
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = "AUTHOR_TIMEOUT" if "timeout" in str(error).lower() else "AUTHOR_NAVIGATION_FAILED"
            decision.rejection_code = code
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


async def _page_signals(page: Any) -> dict[str, Any]:
    if not hasattr(page, "evaluate"):
        return {}
    try:
        signals = await page.evaluate(PAGE_SIGNAL_SCRIPT)
    except Exception:
        return {}
    return signals if isinstance(signals, dict) else {}


def _signals_have_identity(signals: dict[str, Any]) -> bool:
    names = signals.get("headerNames")
    return bool(
        signals.get("headerName")
        or signals.get("headerId")
        or (isinstance(names, list) and any(str(name).strip() for name in names))
    )


async def _wait_for_profile_identity(page: Any) -> None:
    if not hasattr(page, "wait_for_function"):
        return
    try:
        await page.wait_for_function(
            r"""() => {
                const body = (document.body?.innerText || '').trim();
                const header = document.querySelector(
                  'h1, main h2, [class*="nickname"], [class*="user-name"], '
                  + '[class*="display-name"], [class*="author-name"]'
                );
                const name = (header?.innerText || header?.textContent || '').trim();
                const hasAccount = /(?:抖音号|小红书号|账号|UID)[:：]/i.test(body);
                const isDouyin = /(^|\.)douyin\.com$/i.test(location.hostname);
                return isDouyin ? hasAccount : (name.length >= 2 || hasAccount);
            }""",
            timeout=12_000,
        )
        await stabilize_rendered_page(page, 600)
    except Exception:
        pass


def _best_header_name(
    signals: dict[str, Any],
    expected_name: str | None,
) -> str | None:
    """Prefer a profile-header candidate matching the content-page author."""

    raw_names = signals.get("headerNames")
    candidates = (
        [str(value).strip() for value in raw_names if str(value).strip()]
        if isinstance(raw_names, list)
        else []
    )
    fallback = str(signals.get("headerName") or "").strip()
    if fallback and fallback not in candidates:
        candidates.append(fallback)
    expected_key = normalize_identity(expected_name)
    if expected_key:
        for candidate in candidates:
            candidate_key = normalize_identity(candidate)
            if candidate_key and (
                expected_key in candidate_key or candidate_key in expected_key
            ):
                return candidate
    return candidates[0] if candidates else None


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
