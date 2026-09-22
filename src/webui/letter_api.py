"""「函」文档选择入口的 js_api mixin（bridge.py 行数受限，逻辑独立成模块）。

宿主类需提供：``self._window_provider``（pywebview 窗口提供者）；状态为会话级
``self._letter_paths``（函图片路径元组，初始空）。仅接受 .jpg/.jpeg，可多选。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.utils.file_utils import UnsafeFileNameError, require_safe_file_name

_LETTER_FILE_TYPES = ("函文档图片 (*.jpg;*.jpeg)",)
_LETTER_SUFFIXES = {".jpg", ".jpeg"}


class LetterApiMixin:
    _letter_paths: Any = ()
    _window_provider: Any = None

    def pick_letter_file(self) -> dict:
        """弹出多选文件选择器选择函文档图片；返回安全文件名列表供前端展示。"""

        import webview

        window = (
            self._window_provider()
            if self._window_provider
            else webview.windows[0]
        )
        result = window.create_file_dialog(
            webview.FileDialog.OPEN,
            file_types=_LETTER_FILE_TYPES,
            allow_multiple=True,
        )
        if not result:
            return {"ok": False, "names": [], "message": ""}
        raw = result if isinstance(result, (list, tuple)) else (result,)
        names: list[str] = []
        paths: list[Path] = []
        for item in raw:
            path = Path(item)
            if path.suffix.lower() not in _LETTER_SUFFIXES:
                return {
                    "ok": False,
                    "names": [],
                    "message": f"函文档必须是 .jpg 图片，已拒绝：{path.name}",
                }
            try:
                name = require_safe_file_name(path.name)
            except UnsafeFileNameError as error:
                return {"ok": False, "names": [], "message": str(error)}
            if name not in names:
                names.append(name)
                paths.append(path)
        self._letter_paths = tuple(paths)
        return {"ok": True, "names": names, "message": ""}

    def remove_letter_file(self, name: str) -> dict:
        """从已选列表移除单个函文件（按文件名匹配）。"""

        kept = tuple(path for path in self._letter_paths if path.name != name)
        self._letter_paths = kept
        return {"ok": True, "names": [path.name for path in kept]}

    def clear_letter_file(self) -> dict:
        self._letter_paths = ()
        return {"ok": True}

    def letter_state(self) -> dict:
        return {"names": [path.name for path in self._letter_paths]}


__all__ = ["LetterApiMixin"]
