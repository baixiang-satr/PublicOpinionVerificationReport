import asyncio
import json

import pytest

from src.crawler.network_payloads import NetworkPayloadCollector


class FakeRequest:
    resource_type = "xhr"


class FakeResponse:
    status = 200
    request = FakeRequest()
    headers = {"content-type": "application/json"}
    url = "https://example.test/api/content"

    def __init__(self, payload: object) -> None:
        self._payload = payload

    async def body(self) -> bytes:
        return json.dumps(self._payload).encode()


class FakeEventPage:
    def __init__(self) -> None:
        self.listener = None

    def on(self, _event: str, listener) -> None:
        self.listener = listener

    def remove_listener(self, _event: str, listener) -> None:
        if self.listener is listener:
            self.listener = None


@pytest.mark.asyncio
async def test_network_collector_keeps_only_content_shaped_json() -> None:
    page = FakeEventPage()
    collector = NetworkPayloadCollector()
    collector.attach(page)

    page.listener(FakeResponse({"tracking": {"ok": True}}))
    page.listener(
        FakeResponse(
            {"data": {"headline": "接口标题", "articleBody": "接口正文"}}
        )
    )
    await asyncio.sleep(0)
    payloads = await collector.finish(page)

    assert payloads == (
        {"data": {"headline": "接口标题", "articleBody": "接口正文"}},
    )
    assert page.listener is None
