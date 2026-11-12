# Проверка готовности FloCast к демонстрации

Дата: 12 ноября 2026 года. Исправления выполнены в существующем приложении,
без изменения целей, методов и окон исследования. Исходное состояние Git было
чистым. Коммиты, push, публикация, реальное обучение и повторная blind-проверка
не выполнялись. Дизайн основного React сохранён.

Программные исправления по всем направлениям реализованы. До окончательной
приёмки материалов остаётся визуальная проверка исправленного паспорта DOCX:
штатный рендерер не работает без LibreOffice, а получить изображения страниц
через установленный Word в этой сессии не удалось. Это ограничение пункта 3,
а не подтверждённая готовность документа к сдаче.

## Все двадцать пунктов

| № | Статус | Что изменилось и как проверено |
|---|---|---|
| 1 | Исправлено | `pipeline/kaliningrad_customs.py`: убрано назначение тыс. USD по умолчанию. Проверены одинаковые формы с USD, тыс. USD, рублями, млн рублей и неизвестной единицей. Неизвестное значение остаётся без нормализованной суммы, с `unit_unconfirmed`; валютного обмена нет. Проверки в `test_kaliningrad_additional.py`. |
| 2 | Исправлено | Собственные JSON/тексты читаются с явной кодировкой в extended, far_east, kaliningrad_additional, ml_handoff, bundle_sources и затронутых проверках. Внешний текст использует зарегистрированную кодировку, тихая подмена удалена. Проверен Windows cp1251 с `utf8_mode=0`. |
| 3 | Заблокирована визуальная приёмка | В `docs/submission/паспорт.docx` исправлены два абзаца: нет обещания «вернёт не менее», вероятностного ожидания без методики и доказанного максимума. Оценки 93 616, 111 623, 163 119 и 40 067 т сохранены как условные. Числовые исследования не менялись; все остальные части DOCX совпадают с HEAD. Связанные Markdown/PPTX проверены на эти обещания. Требуется просмотр страниц человеком/работающий рендерер DOCX. |
| 4 | Исправлено | Подтверждён конфликт Python 3.13 и NumPy cp311 в старой `.venv`. Отдельная `.cache/venv311` создана Python 3.11.18 через uv 0.13.7 из `uv.lock`, с dev/data/ml; импорты NumPy, statsmodels.api и backend успешны. Старая среда и `.env` сохранены. |
| 5 | Исправлено | TypeScript/Vite/PWA production build успешен; SHA исходников и HTML соответствуют build-info. Проверены корень FastAPI, ресурсы, SPA, config, обновление страницы со старого интерфейса. SW исключает HTML из precache, navigation NetworkOnly, skipWaiting/clientsClaim включены. Отказ 503 для отсутствующей/устаревшей сборки сохранён. |
| 6 | Исправлено | `team_bundle.py` проверяет конечные пути до записи: `./`, повторные разделители, Windows case aliases, конфликт файла/каталога и выход из корня отвергаются. Проверки SHA и резервирование сохранены. Некорректные ZIP не устанавливаются частично; `test_team_bundle.py`. |
| 7 | Исправлено | `far_east_tables.period_info`: месяц, квартал, накопление и произвольный интервал имеют согласованные basis/granularity/start/end. Июль–сентябрь — `interval`, обратный диапазон отклоняется. Интервальные суммы не распределяются по месяцам; `test_far_east.py`. |
| 8 | Исправлено | В обычной цепочке загрузки истории, включая `verify`, проверяется сохранённая сентябрьская диагностика без разбора закрытых чисел 2026. Для числовой диагностики требуется явное разрешение. Полный синтетический тест цепочки без подмены verify и sentinel закрытых значений — `test_bundle_sources.py`. |
| 9 | Исправлено | `ml.hypotheses` сверяет четыре используемые таблицы с манифестом и сохраняет SHA манифеста, таблиц, трёх файлов параметров и модельной основы. Перед публикацией повторно проверяет входы. Подмена входа отвергается; `test_hypotheses.py`. |
| 10 | Исправлено | `ml.export_jury` использует конкретный `model_basis` сценарного исследования с проверенным fingerprint, SHA и версией. Новый latest не подменяет старую модель. Комплект без доказуемой связи отклоняется; `test_jury_export.py`. |
| 11 | Исправлено | Полное происхождение портовой модели, включая Git commit/dirty, входит в fingerprint. Сохранённые модельные байты и каталоги версий не перезаписываются. Синтетическая смена Git даёт другую версию, старая остаётся неизменной; `test_publication.py`. |
| 12 | Исправлено | Staging → полная проверка → неизменяемое поколение → атомарный указатель. Основные читатели, package и reproduce закрепляют один снимок. До commit ошибка сохраняет прежний указатель; после commit обрыв копии не повреждает доступное поколение, retry восстанавливает копию. Проверены поздняя ошибка, KeyboardInterrupt, прерывание mirror, повтор, имя/содержимое ZIP, второй процесс и освобождение блокировки после аварии. |
| 13 | Исправлено с ограничением старого результата | Ценовая проверка явно `retrospective_available_vintage`; даты исторических публикаций не выдуманы. Новый запуск пишет полный паспорт с SHA, протоколом, версиями, единицами и ограничениями. Старый результат имеет честный `legacy_unverified_input` и пустой SHA неизвестного входа. Методы/окна и AST девяти расчётных функций сохранены; четыре синтетических теста. |
| 14 | Исправлено | Проверка React в рабочем потоке под async lock; полное хеширование ограничено TTL 0,5 с, манифест/HTML инвалидируют кэш. Проверены параллельные ресурсы и health, повреждение manifest, изменение исходника той же длины с сохранённым mtime. |
| 15 | Исправлено | Для LLM добавлены Bearer-доступ, явное разрешение удалённого клиента, общая частота, параллельность, max_tokens, ограничение символов и кэш сводок по полному контексту. Локальный development удобен без аккаунтов. Production требует токен даже за loopback proxy; без провайдера сохраняется 503. Проверено подменённым провайдером без платных запросов. |
| 16 | Исправлено | Обычные Python-тесты очищают BB_LLM_* и запрещают настоящий HTTP transport, внешний DNS/TCP/UDP. Mock/ASGI transports доступны. React-тесты запрещают fetch/http/https/net/tls. Полный прогон выполнен с фиктивно заполненными серверными переменными. |
| 17 | Исправлено | `backend/tests/fixtures/synthetic-annual-model.json` — небольшой test-only артефакт, никогда не подключаемый к рабочему приложению. Настоящие ForecastService → API → ForecastResponse проверяются на обоих направлениях, горизонтах 2024/2025/2030, происхождении и пяти видах порчи. Эти тесты входят в обязательный общий pytest CI и не требуют локальных моделей. |
| 18 | Исправлено | Регрессии перенесены с архивного app.js на настоящий CapacityCalculator.tsx. Проверены календарь/ввод, повтор, таймаут config/API, отмена/unmount, поздний ответ, очистка, пять выходных полей и безопасное отображение текста. Арифметика остаётся в backend. |
| 19 | Исправлено | CSV сохраняет объект, маршрут/охват, направление, единицу, origin, обучение, дату, версии, источники, интервалы и предупреждения одного ForecastResponse. Оба направления/короткий и длинный горизонт проверены. Исходные наблюдения не экспортируются; ограничения источников сохраняются. |
| 20 | Исправлено | Ассистент: dialog, начальный/возвращаемый фокус, Tab/Shift+Tab/Escape, inert всего фона, отмена запроса при закрытии; шесть локальных chart boundaries и исправленный backdrop. XLSX: до XML-разбора лимиты общей распаковки 256 МиБ, XML 128 МиБ, одного XML 32 МиБ, 10 000 вложений; DTD/сущности запрещены. DOM и архивные регрессии проходят. |

Дополнительно исправлено деление на ноль во внешней терминальной сверке:
процент при нулевом знаменателе пуст, абсолютное расхождение сохраняется.
Отчёт поддерживает отсутствующий процент. Неиспользуемый UI-каркас не удалялся.

## Проверки и их границы

Итоговые числовые результаты полного прогона и последних проверок приведены
в конце этого документа. Рабочие команды:

```powershell
$env:UV_PROJECT_ENVIRONMENT = '.cache/venv311'
uv sync --locked --python 3.11 --extra dev --extra data --extra ml
npm --prefix frontend ci
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
uv run --locked --no-sync ruff check backend scripts/smoke.py conftest.py
uv run --locked --no-sync ruff format --check backend scripts/smoke.py conftest.py
uv run --locked --no-sync pytest -q
```

Нужен uv **0.13.7**. В текущей копии доступен `.cache/bin/uv.exe`; системный
uv 0.9.5 для этого lock-окружения не использовался. Исходная `.venv` остаётся
неисправной и сохранена; запускайте проверенный Python 3.11 из `.cache/venv311`.
Зависимости Python не менялись. Для frontend добавлены только необходимые
dev-зависимости jsdom 26.1.0 и явно закреплённый esbuild 0.27.2; npm lock обновлён.

Пять явных пропусков полного Python-набора:

- Три отдельно включаемые проверки реального комплекта (`RUN_REAL_MODEL_TESTS=1`).
  Успешный и повреждённый прогноз обязательного CI проверяются синтетическим комплектом.
- Один тест внешнего PDF-обработчика: соответствующий необязательный пакет не установлен.
- Одна ветка symlink: Windows не разрешила создание ссылки. Отказ для `..`
  и остальные ограничения путей проверены отдельно.

Есть одно предупреждение установленного Starlette о будущем переходе TestClient
с httpx на httpx2. Версии зависимостей ради устранения предупреждения не обновлялись.
Docker/container build в этой работе не запускался; локальная сборка и настоящий
HTTP-сервер проверены. Экранный диктор и реальный мобильный браузер отдельно
человеком ещё не проверялись.

## Воспроизводимый запуск

После установки и сборки, из корня проекта:

```powershell
$env:UV_PROJECT_ENVIRONMENT = '.cache/venv311'
uv run --locked --no-sync uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Сайт: http://127.0.0.1:8000/; API: http://127.0.0.1:8000/docs.
Во втором терминале:

```powershell
.\.cache\venv311\Scripts\python.exe scripts/smoke.py
```

Не перезаписывайте пользовательский `.env`. Для проверки отключённого провайдера
можно задать пустые BB_LLM_BASE_URL/API_KEY/MODEL только в проверочном процессе.
Для текущего сохранённого артефакта HTTP-прогноз проверен в обоих направлениях
на горизонтах 2024 и 2030: модель `bb-ml-6237c3f27262`, данные
`bb-provided-9205e8f76a45`. Никакого обучения при этом нет. Для отсутствующей
модели контракт 503/not_connected проверяется на пустом временном каталоге;
успешный путь — на отдельном synthetic test-only комплекте.

## Публичные поля и метаданные

Поля успешных HTTP-ответов прогноза и калькулятора сохранены. Добавлены LLM-коды
403 `LLM_ACCESS_DENIED`, 429 `LLM_RATE_LIMITED`/`LLM_BUSY` и Retry-After;
настройки перечислены в README, `.env.example` и `docs/contracts.md` v0.19.
Без провайдера сохраняется 503 `LLM_NOT_CONFIGURED`.

Новые CSV-столбцы после прежних шестнадцати:
`target_id,target_name,route_id,scope,period_granularity,horizon_years,forecast_origin,training_start,training_end,generated_at,selected_model,observations,sources_json,warnings_json,provenance_json,metrics_json`.

В таблицах уточнены `period_basis`/`period_granularity`, статус неизвестной
таможенной единицы и идентичность рядов разных валют. Новых столбцов ETL нет.
Метаданные исследования: `input_identity`, вложенный `model_basis`, уточнённый
`raw_validation_scope`. Публикация данных использует `data/current.json`.
Ценовой результат получает `passport.json`. Контракты обновлялись до реализации.

## Сохранённые исследования и решения команды

Проверено отсутствие изменения SHA-256 десяти существовавших файлов модели,
сохранённого годового отчёта и `.env`. Существующие ценовые CSV и PNG совпадают
с Git; добавлен только паспорт их происхождения. PPTX не менялся. Изменились
только два содержательных абзаца DOCX, остальные ZIP-части документа неизменны.
Первичные оригиналы, blind-результаты и существующие исследовательские блокировки
не пересобирались, не удалялись и не заменялись.

Старые сценарии без доказуемой связи с точной моделью теперь отклоняются при
экспорте. Недостающий вход — сохранённое доказательство model_path/version/SHA,
а не разрешение подставить текущий latest. Для старого ценового результата
неизвестны SHA исходного входа и исходные версии публикаций; эти сведения
не восстановлены предположением.

Команде по-прежнему нужны предметная приёмка источников/охвата, решение о целевых
рядах расширения, две утверждённые транспортные гипотезы и диапазоны параметров,
CAPEX/OPEX и методика причинного эффекта. Эти решения не принимались от имени
команды. Точность до 2030, причинный эффект резерва и вероятность конкретного
тоннажа не заявляются.

## Участие ИИ и человеческая приёмка

ИИ проверил находки, внёс исходники/контракты/регрессии, подготовил окружение,
выполнил тесты, сборку и HTTP/браузерную проверку. Независимые агенты взаимно
проверили публикацию, ограничения LLM и тестовую изоляцию; найденные крайние
случаи исправлены и проверены. Это автоматическая проверка, не приёмка человеком.

Человеку осталось проверить страницы DOCX, внешний вид и доступность на целевых
устройствах, принять предметные ограничения и права распространения источников,
сверить демонстрацию на машине команды. Подтверждённые действия человека в этой
сессии не выдуманы.

## Фактические результаты

- Полный Python-прогон: **401 passed, 5 skipped**, одно предупреждение; 368,16 с.
- Проверено `locale=cp1251`, `utf8_mode=0`; BB_LLM_* процесса были фиктивно заполнены, реальная внешняя сеть запрещена.
- После последних точечных исправлений: **8 passed** LLM production/offline/cache/limits и **7 passed** UI build/cache/concurrency/health.
- React: **31 passed, 0 skipped**, lint и TypeScript/Vite/PWA build успешны.
- Ruff check и format --check для backend, smoke и conftest успешны; `uv lock --check --offline` успешен.
- AST-аудит production Python: собственных `read_text()` без явной encoding в pipeline/ml/backend нет.
- HTTP smoke на `/api/v1` и production `/demo/v2`: PASS (UI, ресурсы, SPA, config, health, API, калькулятор, отключённый LLM).
- Существующий прогноз: HTTP 200 для inbound/outbound, end_year 2024/2030; успешный synthetic API и отказ повреждённого/отсутствующего артефакта покрыты обязательными тестами.
- SHA build-info/source/index: совпадают. SHA десяти сохранённых файлов модели/отчёта/.env: изменений нет. Старые ценовые CSV/PNG: хеши сохранены.
- `git diff --check`: PASS. Python pyproject/uv.lock не менялись.
- Проверка страниц DOCX: **не выполнена** — нет LibreOffice для штатного рендерера; резервный путь Word не дал изображения окна.
- Локальные JUnit-результаты: `.cache/pytest-release-v19.xml`; в Git не включаются.


## Изменённые файлы

Всего файлов: 84.

```text
.env.example
.gitignore
README.md
backend/api/v1/llm.py
backend/app.py
backend/core/settings.py
backend/frontend.py
backend/infrastructure/llm_client.py
backend/llm_policy.py
backend/tests/fixtures/synthetic-annual-model.json
backend/tests/test_forecasts.py
backend/tests/test_frontend_config.py
backend/tests/test_llm_limits.py
backend/tests/test_synthetic_forecast.py
backend/use_cases/llm.py
conftest.py
docs/architecture.md
docs/contracts.md
docs/devops.md
docs/far-east-handoff.md
docs/hackathon-readiness-2026-11-12.md
docs/jury/regions/price-premium-protocol.md
docs/jury/regions/price_premium.py
docs/jury/regions/regions-comparison.md
docs/jury/regions/results/passport.json
docs/kaliningrad-additional-contract.md
docs/kaliningrad-additional-handoff.md
docs/submission/паспорт.docx
frontend/package-lock.json
frontend/package.json
frontend/src/App.css
frontend/src/App.tsx
frontend/src/AssistantDialog.tsx
frontend/src/CapacityCalculator.tsx
frontend/src/ChartBoundary.tsx
frontend/src/ChartPanels.tsx
frontend/src/report.ts
frontend/tests/api.test.mjs
frontend/tests/app-assistant.test.mjs
frontend/tests/capacity.test.mjs
frontend/tests/dom-harness.mjs
frontend/tests/forecast-fixture.mjs
frontend/tests/forecast-report.test.mjs
frontend/tests/no-network.mjs
frontend/tests/resilience.test.mjs
ml/data.py
ml/experiment_io.py
ml/export_jury.py
ml/hypotheses.py
ml/port_run.py
ml/publication.py
ml/reporting.py
ml/review.py
ml/run.py
pipeline/build.py
pipeline/bundle_sources.py
pipeline/extended.py
pipeline/extended_extract.py
pipeline/far_east.py
pipeline/far_east_tables.py
pipeline/generation.py
pipeline/kaliningrad_additional.py
pipeline/kaliningrad_customs.py
pipeline/ml_handoff.py
pipeline/model_inputs.py
pipeline/package.py
pipeline/port_protocol.py
pipeline/reproduce.py
pipeline/tables.py
pipeline/team_bundle.py
pipeline/verify.py
tests/test_bundle_sources.py
tests/test_dataset_list_audit.py
tests/test_extended.py
tests/test_far_east.py
tests/test_hypotheses.py
tests/test_jury_export.py
tests/test_kaliningrad_additional.py
tests/test_ml_handoff.py
tests/test_pipeline.py
tests/test_price_premium.py
tests/test_publication.py
tests/test_tables.py
tests/test_team_bundle.py
```
