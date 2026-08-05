"""后台执行器：事件推送（Python→JS）、任务线程、登录态线程。

pywebview 的 js_api 方法在独立线程执行，TaskRunner/AuthManagerService 都是
asyncio 协程，因此每个后台任务都在专属线程里跑独立事件循环，通过
``EventSink`` 用 ``window.evaluate_js`` 把事件推回 Vue 侧。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, Any, Callable, Coroutine

from src.auth.store import AuthProfileStore
from src.config.settings import AppConfig, TaskConfig, default_auth_store_dir
from src.crawler.engine import CrawlEngine
from src.screenshot.capture_session import CaptureSession
from src.screenshot.region_capture import RegionCaptureResult, RegionCaptureService
from src.services.models import JobRequest, JobResult, RunnerCallbacks
from src.services.review_session import ReviewSession
from src.services.task_runner import TaskRunner, TaskRunnerError
from src.utils import crash_log
from src.webui.relogin_coordinator import CrawlReloginCoordinator
from src.webui.serialize import finished_payload, log_payload, progress_payload

if TYPE_CHECKING:
    from src.webui.auth_runner import AuthRunner


class EventSink:
    """Thread-safe bridge event emitter (Python → ``window.__poir_event``)."""

    def __init__(self) -> None:
        self._window: Any = None
        self._lock = Lock()

    def bind(self, window: Any) -> None:
        with self._lock:
            self._window = window

    def emit(self, event_type: str, payload: dict | None = None) -> None:
        with self._lock:
            window = self._window
        if window is None:
            return
        data = json.dumps({"type": event_type, "payload": payload or {}}, ensure_ascii=False)
        try:
            window.evaluate_js(f"window.__poir_event && window.__poir_event({data})")
        except Exception:
            # 窗口已关闭或 JS 尚未就绪：事件丢弃，任务继续。
            pass


class AsyncThreadJob:
    """Base: run one coroutine on a dedicated daemon thread.

    Two cancel channels are offered because the consumers differ:
    - ``_asyncio_cancel``: for TaskRunner (expects ``asyncio.Event``)
    - ``_thread_cancel``: for AuthManagerService (expects ``threading.Event``)
    """

    def __init__(self, sink: EventSink) -> None:
        self._sink = sink
        self._lock = Lock()
        self._thread: Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._asyncio_cancel: asyncio.Event | None = None
        self._thread_cancel: Event | None = None
        self._cancel_requested = False

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def cancel(self) -> None:
        with self._lock:
            loop = self._loop
            task = self._task
            asyncio_event = self._asyncio_cancel
            thread_event = self._thread_cancel
        if loop is not None and asyncio_event is not None:
            loop.call_soon_threadsafe(asyncio_event.set)
        if thread_event is not None:
            thread_event.set()
        with self._lock:
            # 线程尚未注册 loop/task 时也能记住本次取消请求。
            self._cancel_requested = True
        if loop is not None and task is not None:
            # 立即中断卡住的 Playwright 调用（仅 AuthRunner 记录 _task）。
            loop.call_soon_threadsafe(task.cancel)

    def _consume_cancel_request(self) -> bool:
        with self._lock:
            requested = self._cancel_requested
            self._cancel_requested = False
        return requested

    def _spawn(self, coroutine: Coroutine) -> None:
        with self._lock:
            self._cancel_requested = False

        def runner() -> None:
            try:
                asyncio.run(coroutine)
            except asyncio.CancelledError:
                pass
            except Exception as error:  # noqa: BLE001 — 统一回吐给 UI
                self._sink.emit("failed", {"message": f"{type(error).__name__}: {error}"})
            finally:
                with self._lock:
                    self._loop = None
                    self._task = None
                    self._asyncio_cancel = None
                    self._thread_cancel = None

        self._thread = Thread(target=runner, daemon=True)
        self._thread.start()


FINAL_ARCHIVE_NAME = "template_final.zip"


def _copy_final_archive(result: JobResult, target_dir: Path | None) -> Path | None:
    """补录导出成功后，把最终 ZIP 复制回原任务目录（与 init 版并列）。"""

    if result.archive_path is None or target_dir is None:
        return None
    try:
        destination = Path(target_dir) / FINAL_ARCHIVE_NAME
        if Path(result.archive_path).resolve() != destination.resolve():
            shutil.copy2(result.archive_path, destination)
        return destination
    except OSError:
        return None


class JobRunner(AsyncThreadJob):
    """Runs TaskRunner requests; keeps the latest result + review session."""

    def __init__(self, config_getter, sink: EventSink, auth_runner: "AuthRunner | None" = None) -> None:
        super().__init__(sink)
        self._config_getter = config_getter
        self._auth_runner = auth_runner
        self.relogin: CrawlReloginCoordinator | None = None
        self.result: JobResult | None = None
        self.session: ReviewSession | None = None
        self.last_checkpoint: str | None = None
        # export_zip 设置：补录导出完成后把最终 ZIP 复制回原任务目录。
        self.final_copy_dir: Path | None = None

    def start(self, request: JobRequest) -> tuple[bool, str]:
        if self.is_running():
            return False, "已有任务正在运行，请先完成或取消。"
        self._spawn(self._run_async(request))
        return True, ""

    def open_session(self, job_dir) -> tuple[bool, str]:
        try:
            self.session = ReviewSession.from_job_dir(job_dir)
        except Exception as error:  # noqa: BLE001
            return False, f"无法打开任务目录：{type(error).__name__}: {error}"
        self._sink.emit("session", {})
        return True, ""

    def refresh_latest_checkpoint(self, output_dir) -> None:
        candidates = sorted(
            (p for p in output_dir.glob("*/job_checkpoint.json") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        self.last_checkpoint = str(candidates[0]) if candidates else None

    def _engine_factory(self) -> Callable[[TaskConfig], CrawlEngine] | None:
        """Build a crawl-engine factory wired to the re-login coordinator."""

        if self._auth_runner is None:
            return None
        self.relogin = CrawlReloginCoordinator(self._sink, self._auth_runner)
        handler = self.relogin.relogin
        return lambda cfg: CrawlEngine(cfg, relogin_handler=handler)

    async def _run_async(self, request: JobRequest) -> None:
        config: AppConfig = self._config_getter()
        callbacks = RunnerCallbacks(
            started=lambda summary: self._sink.emit(
                "started",
                {
                    "job_id": summary.job_id,
                    "label": summary.label,
                    "total": summary.total,
                    "rejected_count": summary.rejected_count,
                },
            ),
            progress=lambda snapshot: self._sink.emit("progress", progress_payload(snapshot)),
            log=lambda event: self._sink.emit("log", log_payload(event)),
        )
        runner = TaskRunner(config, engine_factory=self._engine_factory())
        cancel_event = asyncio.Event()
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._asyncio_cancel = cancel_event
        self._loop.set_exception_handler(crash_log.loop_exception_handler)
        try:
            result = await runner.run(request, callbacks, cancel_event)
        except TaskRunnerError as error:
            self._sink.emit("failed", {"message": str(error)})
            return
        self.result = result
        final_copy = _copy_final_archive(result, self.final_copy_dir)
        self.final_copy_dir = None
        if result.checkpoint_path is not None:
            self.last_checkpoint = str(result.checkpoint_path)
        try:
            self.session = ReviewSession.from_job_dir(result.job_dir)
        except Exception:
            self.session = None
        self._sink.emit("finished", finished_payload(result, final_copy))


class CaptureRunner:
    """Run interactive region captures on one persistent loop thread.

    Playwright objects are bound to the event loop that created them, so a
    dedicated daemon thread runs ``loop.run_forever()`` and dispatches capture
    coroutines to it. Each visible browser closes when capture finishes;
    encrypted per-platform state carries the login into the next window.
    """

    def __init__(self, task_config_getter, sink: EventSink) -> None:
        self._task_config_getter = task_config_getter
        self._sink = sink
        self._lock = Lock()
        self._thread: Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_ready = Event()
        self._session: CaptureSession | None = None
        self._busy = False
        self._asyncio_cancel: asyncio.Event | None = None

    def is_running(self) -> bool:
        with self._lock:
            return self._busy

    def cancel(self) -> None:
        with self._lock:
            loop = self._loop
            event = self._asyncio_cancel
        if loop is not None and event is not None:
            loop.call_soon_threadsafe(event.set)

    def start(
        self,
        *,
        url: str,
        evidence_id: int,
        target: str,
        platform_key: str | None = None,
        storage_state: dict[str, Any] | None,
        assets_dir: Path,
        on_saved: Callable[[str], None],
        focus_texts: tuple[str, ...] = (),
    ) -> tuple[bool, str]:
        with self._lock:
            if self._busy:
                return False, "已有截图窗口打开，请先完成或关闭当前窗口。"
            self._busy = True
        try:
            loop = self._ensure_loop()
        except Exception as error:  # noqa: BLE001 — 统一回吐给 UI
            with self._lock:
                self._busy = False
            return False, f"无法启动截图线程：{type(error).__name__}: {error}"
        asyncio.run_coroutine_threadsafe(
            self._run_capture(
                url=url,
                evidence_id=evidence_id,
                target=target,
                platform_key=platform_key,
                storage_state=storage_state,
                assets_dir=assets_dir,
                on_saved=on_saved,
                focus_texts=focus_texts,
            ),
            loop,
        )
        return True, ""

    def shutdown(self) -> None:
        """Persist session login states and stop the loop (app exit)."""

        with self._lock:
            loop = self._loop
            session = self._session
            thread = self._thread
        if loop is not None and session is not None:
            try:
                asyncio.run_coroutine_threadsafe(session.close(), loop).result(timeout=10)
            except Exception:  # noqa: BLE001 — 退出阶段尽力而为
                pass
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5)

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            loop = self._loop
        if loop is not None and loop.is_running():
            return loop
        self._loop_ready.clear()

        def runner() -> None:
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            with self._lock:
                self._loop = new_loop
            self._loop_ready.set()
            try:
                new_loop.run_forever()
            finally:
                new_loop.close()
                with self._lock:
                    self._loop = None

        thread = Thread(target=runner, daemon=True, name="poir-capture")
        with self._lock:
            self._thread = thread
        thread.start()
        if not self._loop_ready.wait(timeout=10):
            raise RuntimeError("截图线程启动超时。")
        with self._lock:
            if self._loop is None:
                raise RuntimeError("截图线程启动失败。")
            return self._loop

    async def _run_capture(
        self,
        *,
        url: str,
        evidence_id: int,
        target: str,
        platform_key: str | None,
        storage_state: dict[str, Any] | None,
        assets_dir: Path,
        on_saved: Callable[[str], None],
        focus_texts: tuple[str, ...],
    ) -> None:
        cancel_event = asyncio.Event()
        with self._lock:
            self._asyncio_cancel = cancel_event
        try:
            config: TaskConfig = self._task_config_getter()
            if self._session is None:
                store_dir = config.auth_store_dir or default_auth_store_dir()
                self._session = CaptureSession(config, AuthProfileStore(store_dir))
            service = RegionCaptureService(config, session=self._session)
            result = await service.capture(
                url,
                evidence_id=evidence_id,
                target=target,
                platform_key=platform_key,
                storage_state=storage_state,
                assets_dir=assets_dir,
                cancel_event=cancel_event,
                focus_texts=focus_texts,
            )
        except asyncio.CancelledError:
            result = RegionCaptureResult(status="cancelled")
        except Exception as error:  # noqa: BLE001 — 统一回吐给 UI
            result = RegionCaptureResult(
                status="error",
                message=f"{type(error).__name__}: {error}",
            )
        finally:
            with self._lock:
                self._asyncio_cancel = None
                self._busy = False
        if result.status == "saved":
            try:
                on_saved(result.name)
            except Exception as error:  # noqa: BLE001 — 统一回吐给 UI
                result = RegionCaptureResult(
                    status="error",
                    message=f"截图已保存但关联记录失败：{error}",
                )
        self._sink.emit(
            "capture",
            {
                "eid": evidence_id,
                "target": target,
                "status": result.status,
                "name": result.name,
                "message": result.message,
            },
        )
