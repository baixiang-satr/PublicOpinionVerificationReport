"""浏览器上传文件的暂存管理。

上传到 ``output/_uploads/<uuid>/`` 后即交给 WebUIBridge 的 accept_* 方法处理。
URL 文件与函文档在任务运行期间仍会被引用，不能即传即删；整个暂存目录在
服务启动时清空（重启后旧任务无法继续，残留文件无意义）。
"""

from __future__ import annotations

import logging
from pathlib import Path
import shutil
import uuid

logger = logging.getLogger(__name__)


class UploadStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def reset(self) -> None:
        """服务启动时清空上一进程的暂存文件。"""

        try:
            if self.root.is_dir():
                shutil.rmtree(self.root, ignore_errors=True)
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            logger.warning("上传暂存目录清理失败 %s：%s", self.root, error)

    def save(self, kind: str, filename: str, data: bytes) -> Path:
        """保存单个上传文件，返回暂存路径（每次调用一个独立子目录）。"""

        safe_name = Path(filename).name or "upload.bin"
        folder = self.root / f"{kind}-{uuid.uuid4().hex[:12]}"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / safe_name
        target.write_bytes(data)
        return target


__all__ = ["UploadStore"]
