from src.auth.registry import (
    AUTH_POLICIES,
    auth_policy_for_key,
    auth_policy_for_url,
)
from src.crawler.platform_catalog import PLATFORM_DEFINITIONS


def test_auth_registry_covers_all_34_template_platforms_once() -> None:
    catalog_keys = {definition.key for definition in PLATFORM_DEFINITIONS}
    policy_keys = {policy.platform_key for policy in AUTH_POLICIES}

    assert len(AUTH_POLICIES) == 34
    assert len(policy_keys) == 34
    assert policy_keys == catalog_keys
    assert all(policy.probe_url.startswith("https://") for policy in AUTH_POLICIES)
    assert all(policy.requires_valid_state for policy in AUTH_POLICIES)
    assert all(
        auth_policy_for_url(policy.probe_url) == policy
        for policy in AUTH_POLICIES
    )


def test_auth_policy_is_platform_scoped_and_routes_supported_hosts() -> None:
    policy = auth_policy_for_key("zhihu")

    assert policy.auth_scope == "zhihu"
    assert auth_policy_for_url("https://www.zhihu.com/question/123") == policy
    assert auth_policy_for_url("https://example.test/article/123") is None


def test_overlapping_douyin_domain_uses_content_route_not_first_host_match() -> None:
    commerce = auth_policy_for_url(
        "https://haohuo.jinritemai.com/views/product/item2?id=123"
    )
    social = auth_policy_for_url("https://www.douyin.com/video/123")

    assert commerce is not None and commerce.platform_key == "douyin_ecommerce"
    assert social is not None and social.platform_key == "douyin"
    assert auth_policy_for_url("https://www.douyin.com/") is None


def test_douyin_share_short_link_routes_to_douyin_policy() -> None:
    """v.douyin.com 短链必须命中抖音登录态档案（截图窗口才能带态打开）。"""

    policy = auth_policy_for_url("https://v.douyin.com/erOICsACek8/")

    assert policy is not None
    assert policy.platform_key == "douyin"


def test_given_wechat_urls_route_to_login_profiles() -> None:
    official = auth_policy_for_url(
        "https://mp.weixin.qq.com/s/Oxxb7Lc4zEUsbbYXoOtXNg"
    )
    video = auth_policy_for_url("https://weixin.qq.com/sph/AiQbKWmgTm")

    assert official is not None and official.platform_key == "wechat_official"
    assert video is not None and video.platform_key == "wechat_video"
