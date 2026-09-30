"""它机截图环境一键诊断：环境 JSON + 可选真实截图自检。

在源码环境运行（客户机无需运行：每次抓取会自动在任务目录写
``capture_environment.json``，把它回传即可）：

    .venv/Scripts/python.exe tools/check_capture_env.py
    .venv/Scripts/python.exe tools/check_capture_env.py --selftest
    .venv/Scripts/python.exe tools/check_capture_env.py --selftest --json output/capture-env.json

自检内容与生产链路完全一致：离屏窗口启动参数 → 固定视口上下文 →
页面几何探测（innerWidth/devicePixelRatio）→ PrintWindow 窗口抓拍。
退出码：0=环境达标；1=自检发现几何不一致或截图失败（供批量排障脚本用）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from src.config.settings import TaskConfig
from src.screenshot.capture_diagnostics import (
    collect_capture_environment,
    probe_page_geometry,
)
from src.webui.dpi import enable_windows_dpi_awareness

PROJECT_ROOT = Path(__file__).resolve().parents[1]


async def _selftest(config: TaskConfig) -> dict[str, Any]:
    """Take one real window capture through the production path."""

    from PIL import Image
    from playwright.async_api import async_playwright

    from src.screenshot.browser_options import (
        browser_context_options,
        browser_launch_options,
        launch_headed_with_fallback,
    )
    from src.screenshot.window_capture import capture_browser_window

    result: dict[str, Any] = {"ok": False}
    playwright = await async_playwright().start()
    browser = None
    try:
        launch_options = browser_launch_options(config)
        browser = await launch_headed_with_fallback(
            playwright, config, launch_options
        )
        result["browser_version"] = browser.version
        context = await browser.new_context(**browser_context_options(config))
        page = await context.new_page()
        await page.set_content(
            "<html lang='zh-CN'><head><title>capture-env selftest</title>"
            "</head><body style='margin:0;background:#f5f5f5'>"
            "<h1>POIR 截图环境自检</h1><p>若本图被裁切/位移，请回传 JSON。</p>"
            "</body></html>"
        )
        geometry = await probe_page_geometry(page, config)
        result["geometry"] = geometry
        output = (
            Path(tempfile.mkdtemp(prefix="poir-capture-env-")) / "selftest.jpg"
        )
        await capture_browser_window(page, output, config)
        size = output.stat().st_size if output.is_file() else 0
        if size > 0:
            with Image.open(output) as image:
                result["image_size"] = list(image.size)
        result["output"] = str(output)
        result["bytes"] = size
        result["ok"] = bool(geometry.get("ok")) and size > 0
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:  # noqa: BLE001 - 尽力而为
                pass
        await playwright.stop()
    return result


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # 与主程序一致：DPI awareness 决定 GetWindowRect 返回物理像素还是
    # 虚拟化尺寸，直接影响 PrintWindow 抓图分辨率。
    enable_windows_dpi_awareness()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="走生产链路真实抓拍一张并校验渲染几何",
    )
    parser.add_argument("--json", type=Path, default=None, help="诊断 JSON 保存路径")
    args = parser.parse_args()

    config = TaskConfig()
    report: dict[str, Any] = {
        "environment": collect_capture_environment(),
        "viewport": {
            "width": config.viewport_width,
            "height": config.viewport_height,
            "background_crawl_browser": config.background_crawl_browser,
            "browser_channel": config.browser_channel,
        },
    }
    ok = True
    if args.selftest:
        try:
            selftest = asyncio.run(_selftest(config))
        except Exception as error:  # noqa: BLE001 - 失败也是诊断结论
            selftest = {"ok": False, "error": str(error)}
        report["selftest"] = selftest
        ok = bool(selftest.get("ok"))

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text + "\n", encoding="utf-8")
        print(f"诊断 JSON 已保存: {args.json}")
    if args.selftest:
        print("自检结果:", "OK" if ok else "FAIL（请回传此 JSON）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
