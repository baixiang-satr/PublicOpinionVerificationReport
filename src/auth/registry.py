"""Central authentication policy registry for every URL-capable template platform."""

from __future__ import annotations

from urllib.parse import urlsplit

from src.auth.models import PlatformAuthPolicy
from src.crawler.platform_catalog import PLATFORM_DEFINITIONS


_PROBE_URLS: dict[str, str] = {
    "douyin_ecommerce": "https://haohuo.jinritemai.com/views/product/item2?id=3578430953573501732",
    "xianyu": "https://www.goofish.com/item?id=1068457459464",
    "tmall": "https://detail.tmall.com/item.htm?id=816232975219",
    "taobao": "https://item.taobao.com/item.htm?id=831892713069",
    "1688": "https://detail.1688.com/offer/692785071822.html",
    "jd": "https://item.m.jd.com/product/10217506550225.html",
    "pinduoduo": "https://mobile.yangkeduo.com/goods.html?goods_id=953347192563",
    "wechat_official": "https://mp.weixin.qq.com/s/Oxxb7Lc4zEUsbbYXoOtXNg",
    "baijiahao": "https://baijiahao.baidu.com/s?id=1834009156189018132",
    "wechat_video": "https://weixin.qq.com/sph/AiQbKWmgTm",
    "sohu_video": "https://tv.sohu.com/v/MjAyNjA3MjMvbjYyMDIyOTkzMi5zaHRtbA%3D%3D.html",
    "xiaohongshu": "https://www.xiaohongshu.com/explore/667d3e07000000001b00a2c7",
    "douyin": "https://www.douyin.com/video/7589550032567782662",
    "kuaishou": "https://www.kuaishou.com/short-video/3xyaz5mf3dqd49q",
    "bilibili": "https://www.bilibili.com/video/BV19DipBVE3c/",
    "tudou": "https://www.tudou.com/programs/view/Bwi-sLm43jM/",
    "youku": "https://v.youku.com/v_show/id_XNTkzMzkyMzMyNA%3D%3D.html",
    "ixigua": "https://www.ixigua.com/video/7412571429857668386",
    "iqiyi": "https://www.iqiyi.com/v_19rroblr2k.html",
    "weibo": "https://weibo.com/2/detail/5321665832821282",
    "tieba": "https://tieba.baidu.com/p/10843752913",
    "zhihu": "https://www.zhihu.com/question/362425387",
    "toutiao": "https://www.toutiao.com/article/7624715705614877225/",
    "netease_news": "https://www.163.com/dy/article/KS0L7KJR0556F4M0.html",
    "ifeng_news": "https://news.ifeng.com/c/8v6pJifxdFC",
    "huyou": "https://h5-ol.sns.sohu.com/hy-super-h5/share/feed/1328267933184627968",
    "sohu_news": "https://www.sohu.com/a/1030793551_120078003",
    "hupu": "https://m.hupu.com/bbs/641349741.html",
    "meituan": "https://www.meituan.com/news/NN251017149001531",
    "dongchedi": "https://www.dongchedi.com/article/7543253844608811556",
    "uc_browser": "https://www.uc.cn/about/privacy/",
    "browser_360": "https://browser.360.cn/se/help/faq-detail_zy_zy.html",
    "huawei_browser": "https://consumer.huawei.com/cn/support/content/zh-cn16010259/",
    "qq_browser": "https://browser.qq.com/materials/versionlist",
}


# Probe URLs are concrete content pages that can rot (product delisted,
# article deleted, redirect to home).  A dead probe page must never be read
# as "login state expired", so platforms with volatile probe pages get
# fallback candidates; validation tries them in order and only an explicit
# login wall proves expiry.
_FALLBACK_PROBE_URLS: dict[str, tuple[str, ...]] = {
    "tmall": ("https://detail.tmall.com/item.htm?id=557017471577",),
    "taobao": ("https://item.taobao.com/item.htm?id=917048988868",),
    "1688": ("https://detail.1688.com/offer/652702302959.html",),
    "jd": ("https://item.jd.com/100033296948.html",),
    "pinduoduo": ("https://mobile.yangkeduo.com/goods2.html?goods_id=583843098814",),
    "douyin_ecommerce": (
        "https://haohuo.jinritemai.com/ecommerce/trade/detail/index.html"
        "?id=3794554853968183539&origin_type=604",
    ),
    "xianyu": ("https://www.goofish.com/item?categoryId=0&id=953562730533",),
    "weibo": ("https://weibo.com/7798269830/5264010640887796",),
    "toutiao": ("https://www.toutiao.com/article/7610591062242935322/",),
    "baijiahao": ("https://baijiahao.baidu.com/s?id=1852981564697478409",),
    "kuaishou": ("https://www.kuaishou.com/short-video/3xifs9zxiwmvgqe",),
    "douyin": ("https://www.douyin.com/video/7660061608801996068",),
    "ixigua": ("https://www.ixigua.com/7635649905751384165",),
    "zhihu": ("https://zhuanlan.zhihu.com/p/2015007141673596133",),
    "tieba": ("https://tieba.baidu.com/p/10847021396",),
}


AUTH_POLICIES: tuple[PlatformAuthPolicy, ...] = tuple(
    PlatformAuthPolicy(
        platform_key=definition.key,
        display_name=definition.platform_value,
        probe_url=_PROBE_URLS[definition.key],
        host_suffixes=definition.hosts,
        auth_scope=definition.key,
        phone_assist=definition.key
        not in {
            "wechat_official",
            "wechat_video",
            "uc_browser",
            "browser_360",
            "huawei_browser",
            "qq_browser",
        },
        fallback_probe_urls=_FALLBACK_PROBE_URLS.get(definition.key, ()),
        # Crawling is profile-only for every supported website.  A profile
        # is accepted only after AuthManagerService has re-opened its state in
        # a fresh context and AuthProfileStore has committed it atomically.
        requires_valid_state=True,
    )
    for definition in PLATFORM_DEFINITIONS
)

_BY_KEY = {policy.platform_key: policy for policy in AUTH_POLICIES}


def auth_policy_for_key(platform_key: str) -> PlatformAuthPolicy:
    try:
        return _BY_KEY[platform_key]
    except KeyError as error:
        raise KeyError(f"Unknown authentication platform: {platform_key}") from error


def auth_policy_for_url(url: str) -> PlatformAuthPolicy | None:
    host = (urlsplit(url).hostname or "").casefold()
    if not host:
        return None
    matched = [
        _BY_KEY[definition.key]
        for definition in PLATFORM_DEFINITIONS
        if definition.matches(url)
    ]
    if len(matched) == 1:
        return matched[0]

    # Home/login URLs may not satisfy content-path rules. Use a host fallback
    # only when that host belongs to exactly one platform; overlapping domains
    # such as douyin.com must never select an arbitrary authentication profile.
    host_candidates = [
        policy
        for policy in AUTH_POLICIES
        if any(
            host == suffix or host.endswith(f".{suffix}")
            for suffix in policy.host_suffixes
        )
    ]
    return host_candidates[0] if len(host_candidates) == 1 else None


def validate_auth_registry() -> None:
    catalog_keys = {definition.key for definition in PLATFORM_DEFINITIONS}
    policy_keys = set(_BY_KEY)
    if policy_keys != catalog_keys:
        missing = sorted(catalog_keys - policy_keys)
        extra = sorted(policy_keys - catalog_keys)
        raise ValueError(f"Authentication registry mismatch; missing={missing}, extra={extra}")
    if len(AUTH_POLICIES) != len(policy_keys):
        raise ValueError("Authentication registry contains duplicate platform keys.")


validate_auth_registry()
