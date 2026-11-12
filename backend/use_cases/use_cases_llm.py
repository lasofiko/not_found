"""Application use case: ask transport assistant via ports."""

from typing import Protocol

from backend.domain.llm import DEFAULT_SUMMARY_QUESTION, SYSTEM_PROMPT, build_context


class LLMValidationError(ValueError):
    """Invalid user input for the assistant."""


class LLMDataNotFoundError(Exception):
    """No analysis context files are available."""


class LLMNotConfiguredError(Exception):
    """LLM provider settings are missing."""


class LLMUpstreamError(Exception):
    """LLM provider failed or is unreachable."""


class LLMGateway(Protocol):
    """Outbound port to an LLM provider."""

    async def ask(self, context: str, question: str) -> str:
        """Return the model answer for the given context and question."""
        ...


class AnalysisDataRepository(Protocol):
    """Outbound port to analytical JSON context."""

    def load(self) -> dict[str, dict]:
        """Load available analysis files.

        Returns a mapping {file_stem: content_or_error_marker}. Broken files
        must be represented with a ``__error__`` key, not raised.
        """
        ...


class AskTransportAssistant:
    """Orchestrate loading context, building prompt, and calling the model."""

    def __init__(
        self,
        data_repository: AnalysisDataRepository,
        llm_gateway: LLMGateway,
    ) -> None:
        self._data_repository = data_repository
        self._llm_gateway = llm_gateway

    async def summary(self) -> str:
        """Generate the automatic summary for available data."""
        return await self._ask(DEFAULT_SUMMARY_QUESTION)

    async def ask(self, question: str) -> str:
        """Answer a user question about available analytical data."""
        cleaned = question.strip()
        if not cleaned:
            raise LLMValidationError("Пустой вопрос.")
        if len(cleaned) > 4000:
            raise LLMValidationError("Вопрос не может быть длиннее 4000 символов.")
        return await self._ask(cleaned)

    async def _ask(self, question: str) -> str:
        files = self._data_repository.load()
        if not files:
            raise LLMDataNotFoundError("Нет данных для анализа.")
        context = build_context(files)
        return await self._llm_gateway.ask(context, question)

    @property
    def system_prompt(self) -> str:
        """Expose the system prompt for documentation/tests."""
        return SYSTEM_PROMPT
