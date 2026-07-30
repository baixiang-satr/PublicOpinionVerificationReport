"""Desktop application entry point."""

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
    if "--ocr-worker" in sys.argv[1:]:
        # 打包后 OCR 子进程复用本 exe（见 src/ocr/client.py）
        from src.ocr.worker_main import main as ocr_worker_main

        return ocr_worker_main()
    try:
        from src.webui.app import run_app
    except ImportError as error:
        if error.name and error.name.startswith("webview"):
            print("缺少 pywebview，请先运行：pip install -r requirements.txt", file=sys.stderr)
            return 2
        raise
    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
