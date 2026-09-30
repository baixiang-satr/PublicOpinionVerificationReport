"""LLM 失效判定：提示词、响应解析与逐字引用出处校验（纯函数，全离线）。"""

from __future__ import annotations

from src.llm.deletion import (
    DELETION_SYSTEM_PROMPT,
    build_deletion_prompt,
    parse_deletion_response,
    verify_deletion_quote,
)


def test_system_prompt_demands_verbatim_quote() -> None:
    for keyword in ("逐字引用", "不得改写", "不得翻译", "null", "JSON"):
        assert keyword in DELETION_SYSTEM_PROMPT


def test_build_prompt_embeds_url_text_and_length_limits() -> None:
    system, user = build_deletion_prompt("https://a.test/1", "正文内容")
    assert system == DELETION_SYSTEM_PROMPT
    assert "https://a.test/1" in user
    assert "正文内容" in user
    assert "5-80 字" in user
    assert "deleted_quote" in user


def test_parse_accepts_plain_and_fenced_json() -> None:
    assert parse_deletion_response('{"deleted_quote": "内容已删除"}') == "内容已删除"
    fenced = '```json\n{"deleted_quote": "该视频已失效"}\n```'
    assert parse_deletion_response(fenced) == "该视频已失效"


def test_parse_null_and_missing_key_yield_none() -> None:
    assert parse_deletion_response('{"deleted_quote": null}') is None
    assert parse_deletion_response('{"other": "内容已删除"}') is None
    assert parse_deletion_response("null") is None
    assert parse_deletion_response("不是 JSON") is None


def test_parse_enforces_quote_length_limits() -> None:
    assert parse_deletion_response('{"deleted_quote": "已删除"}') is None  # <5 字
    too_long = "已" * 81
    assert parse_deletion_response(f'{{"deleted_quote": "{too_long}"}}') is None
    boundary = "已" * 80
    assert parse_deletion_response(f'{{"deleted_quote": "{boundary}"}}') == boundary


def test_verify_quote_requires_verbatim_source_hit() -> None:
    source = "公告：该作品已被作者删除，感谢关注。"
    assert verify_deletion_quote("该作品已被作者删除", source) == "该作品已被作者删除"
    # 编造的、原文中不存在的句子一律丢弃
    assert verify_deletion_quote("该内容因违规被平台下架", source) is None
    assert verify_deletion_quote(None, source) is None


def test_verify_quote_ignores_whitespace_differences() -> None:
    source = "该视频已失效\n\n  请返回首页"
    assert verify_deletion_quote(" 该视频已失效 ", source) == "该视频已失效"
