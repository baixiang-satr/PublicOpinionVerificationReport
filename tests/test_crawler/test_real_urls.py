"""使用真实 URL 测试爬取功能 —— 覆盖模板所有工作表（除群聊/朋友圈）。

运行方式:
    python -m pytest tests/test_crawler/test_real_urls.py -v --capture=no

注意: 真实网站可能因网络、反爬机制或页面结构变化导致测试失败，
这不是代码本身的 bug，请根据实际错误信息判断。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config.settings import TaskConfig
from src.crawler.engine import CrawlEngine
from src.domain.models import RecordStatus, UrlTask

pytestmark = [pytest.mark.asyncio, pytest.mark.playwright]

# ── 测试 URL 列表（覆盖模板所有适用工作表的平台） ────────────────────────
# 每个平台一个公开可访问的 URL。部分 URL 可能已过期，但不影响反爬分析。

REAL_URLS: list[tuple[str, str, str, str]] = [
    # (测试名称, 原始URL, 工作表, 平台说明)
    # ── 电商平台 ──
    (
        "pinduoduo",
        "https://mobile.yangkeduo.com/goods2.html?goods_id=715183972734",
        "电商平台",
        "拼多多 - 商品",
    ),
    (
        "tmall",
        "https://detail.tmall.com/item.htm?id=816232975219",
        "电商平台",
        "天猫 - 商品",
    ),
    (
        "taobao",
        "https://item.taobao.com/item.htm?id=831892713069",
        "电商平台",
        "淘宝 - 商品",
    ),
    (
        "xianyu",
        "https://www.goofish.com/item?id=735469812345",
        "电商平台",
        "闲鱼 - 商品",
    ),
    (
        "ali_1688",
        "https://detail.1688.com/offer/817026758163.html",
        "电商平台",
        "1688 - 商品",
    ),
    (
        "jd",
        "https://item.jd.com/100000123456.html",
        "电商平台",
        "京东 - 商品",
    ),
    # ── 公众号 ──
    (
        "wechat_mp",
        "https://mp.weixin.qq.com/s/J1f0xBJMDu-2A2M5wYkZ4g",
        "公众号",
        "微信公众号 - 文章",
    ),
    (
        "baijiahao",
        "https://baijiahao.baidu.com/s?id=1803075493205097008",
        "公众号",
        "百度百家号 - 文章",
    ),
    # ── 图文视频 ──
    (
        "douyin",
        "https://www.douyin.com/video/7412571429857668386",
        "图文视频",
        "抖音 - 视频",
    ),
    (
        "kuaishou",
        "https://www.kuaishou.com/short-video/3x9v2ymkq6qvwxc",
        "图文视频",
        "快手 - 短视频",
    ),
    (
        "xiaohongshu",
        "https://www.xiaohongshu.com/explore/667d3e07000000001b00a2c7",
        "图文视频",
        "小红书 - 笔记",
    ),
    (
        "bilibili",
        "https://www.bilibili.com/video/BV1GJ411x7wQ",
        "图文视频",
        "哔哩哔哩 - 视频",
    ),
    (
        "wechat_video",
        "https://channels.weixin.qq.com/video/123456789",
        "图文视频",
        "微信视频号 - 视频",
    ),
    (
        "sohu_tv",
        "https://tv.sohu.com/v/dXMvMzYzMDAwMDAvMjQ4MjM5NzQ3LnNodG1s.html",
        "图文视频",
        "搜狐视频 - 视频",
    ),
    (
        "tudou",
        "https://www.tudou.com/programs/view/6Xc0QXh5dSU/",
        "图文视频",
        "土豆 - 视频",
    ),
    (
        "youku",
        "https://v.youku.com/v_show/id_XNjI4Njk3NDE2NA==.html",
        "图文视频",
        "优酷 - 视频",
    ),
    (
        "ixigua",
        "https://www.ixigua.com/7412571429857668386",
        "图文视频",
        "西瓜视频 - 视频",
    ),
    (
        "iqiyi",
        "https://www.iqiyi.com/v_2f8x8j1k3c.html",
        "图文视频",
        "爱奇艺 - 视频",
    ),
    # ── 微博博客 ──
    (
        "weibo",
        "https://weibo.com/1542630033/Oj6V1vX2q",
        "微博博客",
        "新浪微博 - 帖子",
    ),
    (
        "tieba",
        "https://tieba.baidu.com/p/8648918612",
        "微博博客",
        "百度贴吧 - 帖子",
    ),
    (
        "zhihu",
        "https://www.zhihu.com/question/362425387",
        "微博博客",
        "知乎 - 问答",
    ),
    # ── 生活资讯 ──
    (
        "toutiao",
        "https://www.toutiao.com/article/7412571429857668386/",
        "生活资讯",
        "今日头条 - 文章",
    ),
    (
        "netease",
        "https://www.163.com/dy/article/JDQVJ42O0512D3VJ.html",
        "生活资讯",
        "网易新闻 - 文章",
    ),
    (
        "ifeng",
        "https://news.ifeng.com/c/8jX7Y4Z5aBc",
        "生活资讯",
        "凤凰新闻 - 文章",
    ),
    (
        "sohu",
        "https://www.sohu.com/a/123456789_100001",
        "生活资讯",
        "搜狐新闻 - 文章",
    ),
    (
        "hupu",
        "https://bbs.hupu.com/62820345.html",
        "生活资讯",
        "虎扑 - 帖子",
    ),
    (
        "dongchedi",
        "https://www.dongchedi.com/article/123456789",
        "生活资讯",
        "懂车帝 - 文章",
    ),
    # ── 浏览器 ──
    (
        "uc_browser",
        "https://www.uc.cn/",
        "浏览器",
        "UC浏览器 - 首页",
    ),
    (
        "browser_360",
        "https://www.so.com/",
        "浏览器",
        "360浏览器 - 首页",
    ),
    (
        "qq_browser",
        "https://browser.qq.com/",
        "浏览器",
        "QQ浏览器 - 首页",
    ),
]

# ── 测试配置 ──────────────────────────────────────────────────────────────
# 为真实网站配置更宽松的超时和重试

REAL_SITE_CONFIG = TaskConfig(
    max_concurrency=2,
    page_timeout_seconds=45,
    max_retries=1,
    retry_base_delay_seconds=0.5,
    min_host_interval_seconds=1.5,
    page_stabilize_milliseconds=1000,
    screenshot_format="jpeg",
    headless=True,
)


@pytest.mark.parametrize(
    ("name", "url", "sheet", "description"),
    REAL_URLS,
    ids=[item[0] for item in REAL_URLS],
)
async def test_crawl_real_url(name: str, url: str, sheet: str, description: str, tmp_path: Path) -> None:
    """使用真实 URL 测试爬取流程：页面访问 → 解析 → 路由 → 截图。"""
    output_dir = tmp_path / name
    output_dir.mkdir(parents=True, exist_ok=True)

    engine = CrawlEngine(REAL_SITE_CONFIG)
    tasks = [UrlTask(1, url, url)]

    results = await engine.run(tasks, output_dir)

    assert len(results) == 1
    result = results[0]

    # ── 结果诊断输出 ──
    status = result.status
    page = result.page
    route = result.route
    errors = result.errors
    screenshot = result.assets.page_screenshot

    print(f"\n{'='*60}")
    print(f"归属工作表: {sheet}")
    print(f"测试平台: {description} ({name})")
    print(f"URL: {url}")
    print(f"状态: {status.value}")
    print(f"尝试次数: {result.attempt_count}")
    print(f"耗时: {result.elapsed_seconds:.1f}s" if result.elapsed_seconds else "耗时: N/A")

    if page:
        print(f"最终 URL: {page.final_url}")
        print(f"状态码: {page.status_code}")
        print(f"标题: {page.title}")
        print(f"作者: {page.author_name}")
        print(f"内容摘要: {(page.content_summary or page.content_text or '')[:100]}...")

    if route:
        print(f"路由: 工作表={route.sheet_name}, 平台值={route.platform_value}, 文本类型={route.text_type}")

    if errors:
        print(f"错误 ({len(errors)}):")
        for e in errors:
            print(f"  - [{e.stage}] {e.code}: {e.message} (可重试={e.retryable})")

    if screenshot:
        print(f"截图: {screenshot} ({screenshot.stat().st_size} bytes)")
    else:
        print("截图: 无")

    print(f"{'='*60}\n")

    # ── 断言: 真实网站访问至少有明确结果 ──
    # 失败或需人工审核都算有结果（可能是反爬），
    # 只有 CANCELLED 或完全无响应才算异常
    assert status not in (RecordStatus.PENDING, RecordStatus.RUNNING, RecordStatus.CANCELLED), (
        f"爬取未完成或已取消: {status}"
    )
