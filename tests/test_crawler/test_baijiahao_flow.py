"""Offline regression coverage for Baijiahao extraction and export routing."""

from __future__ import annotations

import asyncio
from urllib.parse import quote

from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_catalog import find_platform
from src.crawler.platform_router import PlatformRouter
from src.crawler.platforms.baijiahao import BaijiahaoExtractor
from src.domain.models import ExtractionSource, RecordResult, RecordStatus, UrlTask
from src.export.row_mapper import TemplateRowMapper
from src.services.export_flow import build_export_rows


class FakePage:
    async def evaluate(self, _script: str) -> None:
        return None


def _extract(document: RenderedDocument):
    definition = find_platform(document.url)
    assert definition is not None
    return asyncio.run(
        BaijiahaoExtractor().extract(FakePage(), document, definition)
    )


def _builder_url(title: str) -> str:
    return (
        "https://baijiahao.baidu.com/builder/na/hot/detail"
        f"?channel={quote('北京')}&title={quote(title)}"
    )


def _mbd_video_url() -> str:
    return (
        "https://mbd.baidu.com/newspage/data/videolanding"
        "?nid=sv_4426235232588179908"
    )


def test_baijiahao_mbd_landingsuper_article_matches_nid_without_prefix() -> None:
    """landingsuper 图文落地页：URL nid 带 news_ 前缀，节点常是纯数字。"""

    target = {
        "nid": "8767880893518512259",
        "title": "图文落地页标题",
        "content": "<p>图文落地页正文内容</p>",
        "publishTime": "2026-08-01 09:30:00",
        "mediaInfo": {"mediaName": "百家号图文作者", "id": "media-news-1"},
    }
    foreign = {
        "nid": "1111111111111111111",
        "title": "外域推荐文章标题",
        "content": "<p>外域推荐正文不应进入结果</p>",
        "mediaInfo": {"mediaName": "外域作者"},
    }
    document = RenderedDocument(
        url=(
            "https://mbd.baidu.com/newspage/data/landingsuper"
            "?nid=news_8767880893518512259"
        ),
        network_payloads=(
            {"data": {"articleInfo": foreign}},
            {"data": {"articleInfo": target}},
        ),
    )

    data = _extract(document)

    assert data is not None
    assert data.title == "图文落地页标题"
    assert data.content_text == "图文落地页正文内容"
    assert data.author_name == "百家号图文作者"


def test_baijiahao_matches_requested_article_and_ignores_recommendation() -> None:
    target = {
        "articleId": "target123",
        "title": "目标百家号标题",
        "content": "<p>目标百家号完整正文</p>",
        "publishTime": "2026-07-31 15:30:00",
        "authorInfo": {
            "name": "目标作者",
            "uk": "author-9",
            "homeUrl": "/home/author-9",
        },
    }
    recommendation = {
        "articleId": "other456",
        "title": "推荐内容标题",
        "content": "推荐内容正文",
        "authorInfo": {"name": "推荐作者"},
    }
    document = RenderedDocument(
        url="https://baijiahao.baidu.com/s?id=target123",
        embedded_payloads=({"items": [recommendation]},),
        network_payloads=({"data": {"articleInfo": target}},),
    )

    data = _extract(document)

    assert data is not None
    assert data.title == "目标百家号标题"
    assert data.content_text == "目标百家号完整正文"
    assert data.author_name == "目标作者"
    assert data.author_id == "author-9"
    assert data.author_url == "https://baijiahao.baidu.com/home/author-9"
    assert data.published_at is not None
    assert data.field_sources["content_text"] == ExtractionSource.NETWORK_JSON


def test_baijiahao_builder_url_matches_exact_title_in_payload() -> None:
    wanted_title = "租房平台自如被指密集单方解约"
    document = RenderedDocument(
        url=_builder_url(wanted_title),
        network_payloads=(
            {
                "items": [
                    {
                        "title": "无关热点",
                        "content": "不能误采的推荐正文",
                        "author": {"name": "其他账号"},
                    },
                    {
                        "title": wanted_title,
                        "content": "<div>命中的热点详情正文</div>",
                        "publish_time": "2026-07-31 15:00:00",
                        "author": {"name": "百家号账号", "uk": "uk-123"},
                    },
                ]
            },
        ),
    )

    data = _extract(document)

    assert data is not None
    assert data.title == wanted_title
    assert data.content_text == "命中的热点详情正文"
    assert data.author_name == "百家号账号"
    assert data.author_id == "uk-123"


def test_baijiahao_mbd_video_landing_extracts_requested_video() -> None:
    target = {
        "nid": "sv_4426235232588179908",
        "videoTitle": "百度视频落地页标题",
        "videoDesc": "百度视频落地页正文",
        "publishTime": "2026-07-31 15:40:00",
        "mediaInfo": {
            "mediaName": "百家号视频作者",
            "id": "media-7788",
        },
    }
    recommendation = {
        "nid": "sv_other",
        "videoTitle": "推荐视频",
        "videoDesc": "不能误采的推荐视频简介",
        "mediaInfo": {"mediaName": "推荐作者"},
    }
    document = RenderedDocument(
        url=_mbd_video_url(),
        embedded_payloads=({"items": [recommendation]},),
        network_payloads=({"data": {"videoInfo": target}},),
    )

    data = _extract(document)

    assert data is not None
    assert data.title == "百度视频落地页标题"
    assert data.content_text == "百度视频落地页正文"
    assert data.author_name == "百家号视频作者"
    assert data.author_id == "media-7788"
    assert data.published_at is not None


def test_baijiahao_mbd_rejects_download_chrome_even_when_it_carries_nid() -> None:
    document = RenderedDocument(
        url=_mbd_video_url(),
        embedded_payloads=(
            {
                "videoInfo": {
                    "nid": "sv_4426235232588179908",
                    "title": "扫码下载百度APP",
                    "content": "搜最新资讯、看热门视频",
                    "author": {"name": "导航栏账号"},
                }
            },
        ),
        meta={
            "og:title": "扫码下载百度APP",
            "description": "搜最新资讯、看热门视频",
        },
    )

    assert _extract(document) is None


def test_baijiahao_builder_url_preserves_query_title_without_page_payload() -> None:
    wanted_title = "租房平台自如被指密集单方解约"

    data = _extract(RenderedDocument(url=_builder_url(wanted_title)))

    assert data is not None
    assert data.title == wanted_title
    assert data.content_text is None
    assert data.field_sources["title"] == ExtractionSource.DERIVED_URL


def test_baijiahao_mbd_needs_review_still_enters_template_with_url() -> None:
    url = _mbd_video_url()
    task = UrlTask(1, url, url)
    record = RecordResult(task=task, status=RecordStatus.NEEDS_REVIEW)

    rows = build_export_rows(
        [record],
        TemplateRowMapper(),
        PlatformRouter(),
    )

    assert len(rows) == 1
    assert rows[0].sheet_name == "公众号"
    assert url in rows[0].values_by_column.values()
    assert record.route is not None
    assert record.route.platform_value == "百度_百家号_公众号"
