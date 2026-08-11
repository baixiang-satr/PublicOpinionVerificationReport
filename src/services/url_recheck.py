"""URL 有效性复验：三态判定与按任务目录持久化。

复验只探测 URL 是否仍可访问（不重新截图、不改写已填字段）：

- ``valid``：页面正常渲染，无访问/内容屏障；
- ``invalid``：HTTP 404、平台明确提示内容不存在/已删除/已下线、重定向首页；
- ``uncertain``：登录墙/验证码/风控/超时/网络错误等无法确认失效的情形。

结果按 ``evidence_id`` 持久化到任务目录 ``url_recheck.json``，断点续跑与
zip 导入重开任务后仍可读取；删除记录时用 ``prune`` 联动清理。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import json
import logging
from pathlib import Path
from typing import Any

from src.tools.page_access import AccessBarrier, AccessKind

logger = logging.getLogger(__name__)

RECHECK_FILENAME = "url_recheck.json"


class RecheckStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    UNCERTAIN = "uncertain"


# 能确证内容已失效的屏障；其余屏障（登录/验证码/风控/空壳等）一律存疑。
_INVALID_KINDS = {AccessKind.CONTENT_UNAVAILABLE, AccessKind.REDIRECTED_HOME}


def classify_barrier(barrier: AccessBarrier | None) -> RecheckStatus:
    """把访问屏障映射为复验三态。"""

    if barrier is None:
        return RecheckStatus.VALID
    if barrier.kind in _INVALID_KINDS:
        return RecheckStatus.INVALID
    return RecheckStatus.UNCERTAIN


@dataclass(frozen=True)
class RecheckEntry:
    status: RecheckStatus
    code: str
    message: str
    checked_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "status": self.status.value,
            "code": self.code,
            "message": self.message,
            "checked_at": self.checked_at,
        }

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "RecheckEntry":
        try:
            status = RecheckStatus(str(values.get("status") or ""))
        except ValueError:
            status = RecheckStatus.UNCERTAIN
        return cls(
            status=status,
            code=str(values.get("code") or ""),
            message=str(values.get("message") or ""),
            checked_at=str(values.get("checked_at") or ""),
        )


class UrlRecheckStore:
    """``job_dir/url_recheck.json`` 的读写（按 evidence_id 存三态结果）。"""

    def __init__(self, job_dir: Path) -> None:
        self._path = Path(job_dir) / RECHECK_FILENAME
        self._entries: dict[int, RecheckEntry] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        for key, values in raw.items():
            try:
                evidence_id = int(key)
            except (TypeError, ValueError):
                continue
            if isinstance(values, dict):
                self._entries[evidence_id] = RecheckEntry.from_dict(values)

    def _save(self) -> None:
        payload = {
            str(evidence_id): entry.to_dict()
            for evidence_id, entry in sorted(self._entries.items())
        }
        try:
            self._path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except OSError as error:
            logger.warning("Unable to persist URL recheck state %s: %s", self._path, error)

    def get(self, evidence_id: int) -> RecheckEntry | None:
        return self._entries.get(int(evidence_id))

    def set(
        self,
        evidence_id: int,
        status: RecheckStatus,
        code: str = "",
        message: str = "",
    ) -> RecheckEntry:
        entry = RecheckEntry(
            status,
            code,
            message,
            datetime.now().astimezone().isoformat(),
        )
        self._entries[int(evidence_id)] = entry
        self._save()
        return entry

    def prune(self, keep_eids: set[int]) -> None:
        """删除已不在任务中的记录复验结果（记录删除后联动清理）。"""

        remaining = {
            evidence_id: entry
            for evidence_id, entry in self._entries.items()
            if evidence_id in keep_eids
        }
        if len(remaining) != len(self._entries):
            self._entries = remaining
            self._save()


__all__ = [
    "RECHECK_FILENAME",
    "RecheckEntry",
    "RecheckStatus",
    "UrlRecheckStore",
    "classify_barrier",
]
