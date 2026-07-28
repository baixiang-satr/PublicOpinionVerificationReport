from src.crawler.field_resolver import consider_field
from src.domain.models import ExtractionSource, PageData


def test_short_network_fragment_does_not_replace_long_platform_article() -> None:
    article = "这是完整正文。" * 100
    page = PageData()
    assert consider_field(
        page,
        "content_text",
        article,
        ExtractionSource.PLATFORM_DOM,
    )

    replaced = consider_field(
        page,
        "content_text",
        "这只是评论中的一个短片段。" * 15,
        ExtractionSource.NETWORK_JSON,
    )

    assert not replaced
    assert page.content_text == article
    assert page.field_sources["content_text"] == ExtractionSource.PLATFORM_DOM


def test_network_content_still_wins_when_it_is_not_materially_shorter() -> None:
    page = PageData()
    assert consider_field(
        page,
        "content_text",
        "平台正文" * 100,
        ExtractionSource.PLATFORM_DOM,
    )

    replaced = consider_field(
        page,
        "content_text",
        "接口正文" * 90,
        ExtractionSource.NETWORK_JSON,
    )

    assert replaced
    assert page.field_sources["content_text"] == ExtractionSource.NETWORK_JSON
