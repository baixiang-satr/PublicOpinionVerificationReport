"""Central URL routing and DOM selector catalog for template-supported platforms."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import re
from typing import Mapping
from urllib.parse import urlsplit

from src.domain.template_schema import SHEET_LAYOUTS


class ExtractorFamily(StrEnum):
    COMMERCE = "commerce"
    ARTICLE = "article"
    SOCIAL = "social"


@dataclass(frozen=True)
class PlatformDefinition:
    key: str
    sheet_name: str
    platform_value: str
    family: ExtractorFamily
    hosts: tuple[str, ...]
    selectors: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    include_patterns: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()

    def matches(self, url: str) -> bool:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if not any(host == item or host.endswith(f".{item}") for item in self.hosts):
            return False
        target = parsed.path.lower()
        if parsed.query:
            target = f"{target}?{parsed.query.lower()}"
        if self.include_patterns and not any(re.search(pattern, target) for pattern in self.include_patterns):
            return False
        return not any(re.search(pattern, target) for pattern in self.exclude_patterns)


def _selectors(**values: tuple[str, ...]) -> Mapping[str, tuple[str, ...]]:
    return values


PLATFORM_DEFINITIONS: tuple[PlatformDefinition, ...] = (
    PlatformDefinition(
        "douyin_ecommerce",
        "电商平台",
        "字节跳动_抖音_电商平台",
        ExtractorFamily.COMMERCE,
        ("haohuo.jinritemai.com", "jinritemai.com", "douyin.com"),
        _selectors(
            title=("[data-e2e='product-title']", "h1"),
            content_text=("[data-e2e='product-desc']", ".product-detail"),
            store_name=("[data-e2e='shop-name']", ".shop-name"),
        ),
        include_patterns=(r"/product", r"product_id=", r"/views/product"),
    ),
    PlatformDefinition(
        "xianyu",
        "电商平台",
        "阿里_闲鱼_电商平台",
        ExtractorFamily.COMMERCE,
        ("goofish.com", "2.taobao.com"),
        _selectors(title=("h1", ".item-title"), content_text=(".item-desc", ".desc"), store_name=(".seller-name",)),
    ),
    PlatformDefinition(
        "tmall",
        "电商平台",
        "阿里_天猫_电商平台",
        ExtractorFamily.COMMERCE,
        ("tmall.com",),
        _selectors(title=("h1", ".tb-detail-hd"), content_text=(".tm-fcs-panel", ".detail-content"), store_name=(".slogo-shopname",)),
    ),
    PlatformDefinition(
        "taobao",
        "电商平台",
        "阿里_淘宝_电商平台",
        ExtractorFamily.COMMERCE,
        ("taobao.com",),
        _selectors(title=("h1", ".ItemTitle--mainTitle"), content_text=(".ItemDesc--content", ".detail-content"), store_name=(".ShopHeader--title",)),
    ),
    PlatformDefinition(
        "1688",
        "电商平台",
        "阿里_1688_电商平台",
        ExtractorFamily.COMMERCE,
        ("1688.com",),
        _selectors(title=("h1", ".title-text"), content_text=(".detail-content",), store_name=(".company-name", ".shop-name")),
    ),
    PlatformDefinition(
        "jd",
        "电商平台",
        "京东_京东商城_电商平台",
        ExtractorFamily.COMMERCE,
        ("jd.com",),
        _selectors(title=(".sku-name", "h1"), content_text=(".detail-content", "#detail"), store_name=(".name a", ".shop-name")),
    ),
    PlatformDefinition(
        "pinduoduo",
        "电商平台",
        "拼多多",
        ExtractorFamily.COMMERCE,
        ("pinduoduo.com", "yangkeduo.com"),
        _selectors(title=("h1", "[class*='goodsName']"), content_text=("[class*='goodsDesc']", "main"), store_name=("[class*='mallName']",)),
    ),
    PlatformDefinition(
        "wechat_official",
        "公众号",
        "微信-公众号",
        ExtractorFamily.ARTICLE,
        ("mp.weixin.qq.com",),
        _selectors(
            title=("#activity-name", "h1"),
            content_text=("#js_content", "article"),
            author_name=("#js_name", ".account_nickname_inner"),
            author_id=(".account_meta_value",),
            published_at=("#publish_time", "em#publish_time"),
        ),
    ),
    PlatformDefinition(
        "baijiahao",
        "公众号",
        "百度_百家号_公众号",
        ExtractorFamily.ARTICLE,
        ("baijiahao.baidu.com",),
        _selectors(
            title=(".article-title", "h1"),
            content_text=(".article-content", "article"),
            author_name=(".author-name", "[class*='authorName']"),
            author_url=(".author-name a", "[class*='author'] a"),
            published_at=(".date", "time"),
        ),
    ),
    PlatformDefinition(
        "wechat_video",
        "图文视频",
        "腾讯_微信_图文视频",
        ExtractorFamily.SOCIAL,
        ("channels.weixin.qq.com", "weixin.qq.com"),
        _selectors(content_text=("[class*='finder'] [class*='desc']", "main"), author_name=("[class*='nickname']",)),
        include_patterns=(r"/video", r"/finder"),
    ),
    PlatformDefinition(
        "sohu_video",
        "图文视频",
        "搜狐_搜狐视频_图文视频",
        ExtractorFamily.SOCIAL,
        ("tv.sohu.com",),
        _selectors(title=("h1", ".video-title"), content_text=(".video-info", "main"), author_name=(".user-name",)),
        include_patterns=(r"/v/",),
    ),
    PlatformDefinition(
        "xiaohongshu",
        "图文视频",
        "行吟科技_小红书_图文视频",
        ExtractorFamily.SOCIAL,
        ("xiaohongshu.com", "xhslink.com"),
        _selectors(
            title=(".note-content .title", "#detail-title"),
            content_text=(".note-content .desc", "#detail-desc"),
            author_name=(".author-wrapper .name", ".username"),
            author_url=(".author-wrapper a",),
            published_at=(".date", ".publish-time"),
        ),
        include_patterns=(r"/explore/", r"/discovery/item/"),
    ),
    PlatformDefinition(
        "douyin",
        "图文视频",
        "字节跳动_抖音_图文视频",
        ExtractorFamily.SOCIAL,
        ("douyin.com", "iesdouyin.com"),
        _selectors(
            content_text=("[data-e2e='video-desc']", "[data-e2e='browse-video-desc']"),
            author_name=("[data-e2e='video-author-name']", "[data-e2e='browse-username']"),
            author_url=("[data-e2e='video-author-name'] a",),
            published_at=("[data-e2e='video-publish-time']",),
        ),
        exclude_patterns=(r"/product", r"product_id=", r"/views/product"),
    ),
    PlatformDefinition(
        "kuaishou",
        "图文视频",
        "快手科技_快手_图文视频",
        ExtractorFamily.SOCIAL,
        ("kuaishou.com", "gifshow.com"),
        _selectors(content_text=(".video-info-title", ".caption"), author_name=(".profile-user-name", ".author-name"), published_at=(".publish-time",)),
        include_patterns=(r"/short-video/", r"/fw/photo/"),
    ),
    PlatformDefinition(
        "bilibili",
        "图文视频",
        "幻电科技_哔哩哔哩_图文视频",
        ExtractorFamily.SOCIAL,
        ("bilibili.com", "b23.tv"),
        _selectors(title=("h1.video-title", "h1"), content_text=(".video-desc-container", ".opus-module-content"), author_name=(".up-name", ".bili-user-profile-name"), author_url=(".up-name", ".up-info-container a")),
    ),
    PlatformDefinition(
        "tudou",
        "图文视频",
        "阿里巴巴_土豆_图文视频",
        ExtractorFamily.SOCIAL,
        ("tudou.com",),
        include_patterns=(r"/programs/view/",),
    ),
    PlatformDefinition(
        "youku",
        "图文视频",
        "阿里巴巴_优酷_图文视频",
        ExtractorFamily.SOCIAL,
        ("youku.com",),
        include_patterns=(r"/v_show/",),
    ),
    PlatformDefinition(
        "ixigua",
        "图文视频",
        "字节跳动_西瓜视频_图文视频",
        ExtractorFamily.SOCIAL,
        ("ixigua.com",),
        include_patterns=(r"/video/", r"/\d{10,}"),
    ),
    PlatformDefinition(
        "iqiyi",
        "图文视频",
        "爱奇艺_爱奇艺_图文视频",
        ExtractorFamily.SOCIAL,
        ("iqiyi.com",),
        include_patterns=(r"/v_", r"/w_"),
    ),
    PlatformDefinition(
        "weibo",
        "微博博客",
        "新浪_新浪微博_博客贴吧",
        ExtractorFamily.SOCIAL,
        ("weibo.com", "weibo.cn"),
        _selectors(content_text=("[class*='detail_wbtext']", "[node-type='feed_list_content']"), author_name=("[class*='head_name']", ".username"), author_url=("[class*='head_name'] a",), published_at=("time", "[class*='time']")),
    ),
    PlatformDefinition(
        "tieba",
        "微博博客",
        "百度_百度贴吧_博客贴吧",
        ExtractorFamily.SOCIAL,
        ("tieba.baidu.com",),
        _selectors(title=(".core_title_txt", "h1"), content_text=(".d_post_content", ".threadlist_abs"), author_name=(".p_author_name", ".frs-author-name"), published_at=(".tail-info",)),
    ),
    PlatformDefinition(
        "zhihu",
        "微博博客",
        "知乎_知乎_博客贴吧",
        ExtractorFamily.SOCIAL,
        ("zhihu.com",),
        _selectors(title=(".Post-Title", ".QuestionHeader-title"), content_text=(".RichContent-inner", "article"), author_name=(".AuthorInfo-name", ".UserLink-link"), author_url=(".AuthorInfo-name a", ".UserLink-link"), published_at=(".ContentItem-time", "time")),
    ),
    PlatformDefinition(
        "toutiao",
        "生活资讯",
        "字节跳动_今日头条_生活资讯",
        ExtractorFamily.ARTICLE,
        ("toutiao.com",),
        _selectors(
            title=("h1", ".article-title"),
            content_text=("article", ".article-content", ".syl-article-base"),
            author_name=("[class*='author']", ".source"),
            published_at=("time", "[class*='time']"),
        ),
        include_patterns=(r"/article/",),
    ),
    PlatformDefinition(
        "netease_news",
        "生活资讯",
        "网易_网易新闻_生活资讯",
        ExtractorFamily.ARTICLE,
        ("163.com",),
        _selectors(
            title=("h1", ".post_title"),
            content_text=("#content", ".post_body"),
            author_name=(".post_info", ".source"),
            published_at=(".post_info", ".post_time_source", "time"),
        ),
        include_patterns=(r"/dy/article/", r"/article/", r"/\d{2}/\d{4}/"),
    ),
    PlatformDefinition(
        "ifeng_news",
        "生活资讯",
        "凤凰网_凤凰新闻_生活资讯",
        ExtractorFamily.ARTICLE,
        ("ifeng.com",),
        _selectors(
            title=("h1", ".article-title"),
            content_text=("#main_content", "#articleBox", ".article-content"),
            author_name=(".source", "[class*='source']"),
            author_url=(".source a", "[class*='source'] a"),
            published_at=("time", "[class*='time']"),
        ),
        include_patterns=(r"/c/",),
    ),
    PlatformDefinition("huyou", "生活资讯", "搜狐_狐友_生活资讯", ExtractorFamily.SOCIAL, ("huyou.sohu.com", "w.sohu.com")),
    PlatformDefinition(
        "sohu_news",
        "生活资讯",
        "搜狐_搜狐新闻_生活资讯",
        ExtractorFamily.ARTICLE,
        ("sohu.com",),
        _selectors(
            title=("h1", ".text-title", ".article-title"),
            content_text=("article", ".article-content", "#mp-editor", ".text"),
            author_name=(".author-name", ".source", "[class*='source']"),
            author_url=(".author-name a", "[class*='source'] a"),
            published_at=(".article-info .time", "#news-time", "time", "[class*='time']"),
        ),
        include_patterns=(r"/a/",),
        exclude_patterns=(r"/video",),
    ),
    PlatformDefinition(
        "hupu",
        "生活资讯",
        "虎扑_虎扑_生活资讯",
        ExtractorFamily.SOCIAL,
        ("hupu.com",),
        include_patterns=(r"/bbs/", r"/\d+(?:-\d+)?(?:\.html)?$"),
    ),
    PlatformDefinition("meituan", "生活资讯", "三快_美团_生活资讯", ExtractorFamily.COMMERCE, ("meituan.com", "dianping.com")),
    PlatformDefinition(
        "dongchedi",
        "生活资讯",
        "字节跳动_懂车帝_生活资讯",
        ExtractorFamily.ARTICLE,
        ("dongchedi.com",),
        include_patterns=(r"/article/", r"/ugc/", r"/video/"),
    ),
    PlatformDefinition("uc_browser", "浏览器", "阿里巴巴_UC浏览器_浏览器", ExtractorFamily.ARTICLE, ("sm.cn", "uc.cn")),
    PlatformDefinition("browser_360", "浏览器", "360_360浏览器_浏览器", ExtractorFamily.ARTICLE, ("so.com", "360kuai.com", "browser.360.cn")),
    PlatformDefinition(
        "huawei_browser",
        "浏览器",
        "华为_华为浏览器_浏览器",
        ExtractorFamily.ARTICLE,
        ("browser.huawei.com", "consumer.huawei.com"),
    ),
    PlatformDefinition("qq_browser", "浏览器", "腾讯_QQ浏览器_浏览器", ExtractorFamily.ARTICLE, ("browser.qq.com", "mb.qq.com")),
)


def find_platform(url: str) -> PlatformDefinition | None:
    return next((definition for definition in PLATFORM_DEFINITIONS if definition.matches(url)), None)


def validate_catalog() -> None:
    keys: set[str] = set()
    for definition in PLATFORM_DEFINITIONS:
        if definition.key in keys:
            raise ValueError(f"Duplicate platform key: {definition.key}")
        keys.add(definition.key)
        layout = SHEET_LAYOUTS[definition.sheet_name]
        platform_column = layout.field_columns["platform"]
        allowed = layout.validation_values[platform_column]
        if definition.platform_value not in allowed:
            raise ValueError(f"Platform value is outside template contract: {definition.platform_value}")


validate_catalog()
