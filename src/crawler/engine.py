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
from src.config.settings import TaskConfig
from src.crawler.author_extractor import AuthorExtractor
from src.crawler.content_parser import ContentParser
from src.crawler.crawl_navigation import CrawlFailure, navigate_with_fallback
from src.crawler.platform_router import PlatformRouter
from src.crawler.platform_scheduler import PlatformTaskScheduler
from src.crawler.rate_limiter import HostRateLimiter, wait_with_cancellation
from src.domain.models import (
    ExtractionSource,
    PageData,
    RecordResult,
    RecordStatus,
    TaskError,
    TaskEvent,
    UrlTask,
)
from src.domain.template_schema import get_sheet_layout
from src.screenshot.asset_collector import AssetCollector
from src.screenshot.author_shooter import AuthorShooter, AuthorScreenshotError
from src.screenshot.browser import BrowserPool
from src.screenshot.page_shooter import PageShooter, PageScreenshotError
from src.utils.ocr import extract_text_from_images
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
        auth_store: AuthProfileStore | None = None,
    ) -> None:
        self._config = config
        self._auth_store = auth_store
        if self._auth_store is None and config.auth_store_dir is not None:
            self._auth_store = AuthProfileStore(config.auth_store_dir)
        self._browser_pool = browser_pool or BrowserPool(config, auth_store=self._auth_store)
        self._parser = parser or ContentParser(config.summary_max_chars)
        self._router = router or PlatformRouter()
        self._shooter = shooter or PageShooter(config)
        self._author_shooter = author_shooter or AuthorShooter(config)
        self._asset_collector = asset_collector or AssetCollector(config)
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
            jobs = [
                asyncio.create_task(
                    self._process_platform_queue(
                        queue,
                        Path(output_dir),
                        on_event,
                        on_result,
                        cancellation,
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
            await self._browser_pool.close()

    async def _process_platform_queue(
        self,
        tasks: list[UrlTask],
        output_dir: Path,
        on_event: Callable[[TaskEvent], None] | None,
        on_result: Callable[[RecordResult], None] | None,
        cancel_event: asyncio.Event,
    ) -> list[RecordResult]:
        results: list[RecordResult] = []
        known_block = self._scheduler.known_auth_block(tasks[0])
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
                if self._scheduler.should_pause_after(result):
                    paused_by = result
            results.append(result)
        return results

    def _publish_paused_result(
        self,
        task: UrlTask,
        message: str,
        on_event: Callable[[TaskEvent], None] | None,
        on_result: Callable[[RecordResult], None] | None,
    ) -> RecordResult:
        result = self._scheduler.auth_paused_result(task, message)
        now = _now()
        result.started_at = now
        result.finished_at = now
        self._emit(result, "start", "平台登录态门禁检查", on_event)
        self._emit(result, "finish", result.status.value, on_event)
        if on_result is not None:
            try:
                on_result(result)
            except Exception:
                pass
        return result

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
        try:
            async with self._browser_pool.page(
                cancel_event,
                result.task.normalized_url,
            ) as page:
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
                        TaskError("screenshot", "PAGE_SCREENSHOT_FAILED", str(error), retryable=False),
                        RecordStatus.FAILED,
                    ) from error
                await self._collect_optional_assets(page, result, output_dir, cancel_event)
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
                missing = _missing_required_fields(result)
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
            code = "NAVIGATION_TIMEOUT" if "timeout" in type(error).__name__.lower() or "timeout" in str(error).lower() else "NAVIGATION_FAILED"
            raise CrawlFailure(
                TaskError("navigation", code, str(error), retryable=True),
                RecordStatus.FAILED,
            ) from error

    async def _collect_optional_assets(
        self,
        page: Any,
        result: RecordResult,
        output_dir: Path,
        cancel_event: asyncio.Event,
    ) -> None:
        if result.page.author_url:
            try:
                result.assets.author_screenshot = await self._author_shooter.capture(
                    page,
                    result.page.author_url,
                    result.task.evidence_id,
                    output_dir,
                    cancel_event,
                )
            except AuthorScreenshotError as error:
                result.errors.append(
                    TaskError("author_screenshot", error.code, str(error), retryable=False)
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                result.errors.append(
                    TaskError(
                        "author_screenshot",
                        "AUTHOR_SCREENSHOT_FAILED",
                        str(error),
                        retryable=False,
                    )
                )

        # Body images are not delivery attachments. They are downloaded only
        # when DOM extraction found no text and OCR may recover useful content.
        if (
            not self._config.ocr_enabled
            or result.page.content_text
            or not result.page.image_urls
        ):
            return
        collected_files: list[Path] = []
        try:
            collected = await self._asset_collector.collect(
                page,
                result.page.image_urls,
                result.task.evidence_id,
                output_dir,
                cancel_event,
            )
            collected_files.extend(collected.files)
            result.errors.extend(collected.errors)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            result.errors.append(
                TaskError(
                    "image_download",
                    "ASSET_COLLECTION_FAILED",
                    str(error),
                    retryable=False,
                )
            )
            return

        if not collected_files:
            return

        try:
            _raise_if_cancelled(cancel_event)
            ocr_text = extract_text_from_images(
                collected_files,
                confidence_threshold=self._config.ocr_confidence_threshold,
            )
            result.page.ocr_text = ocr_text

            if ocr_text and ocr_text != "无文字":
                result.page.content_text = ocr_text
                result.page.content_summary = ocr_text[: self._config.summary_max_chars]
                result.page.summary_truncated = len(ocr_text) > self._config.summary_max_chars
                result.page.field_sources["content_text"] = ExtractionSource.OCR
                logger.info(
                    "OCR extracted text from %d temporary image(s) for evidence %d",
                    len(collected_files),
                    result.task.evidence_id,
                )
            else:
                result.errors.append(
                    TaskError(
                        "ocr",
                        "OCR_NO_TEXT",
                        f"临时读取的 {len(collected_files)} 张图片中未识别到文字",
                        retryable=False,
                    )
                )
        finally:
            for path in collected_files:
                try:
                    path.unlink(missing_ok=True)
                except OSError as error:
                    logger.warning("Unable to remove temporary OCR image %s: %s", path, error)

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


def _missing_required_fields(result: RecordResult) -> list[str]:
    if result.route is None or result.assets.page_screenshot is None:
        return ["route_or_screenshot"]
    layout = get_sheet_layout(result.route.sheet_name)
    reverse_fields = {column: field for field, column in layout.field_columns.items()}
    runtime_values = {
        "url": result.page.final_url or result.task.normalized_url,
        "platform": result.route.platform_value,
        "text_type": result.route.text_type,
        "title": result.page.title,
        "content": result.page.content_summary or result.page.content_text,
        "author_name": result.page.author_name,
        "author_id": result.page.author_id,
        "account_uin": result.page.account_uin,
        "store_name": result.page.store_name,
        "published_at": result.page.published_at,
    }
    missing: list[str] = []
    for column in sorted(layout.required_columns):
        if column == layout.primary_screenshot_column:
            continue
        field = reverse_fields.get(column)
        if field and not runtime_values.get(field):
            missing.append(field)
    return missing


def _raise_if_cancelled(cancel_event: asyncio.Event) -> None:
    if cancel_event.is_set():
        raise asyncio.CancelledError


def _now() -> datetime:
    return datetime.now(DEFAULT_TIMEZONE)
