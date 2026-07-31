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
from collections.abc import Callable
import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.config.settings import TaskConfig
from src.crawler.navigation import navigate_page, stabilize_rendered_page
from src.screenshot.browser_options import (
    STEALTH_SCRIPT_PATH,
    browser_context_options,
    browser_launch_options,
)
from src.screenshot.capture_session import GUEST_KEY, CaptureSession
from src.screenshot.image_checks import UnreadableImageError, is_visually_blank
from src.screenshot.page_layout import align_page_for_capture
from src.screenshot.region_capture_helpers import (
    _capture_name,
    _CaptureState,
    _clip_from_payload,
    _grab_full_screen,
    _hide_overlay,
    _reset_overlay,
    _reset_selection,
    _save_region,
    _selection_html,
)
from src.screenshot.region_capture_scripts import BINDING_NAME, OVERLAY_JS

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)

# Re-exported for the offline test-suite (tests import from this module).
__all__ = [
    "CAPTURE_TARGETS",
    "RegionCaptureResult",
    "RegionCaptureService",
    "_CaptureState",
    "_capture_name",
    "_clip_from_payload",
    "_save_region",
    "_selection_html",
]

CAPTURE_TARGETS = ("content", "author")
_ARM_DELAY_SECONDS = 0.35


@dataclass(frozen=True)
class RegionCaptureResult:
    status: str  # "saved" | "cancelled" | "error"
    name: str = ""
    message: str = ""


class RegionCaptureService:
    """Own one interactive capture flow and close its window on completion."""

    def __init__(
        self,
        config: TaskConfig,
        screen_grabber: Callable[[], Image.Image] | None = None,
        session: CaptureSession | None = None,
    ) -> None:
        self._config = config
        self._grabber = screen_grabber or _grab_full_screen
        self._session = session

    async def capture(
        self,
        url: str,
        *,
        evidence_id: int,
        target: str,
        storage_state: dict[str, Any] | None,
        assets_dir: Path,
        cancel_event: asyncio.Event | None = None,
        platform_key: str | None = None,
    ) -> RegionCaptureResult:
        if self._session is not None:
            return await self._capture_in_session(
                url,
                evidence_id=evidence_id,
                target=target,
                platform_key=platform_key,
                storage_state=storage_state,
                assets_dir=assets_dir,
                cancel_event=cancel_event,
            )
        return await self._capture_standalone(
            url,
            evidence_id=evidence_id,
            target=target,
            storage_state=storage_state,
            assets_dir=assets_dir,
            cancel_event=cancel_event,
        )

    async def _capture_in_session(
        self,
        url: str,
        *,
        evidence_id: int,
        target: str,
        platform_key: str | None,
        storage_state: dict[str, Any] | None,
        assets_dir: Path,
        cancel_event: asyncio.Event | None,
    ) -> RegionCaptureResult:
        """Run the capture and persist its login state before closing."""

        session = self._session
        assert session is not None
        key = platform_key or GUEST_KEY
        context = await session.context_for(key, storage_state)
        page = await session.browse_page_for(key, context)

        state = _CaptureState(context=context, browse_page=page)
        loop = asyncio.get_running_loop()
        done: asyncio.Future[RegionCaptureResult] = loop.create_future()

        def _finish(result: RegionCaptureResult) -> None:
            if not done.done():
                done.set_result(result)

        async def _on_binding(source: dict[str, Any], payload: Any) -> None:
            if done.done():
                return
            source_page = source.get("page")
            if source_page is not None and source_page is not state.select_page:
                # The toolbar the user actually clicked lives on the current
                # front page; follow it across SPA/full navigations.
                state.browse_page = source_page
            await self._handle_action(
                source_page,
                payload,
                evidence_id=evidence_id,
                target=target,
                assets_dir=Path(assets_dir),
                state=state,
                finish=_finish,
            )

        def _on_context_page(new_page: Any) -> None:
            if not state.select_pending and new_page is not state.select_page:
                state.browse_page = new_page

        def _on_browse_close(*_args: Any) -> None:
            _finish(RegionCaptureResult(status="cancelled"))

        session.set_binding_handler(_on_binding)
        context.on("page", _on_context_page)
        page.on("close", _on_browse_close)
        try:
            try:
                await navigate_page(
                    page,
                    url,
                    self._config.page_timeout_seconds * 1000,
                    cancel_event,
                )
                await stabilize_rendered_page(
                    page,
                    self._config.page_stabilize_milliseconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # 导航失败不致命：窗口保持打开，用户可自行跳转/登录后再框选。
                logger.warning(
                    "Region capture navigation failed, window left open: %s", error
                )
            return await self._wait_for_result(done, cancel_event)
        finally:
            try:
                context.remove_listener("page", _on_context_page)
                page.remove_listener("close", _on_browse_close)
            except Exception:  # noqa: BLE001 — 页面/上下文可能已关闭
                pass
            session.set_binding_handler(None)
            # Saving a selection is the terminal action. Close every page,
            # context and the owned browser so the UI cannot remain stuck
            # behind a visible capture window. ``close`` persists VALID
            # platform state first, so later captures/crawls still reuse it.
            await session.close()

    async def _capture_standalone(
        self,
        url: str,
        *,
        evidence_id: int,
        target: str,
        storage_state: dict[str, Any] | None,
        assets_dir: Path,
        cancel_event: asyncio.Event | None,
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
                source_page = source.get("page")
                if source_page is not None and source_page is not state.select_page:
                    state.browse_page = source_page
                await self._handle_action(
                    source_page,
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
        # Some sites expand the document with off-screen placeholders and
        # render the real article/profile mostly beyond the right edge. Align
        # the live page immediately before the frozen OS screenshot so this
        # cross-site layout defect cannot leak into manual evidence captures.
        await align_page_for_capture(state.browse_page)
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
            state.select_pending = True
            try:
                select_page = await state.context.new_page()
            finally:
                state.select_pending = False
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


