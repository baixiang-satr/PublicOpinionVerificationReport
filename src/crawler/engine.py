"""Bounded, cancellable browser crawl engine producing runtime RecordResult objects."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import random
from typing import Any

from src.config.settings import TaskConfig
from src.crawler.author_extractor import AuthorExtractor
from src.crawler.content_parser import ContentParser
from src.crawler.platform_router import PlatformRouter
from src.crawler.rate_limiter import HostRateLimiter, wait_with_cancellation
from src.domain.models import (
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
from src.utils.time_utils import DEFAULT_TIMEZONE


@dataclass
class CrawlFailure(Exception):
    error: TaskError
    status: RecordStatus


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
    ) -> None:
        self._config = config
        self._browser_pool = browser_pool or BrowserPool(config)
        self._parser = parser or ContentParser(config.summary_max_chars)
        self._router = router or PlatformRouter()
        self._shooter = shooter or PageShooter(config)
        self._author_shooter = author_shooter or AuthorShooter(config)
        self._asset_collector = asset_collector or AssetCollector(config)
        self._author = AuthorExtractor(config.allow_nickname_as_id)
        self._rate_limiter = HostRateLimiter(config.min_host_interval_seconds)

    async def run(
        self,
        tasks: list[UrlTask],
        output_dir: Path,
        on_event: Callable[[TaskEvent], None] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> list[RecordResult]:
        if not tasks:
            return []
        cancellation = cancel_event or asyncio.Event()
        await self._browser_pool.start()
        try:
            jobs = [
                asyncio.create_task(
                    self._process(task, Path(output_dir), on_event, cancellation),
                    name=f"crawl-{task.evidence_id}",
                )
                for task in tasks
            ]
            return list(await asyncio.gather(*jobs))
        finally:
            await self._browser_pool.close()

    async def _process(
        self,
        task: UrlTask,
        output_dir: Path,
        on_event: Callable[[TaskEvent], None] | None,
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
        return result

    async def _crawl_attempt(
        self,
        result: RecordResult,
        output_dir: Path,
        cancel_event: asyncio.Event,
    ) -> None:
        _raise_if_cancelled(cancel_event)
        await self._rate_limiter.wait(result.task.normalized_url, cancel_event)
        try:
            async with self._browser_pool.page(cancel_event) as page:
                response = await page.goto(
                    result.task.normalized_url,
                    wait_until="domcontentloaded",
                    timeout=self._config.page_timeout_seconds * 1000,
                )
                final_url = str(page.url)
                status_code = int(response.status) if response is not None else None
                redirect_chain = _redirect_chain(response, result.task.normalized_url, final_url)
                result.page = PageData(
                    final_url=final_url,
                    status_code=status_code,
                    redirect_chain=redirect_chain,
                )
                self._check_response(status_code)
                _raise_if_cancelled(cancel_event)
                if self._config.page_stabilize_milliseconds:
                    await page.wait_for_timeout(self._config.page_stabilize_milliseconds)
                _raise_if_cancelled(cancel_event)
                definition = self._router.definition_for(final_url)
                await _wait_for_platform_marker(page, definition)
                _raise_if_cancelled(cancel_event)
                try:
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
                route = self._router.route(final_url, extracted)
                if route is None:
                    raise CrawlFailure(
                        TaskError("route", "ROUTE_UNSUPPORTED", "未匹配模板允许的平台", retryable=False),
                        RecordStatus.NEEDS_REVIEW,
                    )
                result.route = route
                result.status = RecordStatus.ROUTED
                self._author.finalize(extracted, route)
                if not extracted.title and not extracted.content_text:
                    raise CrawlFailure(
                        TaskError("parse", "EMPTY_PAGE", "页面没有可审计的标题或正文", retryable=False),
                        RecordStatus.NEEDS_REVIEW,
                    )
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
                missing = _missing_required_fields(result)
                if missing:
                    raise CrawlFailure(
                        TaskError(
                            "parse",
                            "REQUIRED_FIELDS_MISSING",
                            f"待人工补录字段：{', '.join(missing)}",
                            retryable=False,
                        ),
                        RecordStatus.NEEDS_REVIEW,
                    )
                await self._collect_optional_assets(page, result, output_dir, cancel_event)
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

        if not result.page.image_urls:
            return
        try:
            collected = await self._asset_collector.collect(
                page,
                result.page.image_urls,
                result.task.evidence_id,
                output_dir,
                cancel_event,
            )
            result.assets.downloaded_images.extend(collected.files)
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

    def _check_response(self, status_code: int | None) -> None:
        if status_code == 403:
            raise CrawlFailure(
                TaskError("navigation", "HTTP_403", "页面返回 HTTP 403，需要人工确认访问权限", False),
                RecordStatus.NEEDS_REVIEW,
            )
        if status_code == 429:
            raise CrawlFailure(
                TaskError("navigation", "HTTP_429", "页面返回 HTTP 429", True),
                RecordStatus.NEEDS_REVIEW,
            )
        if status_code is not None and status_code >= 500:
            raise CrawlFailure(
                TaskError("navigation", "HTTP_5XX", f"页面返回 HTTP {status_code}", True),
                RecordStatus.FAILED,
            )
        if status_code is not None and status_code >= 400:
            raise CrawlFailure(
                TaskError("navigation", f"HTTP_{status_code}", f"页面返回 HTTP {status_code}", False),
                RecordStatus.FAILED,
            )

    async def _backoff(self, attempt: int, cancel_event: asyncio.Event) -> None:
        base = self._config.retry_base_delay_seconds * (2**attempt)
        jitter = random.uniform(0, min(0.25, base / 4)) if base else 0
        await wait_with_cancellation(base + jitter, cancel_event)

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


def _redirect_chain(response: Any, original_url: str, final_url: str) -> list[str]:
    urls: list[str] = []
    request = response.request if response is not None else None
    while request is not None:
        urls.append(str(request.url))
        request = request.redirected_from
    urls.reverse()
    if not urls:
        urls.append(original_url)
    if final_url and urls[-1] != final_url:
        urls.append(final_url)
    return list(dict.fromkeys(urls))


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


async def _wait_for_platform_marker(page: Any, definition: Any) -> None:
    if definition is None or not hasattr(page, "locator"):
        return
    selectors = [
        selector
        for field in ("content_text", "title")
        for selector in definition.selectors.get(field, ())
    ]
    if not selectors:
        return
    try:
        await page.locator(", ".join(selectors)).first.wait_for(state="attached", timeout=1_500)
    except Exception:
        return
