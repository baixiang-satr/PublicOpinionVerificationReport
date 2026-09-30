"""Barrier-only page guards and deleted-content confirmation.

Split out of ``page_access`` (single-file line cap). CAPTCHA/LOGIN/
RESTRICTED text hits keep the original "barrier-only page" guard so real
articles whose chrome mentions login are not rejected; deleted-content
confirmation uses stronger rules because real platform error pages render
full site chrome (nav/footer/recommendations) that defeats both the
exact-title set and the bare short-body heuristic.
"""

from __future__ import annotations

# ── 内容不可用文本特征（page_access 与本模块共用此单一事实源）────────────
UNAVAILABLE_TEXT_MARKERS = (
    "笔记不存在",
    "内容不存在",
    "页面不存在",
    "视频不存在",
    "文章不存在",
    "内容已删除",
    "内容可能已删除",
    "内容已下线",
    "页面已失效",
    "404 not found",
    "该内容已被删除",
    "页面不存在或已删除",
    "内容找不到了",
    "该内容暂时无法查看",
    # 微博删除/权限回收页的真实文案（2026-08-12 实测：已删微博详情页显示
    # “返回 / 暂无查看权限 / 查看个人主页”，无任何“已删除”字样，标题仍是
    # “微博正文 - 微博”，正文为完整站点 chrome+热搜榜单）。微博不区分删除
    # 与权限回收，二者对上报业务同为“公众不可见”，统一判失效。
    "暂无查看权限",
    "暂无权限查看",
    # 知乎内容删除/失效专用错误页（“你似乎来到了没有知识存在的荒原”，数秒后自动跳转首页）。
    "没有知识存在的荒原",
    # ── 各平台"内容已删除"的其他常见说法（2026-08-11 扩充）──
    "视频不见了",
    "作品已删除",
    "作品不存在",
    "视频已删除",
    "视频已下架",
    "该视频已失效",
    "微博已被删除",
    "已被作者删除",
    "已被发布者删除",
    "因违规无法查看",
    "笔记已被删除",
    "贴子不存在",
    "帖子不存在",
    "内容已被下架",
    "该动态已被删除",
)

#: 纯错误页守卫的正文可见字符上限（历史基线）。
_BARRIER_ONLY_MAX_VISIBLE_CHARS = 800
#: 删除文案“文首”窗口：错误页的错误提示位于页面顶部，正常文章顺带提及
#: “已删除”出现在评论区/推荐流深处；窗口越长越容易误伤，越短越容易漏。
_BODY_HEAD_VISIBLE_CHARS = 400

#: 各屏障类型的"纯错误页"精确标题集合（CONTENT_UNAVAILABLE 不再使用——
#: 由 content_unavailable_confirmed 的子串规则覆盖，精确匹配是子串特例）。
_EXACT_BARRIER_TITLES: dict[str, frozenset[str]] = {
    "captcha": frozenset(
        {
            "安全验证",
            "访问验证",
            "人机验证",
            "验证码中间页",
            "captcha",
            "verify you are human",
        }
    ),
    "login": frozenset(
        {
            "登录",
            "账号登录",
            "扫码登录",
            "sign in",
            "log in",
        }
    ),
    "access_restricted": frozenset(
        {
            "访问异常",
            "访问受限",
            "access denied",
        }
    ),
}


def looks_like_barrier_only_page(title: str, body: str, kind: str) -> bool:
    """Avoid rejecting a real article merely because its chrome has a login prompt."""

    normalized_title = title.strip().casefold()
    if normalized_title in _EXACT_BARRIER_TITLES.get(kind, frozenset()):
        return True

    # Substantial public pages commonly include a login modal or login text in
    # the header. Treat text markers as a barrier only when little other page
    # content rendered.
    visible_length = len("".join(body.split()))
    return visible_length < _BARRIER_ONLY_MAX_VISIBLE_CHARS


#: 历史精确标题白名单的兜底："404" 这类极短标题不含完整删除文案，
#: 子串规则覆盖不到，保留精确匹配等价语义（更长的精确标题均是子串特例）。
_EXACT_UNAVAILABLE_TITLES = frozenset({"404"})


def content_unavailable_marker(title: str, body: str) -> str | None:
    """确证"内容已删除/不存在"时返回命中的删除文案 marker，否则 None。

    判定规则与三规则收口完全一致（标题子串/文首命中/短正文纯错误页），
    返回值供失效候选的"判定依据引文"留痕使用。
    """

    normalized_title = title.strip().casefold()
    # 裸 "404" 这类极短标题本身就是确定性错误页信号，无需正文佐证。
    if normalized_title in _EXACT_UNAVAILABLE_TITLES:
        return normalized_title
    normalized = f"{title}\n{body}".casefold()
    if not any(marker in normalized for marker in UNAVAILABLE_TEXT_MARKERS):
        return None
    for marker in UNAVAILABLE_TEXT_MARKERS:
        if marker in normalized_title:
            return marker
    visible = "".join(body.split())
    for marker in UNAVAILABLE_TEXT_MARKERS:
        if marker in visible[:_BODY_HEAD_VISIBLE_CHARS]:
            return marker
    # 含空格的文案（"404 not found"）在去除空白后不可逆，补一轮原始文首窗口。
    head_raw = body[:_BODY_HEAD_VISIBLE_CHARS].casefold()
    for marker in UNAVAILABLE_TEXT_MARKERS:
        if marker in head_raw:
            return marker
    if 0 < len(visible) < _BARRIER_ONLY_MAX_VISIBLE_CHARS:
        for marker in UNAVAILABLE_TEXT_MARKERS:
            if marker in normalized:
                return marker
    return None


def content_unavailable_confirmed(title: str, body: str) -> bool:
    """确证"内容已删除/不存在"：三规则任一命中（见 content_unavailable_marker）。"""

    return content_unavailable_marker(title, body) is not None


__all__ = [
    "UNAVAILABLE_TEXT_MARKERS",
    "content_unavailable_confirmed",
    "content_unavailable_marker",
    "looks_like_barrier_only_page",
]
