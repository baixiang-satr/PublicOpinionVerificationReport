"""崩溃日志：未处理异常与 asyncio 异常写入 LOCALAPPDATA 日志目录。

桌面应用「无报错闪退」最常见的根因是渲染进程内存峰值与后台线程未捕获
异常；faulthandler + sys/threading.excepthook + 循环异常钩子至少能留下
最后现场。日志文件按启动时间命名，多次安装幂等。
"""

from __future__ import annotations

from datetime import datetime
import faulthandler
from pathlib import Path
import sys
import threading
import traceback
from types import TracebackType

from src.config.settings import _local_app_data_root

_handle = None
_log_path: Path | None = None
_previous_hook = None


def install(log_dir: Path | None = None) -> Path:
    """安装崩溃钩子（幂等）；返回日志文件路径。"""

    global _handle, _log_path, _previous_hook
    if _handle is not None and _log_path is not None:
        return _log_path
    directory = Path(log_dir) if log_dir is not None else _local_app_data_root() / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    _log_path = directory / f"crash-{datetime.now():%Y%m%d-%H%M%S}.log"
    _handle = _log_path.open("a", encoding="utf-8", buffering=1)
    _handle.write("=== 崩溃日志开始 ===\n")
    faulthandler.enable(file=_handle)
    _previous_hook = sys.excepthook
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    return _log_path


def log_path() -> Path | None:
    return _log_path


def uninstall() -> None:
    """测试专用：还原钩子并关闭日志文件。"""

    global _handle, _log_path
    if _handle is None:
        return
    if _previous_hook is not None:
        sys.excepthook = _previous_hook
    faulthandler.disable()
    try:
        _handle.close()
    finally:
        _handle = None
        _log_path = None


def loop_exception_handler(_loop: object, context: dict) -> None:
    """asyncio ``set_exception_handler`` 钩子：循环级异常落盘。"""

    message = str(context.get("message") or "asyncio 未处理异常")
    exception = context.get("exception")
    if isinstance(exception, BaseException):
        _write_traceback(
            f"asyncio：{message}",
            type(exception),
            exception,
            exception.__traceback__,
        )
    else:
        _write_line(f"asyncio：{message}")


def _excepthook(exc_type, exc, tb) -> None:
    _write_traceback("未捕获异常", exc_type, exc, tb)
    hook = _previous_hook
    if hook is not None and hook is not _excepthook:
        hook(exc_type, exc, tb)
    else:
        sys.__excepthook__(exc_type, exc, tb)


def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
    thread_name = args.thread.name if args.thread is not None else "?"
    _write_traceback(
        f"后台线程未捕获异常（{thread_name}）",
        args.exc_type,
        args.exc_value,
        args.exc_traceback,
    )


def _write_traceback(
    title: str,
    exc_type: type[BaseException] | None,
    exc: BaseException | None,
    tb: TracebackType | None,
) -> None:
    if _handle is None:
        return
    try:
        _write_line(title)
        traceback.print_exception(exc_type, exc, tb, file=_handle)
    except (OSError, ValueError):
        pass


def _write_line(text: str) -> None:
    if _handle is None:
        return
    try:
        stamp = datetime.now().isoformat(timespec="seconds")
        _handle.write(f"\n=== {stamp} {text} ===\n")
    except (OSError, ValueError):
        pass
