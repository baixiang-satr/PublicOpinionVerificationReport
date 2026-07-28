"""Thread-safe client for the persistent OCR subprocess."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from typing import Any, Callable
from uuid import uuid4

from src.config.settings import TaskConfig
from src.domain.models import OcrStatus
from src.ocr.models import OcrBatchResult, OcrImageResult
from src.ocr.protocol import parse_response, recognition_request


logger = logging.getLogger(__name__)


class OcrCancelled(RuntimeError):
    """Raised when the caller cancels a running OCR request."""


class OcrClient:
    def __init__(
        self,
        executable: Path | None,
        *,
        timeout_seconds: float = 45.0,
        max_restarts: int = 1,
    ) -> None:
        self._executable = _resolve_executable(executable)
        self._timeout_seconds = timeout_seconds
        self._max_restarts = max_restarts
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[str] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, config: TaskConfig) -> "OcrClient":
        return cls(
            config.ocr_python_executable,
            timeout_seconds=config.ocr_worker_timeout_seconds,
            max_restarts=config.ocr_max_restarts,
        )

    def recognize(
        self,
        image_paths: list[Path],
        *,
        confidence_threshold: float,
        cancelled: Callable[[], bool] | None = None,
    ) -> OcrBatchResult:
        paths = [Path(path).resolve() for path in image_paths]
        if not paths:
            return OcrBatchResult(OcrStatus.NO_TEXT)
        if self._executable is None:
            return OcrBatchResult.unavailable(
                paths,
                "未配置可运行 RapidOCR 的 Python 3.12 解释器",
            )
        with self._lock:
            for attempt in range(self._max_restarts + 1):
                try:
                    self._ensure_process()
                    return self._request(paths, confidence_threshold, cancelled)
                except OcrCancelled:
                    self._terminate()
                    raise
                except TimeoutError as error:
                    self._terminate()
                    if attempt >= self._max_restarts:
                        return _batch_error(paths, OcrStatus.TIMEOUT, str(error))
                except Exception as error:
                    self._terminate()
                    if attempt >= self._max_restarts:
                        return _batch_error(
                            paths,
                            OcrStatus.FAILED,
                            f"{type(error).__name__}: {error}",
                        )
            return _batch_error(paths, OcrStatus.FAILED, "OCR worker stopped")

    def close(self) -> None:
        with self._lock:
            process = self._process
            if process is None:
                return
            try:
                if process.poll() is None and process.stdin is not None:
                    process.stdin.write('{"type":"shutdown"}\n')
                    process.stdin.flush()
                    process.wait(timeout=2)
            except Exception:
                pass
            finally:
                self._terminate()

    def cancel_current(self) -> None:
        self._terminate()

    def _ensure_process(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        assert self._executable is not None
        creation_flags = (
            subprocess.CREATE_NO_WINDOW
            if sys.platform == "win32"
            else 0
        )
        self._responses = queue.Queue()
        self._process = subprocess.Popen(
            [
                str(self._executable),
                "-X",
                "utf8",
                "-u",
                "-m",
                "src.ocr.worker_main",
            ],
            cwd=str(Path(__file__).resolve().parents[2]),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            creationflags=creation_flags,
        )
        self._reader = threading.Thread(
            target=self._read_responses,
            args=(self._process,),
            daemon=True,
            name="ocr-response-reader",
        )
        self._reader.start()

    def _request(
        self,
        paths: list[Path],
        threshold: float,
        cancelled: Callable[[], bool] | None,
    ) -> OcrBatchResult:
        process = self._process
        if process is None or process.stdin is None:
            raise RuntimeError("OCR worker is not running")
        request_id = uuid4().hex
        process.stdin.write(
            json.dumps(
                recognition_request(request_id, paths, threshold),
                ensure_ascii=False,
            )
            + "\n"
        )
        process.stdin.flush()
        deadline = time.monotonic() + self._timeout_seconds
        while True:
            if cancelled is not None and cancelled():
                raise OcrCancelled("OCR request cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"OCR worker exceeded {self._timeout_seconds:g} seconds"
                )
            try:
                raw = self._responses.get(timeout=min(0.2, remaining))
            except queue.Empty:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"OCR worker exited with code {process.returncode}"
                    )
                continue
            payload: dict[str, Any] = json.loads(raw)
            if payload.get("request_id") != request_id:
                continue
            return parse_response(payload)

    def _read_responses(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            if line.strip():
                self._responses.put(line)

    def _terminate(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        for stream in (process.stdin, process.stdout):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass


def _resolve_executable(configured: Path | None) -> Path | None:
    if configured is not None:
        path = Path(configured).expanduser().resolve()
        return path if path.is_file() else None
    project = Path(__file__).resolve().parents[2]
    candidates = (
        project / ".ocr-venv" / "Scripts" / "python.exe",
        project / ".ocr-venv" / "bin" / "python",
    )
    for path in candidates:
        if path.is_file():
            return path.resolve()
    if sys.version_info < (3, 14):
        return Path(sys.executable).resolve()
    return None


def _batch_error(
    paths: list[Path],
    status: OcrStatus,
    message: str,
) -> OcrBatchResult:
    logger.warning("OCR batch failed (%s): %s", status.value, message)
    return OcrBatchResult(
        status,
        tuple(
            OcrImageResult(path, status, error=message)
            for path in paths
        ),
        message,
    )
