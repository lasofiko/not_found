# Месячные прогнозы портов на сайте: как внедрить

Итоговая версия от 12.11.2026. На сайт выводятся месячные модели портового каботажа. Основной прогноз — порт Калининград, грузы на паромах, Ridge AR(3) на реальной истории (`bb-ridge-d9c6acb34f45`). Годовая линия Усть-Луга — Балтийск остаётся на сайте как дополнение, её код не менялся.

Команды — для Windows PowerShell, из корня репозитория. Модели берутся из итогового архива `flocast-final-2026-11-12` (папка `models/`).

В текущей рабочей копии интеграция уже выполнена в ветке `data-gen`, созданной
от существовавшей `data/gen`. Проверенный архив распакован без изменения байтов
в `reports/generated/final-archive/flocast-final-2026-11-12/` (вне Git), включая
`research/`, `evaluation/` и инструменты воспроизведения. Проверка архива:

```bash
python3 reports/generated/final-archive/flocast-final-2026-11-12/tools/verify_archive.py reports/generated/final-archive/flocast-final-2026-11-12
```

Патч и копирование моделей повторять не требуется. Не запускайте обучение
или `ml.port_override` для проверки сайта. Инструкция ниже описывает внедрение
в другую рабочую копию; фактические результаты и ограничения этой интеграции —
[в отчёте](final/integration-2026-11-12.md).

## Что меняет патч

| Файл | Что делает |
|---|---|
| `backend/site_models.json` | Какие модели показывать: цели, роли, режим показа, ожидаемая версия модели |
| `backend/port_forecast_service.py` | Читает модели из `models/port/<цель>/latest.json` и метрики из `docs/final/*.csv`; ничего не обучает |
| `backend/api/v1/port_forecasts.py` | `GET /api/v1/port-forecasts` и `GET /api/v1/port-forecasts/{target_id}?end_period=YYYY-MM` |
| `ml/port_predict.py` | Принимает внешний артефакт схемы 0.3 (готовый прогноз Ridge) с проверкой происхождения |
| `ml/port_override.py` | Смена метода после слепой проверки с пометкой (так собран SES для всего каботажа) |
| `docs/final/site-metrics.csv`, `site-blind-metrics.csv` | Метрики итоговых моделей сайта в формате `docs/jury` |
| `backend/scenario_registry.json` | А — резервный паром, Б — сокращение портовой части оборота, В — постоянный дополнительный паром |
| `frontend/src/port-api.ts`, `PortForecastPanel.tsx` | Раздел сайта «Перевалка в каботаже по портам» (`#port-forecast`) |
| `Dockerfile`, `.dockerignore` | Кладут `docs/jury/*.csv` и `docs/final/*.csv` в образ |
| `ml/line_operator_experiment.py` | Эксперимент: ряд оператора и эффект парома. На сайт не выводится |
| Тесты | `backend/tests/test_port_forecasts.py`, `frontend/tests/port-api.test.mjs` |

## Что показывает сайт

| Цель | Модель | Режим | Контроль 2025 / naive | 2026 янв–авг / naive |
|---|---|---|---|---|
| Калининград, паромы | Ridge AR(3) `bb-ridge-d9c6acb34f45` | `primary` | 9,1% / 19,8% | 1,4% / 3,8% |
| Калининград, контейнеры | SES `bb-port-c686b4cb47b0` | `shown` | 12,0% / 11,9% | 8,1% / 8,6% |
| Калининград, весь каботаж | SES `bb-port-09047f079455` (изменён после проверки) | `shown` | 10,3% / 10,1% | 5,5% / 6,6% |
| Владивосток | SES `bb-port-6329b8d38370` | `shown_with_warning` | 15,8% / 18,8% | 12,1% / 0,8% |
| Петропавловск-Камчатский | SES `bb-port-6454be65293a` | `history_only` — без прогноза | — | — |

Числа сайт берёт из `docs/final/site-metrics.csv` и `docs/final/site-blind-metrics.csv`, а не из этой таблицы.

## Шаги

### 1. Применить патч к ветке data-gen

```powershell
git checkout data-gen
git apply --check flocast-site-final.patch
git apply flocast-site-final.patch
# Добавляйте только проверенные файлы патча; модели, архивы и чужие изменения исключите.
git add <проверенные-пути>
git commit -m "Итоговые месячные модели портов на сайте"
```

Если `git apply --check` не проходит, значит ветка ушла вперёд. Используйте `git apply --3way flocast-site-final.patch` и разрешите конфликты.

### 2. Подключить модели

Модели в Git не хранятся (`models/` в `.gitignore`). Скопируйте папку `models` из итогового архива целиком:

```powershell
Copy-Item -Recurse -Force <путь к архиву>\flocast-final-2026-11-12\models\* .\models\
$targets = "kaliningrad_ferries","kaliningrad_containers","kaliningrad_total","vladivostok_total","petropavlovsk_total"
foreach ($t in $targets) { "{0}: {1}" -f $t, (Get-Content ".\models\port\$t\latest.json" | ConvertFrom-Json).model_version }
```

Версии должны совпасть с `expected_model_version` в `backend/site_models.json`:

| Цель | Версия |
|---|---|
| kaliningrad_ferries | bb-ridge-d9c6acb34f45 |
| kaliningrad_containers | bb-port-c686b4cb47b0 |
| kaliningrad_total | bb-port-09047f079455 |
| vladivostok_total | bb-port-6329b8d38370 |
| petropavlovsk_total | bb-port-6454be65293a |

Другая версия — сайт откажется её показывать (HTTP 500). Это защита: на сайт попадает только зафиксированная модель.

### 3. Проверить локально

```powershell
python -m pip install -e ".[ml,dev]"
python -m pytest backend/tests -q
cd frontend; npm ci; npm test; npm run build; cd ..
python -m uvicorn backend.app:app --port 8000
```

Во втором окне:

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/port-forecasts | ConvertTo-Json -Depth 6
Invoke-RestMethod "http://localhost:8000/api/v1/port-forecasts/kaliningrad_ferries?end_period=2026-12" | Select-Object model_version, selected_model, display
```

Ожидается `bb-ridge-d9c6acb34f45`, `ridge_ar3_10`, `primary`. Откройте `http://localhost:8000/#port-forecast`: основной прогноз выбран по умолчанию, четыре карточки метрик, график истории с пунктиром прогноза и полосой 80%.

### 4. Docker

```powershell
$env:BB_MODELS_PATH = (Resolve-Path .\models).Path
docker compose build
docker compose up -d
Invoke-RestMethod http://127.0.0.1:8000/api/v1/port-forecasts | Select-Object status, primary_target
docker compose exec app ls /app/docs/final
```

`models` монтируется в контейнер только для чтения. При текущей интеграции
`docker compose config --quiet` проходит; сборка блокируется доступом к сокету
`/var/run/docker.sock` (`permission denied`). Контейнерный HTTP 200 и содержимое
`/app/docs/final` пока не проверены. После восстановления доступа выполните
команды выше; локальный HTTP-каталог и модели проверены вне контейнера.

## Ответы API и что они значат

| Код | `status` | Причина | Что делать |
|---|---|---|---|
| 200 | `ready` | Всё проверено | — |
| 503 | `not_connected` | Нет `models/port/<цель>/latest.json` | Шаг 2 |
| 500 | `error` | SHA-256 не сошёлся, другая версия модели, нет `docs/final` | Скопировать модели заново; сверить версии |
| 404 | `not_available` | Цели нет в `site_models.json` | — |
| 422 | `invalid_request` | `end_period` вне 2026-01…2030-12 | — |

## Как менять решение о показе

Только через `backend/site_models.json`: поле `display` (`primary` | `shown` | `shown_with_warning` | `history_only`) и `note`. Основная цель — ровно одна, это проверяется при запуске. Новая модель — новый артефакт, новая `expected_model_version` и новые строки в `docs/final/*.csv` (генератор — `tools/build_site_evidence.py` в итоговом архиве). Каждое изменение — запись в `docs/decision-log.md`.

## Эксперимент, не для сайта

```powershell
python -m ml.line_operator_experiment --root C:\nf\bundle --completed-history <архив>\research\completed\experiment\input_bundle\completed_history.csv
```

Результат — `reports/generated/line-operator/`. Ожидаемые числа на снимке `current-data-2026-11-12-8d7bea26dc98`: отношение оператор/порт 0,655; эластичность по судо-дням 0,50 (90%: 0,20–0,83); выигрыш резервного парома за 2026 год около 86 тыс. т (36–138).

## Известные ограничения

- Тест `tests/test_target_handoff.py::...rebuilds_in_new_interpreter` падает и до патча (`ModuleNotFoundError: ml` в новом интерпретаторе); патч его не касается.
- Охват отчётов оператора относительно линии Росморпорта не выяснен; от этого зависит масштаб оценки гипотезы А.
- Для паромов и всего каботажа 2026 год использован при выборе; следующая независимая проверка — октябрь–декабрь 2026 (инструкция — `AGENT_INSTRUCTIONS.md` в архиве).
