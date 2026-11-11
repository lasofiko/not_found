# Запуск и проверка FloCast

## Состав

Один контейнер `app`: FastAPI `backend.app:app` на 8000, production-сборка
утверждённого React из `frontend/`, встроенный вызов `ml.predict` и внешний
OpenAI-compatible LLM по настройкам. Отдельный процесс ML, база, Nginx,
Streamlit/Steam и Redis не используются. Обучение и ETL запускаются отдельно.
При отсутствии ML или LLM API остаётся живым и возвращает явную недоступность.
Образ не содержит моделей, raw/processed, отчётов и секретов.

| Компонент | Вход / зависимость | Адрес |
|---|---|---|
| React production | `frontend/index.html` → `src/main.tsx` → `App.tsx` | `/` того же сервера |
| API | `backend.app:app`, Python 3.11 | `/api/v1`, Swagger `/docs` |
| Vite dev | Node 24.14.0, npm lock | `http://127.0.0.1:5173` |
| ML serving | `ml.predict`, `ml/config.toml`, проверенный `models/latest.json` | внутри Python; без обучения |
| Ассистент | `/llm/summary`, `/llm/ask`, проверенный ForecastResponse | внешний `{BB_LLM_BASE_URL}/chat/completions` |
| Данные и исследования | `pipeline/`, `ml.run`, `ml.port_run` | отдельные CLI, не часть старта API |

## Docker Compose

Нужны Docker Desktop/Engine с Linux-контейнерами, BuildKit и Compose **2.24+**
(используется необязательный `env_file`). Первой сборке нужен доступ к Docker Hub,
GHCR, PyPI и npm registry. Примеры выполняются **из корня проекта**.

PowerShell:

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
docker compose config --quiet
docker compose build
docker compose up --detach --wait --wait-timeout 120
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/status
docker compose logs --tail 100 --follow
```

Одной командой: `docker compose up --build` (логи в текущем терминале).
Сайт http://127.0.0.1:8000/, Swagger http://127.0.0.1:8000/docs.
На Linux/WSL начальное копирование: `test -e .env || cp .env.example .env`.
Остальные Docker-команды одинаковы. В WSL включите интеграцию Docker Desktop.

Остановка без удаления данных:

```powershell
docker compose stop
docker compose down
```

Порт привязан к loopback. Для публичного размещения нужны отдельные решения
по TLS, reverse proxy, авторизации, лимитам запросов, мониторингу и резервированию.
Автоматического deployment и публикации образов нет.

## Переменные

Compose читает корневой `.env` как env_file и для подстановки `${...}`.
Backend вне Docker читает его через pydantic-settings. Vite читает корневые
`BB_*` для серверного proxy. Секреты не имеют префикса `VITE_` и не попадают в JS.

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `BB_APP_NAME` | FloCast | Название API |
| `BB_ENVIRONMENT` | development локально; production в Compose без .env | development/test/production |
| `BB_API_PREFIX` | /api/v1 | API; одинаковый у `/config.json`, Vite и healthcheck |
| `BB_CORS_ORIGINS` | `["http://localhost:5173"]` в коде | JSON-массив точных origin; пример добавляет 127.0.0.1 |
| `BB_LOG_LEVEL` | INFO | Уровень журналов |
| `BB_DEV_BACKEND_URL` | http://127.0.0.1:8000 | Только Vite proxy, не URL браузера |
| `BB_HTTP_PORT` | 8000 | Только Compose: порт хоста |
| `BB_MODELS_PATH` | ./models | Только Compose: локальный каталог → `/app/models:ro` |
| `BB_LLM_BASE_URL` | пусто | База Chat Completions API |
| `BB_LLM_API_KEY` | пусто | Серверный ключ |
| `BB_LLM_MODEL` | пусто | Точный идентификатор модели провайдера |
| `BB_LLM_TIMEOUT_SECONDS` | 60 | Конечное число секунд, минимум 1 |

`.env` необязателен для Compose: без него API запускается с отключённым LLM.
При копировании примера окружение development остаётся явным выбором из файла.
Для размещения задайте production. При нестандартном порте замените 8000 в URL
диагностики. `docker compose config` без `--quiet` может напечатать секреты —
не публикуйте полный вывод. После изменения серверных настроек:

```powershell
docker compose up --detach --force-recreate --wait --wait-timeout 120
```

## Локальная разработка

Python 3.11+, Node 22.12+ (ветка 22) или 24+; CI использует Python 3.11 и Node
24.14.0. Для воспроизводимой установки используйте `uv==0.13.7` и `uv.lock`:

```powershell
python -m pip install "uv==0.13.7"
uv sync --locked --extra dev --extra data --extra ml
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
npm --prefix frontend ci
npm --prefix frontend run build
uv run --locked --no-sync uvicorn backend.app:app --reload
```

Для обслуживания API достаточно `uv sync --locked`: extra `ml` нужен для обучения,
а не чтения JSON-артефакта. `data` добавляет xlrd/libarchive-c; для распаковки 7z
дополнительно нужна системная libarchive (см. инструкции подготовки данных).
Для исследований можно добавить `--extra notebooks`. `uv sync` меняет состав
окружения: при переустановке укажите все необходимые extra вместе.

Для React dev во втором терминале из корня: `npm --prefix frontend run dev`.
Открывайте http://127.0.0.1:5173/. Backend запускается первой командой выше;
build для Vite dev не нужен. Если терминал уже в `frontend`, используйте
`npm ci`, `npm run dev`, `npm run build` без `--prefix frontend`.
Production UI на 8000 требует повторного build после изменения исходников.
Отдельный Docker dev override не нужен: dev запускается этими CLI-командами.

## Модели и данные

Скопируйте полученный от ML Lead проверенный **полный** каталог models по пути
`BB_MODELS_PATH`. В нём нужны `latest.json`, указанный JSON и его `.json.sha256`;
внутренний `model_path` должен оставаться относительно корня `models/...`.
Не переименовывайте вложенные каталоги. Backend проверяет версию, SHA-256,
схему, источник и соответствие `ml/config.toml`. Том read-only; контейнер не обучает.

`GET /status` сообщает готовность ML отдельно от `/health`. Без модели прогноз
возвращает 503 `not_connected`; для неподдержанной географии — 404; повреждение —
500 `error`. Вымышленные результаты не создаются. Для LLM также нужен проверенный
годовой прогноз линии. Месячные исследовательские модели не включаются в API автоматически.

Подготовка настоящих данных и обучение — по [ML README](../ml/README.md) и основному
README. Не запускайте ETL/обучение как smoke test: они записывают результаты;
слепая проверка 2026 выполняется только явно по протоколу команды.

## Ассистент и диагностика

Заполните три `BB_LLM_*` поля в локальном `.env`, не меняя провайдера или модель.
Используйте точный OpenAI-compatible **base URL** провайдера с его API-префиксом;
например, база `https://provider.example/v1` образует `/v1/chat/completions`.
Полный URL `/chat/completions` в base не вставляйте. Для Yandex пример в
`.env.example` требует реальный folder/model URI и права ключа.

| Ошибка | Диагностика |
|---|---|
| `LLM_NOT_CONFIGURED`, 503 | Не заполнено одно из трёх полей; пересоздайте контейнер/перезапустите Uvicorn |
| `LLM_DATA_NOT_FOUND`, 404 | Нет проверенного годового ML-комплекта |
| `LLM_CONTEXT_NOT_AVAILABLE`, 404 | Выбран неподдержанный регион, вид или городская детализация |
| `LLM_CONTEXT_INVALID`, 500 | Модель повреждена/несовместима; восстановите проверенный комплект |
| `LLM_UPSTREAM_ERROR`, 502 с HTTP 400 | Точная model URI, права и совместимость Chat Completions |
| То же с HTTP 404 | Base URL, API-префикс, имя/доступность модели; не удваивать путь |
| HTTP 401/403/429 провайдера | Ключ, разрешения, квоты/лимиты |
| Сетевая ошибка/таймаут | DNS, TLS, proxy и доступ в интернет именно из сервера/контейнера |
| UI 503: не собран/устарел | `npm --prefix frontend run build` или `docker compose up --build` |
| Docker named pipe/daemon unavailable | Запустить Docker Desktop, дождаться Linux Engine, повторить `docker info` |
| npm ENOENT | Запускать из корня с `--prefix frontend` либо внутри frontend без него |
| Порт занят | Поменять `BB_HTTP_PORT`, повторить запуск и использовать новый порт в браузере |

Не отправляйте ключи, `.env`, полный конфиг Compose и тело провайдера в отчёты.
Юнит-тесты LLM проверяют HTTP-контракт через MockTransport; это не успешный запрос
к настоящему провайдеру. Необязательный внешний LLM не участвует в обязательном CI.

## Проверки и CI

```powershell
uv lock --check --offline
uv run --locked --no-sync ruff check backend scripts/smoke.py
uv run --locked --no-sync ruff format --check backend scripts/smoke.py
uv run --locked --no-sync pytest -q --junitxml=reports/ci/pytest.xml
npm --prefix frontend run lint
npm --prefix frontend test
npm --prefix frontend run build
docker compose config --quiet
uv run --locked --no-sync python scripts/smoke.py
```

Последняя команда требует запущенный сервер и проверяет React-ресурсы, SPA,
health, config, маршруты, ошибки и калькулятор. Для чистого запуска без модели
и LLM добавьте `--expect-no-model --expect-llm-disabled`. Для другого порта:
`--base-url http://127.0.0.1:8001`. Эти флаги требуют именно заявленное состояние,
а не пропускают проверки. Внешние LLM-запросы smoke не выполняет.

GitHub Actions: один workflow, jobs checks/container, без CD. `checks` устанавливает
закреплённые extra dev/data/ml, проверяет backend, ESLint, существующие Node-тесты,
TypeScript/Vite, pytest и живой HTTP-сервер. `container` проверяет Compose, сборку,
healthcheck, HTTP smoke, UID 10001 и SIGTERM при `/api/v1` и `/ci/v2`.
Actions закреплены полными SHA, токен только `contents: read`, credentials не сохраняются.
Реальные файлы ML/данных и внешние ключи в CI отсутствуют. Тесты, требующие их,
имеют явные skip; это отражается в pytest, не считается проверкой реальной модели.
Политики веток и удалённый запуск Actions локально не меняются.

Docker исключает секреты и исследовательские файлы из build context; зависимости
сервера фиксируются uv.lock, frontend — package-lock.json. Python и uv закреплены
digest, Node — версией образа (digest требует последующей фиксации). Побайтовая
идентичность OCI и отсутствие уязвимостей не заявляются. История Git отдельно
не переписывается; полный аудит прошлых секретов/образов перед production остаётся задачей.

ИИ подготовил интеграцию, конфигурации и тесты. Команде остаётся проверить настоящий
Docker-запуск в доступной среде, внешний LLM с собственным ключом и предметное
соответствие источников/артефактов; результаты текущих проверок — в отчёте интеграции.
