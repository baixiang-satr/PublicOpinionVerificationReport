"""Stable evidence-ID page screenshots written directly to the staging root."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

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
        is_long_page = False
        if self._config.full_page_screenshot:
            dimensions = await _page_dimensions(page)
            if (
                dimensions is not None
                and dimensions["height"] > self._config.max_full_page_screenshot_height
            ):
                is_long_page = True
                options["full_page"] = False
                options["clip"] = {
                    "x": 0,
                    "y": 0,
                    "width": dimensions["width"],
                    "height": self._config.max_full_page_screenshot_height,
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


async def _page_dimensions(page: Any) -> dict[str, int] | None:
    if not hasattr(page, "evaluate"):
        return None
    try:
        raw = await page.evaluate(
            """() => {
                const root = document.documentElement;
                const body = document.body;
                return {
                  width: Math.max(
                    1,
                    root?.clientWidth || 0,
                    body?.clientWidth || 0
                  ),
                  height: Math.max(
                    1,
                    root?.scrollHeight || 0,
                    root?.offsetHeight || 0,
                    body?.scrollHeight || 0,
                    body?.offsetHeight || 0
                  )
                };
            }"""
        )
        width = min(32_767, max(1, int(raw["width"])))
        height = max(1, int(raw["height"]))
        return {"width": width, "height": height}
    except Exception:
        return None


def _raise_if_cancelled(cancel_event: asyncio.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise asyncio.CancelledError
