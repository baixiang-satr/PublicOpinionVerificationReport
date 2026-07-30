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


@pytest.mark.asyncio
async def test_finish_is_bounded_when_response_body_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """长连接响应体永不返回时，finish 必须有界退出（反爬卡死回归）。"""

    monkeypatch.setattr(
        "src.crawler.network_payloads._FINISH_DRAIN_SECONDS", 0.1
    )

    class HangingResponse:
        status = 200
        request = FakeRequest()
        headers = {"content-type": "application/json"}
        url = "https://example.test/api/stream"

        async def body(self) -> bytes:
            await asyncio.Event().wait()
            return b""

    page = FakeEventPage()
    collector = NetworkPayloadCollector()
    collector.attach(page)
    page.listener(HangingResponse())
    await asyncio.sleep(0)

    payloads = await asyncio.wait_for(collector.finish(page), timeout=2)

    assert payloads == ()
    assert page.listener is None  # 先摘 listener 再有界 drain


@pytest.mark.asyncio
async def test_priority_payloads_survive_junk_flood() -> None:
    """抖音式推荐洪峰填满常规配额后，目标内容载荷仍必须保留且排最前。"""

    page = FakeEventPage()
    collector = NetworkPayloadCollector(max_payloads=2)
    collector.attach(page, "https://www.douyin.com/video/7667987870472788815")

    for index in range(3):
        page.listener(
            FakeResponse({"data": {"headline": f"推荐{index}", "articleBody": "x"}})
        )
        await asyncio.sleep(0)
    target = {
        "aweme_detail": {
            "aweme_id": "7667987870472788815",
            "desc": "目标视频",
            "createTime": 1_751_000_000_000,
            "author": {"nickname": "目标作者"},
        }
    }
    page.listener(FakeResponse(target))
    await asyncio.sleep(0)
    payloads = await collector.finish(page)

    assert payloads[0] == target  # 优先通道排最前
    assert len(payloads) == 3  # 1 优先 + 2 常规配额，第三份推荐被丢弃
