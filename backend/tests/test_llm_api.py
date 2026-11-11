"""HTTP contract tests for the transport assistant endpoints."""

import importlib
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.settings import Settings
from backend.schemas.llm import AnswerResponse
from backend.use_cases.llm import (
    AskTransportAssistant,
    LLMDataNotFoundError,
    LLMNotConfiguredError,
    LLMUpstreamError,
)


@pytest.fixture
def assembled_assistant(forecast_context, monkeypatch):
    """Exercise the production composition root with a local forecast and fake provider."""
    app_module = importlib.import_module("backend.app")

    class Repository:
        def __init__(self):
            self.calls = []

        def get(self, direction, end_year):
            self.calls.append((direction, end_year))
            return forecast_context.model_copy(
                update={
                    "direction": direction,
                    "predictions": forecast_context.predictions[: end_year - 2023],
                }
            )

    repository = Repository()
    gateway = AsyncMock(return_value="Интерпретация тестового прогноза")
    monkeypatch.setattr(app_module, "ForecastService", lambda: repository)
    monkeypatch.setattr(app_module.OpenAILLMClient, "ask", gateway)
    settings = Settings(
        _env_file=None,
        environment="test",
        llm_base_url="https://example.org",
        llm_api_key="test-key",
        llm_model="test-model",
    )
    return TestClient(create_app(settings)), repository, gateway


@pytest.mark.parametrize("direction", ["inbound", "outbound"])
def test_production_context_uses_forecast_service_and_returned_snapshot(assembled_assistant, direction):
    client, repository, gateway = assembled_assistant
    response = client.get("/api/v1/llm/summary", params={"direction": direction, "end_year": 2025})
    assert response.status_code == 200
    context = response.json()["context"]
    assert repository.calls == [(direction, 2025)]
    assert context["direction"] == direction
    assert [p["period"] for p in context["predictions"]] == ["2024", "2025"]
    provider_context = json.loads(gateway.call_args.args[0].split("\n", 1)[1])
    assert provider_context == context
    assert provider_context["result_type"] == "forecast"
    assert provider_context["data_version"] == "test-data"
    assert "scenario_mock" not in gateway.call_args.args[0]


@pytest.mark.parametrize(
    "selection",
    [
        {"region": "primorye"},
        {"region": "kamchatka"},
        {"study_city": "test-city"},
        {"other_region": "78"},
        {"other_city": "test-city"},
        {"mode": "rail_land"},
    ],
)
def test_unsupported_context_is_rejected_in_both_endpoints(assembled_assistant, selection):
    client, repository, gateway = assembled_assistant
    for response in (
        client.get("/api/v1/llm/summary", params=selection),
        client.post("/api/v1/llm/ask", json={"question": "Каков прогноз?", **selection}),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "LLM_CONTEXT_NOT_AVAILABLE"
    assert not repository.calls
    gateway.assert_not_called()


@pytest.mark.parametrize(
    "error, status, code",
    [
        (FileNotFoundError("private file"), 404, "LLM_DATA_NOT_FOUND"),
        (ValueError("private details"), 500, "LLM_CONTEXT_INVALID"),
    ],
)
def test_context_failure_never_calls_provider_or_exposes_details(assembled_assistant, error, status, code):
    client, repository, gateway = assembled_assistant
    repository.get = lambda *_: (_ for _ in ()).throw(error)
    response = client.get("/api/v1/llm/summary")
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "private" not in response.text
    gateway.assert_not_called()


def test_selection_validation_and_ask_direction(assembled_assistant):
    client, repository, gateway = assembled_assistant
    assert client.get("/api/v1/llm/summary?end_year=2031").status_code == 422
    assert client.post("/api/v1/llm/ask", json={"question": "q", "direction": "both"}).status_code == 422
    assert not repository.calls
    response = client.post("/api/v1/llm/ask", json={"question": "  Каков прогноз?  ", "direction": "outbound"})
    assert response.status_code == 200
    assert repository.calls == [("outbound", 2030)]
    assert gateway.call_args.args[1] == "Каков прогноз?"


def _client_with_llm(use_case: AskTransportAssistant) -> TestClient:
    settings = Settings(_env_file=None, environment="test", cors_origins=["http://localhost:5173"])
    app = create_app(settings)
    app.state.llm_use_case = use_case
    return TestClient(app)


def _configured_client(context, answer: str = "mocked answer") -> TestClient:
    use_case = AskTransportAssistant.__new__(AskTransportAssistant)
    use_case.summary = AsyncMock(return_value=AnswerResponse(answer=answer, context=context))
    use_case.ask = AsyncMock(return_value=AnswerResponse(answer=answer, context=context))
    return _client_with_llm(use_case)


def test_summary_returns_answer(forecast_context) -> None:
    client = _configured_client(forecast_context, "автоматическая сводка")
    response = client.get("/api/v1/llm/summary")
    assert response.status_code == 200
    assert response.json() == AnswerResponse(answer="автоматическая сводка", context=forecast_context).model_dump()


def test_ask_returns_answer_and_validates(forecast_context) -> None:
    client = _configured_client(forecast_context, "ответ")
    response = client.post("/api/v1/llm/ask", json={"question": "  Какой объём?  "})
    assert response.status_code == 200
    assert response.json() == AnswerResponse(answer="ответ", context=forecast_context).model_dump()


def test_empty_question_returns_422(forecast_context) -> None:
    client = _configured_client(forecast_context)
    response = client.post("/api/v1/llm/ask", json={"question": "   "})
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"


def test_long_question_returns_422(forecast_context) -> None:
    client = _configured_client(forecast_context)
    response = client.post("/api/v1/llm/ask", json={"question": "x" * 4001})
    assert response.status_code == 422


def test_data_not_found_returns_404() -> None:
    use_case = AskTransportAssistant.__new__(AskTransportAssistant)
    use_case.summary = AsyncMock(side_effect=LLMDataNotFoundError("Нет данных"))
    use_case.ask = AsyncMock(side_effect=LLMDataNotFoundError("Нет данных"))
    client = _client_with_llm(use_case)

    summary = client.get("/api/v1/llm/summary")
    assert summary.status_code == 404
    assert summary.json()["error"]["code"] == "LLM_DATA_NOT_FOUND"

    ask = client.post("/api/v1/llm/ask", json={"question": "test"})
    assert ask.status_code == 404


def test_not_configured_returns_503() -> None:
    settings = Settings(_env_file=None, environment="test")
    client = TestClient(create_app(settings))

    summary = client.get("/api/v1/llm/summary")
    assert summary.status_code == 503
    assert summary.json()["error"]["code"] == "LLM_NOT_CONFIGURED"

    ask = client.post("/api/v1/llm/ask", json={"question": "test"})
    assert ask.status_code == 503


def test_upstream_error_returns_502() -> None:
    use_case = AskTransportAssistant.__new__(AskTransportAssistant)
    use_case.summary = AsyncMock(side_effect=LLMUpstreamError("provider down"))
    use_case.ask = AsyncMock(side_effect=LLMUpstreamError("provider down"))
    client = _client_with_llm(use_case)

    response = client.get("/api/v1/llm/summary")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LLM_UPSTREAM_ERROR"


def test_not_configured_error_class_is_mapped() -> None:
    """A use case raising LLMNotConfiguredError is also mapped to 503."""
    use_case = AskTransportAssistant.__new__(AskTransportAssistant)
    use_case.summary = AsyncMock(side_effect=LLMNotConfiguredError("no key"))
    use_case.ask = AsyncMock(side_effect=LLMNotConfiguredError("no key"))
    client = _client_with_llm(use_case)

    response = client.get("/api/v1/llm/summary")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_NOT_CONFIGURED"


def test_openapi_lists_llm_routes(forecast_context) -> None:
    client = _configured_client(forecast_context)
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/llm/summary" in paths
    assert "/api/v1/llm/ask" in paths


def test_json_read_error_is_reported_in_context(tmp_path) -> None:
    """A malformed JSON file must appear as an error for that file, not crash."""
    context_dir = tmp_path / "jsons"
    context_dir.mkdir()
    (context_dir / "good.json").write_text(json.dumps({"x": {"значение": 1}}), encoding="utf-8")
    (context_dir / "bad.json").write_text("not json", encoding="utf-8")

    from backend.infrastructure.json_loader import JSONLoader

    loader = JSONLoader(context_dir)
    data = loader.load()
    assert "good" in data
    assert "bad" in data
    assert "__error__" in data["bad"]

    from backend.domain.llm import build_context

    text = build_context(data)
    assert "Файл bad: не удалось прочитать" in text


def test_json_wrong_shape_is_reported_in_context(tmp_path) -> None:
    context_dir = tmp_path / "jsons"
    context_dir.mkdir()
    (context_dir / "list.json").write_text("[]", encoding="utf-8")
    (context_dir / "scalar.json").write_text(json.dumps({"metric": 1}), encoding="utf-8")

    from backend.domain.llm import build_context
    from backend.infrastructure.json_loader import JSONLoader

    data = JSONLoader(context_dir).load()
    assert "__error__" in data["list"]
    assert "__error__" in data["scalar"]
    text = build_context(data)
    assert "Файл list: не удалось прочитать" in text
    assert "Файл scalar: не удалось прочитать" in text
