"""截图预览载荷：降采样后 base64 内联，避免 WebView2 内存峰值。

原图（全页长截图、多屏 4K 冻结图）直接 base64 会让渲染进程内存暴涨，
是补录/查看截图阶段无报错闪退的主因之一。预览统一压到最长边
``MAX_PREVIEW_EDGE`` 的 JPEG；已足够小的 JPEG 直接回传原文件。
"""

from __future__ import annotations

import base64
from io import BytesIO
import logging
import mimetypes
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

MAX_PREVIEW_EDGE = 1600
JPEG_QUALITY = 80


def image_payload(path: Path | None) -> dict | None:
    """返回 {"data_url", "name"} 预览载荷；*path* 为 None 时返回 None。"""

    if path is None:
        return None
    path = Path(path)
    preview = _preview_bytes(path)
    if preview is not None:
        raw, mime = preview, "image/jpeg"
    else:
        try:
            raw = path.read_bytes()
        except OSError as error:
            logger.warning("预览读取失败 %s：%s", path, error)
            return None
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(raw).decode("ascii")
    return {"data_url": f"data:{mime};base64,{encoded}", "name": path.name}


def _preview_bytes(path: Path) -> bytes | None:
    """降采样 JPEG 字节；无需降采样（小 JPEG）或失败时返回 None。"""

    try:
        with Image.open(path) as opened:
            width, height = opened.size
            scale = MAX_PREVIEW_EDGE / max(width, height)
            if scale >= 1 and path.suffix.lower() in {".jpg", ".jpeg"}:
                return None
            image = opened.convert("RGB")
            if scale < 1:
                image = image.resize(
                    (
                        max(1, round(width * scale)),
                        max(1, round(height * scale)),
                    ),
                    Image.Resampling.LANCZOS,
                )
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
            return buffer.getvalue()
    except Exception:  # noqa: BLE001 — 解码失败退回原图字节
        logger.warning("预览降采样失败，退回原图：%s", path)
        return None
