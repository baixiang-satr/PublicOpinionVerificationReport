"""Interactive full-screen region screenshots for the review workspace.

Opens a maximized, headed Chromium window at the record's target URL (with
the platform's saved login state when available) plus a small floating
toolbar.  When the user clicks 「开始框选」 the *whole screen* — browser
chrome and address bar included — is frozen into an OS-level screenshot and
shown in a selection tab; the user drags a rubber band over the frozen image
and the chosen region is saved into the job's manual assets directory with a
standardized name.  Because pixels come from the screen itself rather than
from the page, capturing never scrolls or shifts the page.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, replace
import io
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from src.config.settings import TaskConfig
from src.crawler.navigation import navigate_page, stabilize_rendered_page
from src.screenshot.browser_options import (
    STEALTH_SCRIPT_PATH,
    browser_context_options,
    browser_launch_options,
)
from src.screenshot.image_checks import UnreadableImageError, is_visually_blank
from src.utils.file_utils import require_safe_file_name

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)

BINDING_NAME = "__poirRegionCapture"
CAPTURE_TARGETS = ("content", "author")
_MIN_REGION_PX = 8
_ARM_DELAY_SECONDS = 0.35


@dataclass(frozen=True)
class RegionCaptureResult:
    status: str  # "saved" | "cancelled" | "error"
    name: str = ""
    message: str = ""


@dataclass
class _CaptureState:
    """Mutable per-window state shared between binding callbacks."""

    context: Any = None
    browse_page: Any = None
    select_page: Any = None
    image: Any = None  # PIL.Image of the frozen full screen


class RegionCaptureService:
    """Owns one interactive capture browser until the user finishes."""

    def __init__(
        self,
        config: TaskConfig,
        screen_grabber: Callable[[], "Image.Image"] | None = None,
    ) -> None:
        self._config = config
        self._grabber = screen_grabber or _grab_full_screen

    async def capture(
        self,
        url: str,
        *,
        evidence_id: int,
        target: str,
        storage_state: dict[str, Any] | None,
        assets_dir: Path,
        cancel_event: asyncio.Event | None = None,
    ) -> RegionCaptureResult:
        from playwright.async_api import async_playwright

        config = replace(self._config, headless=False)
        launch_options = browser_launch_options(config)
        launch_options["args"] = [*launch_options.get("args", ()), "--start-maximized"]
        context_options = browser_context_options(config, storage_state)
        # The page must fill the whole maximized window, not a fixed viewport.
        context_options.pop("viewport", None)
        context_options.pop("device_scale_factor", None)
        context_options["no_viewport"] = True

        playwright = await async_playwright().start()
        browser = None
        try:
            browser = await playwright.chromium.launch(**launch_options)
            context = await browser.new_context(**context_options)
            if config.enable_stealth and STEALTH_SCRIPT_PATH.is_file():
                await context.add_init_script(path=str(STEALTH_SCRIPT_PATH))
            await context.add_init_script(script=OVERLAY_JS)

            state = _CaptureState(context=context)
            loop = asyncio.get_running_loop()
            done: asyncio.Future[RegionCaptureResult] = loop.create_future()

            def _finish(result: RegionCaptureResult) -> None:
                if not done.done():
                    done.set_result(result)

            async def _on_binding(source: dict[str, Any], payload: Any) -> None:
                if done.done():
                    return
                await self._handle_action(
                    source.get("page"),
                    payload,
                    evidence_id=evidence_id,
                    target=target,
                    assets_dir=Path(assets_dir),
                    state=state,
                    finish=_finish,
                )

            await context.expose_binding(BINDING_NAME, _on_binding)
            page = await context.new_page()
            state.browse_page = page
            page.on("close", lambda *_: _finish(RegionCaptureResult(status="cancelled")))
            try:
                await navigate_page(
                    page,
                    url,
                    config.page_timeout_seconds * 1000,
                    cancel_event,
                )
                await stabilize_rendered_page(page, config.page_stabilize_milliseconds)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # 导航失败不致命：窗口保持打开，用户可自行跳转/登录后再框选。
                logger.warning(
                    "Region capture navigation failed, window left open: %s", error
                )
            return await self._wait_for_result(done, cancel_event)
        finally:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:  # noqa: BLE001 — 关闭阶段尽力而为
                    pass
            await playwright.stop()

    async def _wait_for_result(
        self,
        done: asyncio.Future[RegionCaptureResult],
        cancel_event: asyncio.Event | None,
    ) -> RegionCaptureResult:
        if cancel_event is None:
            return await done
        cancel_waiter = asyncio.ensure_future(cancel_event.wait())
        try:
            await asyncio.wait(
                {asyncio.ensure_future(done), cancel_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if done.done():
                return done.result()
            return RegionCaptureResult(status="cancelled")
        finally:
            cancel_waiter.cancel()
            await asyncio.gather(cancel_waiter, return_exceptions=True)

    async def _handle_action(
        self,
        page: Any,
        payload: Any,
        *,
        evidence_id: int,
        target: str,
        assets_dir: Path,
        state: _CaptureState,
        finish: Callable[[RegionCaptureResult], None],
    ) -> None:
        try:
            data = json.loads(payload) if isinstance(payload, str) else dict(payload)
        except (TypeError, ValueError):
            return
        action = data.get("action")
        if action == "cancel":
            finish(RegionCaptureResult(status="cancelled"))
            return
        if action == "arm":
            await self._arm(state)
            return
        if action == "abort":
            await self._abort_selection(state)
            return
        if action != "confirm":
            return
        clip = _clip_from_payload(data)
        if clip is None or state.image is None:
            await _reset_selection(state, "选区太小或无效，请重新框选。")
            return
        name = _capture_name(evidence_id, target, self._config.screenshot_format)
        assets_dir.mkdir(parents=True, exist_ok=True)
        output = assets_dir / name
        try:
            _save_region(self._config, state.image, clip, output)
        except Exception as error:  # noqa: BLE001 — 统一回吐给 UI
            output.unlink(missing_ok=True)
            finish(
                RegionCaptureResult(
                    status="error",
                    message=f"截图失败：{type(error).__name__}: {error}",
                )
            )
            return
        try:
            blank = is_visually_blank(output)
        except UnreadableImageError:
            blank = True
        if blank:
            output.unlink(missing_ok=True)
            await _reset_selection(state, "截到的区域是空白，请重新框选。")
            return
        finish(RegionCaptureResult(status="saved", name=name))

    async def _arm(self, state: _CaptureState) -> None:
        """Freeze the whole screen and open the rubber-band selection tab."""

        if state.select_page is not None or state.browse_page is None:
            return
        await _hide_overlay(state.browse_page)
        await asyncio.sleep(_ARM_DELAY_SECONDS)
        loop = asyncio.get_running_loop()
        try:
            image = await loop.run_in_executor(None, self._grabber)
        except Exception as error:  # noqa: BLE001 — 截屏失败允许重试
            logger.warning("Full-screen grab failed: %s", error)
            await _reset_overlay(
                state.browse_page,
                f"无法截取屏幕：{type(error).__name__}，请点「开始框选」重试。",
            )
            return
        state.image = image
        try:
            select_page = await state.context.new_page()
            state.select_page = select_page
            select_page.on(
                "close",
                lambda *_: asyncio.ensure_future(self._abort_selection(state)),
            )
            await select_page.set_content(_selection_html(image))
        except Exception as error:  # noqa: BLE001 — 打开选区页失败回退浏览态
            logger.warning("Open selection page failed: %s", error)
            state.image = None
            state.select_page = None
            await _reset_overlay(
                state.browse_page,
                f"无法打开框选页面：{type(error).__name__}，请点「开始框选」重试。",
            )

    async def _abort_selection(self, state: _CaptureState) -> None:
        """Close the selection tab and return the browse window to browsing."""

        page = state.select_page
        state.select_page = None
        state.image = None
        if page is not None:
            try:
                await page.close()
            except Exception:  # noqa: BLE001 — 页面可能已关闭
                pass
        await _reset_overlay(state.browse_page, None)


async def _hide_overlay(page: Any) -> None:
    if page is None:
        return
    try:
        await page.evaluate(
            "() => window.__poirRegionCaptureHide && window.__poirRegionCaptureHide()"
        )
    except Exception:  # noqa: BLE001 — 页面可能已关闭
        pass


async def _reset_overlay(page: Any, message: str | None) -> None:
    if page is None:
        return
    try:
        await page.evaluate(
            "(msg) => window.__poirRegionCaptureReset && window.__poirRegionCaptureReset(msg)",
            message or "",
        )
    except Exception:  # noqa: BLE001 — 页面可能已关闭
        pass


async def _reset_selection(state: _CaptureState, message: str) -> None:
    page = state.select_page
    if page is None:
        return
    try:
        await page.evaluate(
            "(msg) => window.__poirSelectReset && window.__poirSelectReset(msg)",
            message,
        )
    except Exception:  # noqa: BLE001 — 页面可能已关闭
        pass


def _clip_from_payload(data: dict[str, Any]) -> dict[str, int] | None:
    """Validate the JS-reported document-space rect into a Playwright clip."""

    try:
        x = float(data["x"])
        y = float(data["y"])
        width = float(data["width"])
        height = float(data["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if width < _MIN_REGION_PX or height < _MIN_REGION_PX:
        return None
    return {
        "x": max(0, int(x)),
        "y": max(0, int(y)),
        "width": min(32_767, int(width)),
        "height": min(32_767, int(height)),
    }


def _capture_name(evidence_id: int, target: str, screenshot_format: str) -> str:
    """标准化命名：``001_content.jpg`` / ``001_author.jpg``。

    同一记录同一槽位重新截取时直接覆盖同名文件，避免 manual_assets 里
    堆积失效的旧截图；命名刻意避开审计正则 ``\\d{3}主页``。
    """

    extension = "jpg" if screenshot_format == "jpeg" else "png"
    return require_safe_file_name(f"{evidence_id:03d}_{target}.{extension}")


def _grab_full_screen() -> "Image.Image":
    """OS-level screenshot of the whole virtual screen (all monitors)."""

    from PIL import ImageGrab

    return ImageGrab.grab(all_screens=True)


def _save_region(
    config: TaskConfig,
    image: "Image.Image",
    clip: dict[str, int],
    output: Path,
) -> None:
    """Crop the confirmed region out of the frozen screen image."""

    box = (
        clip["x"],
        clip["y"],
        clip["x"] + clip["width"],
        clip["y"] + clip["height"],
    )
    region = image.crop(box)
    if config.screenshot_format == "jpeg":
        region.convert("RGB").save(
            str(output), format="JPEG", quality=config.screenshot_jpeg_quality
        )
    else:
        region.save(str(output), format="PNG")


def _selection_html(image: "Image.Image") -> str:
    """Selection-tab document: frozen screen + rubber band, image-space coords."""

    width, height = image.size
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=82)
    data_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    return (
        SELECTION_HTML.replace("__IMG_SRC__", data_url)
        .replace("__IMG_W__", str(width))
        .replace("__IMG_H__", str(height))
    )


OVERLAY_JS = r"""
(() => {
  if (window.__poirShotInit) return;
  window.__poirShotInit = true;
  const BINDING = '__poirRegionCapture';
  const send = (payload) => {
    try {
      if (typeof window[BINDING] === 'function') window[BINDING](JSON.stringify(payload));
    } catch (err) { /* binding unavailable */ }
  };

  let mode = 'boot';
  let statusEl, startBtn, cancelBtn, hitLayer, selBox, actionBar;
  let dragging = false;
  let startX = 0, startY = 0;
  let viewRect = null;
  let docRect = null;

  const TEXT_BROWSE = '截图模式：可正常浏览页面，找到目标内容后点「开始框选」';
  const TEXT_SELECT = '按住鼠标左键拖拽框选截图区域，ESC 返回浏览';
  const TEXT_SAVING = '正在保存截图…';
  const TEXT_CLOSING = '正在关闭…';

  function setStatus(text) { if (statusEl) statusEl.textContent = text; }

  function enterBrowse() {
    if (!hitLayer) return;
    mode = 'browse';
    docRect = null; viewRect = null; dragging = false;
    hitLayer.style.display = 'none';
    selBox.style.display = 'none';
    actionBar.style.display = 'none';
    startBtn.disabled = false;
    setStatus(TEXT_BROWSE);
  }

  function enterSelect() {
    if (!hitLayer) return;
    mode = 'select';
    docRect = null; viewRect = null; dragging = false;
    selBox.style.display = 'none';
    actionBar.style.display = 'none';
    hitLayer.style.display = 'block';
    setStatus(TEXT_SELECT);
  }

  function updateBox(x, y, w, h) {
    viewRect = { x, y, w, h };
    selBox.style.display = 'block';
    selBox.style.left = x + 'px';
    selBox.style.top = y + 'px';
    selBox.style.width = w + 'px';
    selBox.style.height = h + 'px';
  }

  function showActions() {
    const pad = 8;
    let left = viewRect.x + viewRect.w;
    let top = viewRect.y + viewRect.h + pad;
    actionBar.style.display = 'flex';
    const bw = actionBar.offsetWidth || 180;
    const bh = actionBar.offsetHeight || 36;
    if (left + bw > window.innerWidth - 4) left = Math.max(4, viewRect.x + viewRect.w - bw);
    if (top + bh > window.innerHeight - 4) top = Math.max(4, viewRect.y - bh - pad);
    actionBar.style.left = left + 'px';
    actionBar.style.top = top + 'px';
  }

  function boot() {
    if (!document.documentElement || document.getElementById('__poir-shot-host')) return;
    const host = document.createElement('div');
    host.id = '__poir-shot-host';
    host.style.cssText = 'position:fixed;left:0;top:0;width:0;height:0;z-index:2147483647;';
    (document.body || document.documentElement).appendChild(host);
    const root = host.attachShadow({ mode: 'open' });
    root.innerHTML = [
      '<style>',
      ':host{all:initial}',
      '.tb{position:fixed;top:12px;right:12px;display:flex;align-items:center;gap:8px;',
      'background:rgba(32,33,36,.94);color:#fff;border-radius:8px;padding:8px 12px;',
      'font:13px/1.4 "Microsoft YaHei",system-ui,sans-serif;',
      'box-shadow:0 4px 16px rgba(0,0,0,.35);pointer-events:auto;',
      'user-select:none;max-width:72vw}',
      '.tb button{border:0;border-radius:5px;padding:5px 12px;font-size:13px;',
      'cursor:pointer;color:#fff}',
      '.start{background:#2f7cf6}.quit{background:#6b7280}',
      '.tb button:disabled{opacity:.5;cursor:default}',
      '.hit{position:fixed;left:0;top:0;right:0;bottom:0;cursor:crosshair;',
      'pointer-events:auto;display:none}',
      '.box{position:fixed;border:2px solid #2f7cf6;',
      'box-shadow:0 0 0 9999px rgba(0,0,0,.45);pointer-events:none;display:none;',
      'box-sizing:border-box}',
      '.act{position:fixed;display:none;gap:6px;pointer-events:auto;',
      'font:13px/1 "Microsoft YaHei",system-ui,sans-serif}',
      '.act button{border:0;border-radius:5px;padding:7px 12px;font-size:13px;',
      'cursor:pointer;color:#fff}',
      '.save{background:#2f9e63}.re{background:#6b7280}',
      '</style>',
      '<div class="tb"><span class="st"></span>',
      '<button type="button" class="start">开始框选</button>',
      '<button type="button" class="quit">取消截图</button></div>',
      '<div class="hit"></div>',
      '<div class="box"></div>',
      '<div class="act">',
      '<button type="button" class="save">✓ 保存</button>',
      '<button type="button" class="re">↺ 重选</button></div>'
    ].join('');
    statusEl = root.querySelector('.st');
    startBtn = root.querySelector('.start');
    cancelBtn = root.querySelector('.quit');
    hitLayer = root.querySelector('.hit');
    selBox = root.querySelector('.box');
    actionBar = root.querySelector('.act');

    startBtn.addEventListener('click', () => enterSelect());
    cancelBtn.addEventListener('click', () => {
      if (mode === 'saving' || mode === 'closing') return;
      mode = 'closing';
      startBtn.disabled = true;
      cancelBtn.disabled = true;
      setStatus(TEXT_CLOSING);
      send({ action: 'cancel' });
    });
    root.querySelector('.re').addEventListener('click', () => enterSelect());
    root.querySelector('.save').addEventListener('click', () => {
      if (!docRect || mode !== 'confirm') return;
      mode = 'saving';
      actionBar.style.display = 'none';
      setStatus(TEXT_SAVING);
      send({ action: 'confirm', x: docRect.x, y: docRect.y, width: docRect.w, height: docRect.h });
    });
    hitLayer.addEventListener('pointerdown', (e) => {
      if (mode !== 'select' || e.button !== 0) return;
      dragging = true;
      startX = e.clientX;
      startY = e.clientY;
      try { hitLayer.setPointerCapture(e.pointerId); } catch (err) { /* noop */ }
      updateBox(startX, startY, 0, 0);
      e.preventDefault();
    });
    hitLayer.addEventListener('pointermove', (e) => {
      if (!dragging || mode !== 'select') return;
      const x = Math.min(startX, e.clientX);
      const y = Math.min(startY, e.clientY);
      const w = Math.abs(e.clientX - startX);
      const h = Math.abs(e.clientY - startY);
      updateBox(x, y, w, h);
    });
    hitLayer.addEventListener('pointerup', () => {
      if (!dragging || mode !== 'select') return;
      dragging = false;
      if (viewRect && viewRect.w >= 8 && viewRect.h >= 8) {
        docRect = {
          x: viewRect.x + window.scrollX,
          y: viewRect.y + window.scrollY,
          w: viewRect.w,
          h: viewRect.h
        };
        mode = 'confirm';
        showActions();
      } else {
        selBox.style.display = 'none';
        viewRect = null;
      }
    });
    hitLayer.addEventListener('wheel', (e) => e.preventDefault(), { passive: false });
    hitLayer.addEventListener('contextmenu', (e) => e.preventDefault());
    window.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      if (mode === 'select' || mode === 'confirm') {
        enterBrowse();
        e.preventDefault();
      }
    }, true);
    enterBrowse();
  }

  window.__poirRegionCaptureHide = () => {
    const host = document.getElementById('__poir-shot-host');
    if (host) host.style.visibility = 'hidden';
  };
  window.__poirRegionCaptureReset = (message) => {
    const host = document.getElementById('__poir-shot-host');
    if (host) host.style.visibility = 'visible';
    if (typeof message === 'string' && message) setStatus(message);
    if (mode === 'saving') enterSelect();
  };

  const tryBoot = () => {
    try { boot(); } catch (err) { /* 浮层尽力而为，不影响页面本身 */ }
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', tryBoot, { once: true });
  } else {
    tryBoot();
  }
})();
"""
