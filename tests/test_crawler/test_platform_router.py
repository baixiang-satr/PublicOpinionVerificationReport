import pytest

from src.crawler.platform_catalog import PLATFORM_DEFINITIONS, find_platform, validate_catalog
from src.crawler.platform_router import PlatformRouter
from src.domain.models import PageData
from src.domain.template_schema import SHEET_LAYOUTS


def test_platform_catalog_exactly_covers_url_supported_template_enums() -> None:
    validate_catalog()
    expected = set()
    for sheet_name, layout in SHEET_LAYOUTS.items():
        if sheet_name in {"群聊", "朋友圈"}:
            continue
        platform_column = layout.field_columns["platform"]
        expected.update(layout.validation_values[platform_column])
    actual = {definition.platform_value for definition in PLATFORM_DEFINITIONS}

    assert actual == expected
    assert not {definition.sheet_name for definition in PLATFORM_DEFINITIONS} & {"群聊", "朋友圈"}


@pytest.mark.parametrize(
    ("url", "sheet_name", "platform_value"),
    [
        ("https://item.jd.com/100.html", "电商平台", "京东_京东商城_电商平台"),
        ("https://www.douyin.com/product/123", "电商平台", "字节跳动_抖音_电商平台"),
        ("https://haohuo.jinritemai.com/ecommerce/trade/detail/index.html?id=3794554853968183539", "电商平台", "字节跳动_抖音_电商平台"),
        ("https://mp.weixin.qq.com/s/abc", "公众号", "微信-公众号"),
        ("https://www.douyin.com/video/123", "图文视频", "字节跳动_抖音_图文视频"),
        ("https://tv.sohu.com/v/abc.html", "图文视频", "搜狐_搜狐视频_图文视频"),
        ("https://school.xiaohongshu.com/helper/detail/1210?jumpFrom=ark", "图文视频", "行吟科技_小红书_图文视频"),
        ("https://play.tudou.com/v_show/id_XNDQxODkzMjEwOA%3D%3D.html", "图文视频", "阿里巴巴_土豆_图文视频"),
        ("https://www.zhihu.com/question/1", "微博博客", "知乎_知乎_博客贴吧"),
        ("https://news.sohu.com/a/123", "生活资讯", "搜狐_搜狐新闻_生活资讯"),
        ("https://news.163.com/24/0728/10/ABC.html", "生活资讯", "网易_网易新闻_生活资讯"),
        ("https://bbs.hupu.com/62820345-1.html", "生活资讯", "虎扑_虎扑_生活资讯"),
        ("https://h5-ol.sns.sohu.com/hy-super-h5/share/feed/1328267933184627968", "生活资讯", "搜狐_狐友_生活资讯"),
        ("https://m.sm.cn/article/123", "浏览器", "阿里巴巴_UC浏览器_浏览器"),
        ("https://consumer.huawei.com/cn/support/content/zh-cn16010259/", "浏览器", "华为_华为浏览器_浏览器"),
    ],
)
def test_platform_router_uses_final_domain_and_specific_path(
    url: str,
    sheet_name: str,
    platform_value: str,
) -> None:
    decision = PlatformRouter().route(url, PageData(text_type_hint="正文"))

    assert decision is not None
    assert decision.sheet_name == sheet_name
    assert decision.platform_value == platform_value


def test_platform_router_preserves_comment_hint_and_rejects_unknown_hosts() -> None:
    router = PlatformRouter()
    decision = router.route("https://weibo.com/123/abc", PageData(text_type_hint="评论回复"))

    assert decision is not None
    assert decision.text_type == "评论回复"
    assert router.route("https://example.com/article", PageData()) is None
    assert not router.is_url_supported_sheet("群聊")
    assert not router.is_url_supported_sheet("朋友圈")


@pytest.mark.parametrize(
    "url",
    [
        "https://item.jd.com/100.html",
        "https://www.taobao.com/item.htm?id=1",
        "https://www.goofish.com/item?id=1",
        "https://mobile.yangkeduo.com/goods.html?goods_id=1",
    ],
)
def test_commerce_product_routes_as_merchant(url: str) -> None:
    decision = PlatformRouter().route(url, PageData(text_type_hint="正文"))

    assert decision is not None
    assert decision.sheet_name == "电商平台"
    assert decision.text_type == "商家"


def test_commerce_comment_route_preserves_comment_hint() -> None:
    decision = PlatformRouter().route(
        "https://item.jd.com/100.html",
        PageData(text_type_hint="评论回复"),
    )

    assert decision is not None
    assert decision.text_type == "评论回复"


@pytest.mark.parametrize(
    ("url", "display_name"),
    [
        ("https://www.smzdm.com/p/179421503/", "什么值得买"),
        ("https://www.dazhe.com/deals/152887.html", "打折网"),
        (
            "https://ex.chinadaily.com.cn/exchange/partners/82/stories/example.html",
            "中国日报",
        ),
    ],
)
def test_unmapped_public_sites_report_template_enum_constraint(
    url: str,
    display_name: str,
) -> None:
    router = PlatformRouter()

    assert router.route(url, PageData()) is None
    message = router.unsupported_message(url)
    assert display_name in message
    assert "固定模板" in message
    assert "未自动路由" in message


@pytest.mark.parametrize(
    "url",
    [
        "https://www.xiaohongshu.com/",
        "https://www.xiaohongshu.com/explore/",
        "https://tv.sohu.com/",
        "https://www.163.com/",
        "https://www.ifeng.com/",
        "https://www.hupu.com/",
        "https://w.sohu.com/",
        "https://www.taobao.com/",
        "https://www.goofish.com/",
        "https://www.zhihu.com/signin?next=%2Fquestion%2F362425387",
        "https://tieba.baidu.com/f?kw=python",
        "https://consumer.huawei.com/cn/mobileservices/browser/",
    ],
)
def test_platform_catalog_does_not_treat_homepages_as_content_evidence(url: str) -> None:
    assert find_platform(url) is None


def test_reported_news_platforms_have_field_specific_selectors() -> None:
    for url in (
        "https://news.sohu.com/a/123",
        "https://news.ifeng.com/c/123",
        "https://www.163.com/dy/article/ABC123.html",
    ):
        definition = find_platform(url)
        assert definition is not None
        assert definition.selectors["title"]
        assert definition.selectors["content_text"]
        assert definition.selectors["author_name"]
        assert definition.selectors["published_at"]
