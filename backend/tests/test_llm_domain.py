"""Domain and use-case tests without FastAPI, network, or real settings."""

import asyncio

import pytest

from backend.domain.llm import DEFAULT_SUMMARY_QUESTION, build_context
from backend.schemas.llm import AssistantSelection
from backend.use_cases.llm import (
    AskTransportAssistant,
    LLMContextInvalidError,
    LLMContextNotAvailableError,
    LLMDataNotFoundError,
    LLMUpstreamError,
    LLMValidationError,
)


class FakeRepository:
    def __init__(self, data=None) -> None:
        self._data = data
        self.calls = []

    def get(self, direction, end_year):
        self.calls.append((direction, end_year))
        if self._data is None:
            raise FileNotFoundError("missing")
        return self._data


class FakeGateway:
    def __init__(self, answers: dict[str, str] | None = None) -> None:
        self._answers = answers or {}
        self.calls: list[tuple[str, str]] = []

    async def ask(self, context: str, question: str) -> str:
        self.calls.append((context, question))
        if "fail" in question.lower():
            raise LLMUpstreamError("provider down")
        return self._answers.get(question, f"answer: {question}")


def _run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def test_null_value_is_not_replaced_with_zero() -> None:
    files = {"a": {"объём": {"значение": None, "единица": "т", "тип": "фактические"}}}
    ctx = build_context(files)
    assert "нет данных" in ctx
    assert " 0 " not in ctx


def test_broken_file_is_reported() -> None:
    ctx = build_context({"bad": {"__error__": "boom"}})
    assert "не удалось прочитать" in ctx
    assert "boom" in ctx


def test_filter_by_names() -> None:
    files = {
        "a": {"x": {"значение": 1, "единица": "т", "тип": "факт"}},
        "b": {"y": {"значение": 2, "единица": "т", "тип": "факт"}},
    }
    ctx = build_context(files, ["a"])
    assert "Файл a" in ctx and "Файл b" not in ctx


def test_summary_uses_default_question(forecast_context) -> None:
    gateway = FakeGateway()
    use_case = AskTransportAssistant(FakeRepository(forecast_context), gateway)
    _run(use_case.summary())
    assert len(gateway.calls) == 1
    assert gateway.calls[0][1] == DEFAULT_SUMMARY_QUESTION


def test_ask_strips_question_and_returns_answer(forecast_context) -> None:
    gateway = FakeGateway({"hello": "hi there"})
    use_case = AskTransportAssistant(FakeRepository(forecast_context), gateway)
    answer = _run(use_case.ask("  hello  "))
    assert answer.answer == "hi there"
    assert answer.context == forecast_context
    assert answer.result_type == "interpretation"


def test_empty_question_raises_validation_error() -> None:
    use_case = AskTransportAssistant(FakeRepository(), FakeGateway())
    with pytest.raises(LLMValidationError, match="Пустой"):
        _run(use_case.ask("   "))


def test_long_question_raises_validation_error() -> None:
    use_case = AskTransportAssistant(FakeRepository(), FakeGateway())
    with pytest.raises(LLMValidationError, match="длиннее"):
        _run(use_case.ask("x" * 4001))


def test_missing_data_raises_data_not_found() -> None:
    use_case = AskTransportAssistant(FakeRepository(), FakeGateway())
    with pytest.raises(LLMDataNotFoundError):
        _run(use_case.summary())


def test_upstream_error_is_propagated(forecast_context) -> None:
    gateway = FakeGateway()
    use_case = AskTransportAssistant(FakeRepository(forecast_context), gateway)
    with pytest.raises(LLMUpstreamError, match="provider down"):
        _run(use_case.ask("fail"))


@pytest.mark.parametrize(
    "selection",
    [
        {"region": "primorye"},
        {"region": "kamchatka"},
        {"mode": "road"},
        {"study_city": "manual:Калининград"},
        {"other_region": "78"},
        {"other_city": "manual:Москва"},
    ],
)
def test_unsupported_selection_never_loads_another_forecast_or_calls_llm(forecast_context, selection):
    repository, gateway = FakeRepository(forecast_context), FakeGateway()
    use_case = AskTransportAssistant(repository, gateway)
    with pytest.raises(LLMContextNotAvailableError):
        _run(use_case.summary(AssistantSelection(**selection)))
    assert not repository.calls
    assert not gateway.calls


def test_context_is_same_snapshot_with_forecast_status_and_provenance(forecast_context):
    import json

    gateway = FakeGateway()
    use_case = AskTransportAssistant(FakeRepository(forecast_context), gateway)
    answer = _run(use_case.summary())
    snapshot = json.loads(gateway.calls[0][0].split("\n", 1)[1])
    assert snapshot == answer.context.model_dump(mode="json")
    assert snapshot["result_type"] == "forecast"
    assert snapshot["forecast_origin"] == 2023
    assert snapshot["provenance"]["source_ids"] == ["test-source"]
    assert "1200000" not in gateway.calls[0][0]
    assert "1500000" not in gateway.calls[0][0]


def test_wrong_direction_is_rejected_before_llm(forecast_context):
    gateway = FakeGateway()
    use_case = AskTransportAssistant(FakeRepository(forecast_context), gateway)
    with pytest.raises(LLMContextInvalidError):
        _run(use_case.summary(AssistantSelection(direction="outbound")))
    assert not gateway.calls
