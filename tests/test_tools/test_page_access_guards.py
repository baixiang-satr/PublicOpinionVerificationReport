"""删除内容确证守卫（page_access_guards）三规则与屏障守卫回归。"""

from __future__ import annotations

from src.tools.page_access_guards import (
    content_unavailable_confirmed,
    looks_like_barrier_only_page,
)


def test_unavailable_title_substring_confirms_despite_long_body() -> None:
    """标题含删除文案（子串）即确证，不再要求精确等于固定标题集合。"""

    body = "推荐视频内容 " * 400  # 正文远超 800 可见字符
    assert content_unavailable_confirmed("啊叻？视频不见了？-哔哩哔哩", body)


def test_unavailable_marker_in_body_head_confirms_with_site_chrome() -> None:
    """微博式删除页：标题是平台名、删除文案在文首、站点框架把正文撑过 800 字。

    2026-08-12 实测 10 条已删微博全部误判 valid 的根因形态。
    """

    chrome = "微博 首页 视频 发现 游戏 会员 热门 关注 消息 " * 12
    body = chrome + "抱歉，该微博已被作者删除。查看帮助 " + "热门推荐页脚链接 " * 200
    assert content_unavailable_confirmed("微博", body)


def test_weibo_no_view_permission_page_confirms() -> None:
    """微博删除/权限回收页的真实文案“暂无查看权限”（2026-08-12 实测快照形态）。

    真实页面：标题“微博正文 - 微博”，文首导航后紧跟“返回/暂无查看权限/查看
    个人主页”，后接整页热搜榜单与页脚（正文远超 800 字）。
    """

    body = (
        "NEW 56 无障碍 首页 全部关注 最新微博 特别关注 好友圈 自定义分组 管理 "
        "高校 名人明星 同事 同学 悄悄关注 返回 暂无查看权限 查看个人主页 微博热搜 "
        + "热搜词条 讨论度 " * 300
    )
    assert content_unavailable_confirmed("微博正文 - 微博", body)


def test_unavailable_marker_deep_in_long_body_does_not_confirm() -> None:
    """正文充实的真实页面深处（评论区/推荐流）顺带提及删除，不得误判。"""

    body = "这是一篇正常的新闻报道正文，包含大量真实内容。" * 60 + "相关链接提示：该内容已被删除。"
    assert not content_unavailable_confirmed("正常报道", body)


def test_short_error_page_still_confirms() -> None:
    """原"纯错误页"规则保留：短正文 + 删除文案 → 确证。"""

    assert content_unavailable_confirmed("小红书", "抱歉，笔记不存在")


def test_short_page_without_marker_does_not_confirm() -> None:
    """短正文本身不是删除证据，必须同时命中删除文案。"""

    assert not content_unavailable_confirmed("微博", "页面加载中，请稍候")


def test_exact_404_title_still_confirms() -> None:
    """历史精确标题 "404" 的等价语义保留（极短标题不含完整删除文案）。"""

    body = "推荐内容 " * 400
    assert content_unavailable_confirmed("404", body)


def test_barrier_only_page_rules_unchanged_for_other_kinds() -> None:
    """CAPTCHA/LOGIN/RESTRICTED 的"纯错误页"守卫语义不变。"""

    long_body = "正文内容 " * 500
    assert looks_like_barrier_only_page("账号登录", long_body, "login")
    assert not looks_like_barrier_only_page("正文标题", long_body, "login")
    assert looks_like_barrier_only_page("正文标题", "请先登录", "login")
