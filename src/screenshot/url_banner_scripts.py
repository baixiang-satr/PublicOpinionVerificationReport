"""自动截图 URL 横幅注入脚本（独立模块，避免 page_shooter 行数膨胀）。

截图前向页面注入顶部 fixed 横幅：显示重定向后的最终 URL（location.href）、
深色底白字、最高 z-index 避免被页面遮挡；截图完成后移除，DOM 不留残留。
内容页与个人主页两类自动截图都经 ``PageShooter.capture_named`` 单点覆盖；
人工框选截图本身含浏览器地址栏，不走这里。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

BANNER_ELEMENT_ID = "__poir_url_banner"

_INJECT_JS = """(function () {
  var old = document.getElementById('__poir_url_banner');
  if (old) old.remove();
  var banner = document.createElement('div');
  banner.id = '__poir_url_banner';
  banner.textContent = location.href;
  var s = banner.style;
  s.position = 'fixed';
  s.top = '0';
  s.left = '0';
  s.right = '0';
  s.zIndex = '2147483647';
  s.background = 'rgba(17, 24, 39, 0.94)';
  s.color = '#ffffff';
  s.font = '12px/1.5 Consolas, "Courier New", monospace';
  s.padding = '4px 10px';
  s.wordBreak = 'break-all';
  s.pointerEvents = 'none';
  (document.documentElement || document.body).appendChild(banner);
})();"""

_REMOVE_JS = """(function () {
  var el = document.getElementById('__poir_url_banner');
  if (el) el.remove();
})();"""


async def inject_url_banner(page: Any) -> None:
    """注入 URL 横幅；失败仅记日志，不阻断截图主流程。"""

    try:
        await page.evaluate(_INJECT_JS)
    except Exception as error:  # noqa: BLE001 - banner must never break capture
        logger.warning("Unable to inject URL banner: %s", error)


async def remove_url_banner(page: Any) -> None:
    """移除 URL 横幅；失败仅记日志（finally 中调用不得掩盖截图异常）。"""

    try:
        await page.evaluate(_REMOVE_JS)
    except Exception as error:  # noqa: BLE001
        logger.warning("Unable to remove URL banner: %s", error)


__all__ = ["BANNER_ELEMENT_ID", "inject_url_banner", "remove_url_banner"]
