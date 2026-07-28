"""Stable evidence-ID page screenshots written directly to the staging root."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat, UnidentifiedImageError

from src.config.settings import TaskConfig
from src.crawler.platform_catalog import find_platform
from src.utils.file_utils import UnsafeFileNameError, require_safe_file_name


class PageScreenshotError(RuntimeError):
    """Raised when a primary page screenshot cannot be created."""


class PageShooter:
    def __init__(self, config: TaskConfig) -> None:
        self._config = config

    async def capture(
        self,
        page: Any,
        evidence_id: int,
        output_dir: Path,
        cancel_event: asyncio.Event | None = None,
        *,
        definition: Any = None,
    ) -> Path:
        return await self.capture_named(
            page,
            f"{evidence_id:03d}",
            output_dir,
            cancel_event,
            definition=definition,
        )

    async def capture_named(
        self,
        page: Any,
        file_stem: str,
        output_dir: Path,
        cancel_event: asyncio.Event | None = None,
        *,
        definition: Any = None,
    ) -> Path:
        _raise_if_cancelled(cancel_event)
        extension = "jpg" if self._config.screenshot_format == "jpeg" else "png"
        try:
            file_name = require_safe_file_name(f"{file_stem}.{extension}")
        except UnsafeFileNameError as error:
            raise PageScreenshotError(str(error)) from error
        output_path = Path(output_dir).resolve() / file_name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        options: dict[str, Any] = {
            "path": str(output_path),
            "type": self._config.screenshot_format,
            "full_page": self._config.full_page_screenshot,
            "animations": "disabled",
        }
        if definition is None:
            definition = find_platform(str(getattr(page, "url", "") or ""))
        await _wait_for_content_ready(page, definition, cancel_event)
        await _hide_obstructive_login_overlays(page)
        is_long_page = False
        if self._config.full_page_screenshot:
            dimensions = await _page_dimensions(page, definition)
            if dimensions is not None:
                is_long_page = (
                    dimensions["height"]
                    > self._config.max_full_page_screenshot_height
                )
                has_horizontal_overflow = (
                    dimensions["document_width"]
                    > dimensions["viewport_width"] + 32
                )
            else:
                has_horizontal_overflow = False
            if dimensions is not None and (is_long_page or has_horizontal_overflow):
                options["full_page"] = False
                options["clip"] = {
                    "x": dimensions["focus_x"] if has_horizontal_overflow else 0,
                    "y": 0,
                    "width": dimensions["viewport_width"],
                    "height": min(
                        dimensions["height"],
                        self._config.max_full_page_screenshot_height,
                    ),
                }
        if self._config.screenshot_format == "jpeg":
            options["quality"] = (
                self._config.long_page_jpeg_quality
                if is_long_page
                else self._config.screenshot_jpeg_quality
            )
        try:
            await page.screenshot(**options)
        except Exception as error:
            output_path.unlink(missing_ok=True)
            raise PageScreenshotError(f"Unable to capture screenshot: {error}") from error
        _raise_if_cancelled(cancel_event)
        if not output_path.is_file() or output_path.stat().st_size == 0:
            output_path.unlink(missing_ok=True)
            raise PageScreenshotError("Playwright returned an empty screenshot.")
        if _is_visually_blank(output_path):
            output_path.unlink(missing_ok=True)
            raise PageScreenshotError(
                "Screenshot is blank or near-uniform and is not usable as evidence."
            )
        return output_path


async def _wait_for_content_ready(
    page: Any,
    definition: Any = None,
    cancel_event: asyncio.Event | None = None,
) -> None:
    """Best-effort wait for substantive content, fonts, images and a quiet DOM."""

    _raise_if_cancelled(cancel_event)
    if not hasattr(page, "wait_for_function"):
        return
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
        await page.wait_for_function(content_ready_script, timeout=3_000)
        content_ready = True
    except Exception:
        pass
    if not content_ready and hasattr(page, "evaluate"):
        try:
            content_ready = bool(await page.evaluate(content_ready_script))
        except Exception:
            pass
    if not content_ready:
        raise PageScreenshotError(
            "Page title/content did not become visibly rendered before screenshot."
        )
    try:
        await page.wait_for_function(
            """() =>
                (!document.fonts || document.fonts.status === 'loaded') &&
                Array.from(document.images).every(img => {
                  const rect = img.getBoundingClientRect();
                  const visible = rect.bottom >= 0 && rect.top <= window.innerHeight;
                  return !visible || img.complete;
                })
            """,
            timeout=2_500,
        )
    except Exception:
        pass
    await _wait_for_dom_quiet(page)
    try:
        await page.wait_for_timeout(150)
    except Exception:
        pass
    _raise_if_cancelled(cancel_event)


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
                  quietTimer = setTimeout(finish, 250);
                };
                const observer = new MutationObserver(reset);
                observer.observe(document.body, {
                  childList: true,
                  subtree: true,
                  attributes: true,
                  characterData: true
                });
                hardTimer = setTimeout(finish, 1_000);
                reset();
            })"""
        )
    except Exception:
        pass


async def _hide_obstructive_login_overlays(page: Any) -> None:
    """Hide only large login dialogs/masks when substantive content is behind them."""

    if not hasattr(page, "evaluate"):
        return
    try:
        await page.evaluate(
            """() => {
                const markers = [
                  '扫码登录', '账号登录', '手机号登录', '验证码登录',
                  '登录后免费', '登录后查看', 'sign in', 'log in'
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


async def _page_dimensions(
    page: Any,
    definition: Any = None,
) -> dict[str, int] | None:
    if not hasattr(page, "evaluate"):
        return None
    selectors = [
        selector
        for field in ("content_text", "title")
        for selector in (definition.selectors.get(field, ()) if definition else ())
    ]
    selectors.extend(("article", "main", "[role='main']"))
    selector_json = json.dumps(list(dict.fromkeys(selectors)), ensure_ascii=False)
    try:
        raw = await page.evaluate(
            f"""() => {{
                const selectors = {selector_json};
                const root = document.documentElement;
                const body = document.body;
                const viewportWidth = Math.max(
                    1,
                    window.innerWidth || 0,
                    root?.clientWidth || 0,
                    body?.clientWidth || 0
                );
                const documentWidth = Math.max(
                    viewportWidth,
                    root?.scrollWidth || 0,
                    root?.offsetWidth || 0,
                    body?.scrollWidth || 0,
                    body?.offsetWidth || 0
                );
                const height = Math.max(
                    1,
                    root?.scrollHeight || 0,
                    root?.offsetHeight || 0,
                    body?.scrollHeight || 0,
                    body?.offsetHeight || 0
                );
                let focusX = 0;
                for (const selector of selectors) {{
                  let elements = [];
                  try {{ elements = Array.from(document.querySelectorAll(selector)); }}
                  catch (_) {{ continue; }}
                  const candidate = elements.find(element => {{
                    const rect = element.getBoundingClientRect();
                    const style = getComputedStyle(element);
                    const text = (element.innerText || element.textContent || '').trim();
                    return style.display !== 'none'
                      && style.visibility !== 'hidden'
                      && rect.width >= 120
                      && rect.height >= 20
                      && text.length >= 2;
                  }});
                  if (candidate) {{
                    const rect = candidate.getBoundingClientRect();
                    focusX = Math.max(
                      0,
                      Math.min(documentWidth - viewportWidth, rect.left + scrollX - 48)
                    );
                    break;
                  }}
                }}
                return {{
                  viewportWidth,
                  documentWidth,
                  height,
                  focusX
                }};
            }}"""
        )
        viewport_width = min(32_767, max(1, int(raw["viewportWidth"])))
        document_width = min(32_767, max(viewport_width, int(raw["documentWidth"])))
        height = max(1, int(raw["height"]))
        max_focus_x = max(0, document_width - viewport_width)
        focus_x = min(max_focus_x, max(0, int(raw.get("focusX") or 0)))
        return {
            "viewport_width": viewport_width,
            "document_width": document_width,
            "height": height,
            "focus_x": focus_x,
        }
    except Exception:
        return None


def _is_visually_blank(path: Path) -> bool:
    try:
        with Image.open(path) as source:
            image = source.convert("L")
            minimum_tone, maximum_tone = image.getextrema()
            image.thumbnail((256, 256))
            standard_deviation = float(ImageStat.Stat(image).stddev[0])
            entropy = float(image.entropy())
    except (OSError, UnidentifiedImageError, ValueError) as error:
        raise PageScreenshotError(f"Screenshot file is not a readable image: {error}") from error
    return (
        standard_deviation < 5.0
        and entropy < 1.0
        and maximum_tone - minimum_tone < 32
    )


def _raise_if_cancelled(cancel_event: asyncio.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise asyncio.CancelledError
