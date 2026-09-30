"""LlmClient 请求格式与错误映射测试（httpx.MockTransport，全离线）。"""

import json

import httpx
import pytest

from src.llm.client import LlmClient, LlmError
from src.llm.models import LlmSettings


def _settings() -> LlmSettings:
    return LlmSettings(
        enabled=True,
        base_url="https://llm.test/v1/",
        model="test-model",
        api_key="sk-test",
        timeout_seconds=5.0,
    )


def _client(handler) -> LlmClient:
    return LlmClient(_settings(), transport=httpx.MockTransport(handler))


async def test_complete_posts_chat_completions() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "  正常  "}}]},
        )

    reply = await _client(handler).complete("系统提示", "用户输入")

    assert reply == "正常"
    assert seen["url"] == "https://llm.test/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["model"] == "test-model"
    assert seen["body"]["messages"] == [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "用户输入"},
    ]


async def test_http_error_raises_llm_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    with pytest.raises(LlmError, match="401"):
        await _client(handler).complete("s", "u")


async def test_network_error_raises_llm_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LlmError, match="网络错误"):
        await _client(handler).complete("s", "u")


async def test_malformed_response_raises_llm_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(LlmError, match="格式"):
        await _client(handler).complete("s", "u")
