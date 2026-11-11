# Транспортный ассистент (LLM)

## Назначение

Серверный ассистент для интерпретации проверенного годового прогноза проекта
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
        → ForecastRepository port
          → ForecastService (backend/forecast_service.py)
            → проверка models/latest.json, SHA-256, схемы и происхождения
              → ForecastResponse выбранного направления
        → LLMGateway port
          → LLMGatewayAdapter (backend/adapters/llm.py)
            → OpenAILLMClient (backend/infrastructure/llm_client.py)
              → {BB_LLM_BASE_URL}/chat/completions
```

- `backend/domain/llm.py` — чистые функции построения контекста и system prompt.
- `backend/use_cases/llm.py` — оркестрация, порты и ошибки уровня приложения.
- `backend/adapters/llm.py` — перевод ошибок провайдера.
- `backend/infrastructure/llm_client.py` — OpenAI-compatible HTTP-клиент.
- `backend/app.py` — composition root, собирает use case и кладёт в
  `app.state.llm_use_case`. Если настройки LLM не заданы, use case не
  создаётся, а эндпоинты возвращают `LLM_NOT_CONFIGURED`.

## Контекст

Контекст создаётся автоматически сервером из того же проверенного
`ForecastResponse`, что используется `/forecasts`. Передаются прогнозные точки,
интервалы, период обучения, направление, фактический охват линии, источники,
версии, метрики и предупреждения. Все значения 2024–2030 — `forecast` с точкой
выдачи 2023, даже для прошедших лет. Сам исторический ряд наблюдений, стоимость
и мощность в этом контексте отсутствуют. Линия не представляет весь коридор.

Файлы `backend/jsons/scenario_mock_*.json` — старые синтетические примеры,
исключённые из рабочего контекста. `JSONLoader` и старый `build_context`
сохранены для автономных примеров и проверок формата, но приложением не вызываются.
Реальная модель не обучается при HTTP-запросе; её отсутствие или повреждение
останавливает обработку до вызова провайдера.

## Настройка

Добавьте в `.env` (скопируйте из `.env.example`):

```bash
BB_LLM_BASE_URL=https://ai.api.cloud.yandex.net/v1
BB_LLM_API_KEY=your-secret-key
BB_LLM_MODEL=gpt://<folder_id>/deepseek-v4-flash
BB_LLM_TIMEOUT_SECONDS=60
```

`BB_LLM_API_KEY` читается как `SecretStr` и не попадает в логи/ответы.
Оставьте любое из трёх полей пустым, чтобы отключить ассистента — остальной
backend продолжит работать.

## API

- `GET /api/v1/llm/summary` — автоматическая сводка, с query-параметрами выбора.
- `POST /api/v1/llm/ask` — вопрос пользователя и те же поля в JSON-теле.

Поля и значения по умолчанию: `region: kaliningrad`, `direction: inbound`,
`mode: sea`, `end_year: 2030`, `study_city: all`, `other_region: all`,
`other_city: all`. Допустимы направления inbound/outbound и годы 2024–2030;
подключён только агрегированный выбор Калининграда и морская линия. Пример:

```json
{"question":"Каковы ограничения прогноза?", "direction":"outbound", "end_year":2030}
```

Ответ: `{"answer":"...", "result_type":"interpretation", "context":{...}}`.
`context` имеет схему `ForecastResponse` и совпадает со снимком, переданным LLM.

Коды ошибок:

| Статус | Код | Когда |
|---|---|---|
| 422 | `VALIDATION_ERROR` | пустой или > 4000 символов вопрос |
| 404 | `LLM_DATA_NOT_FOUND` | отсутствует проверенная модель |
| 404 | `LLM_CONTEXT_NOT_AVAILABLE` | неподключённая география, вид или детализация |
| 500 | `LLM_CONTEXT_INVALID` | модель или происхождение не прошли проверку |
| 503 | `LLM_NOT_CONFIGURED` | не заданы `BB_LLM_BASE_URL/API_KEY/MODEL` |
| 502 | `LLM_UPSTREAM_ERROR` | ошибка или недоступность LLM-провайдера |

## Frontend

React-интерфейс (`frontend/src/App.tsx`) использует `/config.json` для
получения `apiBase` и обращается к `/api/v1/llm/summary` и
`/api/v1/llm/ask`. Выбор — применённые география и направление дашборда,
горизонт — до 2030, независимо от отдельного графика прогноза. Панель показывает
фактический охват, версии, источники и ограничения ответа. Для неподключённых
срезов запросы блокируются также на сервере. Смена выбора и очистка отменяют
запросы; старые ответы и ошибки не попадают в новый диалог. Панель показывает
ошибки backend и восстанавливает вопрос при неуспешной актуальной отправке.

## Тестирование

Backend-тесты используют fake repository/gateway и локальный mock
OpenAI-compatible server через `httpx.MockTransport`. Реальный LLM-провайдер
в тестах не вызывается.

```bash
python -m pytest backend/tests/test_llm_domain.py backend/tests/test_llm_api.py backend/tests/test_llm_client.py -q
```
