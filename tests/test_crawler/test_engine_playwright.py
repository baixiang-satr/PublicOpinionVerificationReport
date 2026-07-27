from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import struct
from threading import Thread
from typing import Iterator
import zlib

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
        if self.path == "/image.png":
            body = _png_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/author":
            body = b"<!doctype html><html><body><h1>Fixture Author Home</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = b"""<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="author" content="Fixture Author">
<script type="application/ld+json">
{"@type":"Article","headline":"Fixture Title","articleBody":"Fixture article body","author":{"name":"Fixture Author"}}
</script>
</head><body>
<a rel="author" href="/author">Fixture Author</a>
<article><h1>Fixture Title</h1><p>Fixture article body</p><img src="/image.png" alt="evidence"></article>
</body></html>"""
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


def _png_bytes(width: int = 100, height: int = 100) -> bytes:
    raw = b"".join(b"\x00" + b"\x33\x66\x99" * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


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
    assert result.page.author_url is not None and result.page.author_url.endswith("/author")
    assert result.assets.page_screenshot is not None
    assert result.assets.page_screenshot.read_bytes().startswith(b"\x89PNG")
    assert result.assets.author_screenshot is not None
    assert result.assets.author_screenshot.name == "001主页.png"
    assert result.assets.author_screenshot.read_bytes().startswith(b"\x89PNG")
    assert [path.name for path in result.assets.downloaded_images] == ["001_01.png"]
    assert result.assets.downloaded_images[0].read_bytes().startswith(b"\x89PNG")
