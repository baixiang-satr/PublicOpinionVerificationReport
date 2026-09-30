"""「函」文档选择的 js_api mixin（bridge.py 行数受限，逻辑独立成模块）。

B/S 模式下文件由浏览器上传（``POST /api/upload/letter``）后经
``FileIntakeApiMixin.accept_letter_files`` 登记；本模块只保留已选列表的
查询与移除。仅接受 .jpg/.jpeg，可多份。
"""

from __future__ import annotations

from typing import Any


class LetterApiMixin:
    _letter_paths: Any = ()

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
