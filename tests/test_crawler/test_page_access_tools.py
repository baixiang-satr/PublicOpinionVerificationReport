from __future__ import annotations

import asyncio

import pytest

from src.domain.models import RecordStatus
from src.tools.page_access import (
    AccessKind,
    _read_page_snapshot,
    inspect_page_access,
    wait_for_manual_access,
)


class SnapshotPage:
    def __init__(self, url: str, snapshots: list[dict[str, str]]) -> None:
        self.url = url
        self._snapshots = snapshots
        self._index = 0

    async def evaluate(self, _script: str) -> dict[str, str]:
        return self._snapshots[self._index]

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        self._index = min(self._index + 1, len(self._snapshots) - 1)


class EmptySsrPage:
    url = "https://baijiahao.baidu.com/s?id=target"

    async def evaluate(self, script: str) -> dict[str, object]:
        if "__PRELOADED_STATE__" in script:
            return {
                "articleInfo": {
                    "title": "SSR 标题",
                    "content": "SSR 正文",
                }
            }
        return {"title": "", "body": ""}

    def locator(self, _selector: str) -> object:
        return object()


@pytest.mark.asyncio
async def test_read_page_snapshot_is_bounded_when_renderer_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """渲染线程卡死时快照读取必须有界（反爬卡死回归）。"""

    monkeypatch.setattr(
        "src.tools.page_access._EVALUATE_TIMEOUT_SECONDS", 0.1
    )

    class HangingPage:
        async def evaluate(self, *_args: object, **_kwargs: object) -> None:
            await asyncio.Event().wait()

    title, body = await asyncio.wait_for(
        _read_page_snapshot(HangingPage()),
        timeout=2,
    )
    assert (title, body) == ("", "")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "snapshot", "expected_kind", "expected_code"),
    [
        (
            "https://passport.weibo.com/visitor/visitor",
            {"title": "微博", "body": ""},
            AccessKind.LOGIN,
            "LOGIN_REQUIRED",
        ),
        (
            "https://weibo.com/123/status",
            {"title": "微博", "body": "前方有点拥堵，请登录后使用"},
            AccessKind.LOGIN,
            "LOGIN_REQUIRED",
        ),
        (
            "https://v.youku.com/v_show/example/punish",
            {"title": "安全验证", "body": ""},
            AccessKind.CAPTCHA,
            "CAPTCHA_REQUIRED",
        ),
        (
            "https://www.xiaohongshu.com/explore/missing",
            {"title": "小红书", "body": "抱歉，笔记不存在"},
            AccessKind.CONTENT_UNAVAILABLE,
            "CONTENT_UNAVAILABLE",
        ),
        (
            "https://www.ixigua.com/1234567890",
            {"title": "西瓜视频", "body": "打开 App 看完整内容，内容可能已删除"},
            AccessKind.CONTENT_UNAVAILABLE,
            "CONTENT_UNAVAILABLE",
        ),
        (
            # 知乎内容删除后的专用错误页（标题与正文均含“荒原”提示）。
            "https://www.zhihu.com/question/2068088684528791862",
            {
                "title": "你似乎来到了没有知识存在的荒原 - 知乎",
                "body": "你似乎来到了没有知识存在的荒原 5 秒后自动跳转至知乎首页 去往首页",
            },
            AccessKind.CONTENT_UNAVAILABLE,
            "CONTENT_UNAVAILABLE",
        ),
        (
            "https://www.kuaishou.com/short-video/example",
            {"title": "", "body": '{"result": 1}'},
            AccessKind.API_RESPONSE,
            "UNEXPECTED_API_RESPONSE",
        ),
        (
            "https://risk.jd.com/challenge",
            {"title": "访问提示", "body": "当前操作存在安全风险"},
            AccessKind.ACCESS_RESTRICTED,
            "ACCESS_CHALLENGE",
        ),
        (
            "https://www.xiaohongshu.com/website-login/error",
            {"title": "小红书", "body": "安全限制 IP存在风险，请切换可靠网络环境后重试 300012"},
            AccessKind.ACCESS_RESTRICTED,
            "ACCESS_CHALLENGE",
        ),
    ],
)
async def test_access_tool_classifies_strong_barrier_signals(
    url: str,
    snapshot: dict[str, str],
    expected_kind: AccessKind,
    expected_code: str,
) -> None:
    barrier = await inspect_page_access(SnapshotPage(url, [snapshot]), url, url)

    assert barrier is not None
    assert barrier.kind == expected_kind
    assert barrier.code == expected_code


@pytest.mark.asyncio
async def test_access_tool_rejects_content_redirected_to_home() -> None:
    original = "https://www.example.test/article/123"
    final = "https://www.example.test/"

    barrier = await inspect_page_access(
        SnapshotPage(final, [{"title": "首页", "body": "推荐内容"}]),
        final,
        original,
    )

    assert barrier is not None
    assert barrier.kind == AccessKind.REDIRECTED_HOME
    assert barrier.status == RecordStatus.NEEDS_REVIEW


@pytest.mark.asyncio
async def test_visible_manual_access_wait_continues_after_user_finishes_login() -> None:
    url = "https://example.test/article/123"
    page = SnapshotPage(
        url,
        [
            {"title": "账号登录", "body": "请先登录"},
            {"title": "正文标题", "body": "这是已经成功加载的文章正文内容。"},
        ],
    )

    barrier = await wait_for_manual_access(page, url, url, timeout_seconds=2)

    assert barrier is None


@pytest.mark.asyncio
async def test_normal_article_has_no_access_barrier() -> None:
    url = "https://example.test/article/123"
    page = SnapshotPage(
        url,
        [{"title": "正文标题", "body": "这是正常、足够长且可以审计的文章正文内容。"}],
    )

    assert await inspect_page_access(page, url, url) is None


@pytest.mark.asyncio
async def test_content_shaped_json_is_allowed_for_structured_extraction() -> None:
    url = "https://www.kuaishou.com/short-video/example"
    page = SnapshotPage(
        url,
        [
            {
                "title": "",
                "body": (
                    '{"data":{"title":"视频标题","caption":"视频正文",'
                    '"authorName":"作者"}}'
                ),
            }
        ],
    )

    assert await inspect_page_access(page, url, url) is None


@pytest.mark.asyncio
async def test_empty_ssr_shell_with_article_state_is_allowed() -> None:
    url = EmptySsrPage.url

    assert await inspect_page_access(EmptySsrPage(), url, url) is None


@pytest.mark.asyncio
async def test_article_with_login_modal_text_is_not_rejected_when_content_rendered() -> None:
    url = "https://example.test/article/123"
    body = "这是正常文章正文。" * 100 + "\n登录即代表同意相关服务条款"
    page = SnapshotPage(url, [{"title": "正文标题", "body": body}])

    assert await inspect_page_access(page, url, url) is None


@pytest.mark.asyncio
async def test_manual_access_wait_honors_cancellation() -> None:
    url = "https://example.test/login"
    page = SnapshotPage(url, [{"title": "账号登录", "body": "请先登录"}])
    cancel_event = asyncio.Event()
    cancel_event.set()

    with pytest.raises(asyncio.CancelledError):
        await wait_for_manual_access(
            page,
            url,
            url,
            timeout_seconds=90,
            cancel_event=cancel_event,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("snapshot_title", "snapshot_body"),
    [
        ("哔哩哔哩", "啊叻？视频不见了？"),
        ("微博", "该微博已被作者删除"),
        ("抖音", "作品已删除，去看看其他视频吧"),
        ("微信公众号", "该内容已被发布者删除"),
        ("贴吧", "贴子不存在"),
    ],
)
async def test_access_tool_flags_platform_deleted_pages(
    snapshot_title: str, snapshot_body: str
) -> None:
    """各平台"内容已删除"的不同说法都必须识别为内容失效（2026-08-11 扩充）。"""
    url = "https://www.example.test/content/123"
    barrier = await inspect_page_access(
        SnapshotPage(url, [{"title": snapshot_title, "body": snapshot_body}]),
        url,
        url,
    )
    assert barrier is not None
    assert barrier.kind is AccessKind.CONTENT_UNAVAILABLE


@pytest.mark.asyncio
async def test_access_tool_ignores_deleted_mentions_inside_real_article() -> None:
    """正文充实的真实页面顺带出现"已删除"字样（评论区/推荐流）不得误判失效。"""
    url = "https://www.example.test/article/123"
    body = "这是一篇正常的新闻报道正文，包含大量真实内容。" * 60 + "相关链接提示：该内容已被删除。"
    page = SnapshotPage(url, [{"title": "正常报道", "body": body}])

    assert await inspect_page_access(page, url, url) is None


@pytest.mark.asyncio
async def test_access_tool_flags_deleted_page_with_full_site_chrome() -> None:
    """带完整站点框架的真实删除页（标题=平台名、正文>800 字、删除文案在文首）。

    2026-08-12 实测回归：10 条已删微博全部误判 valid——旧的"精确标题或短
    正文"守卫必然被站点 chrome（导航/页脚/推荐流）击穿。
    """
    chrome = "微博 首页 视频 发现 游戏 会员 热门 关注 消息 " * 12
    body = chrome + "抱歉，该微博已被作者删除。查看帮助 " + "热门推荐页脚链接 " * 200
    url = "https://weibo.com/2068705397/R9tmcyUN0"

    barrier = await inspect_page_access(
        SnapshotPage(url, [{"title": "微博", "body": body}]),
        url,
        url,
    )

    assert barrier is not None
    assert barrier.kind is AccessKind.CONTENT_UNAVAILABLE


@pytest.mark.asyncio
async def test_access_tool_flags_weibo_no_view_permission_page() -> None:
    """微博删除页实测真实文案“暂无查看权限”（2026-08-12 真机快照回归）。"""
    body = (
        "NEW 56 无障碍 首页 全部关注 最新微博 特别关注 好友圈 自定义分组 管理 "
        "高校 名人明星 同事 同学 悄悄关注 返回 暂无查看权限 查看个人主页 微博热搜 "
        + "热搜词条 讨论度 " * 300
    )
    url = "https://weibo.com/2068705397/R9tmcyUN0"

    barrier = await inspect_page_access(
        SnapshotPage(url, [{"title": "微博正文 - 微博", "body": body}]),
        url,
        url,
    )

    assert barrier is not None
    assert barrier.kind is AccessKind.CONTENT_UNAVAILABLE
