"""Baidu Tieba dedicated extractor.

Tieba thread pages are classic HTML: the first floor (楼主) holds the audited
content.  A DOM probe reads the thread title, first-floor body and author,
plus the ``.tail-info`` floor metadata for the publish time.  探针失败时通用
兜底会把整页可见文本（导航/吧列表/版权尾）当正文——一律经
:func:`sanitize_tieba_content` 净化，净化不出可信正文则留空待补录。
"""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_types import PlatformDefinition
from src.crawler.platforms.extract_helpers import (
    apply_json_fields,
    evaluate_value,
    found_any,
)
from src.crawler.platforms.registry import register
from src.domain.models import PageData
from src.utils.time_utils import parse_web_published_at

_DOM_PROBE = r"""
() => {
  const text = (element) => (element ? ((element.innerText || element.textContent || '').trim()) : '');
  const firstFloor =
    document.querySelector('.p_postlist .l_post') ||
    document.querySelector('.l_post') ||
    document.querySelector('[class*="l_post"]');
  const pickIn = (root, selectors) => {
    if (!root) return '';
    for (const selector of selectors) {
      const element = root.querySelector(selector);
      const value = text(element);
      if (value) return value;
    }
    return '';
  };
  const title = text(document.querySelector('.core_title_txt, .core_title, h1'));
  const content = pickIn(firstFloor, ['.j_d_post_content', '.d_post_content'])
    || text(document.querySelector('.j_d_post_content'))
    || text(document.querySelector('.d_post_content'));
  const author = pickIn(firstFloor, ['.p_author_name', '.d_name a', '.d_name', '[class*="author"]']);
  const authorAnchor = firstFloor
    ? firstFloor.querySelector('a.p_author_name, .d_name a, a[href*="/home/main"]')
    : null;
  const tailInfos = Array.from(
    (firstFloor || document).querySelectorAll('.tail-info')
  ).map((node) => text(node)).filter(Boolean);
  return {
    title,
    content,
    author,
    authorUrl: authorAnchor ? authorAnchor.href : '',
    tailInfos
  };
}
"""

_DATE_HINT = re.compile(r"(?:19|20)\d{2}[-/.年]\d{1,2}")

#: 整行匹配即判定为页面框架噪声（导航按钮/页脚版权/吧推荐位）。
_CHROME_LINE_EXACT = frozenset(
    {
        "回复", "点赞", "收藏", "分享", "全部回复", "只看楼主", "正序", "倒序",
        "登录", "注册", "首页", "我的", "发贴", "发帖", "吧", "进吧看看",
        "使用百度前必读", "大家都在逛的吧",
    }
)
_CHROME_LINE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^\d+$",
        r"^关注\d+",
        r"^建吧日期",
        r"^吧主很懒",
        r"^百度版权声明",
        r"^信息网络传播视听节目许可证",
        r"^举报电话",
        r"^参与讨论",
        r"^别让楼主寂寞太久",
        r"^贴吧协议",
        r"^隐私政策",
        r"^投诉反馈",
    )
)

#: 正文中仍含这些短语即判定净化失败（宁可留空，不给错内容）。
_FATAL_PHRASES = ("百度版权声明", "大家都在逛的吧", "别让楼主寂寞太久")

_TITLE_SUFFIX = re.compile(r"[-_—]?百度贴吧\s*$")


class TiebaExtractor:
    platform_keys = ("tieba",)

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        probe = await evaluate_value(page, _DOM_PROBE)
        if not isinstance(probe, Mapping):
            return None
        title = _clean(probe.get("title"))
        published_raw = _pick_time(probe.get("tailInfos"))
        data = PageData(final_url=document.url)
        applied = apply_json_fields(
            data,
            {
                "title": title,
                "content_text": sanitize_tieba_content(probe.get("content"), title),
                "author_name": _clean(probe.get("author")),
                "author_url": _clean(probe.get("authorUrl")),
                "published_at_raw": published_raw,
                "published_at_dt": parse_web_published_at(published_raw) if published_raw else None,
            },
        )
        return data if applied and found_any(data, "content_text", "title") else None


def sanitize_tieba_content(raw: Any, title: str | None = None) -> str | None:
    """净化贴吧正文：标题锚点去导航前缀，遇框架行截断，残留噪声则留空。

    贴吧正文宁可留空待补录，也不交付混入页面框架（"大家都在逛的吧"、
    "百度版权声明" 等）的错误内容。
    """

    if not isinstance(raw, str):
        return None
    lines = [line.strip() for line in raw.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return None
    if title:
        core = _TITLE_SUFFIX.sub("", title).strip()
        if core:
            for index, line in enumerate(lines):
                if line == core:
                    lines = lines[index + 1 :]
                    break
                if core in line:
                    lines = lines[index:]
                    break
    kept: list[str] = []
    for line in lines:
        if _is_chrome_line(line):
            break
        kept.append(line)
    cleaned = "\n".join(kept).strip()
    if not cleaned:
        return None
    if any(phrase in cleaned for phrase in _FATAL_PHRASES):
        return None
    return cleaned


def _is_chrome_line(line: str) -> bool:
    if line in _CHROME_LINE_EXACT:
        return True
    return any(pattern.search(line) for pattern in _CHROME_LINE_PATTERNS)


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _pick_time(tail_infos: Any) -> str | None:
    if not isinstance(tail_infos, (list, tuple)):
        return None
    for item in tail_infos:
        if not isinstance(item, str):
            continue
        # 「建吧日期」是吧信息面板的吧创建时间，不是发帖时间。
        if "建吧" in item or "吧主" in item:
            continue
        if _DATE_HINT.search(item):
            return item.strip()
    return None


register(TiebaExtractor())
