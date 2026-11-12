"""Assistant guards use only local repositories and fake providers."""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.app import create_app
from backend.core.settings import Settings
from backend.infrastructure.llm_client import LLMClientError, OpenAILLMClient
from backend.use_cases.llm import AskTransportAssistant


def app_with_provider(context, **overrides):
    settings = Settings(_env_file=None, environment="test", **overrides)
    app = create_app(settings)
    repository = type("Repository", (), {"get": lambda self, *_: context})()
    gateway = AsyncMock()
    gateway.ask.return_value = "Тестовая интерпретация"
    app.state.llm_use_case = AskTransportAssistant(repository, gateway)
    return app, gateway


def test_local_mode_remote_access_and_token(forecast_context):
    app, gateway = app_with_provider(forecast_context)
    remote = TestClient(app, client=("198.51.100.1", 5000))
    assert remote.get("/api/v1/llm/summary").status_code == 403
    gateway.ask.assert_not_called()
    assert TestClient(app, client=("127.0.0.1", 5000)).get("/api/v1/llm/summary").status_code == 200
    secured, gateway = app_with_provider(forecast_context, llm_access_token="test-access", llm_allow_remote=True)
    remote = TestClient(secured, client=("198.51.100.1", 5000))
    assert remote.get("/api/v1/llm/summary").json()["error"]["code"] == "LLM_ACCESS_DENIED"
    assert remote.get("/api/v1/llm/summary", headers={"Authorization": "Bearer test-access"}).status_code == 200
    assert "test-access" not in remote.get("/openapi.json").text


def test_rate_limit_covers_cache_and_both_endpoints(forecast_context):
    app, gateway = app_with_provider(forecast_context, llm_requests_per_minute=2)
    client = TestClient(app)
    assert client.get("/api/v1/llm/summary").status_code == 200
    assert client.get("/api/v1/llm/summary").status_code == 200
    assert gateway.ask.call_count == 1
    limited = client.post("/api/v1/llm/ask", json={"question": "Тест"})
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "LLM_RATE_LIMITED"
    assert int(limited.headers["retry-after"]) > 0


def test_concurrency_limit_releases_after_failure(forecast_context):
    async def run():
        app, gateway = app_with_provider(forecast_context, llm_max_concurrent_requests=1)
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow(*_):
            entered.set()
            await release.wait()
            return "Тест"

        gateway.ask.side_effect = slow
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            first = asyncio.create_task(client.get("/api/v1/llm/summary"))
            await entered.wait()
            limited = await client.post("/api/v1/llm/ask", json={"question": "Тест"})
            assert limited.status_code == 429
            assert limited.json()["error"]["code"] == "LLM_BUSY"
            release.set()
            assert (await first).status_code == 200
            assert app.state.llm_policy.active == 0
            assert (await client.post("/api/v1/llm/ask", json={"question": "Тест"})).status_code == 200

    asyncio.run(run())


def test_summary_cache_changes_with_model_and_expires(forecast_context, monkeypatch):
    from backend.use_cases import llm

    clock = [10.0]
    monkeypatch.setattr(llm.time, "monotonic", lambda: clock[0])
    context = [forecast_context]
    repository = type("Repository", (), {"get": lambda self, *_: context[0]})()
    gateway = AsyncMock()
    gateway.ask.return_value = "Тест"
    use_case = AskTransportAssistant(repository, gateway, summary_cache_ttl=5)

    async def run():
        await use_case.summary()
        await use_case.summary()
        assert gateway.ask.call_count == 1
        context[0] = forecast_context.model_copy(update={"model_version": "different-test-model"})
        await use_case.summary()
        assert gateway.ask.call_count == 2
        clock[0] += 6
        await use_case.summary()
        assert gateway.ask.call_count == 3

    asyncio.run(run())


def test_provider_receives_output_limit_and_long_response_is_rejected():
    async def run():
        def respond(request):
            import json

            assert json.loads(request.content)["max_tokens"] == 7
            return httpx.Response(200, json={"choices": [{"message": {"content": "x" * 21}}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client = OpenAILLMClient(
                base_url="https://provider.test",
                api_key=SecretStr("test-key"),
                model="test-model",
                timeout_seconds=2,
                max_tokens=7,
                max_answer_chars=20,
                http_client=http,
            )
            with pytest.raises(LLMClientError, match="длину"):
                await client.ask("synthetic", "question")

    asyncio.run(run())


def test_offline_guard_rejects_real_network():
    import socket

    with pytest.raises(AssertionError, match="DNS"):
        socket.getaddrinfo("provider.example", 443)
    with socket.socket() as stream, pytest.raises(AssertionError, match="сеть"):
        stream.connect(("203.0.113.1", 443))
    with socket.socket(type=socket.SOCK_DGRAM) as stream, pytest.raises(AssertionError, match="сеть"):
        stream.sendto(b"test-only", ("203.0.113.1", 53))
    with httpx.Client() as client, pytest.raises(AssertionError, match="transport"):
        client.get("http://127.0.0.1:1/test-only")


def test_development_accepts_docker_gateway_without_a_token(forecast_context):
    app, gateway = app_with_provider(forecast_context)
    app.state.settings.environment = "development"
    client = TestClient(app, client=("172.24.0.1", 5000))
    assert client.get("/api/v1/llm/summary").status_code == 200
    assert gateway.ask.call_count == 1


def test_production_requires_token_even_behind_loopback_proxy(forecast_context):
    app, gateway = app_with_provider(forecast_context)
    app.state.settings.environment = "production"
    client = TestClient(app, client=("127.0.0.1", 5000))
    assert client.get("/api/v1/llm/summary").status_code == 403
    gateway.ask.assert_not_called()


def test_production_without_provider_preserves_optional_service_contract():
    app = create_app(Settings(_env_file=None, environment="production"))
    client = TestClient(app, client=("127.0.0.1", 5000))
    for response in (
        client.get("/api/v1/llm/summary"),
        client.post("/api/v1/llm/ask", json={"question": "Тест"}),
    ):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "LLM_NOT_CONFIGURED"
    assert client.get("/api/v1/health").status_code == 200
