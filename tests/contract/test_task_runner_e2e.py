from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import struct
from threading import Thread
from typing import Iterator
import zlib
from zipfile import ZipFile

import pytest

from src.config.settings import AppConfig, TaskConfig, TemplateConfig
from src.crawler.engine import CrawlEngine
from src.crawler.platform_catalog import find_platform
from src.domain.models import PageData, RecordStatus, RouteDecision, UrlTask
from src.services.models import JobRequest
from src.services.task_runner import TaskRunner
from src.utils.file_utils import build_file_manifest


pytestmark = [pytest.mark.asyncio, pytest.mark.playwright, pytest.mark.excel]


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/image.png":
            body = _png_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
        elif self.path == "/author":
            body = b"<html><body><h1>Local Author</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
        else:
            body = b"""<!doctype html><html><head><meta charset="utf-8">
<script type="application/ld+json">
{"@type":"Article","headline":"Local Title","articleBody":"Local article body","author":{"name":"Local Author"}}
</script></head><body><a rel="author" href="/author">Local Author</a>
<article><h1>Local Title</h1><p>Local article body</p>
<img src="/image.png" alt="evidence"></article></body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class FixtureRouter:
    definition = find_platform("https://www.zhihu.com/question/1")

    def definition_for(self, _url: str):
        return self.definition

    def route(self, _url: str, _page: PageData) -> RouteDecision:
        return RouteDecision("微博博客", "知乎_知乎_博客贴吧", "正文")


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


@pytest.mark.asyncio
async def test_task_runner_creates_real_fixed_template_archive(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    source_template = project_root / "template"
    source_manifest = build_file_manifest(source_template)
    config = AppConfig(
        template=TemplateConfig(
            source_dir=source_template,
            output_dir=tmp_path / "output",
        ),
        task=TaskConfig(
            max_concurrency=1,
            max_retries=0,
            min_host_interval_seconds=0,
            page_stabilize_milliseconds=0,
            screenshot_format="png",
        ),
    )
    runner = TaskRunner(
        config,
        engine_factory=lambda task_config: CrawlEngine(
            task_config,
            router=FixtureRouter(),
        ),
    )

    with local_server() as base_url:
        result = await runner.run(
            JobRequest(
                tasks=(UrlTask(1, f"{base_url}/article", f"{base_url}/article"),),
                job_id="task-runner-e2e",
            )
        )

    assert result.records[0].status == RecordStatus.EXPORTED
    assert result.archive_path is not None
    with ZipFile(result.archive_path) as archive:
        assert archive.namelist() == [
            "template/001.png",
            "template/001主页.png",
            "template/template.xlsx",
        ]
    assert build_file_manifest(source_template) == source_manifest


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
