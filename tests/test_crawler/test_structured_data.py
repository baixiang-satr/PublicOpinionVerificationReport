"""Regression tests: foreign JSON nodes must not override the main object."""

from src.crawler.structured_data import (
    StructuredDataExtractor,
    candidate_scope,
    url_content_ids,
)
from src.domain.models import ExtractionSource, PageData


def test_url_content_ids_reads_path_and_query() -> None:
    ids = url_content_ids(
        "https://www.douyin.com/video/7660061608801996068?previous_page=1"
    )

    assert "7660061608801996068" in ids


def test_url_content_ids_reads_bilibili_bv_id() -> None:
    ids = url_content_ids("https://www.bilibili.com/video/BV19DipBVE3c/")

    assert "BV19DipBVE3c" in ids


def test_kuaishou_compact_and_numeric_photo_ids_are_url_scoped() -> None:
    ids = url_content_ids(
        "https://m.gifshow.com/fw/photo/3xev27cpa7jba4i"
        "?photoId=3xev27cpa7jba4i"
        "&shareObjectId=5226990523508912741"
    )

    assert "3xev27cpa7jba4i" in ids
    assert "5226990523508912741" in ids
    assert candidate_scope({"photoId": "5226990523508912741"}, ids) == "main"
    assert candidate_scope({"photoId": "9000111222333444555"}, ids) == "foreign"


def test_candidate_scope_marks_comment_node_foreign() -> None:
    url_ids = url_content_ids("https://weibo.com/5644764907/5266894313755145")
    main_node = {"id_str": "5266894313755145", "text_raw": "正文"}
    comment_node = {"id": "5270000000000001", "text": "评论摘要"}
    idless_node = {"description": "没有强 ID 的节点"}

    assert candidate_scope(main_node, url_ids) == "main"
    assert candidate_scope(comment_node, url_ids) == "foreign"
    assert candidate_scope(idless_node, url_ids) == "unknown"


def test_record44_comment_summary_cannot_replace_full_body() -> None:
    # Regression for run 20260728-233950-f62e408e record 44: a ~342-char
    # comment summary replaced the 8,005-char article body.
    full_body = "正文内容" * 2_000
    payloads = [
        {
            "data": {
                "comments": [
                    {
                        "id": "5270000000000001",
                        "text": "评论摘要" * 80,
                        "user": {"screen_name": "评论用户"},
                    }
                ]
            }
        },
        {
            "data": {
                "status": {
                    "id_str": "5266894313755145",
                    "text_raw": full_body,
                    "user": {"screen_name": "正文作者"},
                }
            }
        },
    ]
    data = PageData(final_url="https://weibo.com/5644764907/5266894313755145")

    StructuredDataExtractor().apply(payloads, data, ExtractionSource.NETWORK_JSON)

    assert data.content_text == full_body
    assert data.author_name == "正文作者"


def test_recommendation_cards_cannot_override_main_fields() -> None:
    payloads = [
        {
            "aweme_list": [
                {
                    "aweme_id": "7660061608801996068",
                    "desc": "当前视频正文",
                    "author": {"nickname": "当前作者"},
                },
                {
                    "aweme_id": "7000000000000000999",
                    "desc": "推荐视频摘要",
                    "author": {"nickname": "推荐作者"},
                },
            ]
        }
    ]
    data = PageData(final_url="https://www.douyin.com/video/7660061608801996068")

    StructuredDataExtractor().apply(payloads, data, ExtractionSource.EMBEDDED_JSON)

    assert data.content_text == "当前视频正文"
    assert data.author_name == "当前作者"


def test_unknown_scope_nodes_keep_legacy_merge_behavior() -> None:
    # Pages whose URL exposes no strong ID must not starve structured fields.
    payloads = [
        {
            "data": {
                "headline": "接口标题",
                "articleBody": "接口正文",
                "authorName": "接口作者",
            }
        }
    ]
    data = PageData(final_url="https://example.test/article/detail")

    StructuredDataExtractor().apply(payloads, data, ExtractionSource.NETWORK_JSON)

    assert data.title == "接口标题"
    assert data.content_text == "接口正文"
    assert data.author_name == "接口作者"
