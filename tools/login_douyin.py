"""抖音交互式登录 CLI：打开可见 Edge 窗口，人工扫码/过验证后保存登录态。

运行方式：
    .venv\\Scripts\\python.exe tools\\login_douyin.py

窗口打开后请在 5 分钟内完成扫码登录（如出现滑块先完成滑块）。
登录态保存到本机加密库（LOCALAPPDATA，DPAPI），后续爬取/截图自动复用。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.auth.service import AuthManagerService
from src.auth.store import AuthProfileStore
from src.config.settings import TaskConfig, default_auth_store_dir


async def main() -> int:
    config = TaskConfig(
        headless=False,
        manual_intervention_timeout_seconds=300,
        enable_stealth=True,
        enable_extra_stealth=True,
    )
    store = AuthProfileStore(default_auth_store_dir())
    service = AuthManagerService(config, store)

    def on_progress(platform_key: str, status: object, message: str) -> None:
        print(f"  [{status}] {message}")

    print("打开抖音登录窗口（Edge）；请完成滑块/扫码登录，最长等待 5 分钟…")
    result = await service.probe(
        "douyin",
        use_saved_state=True,
        interactive=True,
        cancel_event=Event(),
        on_progress=on_progress,
    )
    print(f"\n结果: {result.status.value} — {result.message}")
    return 0 if result.status.value in {"valid", "guest_ok"} else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
