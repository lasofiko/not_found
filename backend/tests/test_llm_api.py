"""HTTP contract tests for the transport assistant endpoints."""

import json
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.settings import Settings
from backend.use_cases.llm import (
    AskTransportAssistant,
    LLMDataNotFoundError,
    LLMNotConfiguredError,
    LLMUpstreamError,
)


def _client_with_llm(use_case: AskTransportAssistant) -> TestClient:
    settings = Settings(_env_file=None, environment="test", cors_origins=["http://localhost:5173"])
    app = create_app(settings)
    app.state.llm_use_case = use_case
    return TestClient(app)


def _configured_client(answer: str = "mocked answer") -> TestClient:
    use_case = AskTransportAssistant.__new__(AskTransportAssistant)
    use_case.summary = AsyncMock(return_value=answer)
    use_case.ask = AsyncMock(return_value=answer)
    return _client_with_llm(use_case)


def test_summary_returns_answer() -> None:
    client = _configured_client("автоматическая сводка")
    response = client.get("/api/v1/llm/summary")
    assert response.status_code == 200
    assert response.json() == {"answer": "автоматическая сводка"}


def test_ask_returns_answer_and_validates() -> None:
    client = _configured_client("ответ")
    response = client.post("/api/v1/llm/ask", json={"question": "  Какой объём?  "})
    assert response.status_code == 200
    assert response.json() == {"answer": "ответ"}


def test_empty_question_returns_422() -> None:
    client = _configured_client()
    response = client.post("/api/v1/llm/ask", json={"question": "   "})
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"


def test_long_question_returns_422() -> None:
    client = _configured_client()
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


def test_openapi_lists_llm_routes() -> None:
    client = _configured_client()
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
