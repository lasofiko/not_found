"""OpenAI-compatible async LLM client. Settings are injected, not read from env."""

import httpx
from pydantic import SecretStr


class LLMClientError(Exception):
    """Infrastructure-level failure when talking to the LLM provider."""


class OpenAILLMClient:
    """Call ``{base_url}/chat/completions`` asynchronously.

    The constructor receives all provider settings; the client never reads
    ``.env`` or global configuration.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr,
        model: str,
        timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client

    async def ask(self, context: str, question: str) -> str:
        if not self._base_url or not self._api_key.get_secret_value() or not self._model:
            raise LLMClientError("LLM-провайдер не настроен")

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": f"Данные:\n{context}\n\nВопрос: {question}"},
            ],
        }

        async def _post(client: httpx.AsyncClient) -> str:
            try:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
                    json=payload,
                )
            except httpx.TimeoutException as exc:
                raise LLMClientError("Таймаут при обращении к модели") from exc
            except httpx.HTTPError as exc:
                raise LLMClientError(f"Сетевая ошибка: {exc}") from exc

            if response.status_code != 200:
                raise LLMClientError(f"Ошибка модели: HTTP {response.status_code}")

            try:
                data = response.json()
            except ValueError as exc:
                raise LLMClientError("Некорректный JSON в ответе модели") from exc

            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise LLMClientError("Неожиданный формат ответа модели") from exc

            if not isinstance(content, str) or not content.strip():
                raise LLMClientError("Пустой ответ модели")

            return content.strip()

        if self._http_client is not None:
            return await _post(self._http_client)

        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            return await _post(client)

    @property
    def _system_prompt(self) -> str:
        from backend.domain.llm import SYSTEM_PROMPT

        return SYSTEM_PROMPT
