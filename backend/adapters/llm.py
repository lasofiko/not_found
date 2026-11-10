"""Adapters: implement use-case ports via infrastructure."""

from backend.infrastructure.json_loader import JSONLoader, JSONLoaderError
from backend.infrastructure.llm_client import LLMClientError, OpenAILLMClient
from backend.use_cases.llm import (
    AnalysisDataRepository,
    LLMDataNotFoundError,
    LLMGateway,
    LLMUpstreamError,
)


class LLMGatewayAdapter(LLMGateway):
    """Translate infrastructure LLM failures into application errors."""

    def __init__(self, client: OpenAILLMClient) -> None:
        self._client = client

    async def ask(self, context: str, question: str) -> str:
        try:
            return await self._client.ask(context, question)
        except LLMClientError as exc:
            raise LLMUpstreamError(str(exc)) from exc


class AnalysisDataRepositoryAdapter(AnalysisDataRepository):
    """Wrap JSON loader so the use case receives the expected mapping."""

    def __init__(self, loader: JSONLoader) -> None:
        self._loader = loader

    def load(self) -> dict[str, dict]:
        try:
            data = self._loader.load()
        except JSONLoaderError as exc:
            raise LLMDataNotFoundError(str(exc)) from exc
        if not data:
            raise LLMDataNotFoundError("Нет данных для анализа.")
        return data
