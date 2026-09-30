"""LLM 设置持久化：api_key 经 Windows DPAPI 加密，其余字段明文 JSON。

存储位置为 ``LOCALAPPDATA/PublicOpinionVerificationReport/llm_settings.json``；
文件损坏或解密失败一律回落到默认（关闭）配置，绝不因设置损坏影响抓取。
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from pathlib import Path

from src.auth.protection import (
    StateProtectionError,
    StateProtector,
    default_state_protector,
)
from src.config.settings import _local_app_data_root
from src.llm.models import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_INPUT_CHARS,
    LlmSettings,
)

logger = logging.getLogger(__name__)

_SETTINGS_FILENAME = "llm_settings.json"


class LlmSettingsStore:
    """Load/save LLM settings; the API key is DPAPI-encrypted at rest."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        protector: StateProtector | None = None,
    ) -> None:
        self._path = path or (_local_app_data_root() / _SETTINGS_FILENAME)
        self._protector = protector

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> LlmSettings:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return LlmSettings()
        except (OSError, ValueError) as error:
            logger.warning("LLM settings file unreadable, using defaults: %s", error)
            return LlmSettings()
        if not isinstance(raw, dict):
            return LlmSettings()
        return LlmSettings(
            enabled=bool(raw.get("enabled", False)),
            base_url=str(raw.get("base_url") or DEFAULT_BASE_URL),
            model=str(raw.get("model") or ""),
            api_key=self._decrypt_key(str(raw.get("api_key_b64") or "")),
            timeout_seconds=_bounded_timeout(raw.get("timeout_seconds")),
            max_input_chars=_bounded_max_chars(raw.get("max_input_chars")),
        )

    def save(self, settings: LlmSettings) -> None:
        payload = {
            "enabled": settings.enabled,
            "base_url": settings.base_url,
            "model": settings.model,
            "api_key_b64": self._encrypt_key(settings.api_key),
            "timeout_seconds": settings.timeout_seconds,
            "max_input_chars": settings.max_input_chars,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self._path)

    def _encrypt_key(self, api_key: str) -> str:
        if not api_key:
            return ""
        protector = self._protector or default_state_protector()
        return base64.b64encode(protector.protect(api_key.encode("utf-8"))).decode("ascii")

    def _decrypt_key(self, encoded: str) -> str:
        if not encoded:
            return ""
        try:
            protector = self._protector or default_state_protector()
            return protector.unprotect(base64.b64decode(encoded)).decode("utf-8")
        except (StateProtectionError, ValueError, binascii.Error) as error:
            # 换机/换用户后 DPAPI 解密必然失败：按未配置 Key 处理。
            logger.warning("Stored LLM API key undecryptable, cleared: %s", error)
            return ""


def _bounded_timeout(value: object) -> float:
    try:
        timeout = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    return min(120.0, max(5.0, timeout))


def _bounded_max_chars(value: object) -> int:
    try:
        chars = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return MAX_INPUT_CHARS
    return min(20000, max(500, chars))


_default_store: LlmSettingsStore | None = None


def default_llm_settings_store() -> LlmSettingsStore:
    """Process-wide store shared by the bridge mixin and the crawl engine."""

    global _default_store
    if _default_store is None:
        _default_store = LlmSettingsStore()
    return _default_store
