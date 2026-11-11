# Интеграция main/devops — 12 ноября 2026

## Перенос в основной проект

По запросу пользователя подготовленные 19 файлов перенесены в
`C:\Users\Соня\python\PycharmProjects\not_found`, ветка `integration/main-devops`.
Перед переносом рабочее дерево было чистым; содержимое скопированных файлов
проверено SHA-256. `git diff --check` и `docker compose config --quiet` проходят.
Коммиты и push не выполнялись. В основном проекте перенесён результат интеграции
как незакоммиченные файлы; состояние MERGE_HEAD отдельной копии не переносилось.
Ниже сохранён отчёт о подготовке и проверках в отдельной копии. Для запуска
теперь используйте корень основного проекта вместо `C:\workspace\flocast-merged`.

## Результат и границы

Рабочая копия: `C:\workspace\flocast-merged`, локальная ветка
`integration/main-devops`. База — доступная локальная `main` / `origin/main`
`c1b5ea7`; источник — `devops` `ba02d72`. Общий предок —
`e5148c4faa13fb87d0fdb65811599a54474713a3`. В devops один отдельный коммит.
Актуальность относительно GitHub после последнего fetch не подтверждена.

Коммиты и push не выполнялись. HEAD остаётся `c1b5ea7`, MERGE_HEAD указывает
на `ba02d72`: подготовлено слияние без завершающего коммита. Конфликтов в индексе
нет. Часть исправлений остаётся unstaged; это ожидаемое незакоммиченное состояние.
Исходные ветки и рабочая папка пользователя не изменялись этим продолжением.
В исходной папке находится отдельная ветка `fix_ui/ux` с незавершёнными UI-правками;
они в эту интеграцию не перенесены. Пустая папка `flocast-integration` не является
результатом: используйте `flocast-merged`.

При слиянии были конфликты в README, архитектуре и pyproject. Сохранены актуальные
описание, пакеты и optional extras из main; добавлены инструкции инфраструктуры
и требование версии uv из devops. Из devops сохранён подход uv/lock, Docker и
GitHub Actions; их реализация переработана под нынешний React/FastAPI.
Бизнес-логика, ML, API и frontend из main сохранены. `git diff main -- frontend`
пуст: дизайн и исходники frontend не изменены.

## Проблемы и решения

| ID | Компонент | Проблема | Критичность | Исправление | Статус |
|---|---|---|---|---|---|
| DEV-01 | Docker | Старый образ не собирал нынешний React и не включал нужные модули | высокая | Multi-stage Node → Python, копирование backend/ml/pipeline и React dist | реализовано; сборка заблокирована средой |
| DEV-02 | Запуск | Не было единой актуальной Compose-конфигурации | высокая | Один app, относительный API, bind моделей read-only | config проходит |
| DEV-03 | Зависимости | Старый lock не соответствовал нынешнему pyproject | высокая | Перегенерирован uv.lock, uv 0.13.7 | offline check проходит |
| DEV-04 | CI | Старые проверки не охватывали React и интеграцию | высокая | ESLint/tests/build, Python, HTTP smoke, Docker с двумя API-префиксами | workflow подготовлен; удалённо не запускался |
| DEV-05 | LLM | Ошибки 400/404 и сети недостаточно объясняли конфигурацию | средняя | Безопасные сообщения для 400/401/403/404/429 и сети | локальные тесты проходят |
| DEV-06 | Settings | Некорректные base URL и бесконечный timeout | средняя | Проверка HTTP(S), запрет credentials/query/fragment, конечный timeout | реализовано |
| DEV-07 | Образ | Секреты, данные и инструменты разработки могли попасть в build | высокая | Allowlist dockerignore, runtime без dev extra, UID 10001 | проверено по коду; образ не проверен |
| DEV-08 | Документация | Запуск не соответствовал новой инфраструктуре | средняя | README, architecture, contracts, devops и этот отчёт | обновлено |
| ENV-01 | Docker Desktop | Linux Engine недоступен; buildx lock запрещён | блокер проверки | Нужен доступный Docker Desktop/Engine | не устранено в среде агента |
| ENV-02 | Frontend build | esbuild spawn EPERM | блокер проверки | Повторить стандартный build в обычном терминале | TypeScript проходит; Vite не собран |
| ENV-03 | pytest | WinError 5 при tmp_path и pytest cache | блокер проверки | Повторить полный suite вне ограниченной среды | полный suite не подтверждён |

## Изменённые файлы

| Файл | Изменение и причина |
|---|---|
| `Dockerfile` | Сборка React через npm ci, runtime FastAPI с lock, без root; исходники React нужны существующей проверке SHA-256 сборки |
| `.dockerignore` | Только необходимые build inputs; исключены .env, данные, модели, node_modules, тесты и кэши |
| `compose.yaml` | Один сервис app, loopback:8000, необязательный .env, модели read-only, read-only rootfs/tmpfs, drop capabilities |
| `.github/workflows/ci.yml` | Единственный GitHub workflow для реальных Python/React/HTTP/Docker проверок, без CD и публикации |
| `.python-version` | Python 3.11 для uv |
| `pyproject.toml` | Существующие зависимости сохранены, закреплена версия uv |
| `uv.lock` | Точные версии и хеши для нынешнего pyproject/extra |
| `.gitignore` | Исключены .cache и диагностические reports/ci |
| `.env.example` | Переменные Compose и пояснение base URL; без ключей |
| `backend/core/settings.py` | Валидация base URL и конечного timeout |
| `backend/infrastructure/llm_client.py` | Диагностика upstream HTTP и сети без раскрытия ключей/тела ответа |
| `backend/tests/test_config.py` | Проверки ошибочных настроек |
| `backend/tests/test_llm_client.py` | Контракты ошибок провайдера и безопасных сообщений |
| `scripts/smoke.py` | Реальные HTTP-проверки UI, ресурсов, SPA, API, калькулятора и ожидаемой недоступности внешних компонентов |
| `README.md` | Единый контейнерный и локальный запуск, ссылки на подробности |
| `docs/architecture.md` | Фактическая инфраструктура и взаимодействие |
| `docs/contracts.md` | Совместимые сообщения ошибок; публичные поля не менялись |
| `docs/devops.md` | PowerShell/Linux, env, модели, LLM, CI, диагностика и ограничения |
| `docs/devops-integration-report.md` | Этот отчёт |

## Карта компонентов

| Компонент | Точка входа / адрес | Зависимости |
|---|---|---|
| Браузер / React | `/` на `http://127.0.0.1:8000` | Production dist из нынешнего frontend; карта требует интернет |
| Vite dev | `http://127.0.0.1:5173` | Тот же frontend, proxy к backend:8000 |
| FastAPI | `backend.app:app`, `/api/v1`, `/docs`, `/config.json` | fastapi, uvicorn, settings, httpx |
| ML serving | `ForecastService` → `ml.predict` | JSON-артефакт и SHA-256 в `/app/models`; без обучения при запросе |
| Сценарии | `backend/scenarios.py` | Проверяемый ввод, общий Python-расчёт |
| AI | `/api/v1/llm/*` | Проверенный ForecastResponse и внешний Chat Completions API |
| Исследования / ETL | CLI `ml/`, `pipeline/` | Исходные данные и optional extras; вне контейнерного старта |

Контейнер один: app:8000. Браузер использует относительный API-префикс из
`/config.json`; Docker hostname в браузер не передаётся. Нет оснований добавлять
Streamlit, Steam, Redis, БД, Nginx или отдельный ML-контейнер. Обучение требует
extra ml, чтение готового JSON — нет. Артефакты и внешние ключи не входят в образ.

## Проверки

| Проверка | Метод / команда | Результат | Ограничение |
|---|---|---|---|
| Конфликты | `git ls-files -u` | пустой вывод | Слияние остаётся без коммита по указанию пользователя |
| Frontend сохранён | `git diff main -- frontend` | пустой вывод | Новые UI-правки другой рабочей копии не входят |
| Whitespace | `git diff --check` | без ошибок | Есть штатные предупреждения LF/CRLF |
| Python lint | `ruff check backend scripts/smoke.py` | PASS | Проверяемая область указана явно |
| Python format | `ruff format --check backend scripts/smoke.py` | PASS, 47 файлов | Не заявляется lint всего исследовательского кода |
| Lock | uv 0.13.7, `uv --cache-dir .cache/uv lock --check --offline` | PASS, 133 пакета | Системный uv 0.9.5 не подходит |
| Frontend lint | `npm --prefix frontend run lint` | PASS | Код завершился без замечаний |
| TypeScript | стадия `tsc -b` в `npm run build` | PASS | Следующая стадия Vite упала |
| Frontend tests | `node --experimental-strip-types --test --test-isolation=none tests/*.test.mjs` из frontend | 23 PASS, 1 SKIP | Обычный npm test блокирует spawn EPERM; skip требует реальный API-прогноз |
| Production build | `npm --prefix frontend run build` | BLOCKED | Vite/esbuild: spawn EPERM |
| Полный pytest | `python -m pytest -q`, затем первый сбой и отдельный basetemp | BLOCKED | WinError 5 на временных каталогах; нельзя объявить suite зелёным |
| Settings и LLM tests | `pytest -q -p no:cacheprovider backend/tests/test_config.py backend/tests/test_llm_client.py` | 24 PASS, 1 ERROR setup | Единственная ошибка test_config зависит от tmp_path; LLM — MockTransport |
| Импорты | backend.app, ml.predict, ml.port_predict, pipeline.validate | PASS | Это не обучение/оценка моделей |
| API | TestClient без .env: health/status/config | 200 / 200 / 200 | Внутрипроцессная проверка, не сеть контейнера |
| Живой backend | Uvicorn на 127.0.0.1:18080; Invoke-RestMethod/Invoke-WebRequest | PASS: health ok, config /api/v1, status HTTP 200 | Проверен отдельный процесс и TCP/HTTP; первый запрос до готовности был отклонён, после startup повтор прошёл |
| Отсутствующий LLM | TestClient, GET llm/summary | 503 как ожидается | Приложение продолжает работать |
| Настоящий ML-комплект | ForecastService с models из исходной рабочей папки; сравнение с сохранённым forecast.json | PASS для inbound/outbound, по 7 значений | Версии bb-ml-6237c3f27262 / bb-provided-9205e8f76a45; это проверка serving/export, не новое подтверждение точности |
| Настоящий LLM | summary через существующие настройки исходной папки и настоящий прогноз | FAIL: LLMUpstreamError, сетевая ошибка | Ключ/модель не изменены и не выведены; успешный ответ провайдера не получен |
| LLM-клиент отдельно | pytest test_llm_client с отключённым cacheprovider | 13 PASS | 12 тестов settings deselected при дополнительном запуске; не полный backend suite |
| Compose | `docker compose config --quiet` | PASS | Не требует daemon |
| Docker build | `docker compose build` | BLOCKED | Access denied к buildx .lock |
| Docker up/ps/logs | Соответствующие compose-команды | BLOCKED | Нет dockerDesktopLinuxEngine pipe |
| HTTP smoke с React | `scripts/smoke.py` | Не выполнен целиком | Нет доступной новой production-сборки |
| GitHub Actions | Локальный просмотр workflow | Не запускался | Нет commit/push по указанию пользователя |

## Запуск после снятия ограничений среды

В обычном PowerShell, с работающим Docker Desktop (Linux containers):

```powershell
Set-Location C:\workspace\flocast-merged
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
docker compose config --quiet
docker compose build
docker compose up --detach --wait --wait-timeout 120
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
docker compose logs --tail 100
```

Сайт: `http://127.0.0.1:8000/`. Без моделей прогноз явно недоступен.
Для настоящего прогноза задайте `BB_MODELS_PATH` на полный проверенный models
и пересоздайте app. Для LLM заполните три серверных BB_LLM_* поля, затем также
пересоздайте app; провайдер и модель самостоятельно не заменялись.

```powershell
python -m pip install "uv==0.13.7"
uv sync --locked --extra dev --extra data --extra ml
npm --prefix frontend ci
npm --prefix frontend run build
uv run --locked --no-sync pytest -q
uv run --locked --no-sync ruff check backend scripts/smoke.py
uv run --locked --no-sync python scripts/smoke.py
docker compose down
```

При проверке чистого контейнера без модели/LLM у smoke добавьте
`--expect-no-model --expect-llm-disabled`. Для локального сервера:
`uv run --locked --no-sync uvicorn backend.app:app --reload`;
во втором терминале `npm --prefix frontend run dev`.
Подробные команды и все переменные — в [devops.md](devops.md).

## Оценка готовности

| Направление | Подтверждённый статус | Что остаётся |
|---|---|---|
| Docker | Конфигурация соответствует текущим путям и entrypoint | Реальная сборка, запуск, UID/healthcheck/SIGTERM |
| Compose | Синтаксис валиден, один согласованный сервис | Проверка сети и read-only runtime |
| CI/CD | CI актуализирован; CD не добавлен | Реальный запуск Actions после решения команды о публикации |
| Backend integration | Импорты, TestClient, живой Uvicorn по HTTP и отказ LLM проверены | Полный pytest и smoke с React |
| Frontend integration | main сохранён, lint/типизация/23 теста проходят | Vite production build, браузер и контейнер |
| ML integration | Настоящий комплект читается для обоих направлений и совпадает с экспортом | Проверить тот же комплект в контейнере; ничего не переобучено |
| LLM integration | HTTP-контракт и диагностика проверены локально; реальный запрос завершился сетевой ошибкой | Доступ к провайдеру из целевого сервера и успешный реальный ответ |
| Environment management | Единые BB_* и проверенный lock | Повторить чистую установку в целевой среде |
| Security | Исключения секретов, UID, read-only и capabilities заданы | Полный аудит истории, скан образа, TLS/auth/лимиты перед production |
| Reproducibility | npm lock и uv lock, инструкция | Сквозная сборка ещё не подтверждена; Node tag без digest |

Готовность production не заявляется. Нет согласованной production-площадки,
TLS, авторизации, мониторинга и правил CD. Изменение целевых рядов, гипотез и
метрик не выполнялось. Код и отчёт подготовлены с помощью ИИ; человеческая
приёмка включает реальный контейнер, внешний LLM и предметную проверку
артефактов, версий и происхождения данных.
