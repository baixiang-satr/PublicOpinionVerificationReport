"""URL 有效性复验任务：复用登录态与浏览器逐条探测会话记录的 URL。

只探测有效性（不重新截图、不改写已填字段）：HTTP 状态码 + 页面内容屏障
经 ``classify_barrier`` 归并为 有效/已失效/存疑 三态，逐条持久化到任务目
录 ``url_recheck.json`` 并推送 ``url_recheck`` 事件；限速语义与抓取一致
（按主机间隔），可随时取消。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from src.auth.registry import auth_policy_for_url
from src.screenshot.browser_options import (
    browser_context_options,
    browser_launch_options,
)
from src.services.review_session import ReviewSession
from src.services.url_recheck import RecheckStatus, UrlRecheckStore, classify_barrier
from src.tools.page_access import inspect_http_response, inspect_page_access
from src.webui.runner import AsyncThreadJob, EventSink

logger = logging.getLogger(__name__)

_STEALTH_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "libs" / "stealth.min.js"


class RecheckRunner(AsyncThreadJob):
    """在专属线程 + 独立 asyncio 循环中运行的 URL 复验任务。"""

    def __init__(self, config_getter: Callable[[], Any], sink: EventSink) -> None:
        super().__init__(sink)
        self._config_getter = config_getter

    def start(self, session: ReviewSession, auth_store: Any) -> tuple[bool, str]:
        if self.is_running():
            return False, "复验任务正在运行。"
        targets = [
            (record.task.evidence_id, url)
            for record in session.records()
            if (
                url := (record.page.final_url or record.task.original_url or "").strip()
            ).startswith(("http://", "https://"))
        ]
        if not targets:
            return False, "当前任务没有可复验的 URL。"
        self._spawn(self._run(Path(session.job_dir), targets, auth_store))
        return True, ""

    async def _run(
        self,
        job_dir: Path,
        targets: list[tuple[int, str]],
        auth_store: Any,
    ) -> None:
        cancel_event = asyncio.Event()
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._asyncio_cancel = cancel_event
            # 注册任务句柄，cancel() 可立即中断卡住的 Playwright 调用。
            self._task = asyncio.current_task()
        config = self._config_getter().task
        store = UrlRecheckStore(job_dir)
        total = len(targets)
        done = 0
        cancelled = False
        playwright = None
        browser = None
        try:
            from playwright.async_api import async_playwright

            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(**browser_launch_options(config))
            last_visit: dict[str, float] = {}
            loop = asyncio.get_running_loop()
            for evidence_id, url in targets:
                if cancel_event.is_set():
                    cancelled = True
                    break
                host = urlsplit(url).hostname or ""
                interval = max(0.0, float(config.min_host_interval_seconds))
                elapsed = loop.time() - last_visit.get(host, loop.time() - interval)
                if elapsed < interval:
                    await asyncio.sleep(interval - elapsed)
                last_visit[host] = loop.time()
                status, code, message = await self._probe(browser, config, auth_store, url)
                entry = store.set(evidence_id, status, code, message)
                done += 1
                payload: dict[str, Any] = entry.to_dict()
                payload.update({"eid": evidence_id, "done": done, "total": total})
                self._sink.emit("url_recheck", payload)
        except asyncio.CancelledError:
            cancelled = True
        finally:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:  # noqa: BLE001 - 关闭失败不影响复验结果
                    pass
            if playwright is not None:
                try:
                    await playwright.stop()
                except Exception:  # noqa: BLE001
                    pass
            self._sink.emit(
                "url_recheck_done",
                {"cancelled": cancelled, "done": done, "total": total},
            )
            self._sink.emit("session", {})

    async def _probe(
        self,
        browser: Any,
        config: Any,
        auth_store: Any,
        url: str,
    ) -> tuple[RecheckStatus, str, str]:
        policy = auth_policy_for_url(url)
        state = None
        if policy is not None and auth_store is not None:
            try:
                state = auth_store.load_state(policy.platform_key)
            except Exception:  # noqa: BLE001 - 登录态不可用时按游客探测
                state = None
        context = None
        try:
            context = await browser.new_context(**browser_context_options(config, state))
            if config.enable_stealth and _STEALTH_SCRIPT_PATH.is_file():
                await context.add_init_script(path=str(_STEALTH_SCRIPT_PATH))
            page = await context.new_page()
            try:
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=int(config.page_timeout_seconds * 1000),
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - 超时/网络错误一律存疑
                return (
                    RecheckStatus.UNCERTAIN,
                    "NAVIGATION_FAILED",
                    f"页面访问失败（{type(error).__name__}），无法确认是否失效。",
                )
            if config.page_stabilize_milliseconds > 0:
                await page.wait_for_timeout(int(config.page_stabilize_milliseconds))
            barrier = inspect_http_response(
                int(response.status) if response is not None else None
            )
            if barrier is None:
                barrier = await inspect_page_access(page, str(page.url), url)
            if barrier is None:
                return RecheckStatus.VALID, "", ""
            return classify_barrier(barrier), barrier.code, barrier.message
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - 单条失败不中断整批复验
            logger.warning("URL recheck probe failed for %s: %s", url, error)
            return (
                RecheckStatus.UNCERTAIN,
                "RECHECK_ERROR",
                f"复验异常（{type(error).__name__}），无法确认是否失效。",
            )
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:  # noqa: BLE001
                    pass


__all__ = ["RecheckRunner"]
