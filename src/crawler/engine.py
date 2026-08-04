"""Bounded, cancellable browser crawl engine producing runtime RecordResult objects."""
from __future__ import annotations
import asyncio
from collections.abc import Callable
from datetime import datetime
import logging
from pathlib import Path
import random
from typing import Any
from src.auth.store import AuthProfileStore
from src.crawler.authenticated_access import guest_ui_error
from src.config.settings import TaskConfig
from src.crawler.auth_preflight import preflight_or_empty
from src.crawler.author_extractor import AuthorExtractor
from src.crawler.content_parser import ContentParser
from src.crawler.crawl_navigation import CrawlFailure, navigate_with_fallback
from src.crawler.field_quality import missing_required_fields
from src.crawler.ocr_pipeline import OcrPipeline
from src.crawler.optional_assets import collect_optional_assets
from src.crawler.platform_router import PlatformRouter
from src.crawler.platform_scheduler import PlatformTaskScheduler
from src.crawler.rate_limiter import HostRateLimiter, wait_with_cancellation
from src.crawler.relogin import ReloginHandler, heal_or_relogin, relogin_after_auth_failure
from src.domain.models import (
    PageData,
    RecordResult,
    RecordStatus,
    TaskError,
    TaskEvent,
    UrlTask,
)
from src.screenshot.asset_collector import AssetCollector
from src.screenshot.author_shooter import AuthorShooter
from src.screenshot.browser import BrowserPool
from src.screenshot.page_shooter import PageShooter, PageScreenshotError
from src.utils.time_utils import DEFAULT_TIMEZONE

logger = logging.getLogger(__name__)
class CrawlEngine:
    def __init__(
        self,
        config: TaskConfig,
        *,
        browser_pool: BrowserPool | None = None,
        parser: ContentParser | None = None,
        router: PlatformRouter | None = None,
        shooter: PageShooter | None = None,
        author_shooter: AuthorShooter | None = None,
        asset_collector: AssetCollector | None = None,
        ocr_pipeline: OcrPipeline | None = None,
        auth_store: AuthProfileStore | None = None,
        relogin_handler: ReloginHandler | None = None,
    ) -> None:
        self._config = config
        self._relogin_handler = relogin_handler
        self._auth_store = auth_store
        if self._auth_store is None and config.auth_store_dir is not None:
            self._auth_store = AuthProfileStore(config.auth_store_dir)
        self._browser_pool = browser_pool or BrowserPool(config, auth_store=self._auth_store)
        self._parser = parser or ContentParser(config.summary_max_chars)
        self._router = router or PlatformRouter()
        self._shooter = shooter or PageShooter(config)
        self._author_shooter = author_shooter or AuthorShooter(config)
        self._asset_collector = asset_collector or AssetCollector(config)
        self._ocr_pipeline = ocr_pipeline or OcrPipeline(config)
        self._author = AuthorExtractor(config.allow_nickname_as_id)
        self._rate_limiter = HostRateLimiter(config.min_host_interval_seconds)
        self._scheduler = PlatformTaskScheduler(
            config,
            self._router,
            self._auth_store,
        )

    async def run(
        self,
        tasks: list[UrlTask],
        output_dir: Path,
        on_event: Callable[[TaskEvent], None] | None = None,
        on_result: Callable[[RecordResult], None] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> list[RecordResult]:
        if not tasks:
            return []
        cancellation = cancel_event or asyncio.Event()
        queues = self._scheduler.queues(tasks)
        await self._browser_pool.start()
        try:
            auth_preflight = await preflight_or_empty(
                self._config, self._browser_pool, self._auth_store,
                queues, on_event, self._emit, cancellation,
            )
            jobs = [
                asyncio.create_task(
                    self._process_platform_queue(
                        queue,
                        Path(output_dir),
                        on_event,
                        on_result,
                        cancellation,
                        auth_preflight,
                    ),
                    name=f"crawl-platform-{queue_key}",
                )
                for queue_key, queue in queues.items()
            ]
            grouped_results = await asyncio.gather(*jobs)
            return sorted(
                (
                    result
                    for group in grouped_results
                    for result in group
                ),
                key=lambda result: result.task.evidence_id,
            )
        finally:
            await self._ocr_pipeline.close()
            if cancellation.is_set() and hasattr(
                self._browser_pool,
                "close_for_cancellation",
            ):
                await self._browser_pool.close_for_cancellation()
            else:
                await self._browser_pool.close()

    async def _process_platform_queue(
        self,
        tasks: list[UrlTask],
        output_dir: Path,
        on_event: Callable[[TaskEvent], None] | None,
        on_result: Callable[[RecordResult], None] | None,
        cancel_event: asyncio.Event,
        auth_preflight: dict[str, bool],
    ) -> list[RecordResult]:
        results: list[RecordResult] = []
        known_block = self._scheduler.known_auth_block(tasks[0])
        if known_block is not None:
            # A stored EXPIRED marker may be stale; give the preserved state
            # one probe, then offer an interactive re-login before pausing.
            blocked_platform = self._scheduler.blocked_auth_platform(tasks[0])
            if blocked_platform is not None and await heal_or_relogin(
                self._relogin_handler, self._browser_pool, blocked_platform,
                auth_preflight, cancel_event,
            ):
                logger.info(
                    "Auth profile for %s restored before crawl; platform not paused.",
                    blocked_platform,
                )
                known_block = None
        if known_block is not None:
            for task in tasks:
                results.append(
                    self._publish_paused_result(
                        task,
                        known_block,
                        on_event,
                        on_result,
                    )
                )
            return results

        paused_by: RecordResult | None = None
        for task in tasks:
            if cancel_event.is_set():
                results.append(
                    self._publish_synthetic_result(
                        self._scheduler.cancelled_result(task),
                        on_event,
                        on_result,
                    )
                )
                continue
            if paused_by is not None:
                result = self._publish_paused_result(
                    task,
                    (
                        f"同平台记录 #{paused_by.task.evidence_id:03d} 检测到登录或验证屏障；"
                        "已暂停该平台剩余 URL，请在“管理平台登录态”中复验后重试。"
                    ),
                    on_event,
                    on_result,
                )
            else:
                result = await self._process(
                    task,
                    output_dir,
                    on_event,
                    on_result,
                    cancel_event,
                )
                if await relogin_after_auth_failure(
                    self._relogin_handler, task, result, cancel_event
                ):
                    result = await self._process(task, output_dir, on_event, on_result, cancel_event)
                if self._scheduler.should_pause_after(result):
                    paused_by = result
            results.append(result)
        return results

    def _publish_synthetic_result(
        self,
        result: RecordResult,
        on_event: Callable[[TaskEvent], None] | None,
        on_result: Callable[[RecordResult], None] | None,
    ) -> RecordResult:
        now = _now()
        result.started_at = now
        result.finished_at = now
        self._emit(result, "finish", result.status.value, on_event)
        if on_result is not None:
            try:
                on_result(result)
            except Exception:
                pass
        return result

    def _publish_paused_result(
        self,
        task: UrlTask,
        message: str,
        on_event: Callable[[TaskEvent], None] | None,
        on_result: Callable[[RecordResult], None] | None,
    ) -> RecordResult:
        return self._publish_synthetic_result(
            self._scheduler.auth_paused_result(task, message),
            on_event,
            on_result,
        )

    async def _process(
        self,
        task: UrlTask,
        output_dir: Path,
        on_event: Callable[[TaskEvent], None] | None,
        on_result: Callable[[RecordResult], None] | None,
        cancel_event: asyncio.Event,
    ) -> RecordResult:
        result = RecordResult(task=task, status=RecordStatus.RUNNING, started_at=_now())
        self._emit(result, "start", "开始访问页面", on_event)
        try:
            for attempt in range(self._config.max_retries + 1):
                result.attempt_count = attempt + 1
                try:
                    await self._crawl_attempt(result, output_dir, cancel_event)
                    break
                except CrawlFailure as failure:
                    result.errors.append(failure.error)
                    if failure.error.retryable and attempt < self._config.max_retries:
                        self._emit(result, "retry", failure.error.message, on_event)
                        await self._backoff(attempt, cancel_event)
                        continue
                    result.status = failure.status
                    break
        except asyncio.CancelledError:
            result.status = RecordStatus.CANCELLED
            result.errors.append(TaskError("crawl", "CANCELLED", "任务已取消", retryable=False))
        except Exception as error:
            result.add_error(TaskError("crawl", "UNEXPECTED", str(error), retryable=False))
        finally:
            result.finished_at = _now()
            self._emit(result, "finish", result.status.value, on_event)
            if on_result is not None:
                try:
                    on_result(result)
                except Exception:
                    pass
        return result

    async def _crawl_attempt(
        self,
        result: RecordResult,
        output_dir: Path,
        cancel_event: asyncio.Event,
    ) -> None:
        _raise_if_cancelled(cancel_event)
        # Jitter avoids same-host requests starting at the same instant.
        await wait_with_cancellation(random.uniform(0.3, 1.0), cancel_event)
        await self._rate_limiter.wait(result.task.normalized_url, cancel_event)
        manual_definition = self._router.definition_for(result.task.normalized_url)
        if manual_definition is not None:
            # Preserve a truthful worksheet route even when navigation,
            # captcha handling or parsing fails before the normal route step.
            result.route = self._router.route(
                result.task.normalized_url,
                result.page,
            )
        if manual_definition is not None and manual_definition.manual_only:
            raise CrawlFailure(
                TaskError(
                    "access",
                    "MANUAL_ONLY_PLATFORM",
                    (
                        f"{manual_definition.platform_value} 的网页端无法稳定抓取。"
                        "请在「采集与补录」中点击 URL 打开原页面，人工填写字段并用全屏截图补齐证据。"
                    ),
                    retryable=False,
                ),
                RecordStatus.NEEDS_REVIEW,
            )
        try:
            async with self._browser_pool.page(
                cancel_event,
                result.task.normalized_url,
            ) as page, asyncio.timeout(self._config.page_processing_timeout_seconds):
                navigation = await navigate_with_fallback(
                    page,
                    result.task.normalized_url,
                    self._config,
                    self._router,
                    self._browser_pool,
                    cancel_event,
                )
                result.errors.extend(navigation.warnings)
                final_url = navigation.final_url
                status_code = navigation.status_code
                redirect_chain = navigation.redirect_chain
                definition = navigation.definition
                result.page = PageData(
                    final_url=final_url,
                    status_code=status_code,
                    redirect_chain=redirect_chain,
                )
                auth_error = await guest_ui_error(
                    page, self._browser_pool, result.task.normalized_url
                )
                if auth_error is not None:
                    raise CrawlFailure(auth_error, RecordStatus.NEEDS_REVIEW)
                self._browser_pool.mark_access_valid(
                    page,
                    result.task.normalized_url,
                )
                _raise_if_cancelled(cancel_event)
                try:
                    if isinstance(self._parser, ContentParser):
                        extracted = await self._parser.extract(
                            page,
                            definition,
                            network_payloads=navigation.network_payloads,
                        )
                    else:
                        extracted = await self._parser.extract(page, definition)
                except Exception as error:
                    raise CrawlFailure(
                        TaskError("parse", "PARSE_FAILED", str(error), retryable=False),
                        RecordStatus.NEEDS_REVIEW,
                    ) from error
                extracted.final_url = final_url
                extracted.status_code = status_code
                extracted.redirect_chain = redirect_chain
                result.page = extracted
                result.status = RecordStatus.CRAWLED
                route_url = (
                    final_url
                    if self._router.definition_for(final_url) is not None
                    else result.task.normalized_url
                )
                route = self._router.route(route_url, extracted)
                if route is None:
                    unsupported_message = getattr(
                        self._router,
                        "unsupported_message",
                        lambda _url: "未匹配模板允许的平台",
                    )
                    raise CrawlFailure(
                        TaskError(
                            "route",
                            "ROUTE_UNSUPPORTED",
                            unsupported_message(final_url),
                            retryable=False,
                        ),
                        RecordStatus.NEEDS_REVIEW,
                    )
                result.route = route
                result.status = RecordStatus.ROUTED
                self._author.finalize(extracted, route)
                try:
                    result.assets.page_screenshot = await self._shooter.capture(
                        page,
                        result.task.evidence_id,
                        output_dir,
                        cancel_event,
                    )
                except PageScreenshotError as error:
                    raise CrawlFailure(
                        TaskError("screenshot", "PAGE_SCREENSHOT_FAILED", str(error), retryable=True),
                        RecordStatus.FAILED,
                    ) from error
                await self._collect_optional_assets(
                    page,
                    result,
                    output_dir,
                    cancel_event,
                )
                screenshot_ocr_timeout = max(
                    0.05,
                    min(
                        75.0,
                        self._config.ocr_worker_timeout_seconds + 20.0,
                        self._config.page_processing_timeout_seconds * 0.20,
                    ),
                )
                try:
                    async with asyncio.timeout(screenshot_ocr_timeout):
                        result.errors.extend(
                            await self._ocr_pipeline.recover_screenshot_fields(
                                result.page,
                                result.assets.page_screenshot,
                                cancel_event,
                            )
                        )
                except TimeoutError:
                    result.errors.append(
                        TaskError(
                            "ocr",
                            "SCREENSHOT_OCR_TIMEOUT",
                            "主截图 OCR 字段恢复超时；已保留已提取字段",
                            retryable=True,
                        )
                    )
                if not extracted.title and not extracted.content_text:
                    raise CrawlFailure(
                        TaskError(
                            "parse",
                            "EMPTY_PAGE",
                            "页面没有可审计的标题或正文",
                            retryable=False,
                        ),
                        RecordStatus.NEEDS_REVIEW,
                    )
                missing = missing_required_fields(result)
                if missing:
                    result.errors.append(
                        TaskError(
                            "export_validation",
                            "PARTIAL_FIELDS_MISSING",
                            f"已按现有内容导出；空缺字段：{', '.join(missing)}",
                            retryable=False,
                        )
                    )
                result.status = RecordStatus.ASSETS_READY
        except CrawlFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as error:
            processing_timeout = isinstance(error, TimeoutError)
            code = "PAGE_PROCESSING_TIMEOUT" if processing_timeout else "NAVIGATION_TIMEOUT" if "timeout" in type(error).__name__.lower() or "timeout" in str(error).lower() else "NAVIGATION_FAILED"
            raise CrawlFailure(
                TaskError("navigation", code, str(error) or "页面处理超过硬超时", retryable=not processing_timeout),
                RecordStatus.FAILED,
            ) from error

    async def _collect_optional_assets(
        self,
        page: Any,
        result: RecordResult,
        output_dir: Path,
        cancel_event: asyncio.Event,
    ) -> None:
        await collect_optional_assets(
            config=self._config,
            author_shooter=self._author_shooter,
            asset_collector=self._asset_collector,
            ocr_pipeline=self._ocr_pipeline,
            page=page,
            result=result,
            output_dir=output_dir,
            cancel_event=cancel_event,
        )

    async def _backoff(self, attempt: int, cancel_event: asyncio.Event) -> None:
        # Exponential backoff with full jitter (AWS-recommended strategy)
        # 指数退避 + 全抖动，比固定退避更难被风控检测
        base = self._config.retry_base_delay_seconds * (2**attempt)
        cap = min(base, 30.0)  # Cap at 30 seconds max
        sleep = random.uniform(0, cap)
        logger.info("Backoff attempt %d: sleeping %.1fs (base=%.1f)", attempt + 1, sleep, base)
        await wait_with_cancellation(sleep, cancel_event)

    @staticmethod
    def _emit(
        result: RecordResult,
        stage: str,
        message: str,
        callback: Callable[[TaskEvent], None] | None,
    ) -> None:
        if callback is None:
            return
        try:
            callback(TaskEvent(result.task.evidence_id, result.status, stage, message))
        except Exception:
            pass


def _raise_if_cancelled(cancel_event: asyncio.Event) -> None:
    if cancel_event.is_set():
        raise asyncio.CancelledError

def _now() -> datetime:
    return datetime.now(DEFAULT_TIMEZONE)
