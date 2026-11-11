"""Domain and use-case tests without FastAPI, network, or real settings."""

import asyncio

import pytest

from backend.domain.llm import DEFAULT_SUMMARY_QUESTION, build_context
from backend.use_cases.llm import (
    AskTransportAssistant,
    LLMDataNotFoundError,
    LLMUpstreamError,
    LLMValidationError,
)


class FakeRepository:
    def __init__(self, data: dict[str, dict]) -> None:
        self._data = data

    def load(self) -> dict[str, dict]:
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


def test_summary_uses_default_question() -> None:
    gateway = FakeGateway()
    use_case = AskTransportAssistant(FakeRepository({"a": {"x": {"значение": 1}}}), gateway)
    _run(use_case.summary())
    assert len(gateway.calls) == 1
    assert gateway.calls[0][1] == DEFAULT_SUMMARY_QUESTION


def test_ask_strips_question_and_returns_answer() -> None:
    gateway = FakeGateway({"hello": "hi there"})
    use_case = AskTransportAssistant(FakeRepository({"a": {"x": {"значение": 1}}}), gateway)
    answer = _run(use_case.ask("  hello  "))
    assert answer == "hi there"


def test_empty_question_raises_validation_error() -> None:
    use_case = AskTransportAssistant(FakeRepository({"a": {}}), FakeGateway())
    with pytest.raises(LLMValidationError, match="Пустой"):
        _run(use_case.ask("   "))


def test_long_question_raises_validation_error() -> None:
    use_case = AskTransportAssistant(FakeRepository({"a": {}}), FakeGateway())
    with pytest.raises(LLMValidationError, match="длиннее"):
        _run(use_case.ask("x" * 4001))


def test_missing_data_raises_data_not_found() -> None:
    use_case = AskTransportAssistant(FakeRepository({}), FakeGateway())
    with pytest.raises(LLMDataNotFoundError):
        _run(use_case.summary())


def test_upstream_error_is_propagated() -> None:
    gateway = FakeGateway()
    use_case = AskTransportAssistant(FakeRepository({"a": {}}), gateway)
    with pytest.raises(LLMUpstreamError, match="provider down"):
        _run(use_case.ask("fail"))
