"""Bounded, cancellable browser crawl engine producing runtime RecordResult objects."""
from __future__ import annotations
import asyncio
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
import random
from typing import Any
from src.auth.store import AuthProfileStore
from src.crawler.authenticated_access import guest_ui_error
from src.config.settings import TaskConfig
from src.crawler.auth_preflight import preflight_or_empty
from src.crawler.author_extractor import AuthorExtractor
from src.crawler.backoff import backoff
from src.crawler.content_parser import ContentParser
from src.crawler.crawl_navigation import CrawlFailure, navigate_with_fallback
from src.crawler.engine_events import emit_event
from src.crawler.field_quality import missing_required_fields
from src.crawler.llm_deletion import InvalidUrlCandidate, LlmDeletionJudge
from src.crawler.llm_fallback import LlmFallback
from src.crawler.ocr_pipeline import OcrPipeline
from src.crawler.optional_assets import (
    author_screenshot_required_unmet,
    collect_optional_assets,
)
from src.crawler.platform_queue import run_platform_queue
from src.crawler.platform_router import PlatformRouter
from src.crawler.platform_scheduler import PlatformTaskScheduler
from src.crawler.rate_limiter import HostRateLimiter, wait_with_cancellation
from src.crawler.relogin import ReloginHandler
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

#: 规则已确证内容失效的错误码：直接收集为失效候选，不发起 LLM 请求。
_RULE_CONFIRMED_DELETION_CODES = frozenset(
    {"CONTENT_UNAVAILABLE", "CONTENT_NOT_FOUND", "CONTENT_REDIRECTED_TO_HOME"}
)

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
        llm_fallback: LlmFallback | None = None,
        deletion_judge: LlmDeletionJudge | None = None,
    ) -> None:
        self._config = config
        self._relogin_handler = relogin_handler
        self._llm_fallback = llm_fallback or LlmFallback(config)
        self._deletion_judge = deletion_judge or LlmDeletionJudge(config)
        #: 抓取中判定的失效候选（规则确证 + LLM 逐字引用确证），由
        #: TaskRunner 在任务完成后持久化到任务目录。
        self.invalid_candidates: list[InvalidUrlCandidate] = []
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
                queues, on_event, emit_event, cancellation,
            )
            jobs = [
                asyncio.create_task(
                    run_platform_queue(
                        queue,
                        Path(output_dir),
                        config=self._config,
                        scheduler=self._scheduler,
                        process=self._process,
                        relogin_handler=self._relogin_handler,
                        browser_pool=self._browser_pool,
                        on_event=on_event,
                        on_result=on_result,
                        cancel_event=cancellation,
                        auth_preflight=auth_preflight,
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

    async def _process(
        self,
        task: UrlTask,
        output_dir: Path,
        on_event: Callable[[TaskEvent], None] | None,
        on_result: Callable[[RecordResult], None] | None,
        cancel_event: asyncio.Event,
    ) -> RecordResult:
        result = RecordResult(task=task, status=RecordStatus.RUNNING, started_at=_now())
        emit_event(result, "start", "开始访问页面", on_event)
        try:
            for attempt in range(self._config.max_retries + 1):
                result.attempt_count = attempt + 1
                try:
                    await self._crawl_attempt(result, output_dir, cancel_event)
                    break
                except CrawlFailure as failure:
                    result.errors.append(failure.error)
                    if failure.error.retryable and attempt < self._config.max_retries:
                        emit_event(result, "retry", failure.error.message, on_event)
                        await backoff(
                            self._config.retry_base_delay_seconds,
                            attempt,
                            cancel_event,
                        )
                        continue
                    result.status = failure.status
                    if failure.error.code in _RULE_CONFIRMED_DELETION_CODES:
                        self._record_invalid_candidate(
                            result.task,
                            failure.error.code,
                            failure.error.evidence or failure.error.message,
                        )
                    break
        except asyncio.CancelledError:
            result.status = RecordStatus.CANCELLED
            result.errors.append(TaskError("crawl", "CANCELLED", "任务已取消", retryable=False))
        except Exception as error:
            result.add_error(TaskError("crawl", "UNEXPECTED", str(error), retryable=False))
        finally:
            result.finished_at = _now()
            emit_event(result, "finish", result.status.value, on_event)
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
                    platform_key=definition.key,
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
                # 大模型兜底：标题/作者/发布时间缺失时从原文补全（默认
                # 关闭；在「大模型设置」中启用并填齐 Key 后生效）。
                await self._llm_fallback.fill_missing_fields(
                    extracted,
                    cancel_event,
                )
                # 大模型失效判定：仅规则未确证的灰区页面（正文极短/空壳，
                # 疑似软 404）触发；引文须逐字命中原文才采信，设置未启用或
                # 判定失败一律静默保持原状态。
                deleted_quote = await self._deletion_judge.detect_deleted_quote(
                    extracted,
                    cancel_event,
                )
                if deleted_quote is not None:
                    self._record_invalid_candidate(
                        result.task,
                        "CONTENT_DELETED_LLM",
                        deleted_quote,
                    )
                    raise CrawlFailure(
                        TaskError(
                            "access",
                            "CONTENT_DELETED_LLM",
                            f"大模型从页面原文逐字引用确认内容已删除：{deleted_quote}",
                            retryable=False,
                            evidence=deleted_quote,
                        ),
                        RecordStatus.FAILED,
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
                result.status = (
                    RecordStatus.NEEDS_REVIEW
                    if author_screenshot_required_unmet(definition.key, result)
                    else RecordStatus.ASSETS_READY
                )
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

    def _record_invalid_candidate(
        self,
        task: UrlTask,
        code: str,
        citation: str,
    ) -> None:
        """收集失效候选（按 evidence_id 幂等替换，保留最新判定引文）。"""

        candidate = InvalidUrlCandidate(
            evidence_id=task.evidence_id,
            url=task.normalized_url,
            code=code,
            citation=citation,
        )
        self.invalid_candidates = [
            item
            for item in self.invalid_candidates
            if item.evidence_id != candidate.evidence_id
        ]
        self.invalid_candidates.append(candidate)

    async def _collect_optional_assets(
        self,
        page: Any,
        result: RecordResult,
        output_dir: Path,
        cancel_event: asyncio.Event,
        platform_key: str | None = None,
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
            platform_key=platform_key,
        )


def _raise_if_cancelled(cancel_event: asyncio.Event) -> None:
    if cancel_event.is_set():
        raise asyncio.CancelledError

def _now() -> datetime:
    return datetime.now(DEFAULT_TIMEZONE)
