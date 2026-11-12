"""Application use case: ask transport assistant via ports."""

import hashlib
import time
from collections import OrderedDict
from typing import Protocol

from backend.domain.llm import DEFAULT_SUMMARY_QUESTION, SYSTEM_PROMPT, build_forecast_context
from backend.schemas.forecast import ForecastResponse
from backend.schemas.llm import AnswerResponse, AssistantSelection


class LLMValidationError(ValueError):
    """Invalid user input for the assistant."""


class LLMDataNotFoundError(Exception):
    """No verified forecast is available."""


class LLMContextNotAvailableError(Exception):
    """The requested slice is not connected."""


class LLMContextInvalidError(Exception):
    """The forecast artifact could not be validated."""


class LLMNotConfiguredError(Exception):
    """LLM provider settings are missing."""


class LLMUpstreamError(Exception):
    """LLM provider failed or is unreachable."""


class LLMGateway(Protocol):
    """Outbound port to an LLM provider."""

    async def ask(self, context: str, question: str, *, system_prompt: str | None = None) -> str:
        """Return the model answer for the given context and question."""
        ...


class ForecastRepository(Protocol):
    """Outbound port to the existing validated annual forecast."""

    def get(self, direction: str, end_year: int) -> ForecastResponse: ...


class AskTransportAssistant:
    """Orchestrate loading context, building prompt, and calling the model."""

    def __init__(
        self,
        data_repository: ForecastRepository,
        llm_gateway: LLMGateway,
        *,
        summary_cache_ttl: float = 60,
    ) -> None:
        self._data_repository = data_repository
        self._llm_gateway = llm_gateway
        self._summary_cache_ttl = summary_cache_ttl
        self._summary_cache: OrderedDict[str, tuple[float, AnswerResponse]] = OrderedDict()

    async def summary(self, selection: AssistantSelection | None = None) -> AnswerResponse:
        """Generate the automatic summary for available data."""
        return await self._ask(DEFAULT_SUMMARY_QUESTION, selection or AssistantSelection(), cache=True)

    async def ask(self, question: str, selection: AssistantSelection | None = None) -> AnswerResponse:
        """Answer a user question about available analytical data."""
        cleaned = question.strip()
        if not cleaned:
            raise LLMValidationError("Пустой вопрос.")
        if len(cleaned) > 4000:
            raise LLMValidationError("Вопрос не может быть длиннее 4000 символов.")
        return await self._ask(cleaned, selection or AssistantSelection())

    async def _ask(self, question: str, selection: AssistantSelection, *, cache: bool = False) -> AnswerResponse:
        if (selection.region, selection.mode, selection.study_city, selection.other_region, selection.other_city) != (
            "kaliningrad",
            "sea",
            "all",
            "all",
            "all",
        ):
            raise LLMContextNotAvailableError("Для выбранной географии, вида или детализации контекст не подключён.")
        try:
            forecast = self._data_repository.get(selection.direction, selection.end_year)
            if forecast.direction != selection.direction or [p.period for p in forecast.predictions] != [
                str(year) for year in range(2024, selection.end_year + 1)
            ]:
                raise ValueError("Контекст не соответствует запрошенному срезу")
        except FileNotFoundError as exc:
            raise LLMDataNotFoundError("Проверенная модель линии отсутствует. Подключите комплект models.") from exc
        except (ValueError, OSError, KeyError, TypeError, ArithmeticError) as exc:
            raise LLMContextInvalidError("Не удалось проверить модель и происхождение контекста ассистента.") from exc
        context = build_forecast_context(forecast.model_dump(mode="json"))
        key = hashlib.sha256(context.encode("utf-8")).hexdigest()
        if cache:
            now = time.monotonic()
            for stale in [k for k, (expires, _) in self._summary_cache.items() if expires <= now]:
                del self._summary_cache[stale]
            if key in self._summary_cache:
                return self._summary_cache[key][1].model_copy(deep=True)
        answer = await self._llm_gateway.ask(context, question)
        response = AnswerResponse(answer=answer, context=forecast)
        if cache and self._summary_cache_ttl > 0:
            self._summary_cache[key] = (time.monotonic() + self._summary_cache_ttl, response.model_copy(deep=True))
            while len(self._summary_cache) > 32:
                self._summary_cache.popitem(last=False)
        return response

    @property
    def system_prompt(self) -> str:
        """Expose the system prompt for documentation/tests."""
        return SYSTEM_PROMPT
