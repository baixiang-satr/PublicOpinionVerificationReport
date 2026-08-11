"""「函」文档选择入口的 js_api mixin（bridge.py 行数受限，逻辑独立成模块）。

宿主类需提供：``self._pick_file``（原生文件选择框）、
``self._letter_path``（会话级函文件路径状态，初始 None）。
"""

from __future__ import annotations

from typing import Any

from src.utils.file_utils import UnsafeFileNameError, require_safe_file_name

_LETTER_FILE_TYPES = (
    "函文档 (*.doc;*.docx;*.pdf;*.jpg;*.jpeg;*.png;*.bmp;*.webp)",
    "全部文件 (*.*)",
)


class LetterApiMixin:
    _letter_path: Any = None

    def pick_letter_file(self) -> dict:
        """弹出文件选择器选择函文档；返回安全文件名供前端展示。"""

        path = self._pick_file(_LETTER_FILE_TYPES)
        if path is None:
            return {"ok": False, "name": "", "message": ""}
        try:
            name = require_safe_file_name(path.name)
        except UnsafeFileNameError as error:
            return {"ok": False, "name": "", "message": str(error)}
        self._letter_path = path
        return {"ok": True, "name": name, "message": ""}

    def clear_letter_file(self) -> dict:
        self._letter_path = None
        return {"ok": True}

    def letter_state(self) -> dict:
        path = self._letter_path
        return {"name": path.name if path else ""}


__all__ = ["LetterApiMixin"]
