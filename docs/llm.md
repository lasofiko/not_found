# Транспортный ассистент (LLM)

## Назначение

Серверный ассистент для интерпретации аналитического JSON-контекста проекта
FloCast. Frontend обращается к основному backend API, а backend уже вызывает
OpenAI-compatible LLM-провайдера. Ключ и URL недоступны в браузере.

Ассистент не заменяет и не смешивает статусы `observed`, `estimated`,
`forecast`, `scenario` и `synthetic`. Его ответ — интерпретация переданного
контекста, а не новое наблюдение или прогноз.

## Архитектура

```
frontend/src/App.tsx
  → /config.json (apiBase)
  → GET/POST /api/v1/llm/*
    → backend/api/v1/llm.py
      → AskTransportAssistant (backend/use_cases/llm.py)
        → AnalysisDataRepository port
          → AnalysisDataRepositoryAdapter (backend/adapters/llm.py)
            → JSONLoader (backend/infrastructure/json_loader.py)
              → backend/jsons/*.json
        → LLMGateway port
          → LLMGatewayAdapter (backend/adapters/llm.py)
            → OpenAILLMClient (backend/infrastructure/llm_client.py)
              → {BB_LLM_BASE_URL}/chat/completions
```

- `backend/domain/llm.py` — чистые функции построения контекста и system prompt.
- `backend/use_cases/llm.py` — оркестрация, порты и ошибки уровня приложения.
- `backend/adapters/llm.py` — связь портов с инфраструктурой, перевод ошибок.
- `backend/infrastructure/*` — JSON-загрузчик и OpenAI-compatible HTTP-клиент.
- `backend/app.py` — composition root, собирает use case и кладёт в
  `app.state.llm_use_case`. Если настройки LLM не заданы, use case не
  создаётся, а эндпоинты возвращают `LLM_NOT_CONFIGURED`.

## Контекст

Контекстные файлы кладутся в `backend/jsons/`. Каждый `*.json` читается как
объект верхнего уровня. Значения показателей оформляются объектом:

```json
{
  "объём грузов за 2025 год": {
    "значение": 1200000,
    "единица": "т",
    "тип": "фактические"
  }
}
```

- `значение`: число или `null`. `null` показывается как «нет данных».
- `единица`: строка, отображается рядом со значением.
- `тип`: строка, отображается в скобках.

Битые JSON-файлы не останавливают обработку: они попадают в контекст с
пометкой об ошибке.

## Настройка

Добавьте в `.env` (скопируйте из `.env.example`):

```bash
BB_LLM_BASE_URL=https://llm.api.cloud.yandex.net/v1
BB_LLM_API_KEY=your-secret-key
BB_LLM_MODEL=your-model-name
BB_LLM_TIMEOUT_SECONDS=60
```

`BB_LLM_API_KEY` читается как `SecretStr` и не попадает в логи/ответы.
Оставьте любое из трёх полей пустым, чтобы отключить ассистента — остальной
backend продолжит работать.

## API

- `GET /api/v1/llm/summary` — автоматическая сводка по контексту.
- `POST /api/v1/llm/ask` — вопрос пользователя. Тело: `{"question":"..."}`.

Ответ обоих эндпоинтов: `{"answer":"..."}`.

Коды ошибок:

| Статус | Код | Когда |
|---|---|---|
| 422 | `VALIDATION_ERROR` | пустой или > 4000 символов вопрос |
| 404 | `LLM_DATA_NOT_FOUND` | нет JSON-файлов в `backend/jsons/` |
| 503 | `LLM_NOT_CONFIGURED` | не заданы `BB_LLM_BASE_URL/API_KEY/MODEL` |
| 502 | `LLM_UPSTREAM_ERROR` | ошибка или недоступность LLM-провайдера |

## Frontend

React-интерфейс (`frontend/src/App.tsx`) использует `/config.json` для
получения `apiBase` и обращается к `/api/v1/llm/summary` и
`/api/v1/llm/ask`. Панель ассистента показывает историю текущего диалога,
индикатор загрузки, ошибки backend и восстанавливает текст вопроса при
неуспешной отправке. Внешний чат-виджет удалён.

## Тестирование

Backend-тесты используют fake repository/gateway и локальный mock
OpenAI-compatible server через `httpx.MockTransport`. Реальный LLM-провайдер
в тестах не вызывается.

```bash
python -m pytest backend/tests/test_llm_domain.py backend/tests/test_llm_api.py backend/tests/test_llm_client.py -q
```
