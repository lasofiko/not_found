# Исправления ревью от 11.11.2026

Исправлены десять подтверждённых замечаний `review.md`. Пользователь утвердил план
и выбрал контекст реального годового прогноза линии для ассистента. Пользовательские
архивы, CSV, patch и исходный текст ревью сохранены; коммит не создавался.

## Изменения и затронутые файлы

| № | Изменение | Основные файлы |
|---|---|---|
| 1 | Ассистент получает проверенный ForecastResponse выбранного направления; ответ interpretation возвращает тот же context. Неподдержанная география и повреждение останавливают обработку до LLM. Старые JSON исключены и помечены synthetic. | `backend/app.py`, `backend/{adapters,api/v1,domain,schemas,use_cases}/llm.py`, `backend/core/exceptions.py`, `backend/jsons/scenario_mock_{a,b}.json`; `frontend/src/{App.tsx,api.ts,assistant.ts}` |
| 2 | Версии модели/данных, SHA и восстановленная блокировка связывают метрики с замороженным запуском. Хеш и равенство двух копий проверки обязательны для обеих схем. | `ml/{blind_binding,export_jury,port_run}.py` |
| 3 | Сводки используют текущие метрики и сценарные таблицы; равенство baseline, пропуски и сработавший ж/д предел отображаются корректно. | `ml/export_jury.py` |
| 4 | Исследование выбирается через latest; версия, путь и хеш метрик входят в fingerprint и манифест гипотез. | `ml/hypotheses.py` |
| 5 | Полнота processed проверяется до установки. Проверка сверяет неизменяемые non-CSV с исходным ZIP даже при подмене outputs-хеша. | `pipeline/{team_bundle,bundle_sources}.py` |
| 6 | Передача ML создаётся из чистого каталога вместе с README; инструкции включены в fingerprint. | `pipeline/ml_handoff.py` |
| 7 | Пустая масса не попадает в Decimal и не становится нулём; неполные сверки видны в CSV, журнале и отчётах. | `pipeline/{build,target_audit}.py` |
| 8 | Отмена и поколение запроса защищают историю, ввод и загрузку. Смена выбранного среза либо версии модели/данных отделяет новый диалог. | `frontend/src/{App.tsx,App.css,assistant.ts}` |
| 9 | FastAPI и Vite возвращают только внутренние Location, подозрительные пути ведут в корень. | `backend/redirects.py`, `backend/app.py`, `frontend/src/legacy-redirect.ts`, `frontend/vite.config.ts` |
| 10 | Человекочитаемый аудит вычисляется из проверенных URL/файлов/ZIP; приложение DOCX учитывается отдельно от ссылок разделов. | `scripts/check_dataset_list.py` |

Регрессии: `backend/tests/{conftest,test_llm_api,test_llm_domain,test_redirects}.py`,
`frontend/tests/{assistant,legacy-redirect}.test.mjs`,
`tests/test_{bundle_sources,dataset_list_audit,hypotheses,jury_export,ml_handoff,pipeline,port_protocol,team_bundle}.py`.

Документация: корневой README, `data/README.md`, `ml/README.md`,
`docs/{contracts,api_contract,architecture,llm,ml-run-guide,team-bundle-processing,ai-work-report,review-fixes}.md`.
Контракт обновлён до реализации. Публичные добавления: параметры среза LLM,
`result_type` и `context` ответа, происхождение слепых метрик, вход исследования
в манифесте гипотез, `reconciliation.check_status`,
`transformation_log.reconciliation_incomplete` и `target-quality.line_terminal_incomplete_years`.

## Проверки

| Проверка | Результат |
|---|---|
| `.venv/bin/python -m pytest -q` | 346 passed, 1 skipped. Пропущен существующий PDF-тест: в окружении отсутствует PyMuPDF. |
| Ruff check и format --check для backend | Пройдены, 46 файлов форматированы. |
| Ruff F/I/B для изменённых ML-модулей и тестов | Пройдено. Общая косметическая переработка старого ML/pipeline не проводилась. |
| `npm --prefix frontend run lint` | Пройдено. |
| `npm --prefix frontend run build` | Пройдено: TypeScript, Vite, PWA; два нефатальных предупреждения Rollup об аннотациях zod. |
| Frontend с Node 24 и NF_FORECAST_FIXTURE | 24 passed, 0 skipped; fixture прочитан из существующего ForecastService без обучения. |
| Пять имеющихся frozen ML-комплектов | Read-only проверка связи дала legacy_frozen_run для каждой цели; raw 2026 не читались. |
| `git diff --check` | Пройдено. |
| Chromium: production и Vite, 1440×1000 и 390×844 | 10 сценариев пройдены: контекст/источники, очистка во время ask, смена направления, неприменённый выбор, неподдержанный регион и ручной город, новая версия модели, ошибка модели/повтор, мобильная панель, фактические redirects обоих серверов. Ошибок JavaScript нет. |

Браузер использовал отдельный локальный backend с синтетическим ForecastResponse
и HTTP-провайдером на loopback. Внешние браузерные запросы блокировались.
Результат и снимки экрана текущей проверки сохранены локально:
`/tmp/not-found-browser/{result.json,assistant-desktop.png,assistant-mobile.png,assistant-unavailable.png}`.
Тестовый скрипт — `/tmp/not-found-browser/smoke.mjs`, сервер — `/tmp/not-found-ui-server.py`;
это временные инструменты приёмки, зависимости проекта не расширены.

Команда полного frontend-набора с доступным артефактом:

```bash
NF_FORECAST_FIXTURE=/tmp/not-found-review-forecast.json \
node --experimental-strip-types --test --test-isolation=none frontend/tests/*.test.mjs
```

Backend HTTP-тесты выполнены вне ограниченной песочницы: внутри неё TestClient
зависает, как отмечено в исходном ревью. Frontend-зависимости восстановлены
`npm ci`; lockfile и pyproject не изменялись. npm сообщает прежние 12 advisory
(2 low, 10 moderate); обновление зависимостей не входит в десять исправлений.

`pipeline.verify` текущего локального комплекта возвращает ошибку несовпадения
SHA-256 кода/документов: `pipeline/{build,bundle_sources,ml_handoff,target_audit,team_bundle}.py`,
README, `data/README.md`, `docs/{architecture,contracts,decision-log,team-bundle-processing}.md`,
pyproject. Среди них есть прежние несоответствия из ревью, а также файлы этих
исправлений. Реальные комплекты, манифесты и хеши не переписывались. Для передачи
нового комплекта нужна воспроизводимая сборка текущим кодом с доступными входами;
успешные синтетические регрессии не подтверждают свежесть локальной выгрузки.

## Участие ИИ и открытые решения

Изменения, автоматические тесты и перекрёстную техническую проверку подготовил ИИ.
Внешний LLM не вызывался. Пользователь утвердил план и выбор контекста ассистента;
проверка реализации человеком пока не зарегистрирована. Предметная приёмка
исходных чисел, полное покрытие коридора, CAPEX/OPEX и подтверждённые эффекты
действий сохраняют прежний статус. История моделей, закрытые факты и сохранённые
слепые проверки не изменены.
