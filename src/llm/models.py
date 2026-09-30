"""LLM 设置数据模型与序列化（OpenAI 兼容接口）。"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_INPUT_CHARS = 6000
MAX_TITLE_CHARS = 120
MAX_AUTHOR_CHARS = 40


@dataclass(frozen=True)
class LlmSettings:
    """OpenAI 兼容接口的连接设置；api_key 仅在加密后持久化。"""

    enabled: bool = False
    base_url: str = DEFAULT_BASE_URL
    model: str = ""
    api_key: str = ""
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_input_chars: int = MAX_INPUT_CHARS

    def is_complete(self) -> bool:
        """启用且地址、模型、Key 齐备时才允许发起请求。"""

        return bool(
            self.enabled
            and self.base_url.strip()
            and self.model.strip()
            and self.api_key.strip()
        )

    def masked_payload(self) -> dict:
        """供前端展示的载荷；Key 只回传掩码，永不回传原文。"""

        return {
            "enabled": self.enabled,
            "base_url": self.base_url,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "max_input_chars": self.max_input_chars,
            "has_api_key": bool(self.api_key),
            "api_key_masked": mask_api_key(self.api_key),
        }


def mask_api_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}…{key[-4:]}"
