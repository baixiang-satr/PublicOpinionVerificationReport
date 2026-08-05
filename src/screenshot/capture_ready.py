"""Shared capture-readiness helpers moved out of page_shooter for line limits.

The module keeps the names that other callers import from
``src.screenshot.page_shooter`` (``wait_for_capture_ready``,
``hide_obstructive_login_overlays``, ``PageScreenshotError``) bound here and
re-exported there, so existing import sites stay valid.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from src.crawler.navigation import wait_for_substantive_content
from src.crawler.platform_catalog import find_platform
from src.screenshot.capture_auth import douyin_desktop_surface_ready
from src.screenshot.image_checks import UnreadableImageError, is_visually_blank


class PageScreenshotError(RuntimeError):
    """Raised when a primary page screenshot cannot be created."""


async def wait_for_capture_ready(
    page: Any,
    definition: Any = None,
    cancel_event: asyncio.Event | None = None,
    *,
    require_content: bool = True,
    strict_platform_content: bool = True,
) -> bool:
    """Wait for substantive content and stable visible assets before capture.

    The same readiness gate is used by automatic and interactive screenshots.
    Automatic evidence rejects an unrendered page; an operator-driven capture
    may continue after the bounded wait so a manually repaired page remains
    under the operator's control.
    """

    _raise_if_cancelled(cancel_event)
    if not hasattr(page, "wait_for_function"):
        return False
    if definition is None:
        definition = find_platform(str(getattr(page, "url", "") or ""))
    strict_platform = bool(
        definition is not None
        and getattr(definition, "key", "")
        in {"douyin", "toutiao", "wechat_video", "xiaohongshu", "ixigua"}
    )
    selectors = [
        selector
        for field in ("title", "content_text")
        for selector in (definition.selectors.get(field, ()) if definition else ())
    ]
    selectors.extend(("h1", "article", "main", "[role='main']"))
    selector_json = json.dumps(list(dict.fromkeys(selectors)), ensure_ascii=False)
    content_ready_script = f"""() => {{
        const selectors = {selector_json};
        const visibleText = (element) => {{
          if (!element) return '';
          const style = getComputedStyle(element);
          const rect = element.getBoundingClientRect();
          if (
            style.display === 'none' ||
            style.visibility === 'hidden' ||
            rect.width <= 0 ||
            rect.height <= 0
          ) return '';
          return (element.innerText || element.textContent || '').trim();
        }};
        return selectors.some(selector => {{
          try {{ return visibleText(document.querySelector(selector)).length >= 2; }}
          catch (_) {{ return false; }}
        }}) || (document.body?.innerText || '').trim().length >= 40;
    }}"""
    content_ready = False
    try:
        await page.wait_for_function(content_ready_script, timeout=8_000)
        content_ready = True
    except Exception:
        pass
    if not content_ready and hasattr(page, "evaluate"):
        try:
            content_ready = bool(await page.evaluate(content_ready_script))
        except Exception:
            pass
    if not content_ready and require_content:
        raise PageScreenshotError(
            "Page title/content did not become visibly rendered before screenshot."
        )
    if not content_ready:
        return False
    if strict_platform and strict_platform_content:
        substantive = await wait_for_substantive_content(
            page,
            definition,
            timeout_milliseconds=15_000,
        )
        if (
            not substantive
            and getattr(definition, "key", "") == "douyin"
            and await douyin_desktop_surface_ready(page)
        ):
            substantive = True
        if not substantive and require_content:
            raise PageScreenshotError(
                "Platform content/player did not finish rendering before screenshot."
            )
        if not substantive:
            return False
    try:
        await page.wait_for_function(
            """() =>
                (!document.fonts || document.fonts.status === 'loaded') &&
                Array.from(document.images).every(img => {
                  const rect = img.getBoundingClientRect();
                  const visible = rect.bottom >= 0
                    && rect.top <= window.innerHeight
                    && rect.right >= 0
                    && rect.left <= window.innerWidth;
                  return !visible || (
                    img.complete
                    && (
                      img.naturalWidth > 0
                      || !(img.currentSrc || img.src)
                    )
                  );
                }) &&
                Array.from(document.querySelectorAll('video')).every(video => {
                  const rect = video.getBoundingClientRect();
                  const visible = rect.bottom >= 0
                    && rect.top <= window.innerHeight
                    && rect.right >= 0
                    && rect.left <= window.innerWidth;
                  // A poster URL can exist before its pixels are painted.
                  // Require actual current-frame data; the bounded timeout
                  // still lets image-only or access-restricted pages proceed.
                  return !visible || video.readyState >= 2;
                })
            """,
            timeout=6_000,
        )
    except Exception:
        pass
    await _wait_for_dom_quiet(page)
    try:
        # Leave one final paint window after the last DOM/image mutation.
        # This prevents capturing a populated DOM before its pixels appear.
        await page.wait_for_timeout(600)
    except Exception:
        pass
    _raise_if_cancelled(cancel_event)
    return True


async def _wait_for_dom_quiet(page: Any) -> None:
    if not hasattr(page, "evaluate"):
        return
    try:
        await page.evaluate(
            """() => new Promise(resolve => {
                if (!document.body || typeof MutationObserver === 'undefined') {
                  resolve();
                  return;
                }
                let quietTimer;
                let hardTimer;
                const finish = () => {
                  clearTimeout(quietTimer);
                  clearTimeout(hardTimer);
                  observer.disconnect();
                  resolve();
                };
                const reset = () => {
                  clearTimeout(quietTimer);
                  quietTimer = setTimeout(finish, 750);
                };
                const observer = new MutationObserver(reset);
                observer.observe(document.body, {
                  childList: true,
                  subtree: true,
                  attributes: true,
                  characterData: true
                });
                hardTimer = setTimeout(finish, 3_000);
                reset();
            })"""
        )
    except Exception:
        pass


async def hide_obstructive_login_overlays(page: Any) -> None:
    """Dismiss large login/app-open overlays while preserving page content."""

    if not hasattr(page, "evaluate"):
        return
    try:
        await page.evaluate(
            """() => {
                // 平台已知遮挡弹窗：微信视频号预览页的扫码引导层（无文本按钮）
                for (const known of ['.qr-modal-overlay']) {
                  try { document.querySelectorAll(known).forEach(el => el.remove()); } catch (_) {}
                }
                const markers = [
                  '扫码登录', '账号登录', '手机号登录', '验证码登录',
                  '登录后免费', '登录后查看', 'sign in', 'log in',
                  '打开app看完整内容', '打开app查看', '打开客户端查看',
                  '打开西瓜视频查看', '打开方式', '下载app继续观看'
                ];
                const dismissLabels = [
                  '否', '取消', '稍后', '关闭', '继续浏览器', '继续使用浏览器'
                ];
                const viewportArea = Math.max(1, innerWidth * innerHeight);
                const candidates = Array.from(document.querySelectorAll(
                  '[role="dialog"], [class*="modal"], [class*="dialog"], '
                  + '[class*="login"], [class*="popup"], [class*="mask"]'
                ));
                let removedAny = false;
                for (const element of candidates) {
                  const rect = element.getBoundingClientRect();
                  const style = getComputedStyle(element);
                  const text = (element.innerText || element.textContent || '')
                    .trim().toLowerCase();
                  const visible = style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && rect.width > 0 && rect.height > 0;
                  const looksLikeLogin = markers.some(marker => text.includes(marker));
                  const looksLikeOverlay = element.getAttribute('role') === 'dialog'
                    || ['fixed', 'absolute'].includes(style.position)
                    || rect.width * rect.height >= viewportArea * 0.08;
                  if (!visible || !looksLikeLogin || !looksLikeOverlay) continue;

                  const buttons = Array.from(element.querySelectorAll(
                    'button, [role="button"], a'
                  ));
                  const dismiss = buttons.find(button => {
                    const label = (button.innerText || button.textContent || '').trim();
                    return dismissLabels.some(item => label === item || label.includes(item));
                  });
                  if (dismiss) {
                    try { dismiss.click(); } catch (_) {}
                  }

                  let target = element;
                  for (let depth = 0; depth < 3 && target.parentElement; depth += 1) {
                    const parent = target.parentElement;
                    const parentRect = parent.getBoundingClientRect();
                    const parentStyle = getComputedStyle(parent);
                    if (
                      ['fixed', 'absolute'].includes(parentStyle.position)
                      && parentRect.width * parentRect.height >= rect.width * rect.height
                    ) {
                      target = parent;
                    } else {
                      break;
                    }
                  }
                  removedAny = true;
                  target.style.setProperty('display', 'none', 'important');
                }

                if (!removedAny) return;
                const masks = Array.from(document.querySelectorAll(
                  '[class*="mask"], [class*="overlay"], [class*="backdrop"]'
                ));
                for (const element of masks) {
                  const rect = element.getBoundingClientRect();
                  const style = getComputedStyle(element);
                  const text = (element.innerText || '').trim();
                  const coversViewport = rect.width * rect.height >= viewportArea * 0.65;
                  const fixedMask = ['fixed', 'absolute'].includes(style.position)
                    && coversViewport && text.length < 20;
                  if (fixedMask) {
                    element.style.setProperty('display', 'none', 'important');
                  }
                }
            }"""
        )
    except Exception:
        pass


async def _pause_playing_media(page: Any) -> None:
    """Freeze visible audio/video frames so screenshot capture is stable."""

    if not hasattr(page, "evaluate"):
        return
    try:
        await page.evaluate(
            """() => {
                for (const media of document.querySelectorAll('video, audio')) {
                  try { media.pause(); } catch (_) {}
                }
            }"""
        )
    except Exception:
        pass


def _is_visually_blank(path: Path) -> bool:
    try:
        return is_visually_blank(path)
    except UnreadableImageError as error:
        raise PageScreenshotError(str(error)) from error


def _raise_if_cancelled(cancel_event: asyncio.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise asyncio.CancelledError
