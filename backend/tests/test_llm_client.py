"""OpenAI-compatible client tests using a local mock transport."""

import asyncio
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from backend.infrastructure.llm_client import LLMClientError, OpenAILLMClient


def _run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _mock_transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _client(
    base_url: str = "https://llm.test",
    api_key: str = "secret",
    model: str = "model",
    timeout: float = 5.0,
    http_client: httpx.AsyncClient | None = None,
) -> OpenAILLMClient:
    return OpenAILLMClient(
        base_url=base_url,
        api_key=SecretStr(api_key),
        model=model,
        timeout_seconds=timeout,
        http_client=http_client,
    )


def _ok_response(request: httpx.Request) -> httpx.Response:
    assert request.headers["authorization"] == "Bearer secret"
    body = {"choices": [{"message": {"content": "Привет"}}]}
    return httpx.Response(200, json=body)


async def _with_mock(
    handler: Callable[[httpx.Request], httpx.Response],
    action: Callable[[httpx.AsyncClient, OpenAILLMClient], object],
) -> object:
    transport = _mock_transport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = _client(http_client=http)
        return await action(http, client)


def test_client_returns_answer() -> None:
    async def action(_http: httpx.AsyncClient, client: OpenAILLMClient) -> str:
        return await client.ask("context", "question")

    answer = _run(_with_mock(_ok_response, action))
    assert answer == "Привет"


def test_client_handles_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timeout")

    async def action(_http: httpx.AsyncClient, client: OpenAILLMClient) -> None:
        with pytest.raises(LLMClientError, match="Таймаут"):
            await client.ask("context", "question")

    _run(_with_mock(handler, action))


def test_client_handles_non_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    async def action(_http: httpx.AsyncClient, client: OpenAILLMClient) -> None:
        with pytest.raises(LLMClientError, match="HTTP 429"):
            await client.ask("context", "question")

    _run(_with_mock(handler, action))


def test_client_handles_malformed_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    async def action(_http: httpx.AsyncClient, client: OpenAILLMClient) -> None:
        with pytest.raises(LLMClientError, match="JSON"):
            await client.ask("context", "question")

    _run(_with_mock(handler, action))


def test_client_handles_missing_choices() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    async def action(_http: httpx.AsyncClient, client: OpenAILLMClient) -> None:
        with pytest.raises(LLMClientError, match="формат"):
            await client.ask("context", "question")

    _run(_with_mock(handler, action))


def test_client_rejects_empty_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "   "}}]})

    async def action(_http: httpx.AsyncClient, client: OpenAILLMClient) -> None:
        with pytest.raises(LLMClientError, match="Пустой"):
            await client.ask("context", "question")

    _run(_with_mock(handler, action))


def test_client_rejects_unconfigured() -> None:
    client = OpenAILLMClient(
        base_url="",
        api_key=SecretStr(""),
        model="",
        timeout_seconds=5.0,
    )
    with pytest.raises(LLMClientError, match="не настроен"):
        _run(client.ask("context", "question"))


@pytest.mark.parametrize(
    "status, hint",
    [(400, "совместимость"), (404, "BB_LLM_BASE_URL"), (401, "API-ключ"), (403, "права"), (429, "лимит")],
)
def test_provider_diagnostics_hide_response_secrets(status: int, hint: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://llm.test/chat/completions"
        return httpx.Response(status, text="private-provider-body-secret")

    async def action(_http: httpx.AsyncClient, client: OpenAILLMClient) -> None:
        with pytest.raises(LLMClientError, match=hint) as error:
            await client.ask("context", "question")
        assert "private-provider-body" not in str(error.value)

    _run(_with_mock(handler, action))


def test_network_error_hides_request_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret-key-in-network-exception", request=request)

    async def action(_http: httpx.AsyncClient, client: OpenAILLMClient) -> None:
        with pytest.raises(LLMClientError, match="Сетевая ошибка") as error:
            await client.ask("context", "question")
        assert "secret-key" not in str(error.value)

    _run(_with_mock(handler, action))
