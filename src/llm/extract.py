"""LLM 提取兜底：提示词构造、响应解析与字段回填。

反幻觉铁律：模型返回的标题/作者必须在原文中（忽略空白）子串命中；
发布时间必须能直接从原文的日期片段中复解析出同一时刻（分钟级）。任何
无法在原文中找到出处的值一律丢弃——LLM 只做“提取”，绝不做“生成”。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from src.domain.models import PageData
from src.llm.models import MAX_AUTHOR_CHARS, MAX_TITLE_CHARS
from src.utils.time_utils import (  # noqa: SLF001 - 复用既有日期片段规则
    _DATE_FRAGMENT_PATTERN,
    parse_published_at_from_text,
    parse_web_published_at,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是网页字段提取助手。只从用户提供的原文中提取信息，禁止使用原文之外"
    "的知识或编造内容。找不到的字段输出 null。只输出 JSON，不要输出任何解释。"
)

_USER_TEMPLATE = """网页地址：{url}

网页原文（正文与截图 OCR 文字，可能不完整）：
{text}

请从上面的原文中提取三个字段，输出 JSON：
{{"title": 文章标题或 null, "author_name": 作者/账号昵称或 null, "published_at": 发布时间原文或 null}}"""


def needs_fallback(page: PageData) -> bool:
    """标题/作者/发布时间任一缺失时才值得调用 LLM。"""

    return bool(not page.title or not page.author_name or page.published_at is None)


def source_text(page: PageData, max_chars: int) -> str:
    """拼接正文与 OCR 文本作为 LLM 输入，超长截断。"""

    parts = [
        text.strip()
        for text in (page.content_text, page.ocr_text)
        if text and text.strip()
    ]
    return "\n".join(parts)[:max_chars]


def build_extraction_prompt(url: str, text: str) -> tuple[str, str]:
    return SYSTEM_PROMPT, _USER_TEMPLATE.format(url=url, text=text)


def parse_extraction_response(raw: str) -> dict:
    """容错解析模型输出：剥离代码围栏，截取首个 JSON 对象。"""

    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def apply_extraction(page: PageData, data: dict, source: str) -> list[str]:
    """把通过原文校验的字段回填到 page，返回实际填入的字段名列表。"""

    filled: list[str] = []
    title = _clean(data.get("title"), MAX_TITLE_CHARS)
    if title and not page.title and _appears_in_source(title, source):
        page.title = title
        filled.append("title")
    author = _clean(data.get("author_name"), MAX_AUTHOR_CHARS)
    if author and not page.author_name and _appears_in_source(author, source):
        page.author_name = author
        filled.append("author_name")
    raw_time = _clean(data.get("published_at"), 60)
    if raw_time and page.published_at is None:
        parsed = parse_published_at_from_text(raw_time)
        if parsed is not None and _time_in_source(parsed, source):
            page.published_at = parsed
            page.published_at_raw = raw_time
            filled.append("published_at")
    if not filled and data:
        logger.info(
            "LLM 返回未能通过原文校验，已丢弃：%r",
            {key: data.get(key) for key in ("title", "author_name", "published_at")},
        )
    return filled


def _clean(value: object, max_chars: int) -> str:
    if not isinstance(value, str):
        return ""
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) > max_chars:
        return ""
    return text


def _appears_in_source(value: str, source: str) -> bool:
    return _squash(value) in _squash(source)


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _time_in_source(parsed: datetime, source: str) -> bool:
    """原文任一日期片段解析结果与模型时间一致（分钟级）才算有出处。"""

    target = parsed.replace(second=0, microsecond=0)
    for match in _DATE_FRAGMENT_PATTERN.finditer(source):
        candidate = parse_web_published_at(match.group(0))
        if candidate is not None and candidate.replace(second=0, microsecond=0) == target:
            return True
    return False
