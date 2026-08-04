"""Strict rendered-UI gate before extraction or evidence capture."""

from __future__ import annotations

from typing import Any

from src.auth.login_evidence import page_authenticated_ui_state
from src.domain.models import TaskError


async def guest_ui_error(
    page: Any,
    browser_pool: Any,
    original_url: str,
) -> TaskError | None:
    """Return a blocking error when saved state still renders guest UI."""

    if await page_authenticated_ui_state(page) is not False:
        return None
    message = (
        "目标页面仍显示登录入口，保存登录态未在抓取 context 中生效；"
        "已在提取和截图前停止，避免生成游客身份数据。"
    )
    browser_pool.mark_access_invalid(
        page,
        original_url,
        barrier_code="LOGIN_UI_VISIBLE",
        message=message,
    )
    return TaskError("auth", "PLATFORM_AUTH_PAUSED", message, retryable=False)
