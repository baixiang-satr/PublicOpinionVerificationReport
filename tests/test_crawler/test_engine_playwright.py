from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Iterator

import pytest

from src.config.settings import TaskConfig
from src.crawler.engine import CrawlEngine
from src.crawler.platform_catalog import find_platform
from src.domain.models import PageData, RecordStatus, RouteDecision, UrlTask


pytestmark = [pytest.mark.asyncio, pytest.mark.playwright]


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/article")
            self.end_headers()
            return
        body = b"""<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="author" content="Fixture Author">
<script type="application/ld+json">
{"@type":"Article","headline":"Fixture Title","articleBody":"Fixture article body","author":{"name":"Fixture Author"}}
</script>
</head><body><article><h1>Fixture Title</h1><p>Fixture article body</p></article></body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class FixtureRouter:
    definition = find_platform("https://www.zhihu.com/question/1")

    def definition_for(self, _final_url: str):
        return self.definition

    def route(self, _final_url: str, _page: PageData) -> RouteDecision:
        assert self.definition is not None
        return RouteDecision(self.definition.sheet_name, self.definition.platform_value, "正文")


@contextmanager
def local_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def test_real_browser_records_redirect_status_parses_and_screenshots(tmp_path: Path) -> None:
    config = TaskConfig(
        max_concurrency=1,
        max_retries=0,
        min_host_interval_seconds=0,
        page_stabilize_milliseconds=0,
        screenshot_format="png",
    )
    with local_server() as base_url:
        source_url = f"{base_url}/redirect"
        [result] = await CrawlEngine(config, router=FixtureRouter()).run(
            [UrlTask(1, source_url, source_url)],
            tmp_path,
        )

    assert result.status == RecordStatus.ASSETS_READY
    assert result.page.status_code == 200
    assert result.page.final_url is not None and result.page.final_url.endswith("/article")
    assert result.page.redirect_chain[0].endswith("/redirect")
    assert result.page.redirect_chain[-1].endswith("/article")
    assert result.page.title == "Fixture Title"
    assert result.page.author_name == "Fixture Author"
    assert result.assets.page_screenshot is not None
    assert result.assets.page_screenshot.read_bytes().startswith(b"\x89PNG")
