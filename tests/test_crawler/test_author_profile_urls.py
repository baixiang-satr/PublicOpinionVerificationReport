from src.crawler.author_profile_urls import (
    derive_author_profile_url,
    is_author_profile_url,
)


def test_derives_stable_platform_profile_urls() -> None:
    assert (
        derive_author_profile_url(
            "https://www.douyin.com/video/123",
            "MS4wLjABAAAAstable_id",
        )
        == "https://www.douyin.com/user/MS4wLjABAAAAstable_id"
    )
    assert (
        derive_author_profile_url("https://www.bilibili.com/video/BV1abc", "12345")
        == "https://space.bilibili.com/12345"
    )
    assert (
        derive_author_profile_url("https://weibo.com/123/status", "5644764907")
        == "https://weibo.com/u/5644764907"
    )
    assert (
        derive_author_profile_url(
            "https://www.xiaohongshu.com/explore/123",
            "65af0123abcd",
        )
        == "https://www.xiaohongshu.com/user/profile/65af0123abcd"
    )
    assert (
        derive_author_profile_url(
            "https://www.toutiao.com/article/123/",
            "MS4wLjABAAAA-token",
        )
        == "https://www.toutiao.com/c/user/token/MS4wLjABAAAA-token/"
    )


def test_derives_wechat_and_existing_profile_urls_without_guessing_an_id() -> None:
    assert (
        derive_author_profile_url(
            "https://mp.weixin.qq.com/s?__biz=Mzk0NzMwNjU5Nw==&mid=1",
            None,
        )
        == "https://mp.weixin.qq.com/mp/profile_ext?action=home"
        "&__biz=Mzk0NzMwNjU5Nw==&scene=124#wechat_redirect"
    )
    profile = "https://h5-ol.sns.sohu.com/hy-super-h5/share/profile/abc?sf_hy=wechat"
    assert derive_author_profile_url(profile, None) == profile


def test_does_not_guess_unknown_or_nonnumeric_profile_ids() -> None:
    assert derive_author_profile_url("https://example.com/post/1", "42") is None
    assert derive_author_profile_url("https://www.bilibili.com/video/BV1abc", "nickname") is None


def test_repost_source_article_is_not_treated_as_author_home() -> None:
    assert not is_author_profile_url(
        "https://h.xinhuaxmt.com/vh512/share/13222030?homeshow=1",
        "https://www.163.com/news/article/L35RNISH000189FH.html",
    )
    assert is_author_profile_url(
        "https://tv.sohu.com/user/386234413",
        "https://tv.sohu.com/v/example.html",
    )
