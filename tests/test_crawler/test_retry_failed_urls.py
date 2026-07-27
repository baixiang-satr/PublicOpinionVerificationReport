"""
针对上一轮失效的平台，尝试多个候选 URL 重新测试，直到找到有效 URL 为止。

测试平台: 网易新闻, 搜狐新闻, 凤凰新闻, 虎扑, 搜狐视频, 小红书

每个平台准备多组候选 URL，按顺序测试。若所有候选 URL 都返回无效结果
（404/405/超时/空页面），则可判断该平台本身对 headless 浏览器有额外封锁。

运行方式:
    set POR_RUN_EXTERNAL_TESTS=1
    python -m pytest tests/test_crawler/test_retry_failed_urls.py -v --capture=no
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.config.settings import TaskConfig
from src.crawler.engine import CrawlEngine
from src.domain.models import RecordStatus, UrlTask

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.playwright,
    pytest.mark.external,
    pytest.mark.skipif(
        os.getenv("POR_RUN_EXTERNAL_TESTS") != "1",
        reason="Set POR_RUN_EXTERNAL_TESTS=1 to run real-site diagnostics.",
    ),
]

# ── 候选 URL 列表（每个平台多组） ──────────────────────────────────────
# 每组: (测试名称, URL, 工作表, 说明, 候选序号)

CANDIDATE_URLS: list[tuple[str, str, str, str, int]] = []

# ── 1. 网易新闻 ──────────────────────────────────────────────────────────
# 不同频道、不同 ID 格式
NETEASE_CANDIDATES = [
    ("netease", "https://www.163.com/", "生活资讯", "网易 - 首页(发现文章链接)", 0),
    ("netease", "https://news.163.com/", "生活资讯", "网易新闻 - 首页", 1),
    ("netease", "https://war.163.com/", "生活资讯", "网易军事 - 首页", 2),
    ("netease", "https://www.163.com/dy/article/IA6V2S1F0512D3VJ.html", "生活资讯", "网易号 - 文章1", 3),
    ("netease", "https://www.163.com/dy/article/JDQVJ42O0512D3VJ.html", "生活资讯", "网易号 - 文章2(上次404)", 4),
    ("netease", "https://www.163.com/dy/article/HJ7V5T4O0514R9P4.html", "生活资讯", "网易号 - 文章3", 5),
    ("netease", "https://news.163.com/24/0728/10/IA6V2S1F00001124.html", "生活资讯", "网易新闻 - 旧格式", 6),
    ("netease", "https://www.163.com/24/0728/10/IA6V2S1F00001124.html", "生活资讯", "网易新闻 - 通用域", 7),
]
CANDIDATE_URLS.extend(NETEASE_CANDIDATES)

# ── 2. 搜狐新闻 ──────────────────────────────────────────────────────────
# 不同路径格式、数字 ID 猜测
SOHU_CANDIDATES = [
    ("sohu", "https://www.sohu.com/", "生活资讯", "搜狐 - 首页(发现文章链接)", 0),
    ("sohu", "https://www.sohu.com/a/490071234_121124362", "生活资讯", "搜狐新闻 - 文章1", 1),
    ("sohu", "https://www.sohu.com/a/362251234_120123456", "生活资讯", "搜狐新闻 - 文章2", 2),
    ("sohu", "https://www.sohu.com/a/123456789_100001", "生活资讯", "搜狐新闻 - 文章3(上次超时)", 3),
    ("sohu", "https://www.sohu.com/a/500000000_121124000", "生活资讯", "搜狐新闻 - 文章4", 4),
    ("sohu", "https://www.sohu.com/a/470000000_121124000", "生活资讯", "搜狐新闻 - 文章5", 5),
    ("sohu", "https://news.sohu.com/", "生活资讯", "搜狐新闻 - 首页", 6),
    ("sohu", "https://business.sohu.com/", "生活资讯", "搜狐财经 - 首页", 7),
]
CANDIDATE_URLS.extend(SOHU_CANDIDATES)

# ── 3. 凤凰新闻 ──────────────────────────────────────────────────────────
IFENG_CANDIDATES = [
    ("ifeng", "https://www.ifeng.com/", "生活资讯", "凤凰网 - 首页(发现文章链接)", 0),
    ("ifeng", "https://news.ifeng.com/", "生活资讯", "凤凰新闻 - 首页", 1),
    ("ifeng", "https://news.ifeng.com/c/8jX7Y4Z5aBc", "生活资讯", "凤凰新闻 - 文章(上次404)", 2),
    ("ifeng", "https://news.ifeng.com/c/8f7d4e8f-1234", "生活资讯", "凤凰新闻 - 文章2", 3),
    ("ifeng", "https://news.ifeng.com/c/8f7d4e8f1234", "生活资讯", "凤凰新闻 - 文章3", 4),
    ("ifeng", "https://news.ifeng.com/c/8f7d4e8f", "生活资讯", "凤凰新闻 - 文章4", 5),
    ("ifeng", "https://news.ifeng.com/c/8f7d4e8f0000", "生活资讯", "凤凰新闻 - 文章5", 6),
    ("ifeng", "https://news.ifeng.com/c/8f7d4e8f-5678-1234-abcd-123456789abc", "生活资讯", "凤凰新闻 - 文章6(UUID格式)", 7),
]
CANDIDATE_URLS.extend(IFENG_CANDIDATES)

# ── 4. 虎扑 ──────────────────────────────────────────────────────────────
HUPU_CANDIDATES = [
    ("hupu", "https://www.hupu.com/", "生活资讯", "虎扑 - 首页(发现文章链接)", 0),
    ("hupu", "https://bbs.hupu.com/", "生活资讯", "虎扑BBS - 首页", 1),
    ("hupu", "https://bbs.hupu.com/62820345.html", "生活资讯", "虎扑 - 帖子(上次405)", 2),
    ("hupu", "https://bbs.hupu.com/62820345", "生活资讯", "虎扑 - 帖子无html后缀", 3),
    ("hupu", "https://m.hupu.com/bbs/62820345.html", "生活资讯", "虎扑 - 移动端帖子", 4),
    ("hupu", "https://bbs.hupu.com/62820345-1.html", "生活资讯", "虎扑 - 帖子带分页", 5),
    ("hupu", "https://bbs.hupu.com/62820346.html", "生活资讯", "虎扑 - 帖子相邻ID", 6),
    ("hupu", "https://bbs.hupu.com/62820345-2.html", "生活资讯", "虎扑 - 帖子2页", 7),
]
CANDIDATE_URLS.extend(HUPU_CANDIDATES)

# ── 5. 搜狐视频 ──────────────────────────────────────────────────────────
SOHUTV_CANDIDATES = [
    ("sohu_tv", "https://tv.sohu.com/", "图文视频", "搜狐视频 - 首页(发现视频链接)", 0),
    ("sohu_tv", "https://tv.sohu.com/v/dXMvMzYzMDAwMDAvMjQ4MjM5NzQ3LnNodG1s.html", "图文视频", "搜狐视频 - 视频(上次404)", 1),
    ("sohu_tv", "https://tv.sohu.com/v/dXMvMzYzMDAwMDAvMjQ4MjM5NzQ3.html", "图文视频", "搜狐视频 - 视频2", 2),
    ("sohu_tv", "https://tv.sohu.com/v/dXMvMzYzMDAwMDAvMjQ4MjM5.html", "图文视频", "搜狐视频 - 视频3", 3),
    ("sohu_tv", "https://my.tv.sohu.com/", "图文视频", "搜狐视频 - 我的", 4),
    ("sohu_tv", "https://tv.sohu.com/star/", "图文视频", "搜狐视频 - 明星", 5),
]
CANDIDATE_URLS.extend(SOHUTV_CANDIDATES)

# ── 6. 小红书 ────────────────────────────────────────────────────────────
XHS_CANDIDATES = [
    ("xiaohongshu", "https://www.xiaohongshu.com/", "图文视频", "小红书 - 首页(发现笔记)", 0),
    ("xiaohongshu", "https://www.xiaohongshu.com/explore/667d3e07000000001b00a2c7", "图文视频", "小红书 - 笔记(上次404)", 1),
    ("xiaohongshu", "https://www.xiaohongshu.com/explore/667d3e07000000001b00a2c8", "图文视频", "小红书 - 笔记2", 2),
    ("xiaohongshu", "https://www.xiaohongshu.com/explore/667d3e07000000001b00a2c9", "图文视频", "小红书 - 笔记3", 3),
    ("xiaohongshu", "https://www.xiaohongshu.com/explore/667d3e07000000001b00a2d0", "图文视频", "小红书 - 笔记4", 4),
    ("xiaohongshu", "https://www.xiaohongshu.com/explore/667d3e07000000001b00a2d1", "图文视频", "小红书 - 笔记5", 5),
    ("xiaohongshu", "https://www.xiaohongshu.com/explore/667d3e07000000001b00a2d2", "图文视频", "小红书 - 笔记6", 6),
    ("xiaohongshu", "https://www.xiaohongshu.com/discovery/item/667d3e07000000001b00a2c7", "图文视频", "小红书 - discovery路径", 7),
]
CANDIDATE_URLS.extend(XHS_CANDIDATES)

# ── 测试配置 ──────────────────────────────────────────────────────────────
RETEST_CONFIG = TaskConfig(
    max_concurrency=1,
    page_timeout_seconds=30,
    max_retries=0,
    min_host_interval_seconds=0.5,
    page_stabilize_milliseconds=500,
    screenshot_format="jpeg",
    headless=True,
)


@pytest.mark.parametrize(
    ("name", "url", "sheet", "description", "candidate_idx"),
    CANDIDATE_URLS,
    ids=[f"{item[0]}_cand{item[4]}" for item in CANDIDATE_URLS],
)
async def test_candidate_url(name: str, url: str, sheet: str, description: str, candidate_idx: int, tmp_path: Path) -> None:
    """尝试一个候选 URL，记录访问结果。"""
    output_dir = tmp_path / f"{name}_cand{candidate_idx}"
    output_dir.mkdir(parents=True, exist_ok=True)

    engine = CrawlEngine(RETEST_CONFIG)
    tasks = [UrlTask(1, url, url)]
    results = await engine.run(tasks, output_dir)

    assert len(results) == 1
    result = results[0]
    page = result.page
    errors = result.errors
    screenshot = result.assets.page_screenshot

    # 仅把完成取证的内容页视为有效；首页、受限页和诊断响应都不是证据。
    is_valid = False
    if result.status == RecordStatus.ASSETS_READY:
        is_valid = True

    tag = "✅有效" if is_valid else "❌无效"
    print(f"\n{'='*60}")
    print(f"[{tag}] {description}")
    print(f"  URL: {url}")
    print(f"  状态: {result.status.value}, 状态码: {page.status_code if page else 'N/A'}")
    if errors:
        for e in errors:
            print(f"  错误: [{e.stage}] {e.code}: {e.message[:80]}...")
    if screenshot:
        print(f"  截图: {screenshot.name} ({screenshot.stat().st_size} bytes)")
    print(f"{'='*60}")
