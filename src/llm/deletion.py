"""LLM 失效判定：删除文案"逐字引用"提取的提示词、响应解析与出处校验。

与字段兜底同一反幻觉铁律：模型返回的删除引文必须在原文中（忽略空白）
子串命中，否则一律丢弃——LLM 只做"引用"，绝不做"生成"。
"""

from __future__ import annotations

import json
import logging
import re

from src.llm.extract import _appears_in_source  # noqa: SLF001 - 白空间忽略子串校验复用

logger = logging.getLogger(__name__)

#: 引文长度约束：过短没有判定价值，过长基本是模型拼贴。
MIN_QUOTE_CHARS = 5
MAX_QUOTE_CHARS = 80

DELETION_SYSTEM_PROMPT = (
    "你是网页内容核验助手。判断用户提供的网页原文是否明确表明该内容已被删除、"
    "不存在、已下线或已无查看权限。若是，从原文中逐字引用一句最能表明内容已"
    "删除或不存在的原句：必须是原文中连续出现的文字，不得改写、不得翻译、"
    "不得增补或删减原文没有的字。若不是或找不到这样的原句，输出 null。"
    "只输出 JSON，不要输出任何解释。"
)

_USER_TEMPLATE = """网页地址：{url}

网页原文（正文与截图 OCR 文字，可能不完整）：
{text}

如果原文明确表明内容已删除/不存在/已下线/无查看权限，请逐字引用一句原句（{min_chars}-{max_chars} 字），否则输出 null：
{{"deleted_quote": 逐字引用的原句或 null}}"""


def build_deletion_prompt(url: str, text: str) -> tuple[str, str]:
    return DELETION_SYSTEM_PROMPT, _USER_TEMPLATE.format(
        url=url,
        text=text,
        min_chars=MIN_QUOTE_CHARS,
        max_chars=MAX_QUOTE_CHARS,
    )


def parse_deletion_response(raw: str) -> str | None:
    """容错解析模型输出：剥离代码围栏，截取首个 JSON 对象并校验引文长度。"""

    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    quote = data.get("deleted_quote")
    if not isinstance(quote, str):
        return None
    quote = re.sub(r"\s+", " ", quote).strip()
    if not MIN_QUOTE_CHARS <= len(quote) <= MAX_QUOTE_CHARS:
        logger.info("LLM 删除引文长度不合规（%d 字），已丢弃：%r", len(quote), quote[:40])
        return None
    return quote


def verify_deletion_quote(quote: str | None, source: str) -> str | None:
    """引文必须在原文中（忽略空白）子串命中才采信，否则返回 None。"""

    if not quote:
        return None
    normalized = re.sub(r"\s+", " ", quote).strip()
    if normalized and _appears_in_source(normalized, source):
        return normalized
    logger.info("LLM 删除引文未在原文中命中（疑似编造），已丢弃：%r", quote[:40])
    return None


__all__ = [
    "DELETION_SYSTEM_PROMPT",
    "MAX_QUOTE_CHARS",
    "MIN_QUOTE_CHARS",
    "build_deletion_prompt",
    "parse_deletion_response",
    "verify_deletion_quote",
]
