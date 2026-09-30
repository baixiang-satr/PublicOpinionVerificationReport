"""Application entry point: starts the B/S web server (port 6667 by default)."""

from __future__ import annotations

import os
from pathlib import Path
import sys


def _prepare_frozen_environment() -> None:
    """PyInstaller 打包运行时：优先使用随包携带的 Playwright 浏览器。"""

    if not getattr(sys, "frozen", False):
        return
    exe_dir = Path(sys.executable).resolve().parent
    browsers = exe_dir / "ms-playwright"
    if browsers.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers))


def main() -> int:
    _prepare_frozen_environment()
    # 崩溃日志最先安装：闪退排查需要最后一次现场。
    from src.utils.crash_log import install as install_crash_logging

    install_crash_logging()
    if "--ocr-worker" in sys.argv[1:]:
        # 打包后 OCR 子进程复用本 exe（见 src/ocr/client.py）
        from src.ocr.worker_main import main as ocr_worker_main

        return ocr_worker_main()
    from src.server.run import run_server

    return run_server()


if __name__ == "__main__":
    raise SystemExit(main())
