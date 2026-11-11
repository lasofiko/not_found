"""Adapters: implement use-case ports via infrastructure."""

from backend.infrastructure.llm_client import LLMClientError, OpenAILLMClient
from backend.use_cases.llm import (
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
