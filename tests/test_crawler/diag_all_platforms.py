"""
Diagnostic script: test all 30 platforms and compare results.
Run: python tests/test_crawler/diag_all_platforms.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.config.settings import TaskConfig
from src.crawler.engine import CrawlEngine
from src.domain.models import UrlTask, RecordStatus


CONFIG = TaskConfig(
    max_concurrency=1,
    page_timeout_seconds=30,
    max_retries=0,
    min_host_interval_seconds=0.5,
    page_stabilize_milliseconds=500,
    screenshot_format="jpeg",
    headless=True,
)

URLS = [
    ("pinduoduo",  "拼多多", "https://mobile.yangkeduo.com/goods2.html?goods_id=715183972734"),
    ("tmall",      "天猫",   "https://detail.tmall.com/item.htm?id=816232975219"),
    ("taobao",     "淘宝",   "https://item.taobao.com/item.htm?id=831892713069"),
    ("xianyu",     "闲鱼",   "https://www.goofish.com/item?id=735469812345"),
    ("ali_1688",   "1688",   "https://detail.1688.com/offer/817026758163.html"),
    ("jd",         "京东",   "https://item.jd.com/100000123456.html"),
    ("wechat_mp",  "公众号", "https://mp.weixin.qq.com/s/J1f0xBJMDu-2A2M5wYkZ4g"),
    ("baijiahao",  "百家号", "https://baijiahao.baidu.com/s?id=1803075493205097008"),
    ("douyin",     "抖音",   "https://www.douyin.com/video/7412571429857668386"),
    ("kuaishou",   "快手",   "https://www.kuaishou.com/short-video/3x9v2ymkq6qvwxc"),
    ("xiaohongshu","小红书", "https://www.xiaohongshu.com/explore/667d3e07000000001b00a2c7"),
    ("bilibili",   "B站",    "https://www.bilibili.com/video/BV1GJ411x7wQ"),
    ("wechat_video","视频号","https://channels.weixin.qq.com/video/123456789"),
    ("sohu_tv",    "搜狐视频","https://tv.sohu.com/v/dXMvMzYzMDAwMDAvMjQ4MjM5NzQ3LnNodG1s.html"),
    ("tudou",      "土豆",   "https://www.tudou.com/programs/view/6Xc0QXh5dSU/"),
    ("youku",      "优酷",   "https://v.youku.com/v_show/id_XNjI4Njk3NDE2NA==.html"),
    ("ixigua",     "西瓜",   "https://www.ixigua.com/7412571429857668386"),
    ("iqiyi",      "爱奇艺", "https://www.iqiyi.com/v_2f8x8j1k3c.html"),
    ("weibo",      "微博",   "https://weibo.com/1542630033/Oj6V1vX2q"),
    ("tieba",      "贴吧",   "https://tieba.baidu.com/p/8648918612"),
    ("zhihu",      "知乎",   "https://www.zhihu.com/question/362425387"),
    ("toutiao",    "头条",   "https://www.toutiao.com/article/7412571429857668386/"),
    ("netease",    "网易",   "https://www.163.com/dy/article/JDQVJ42O0512D3VJ.html"),
    ("ifeng",      "凤凰",   "https://news.ifeng.com/c/8jX7Y4Z5aBc"),
    ("sohu",       "搜狐",   "https://www.sohu.com/a/123456789_100001"),
    ("hupu",       "虎扑",   "https://bbs.hupu.com/62820345.html"),
    ("dongchedi",  "懂车帝", "https://www.dongchedi.com/article/123456789"),
    ("uc_browser", "UC",     "https://www.uc.cn/"),
    ("browser_360","360",    "https://www.so.com/"),
    ("qq_browser", "QQ",     "https://browser.qq.com/"),
]


async def test_one(name: str, url: str) -> list[str]:
    engine = CrawlEngine(CONFIG)
    result = (await engine.run([UrlTask(1, url, url)], Path(f"output/diag/{name}")))[0]
    lines = []
    status = result.status.value
    code = result.page.status_code if result.page else "N/A"
    shot = "Y" if result.assets.page_screenshot else "N"
    author = (result.page.author_name or "-") if result.page else "-"
    title = (result.page.title or "-")[:50] if result.page else "-"
    err_codes = "; ".join(e.code for e in result.errors) if result.errors else "none"
    lines.append(f"{name:20s} | {status:15s} | HTTP {str(code):>4s} | shot={shot} | {err_codes}")
    return lines


async def main():
    output_base = Path("output/diag_comparison")

    # One engine for all URLs (shared browser pool)
    tasks = [UrlTask(i + 1, url, url) for i, (_, _, url) in enumerate(URLS)]
    engine = CrawlEngine(TaskConfig(
        max_concurrency=2,
        page_timeout_seconds=30,
        max_retries=0,
        min_host_interval_seconds=0.5,
        page_stabilize_milliseconds=500,
        screenshot_format="jpeg",
        headless=True,
    ))
    results = await engine.run(tasks, output_base)

    header = f"{'Platform':20s} | {'Status':15s} | HTTP    | Shot | Error codes"
    print(header)
    print("-" * 90)
    for (name, label, _), result in zip(URLS, results):
        status = result.status.value
        code = str(result.page.status_code) if result.page and result.page.status_code else "N/A"
        shot = "Y" if result.assets.page_screenshot else "N"
        err_codes = "; ".join(e.code for e in result.errors) if result.errors else "OK"
        print(f"{name:20s} | {status:15s} | HTTP {code:>4s} |  {shot}   | {err_codes}")
        sys.stdout.flush()

    print(f"\nAll done! Runtime details in: {output_base.resolve()}")

if __name__ == "__main__":
    asyncio.run(main())
