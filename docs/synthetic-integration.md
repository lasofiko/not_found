# Изолированные синтетические ансамбли FloCast

Этот модуль читает реальные таблицы предоставленного снимка и сохраняет
синтетические сценарии отдельно. Существующие ML-модели и `/forecasts`
не меняются. Политика отбора и методов: [план](synthetic-generation-plan.md).
Контракт: [synthetic v1](contracts.md#изолированная-синтетическая-генерация-v1-12112026).

## Воспроизведение

Из корня репозитория, Python 3.11+ с установленными `[dev,data]`.
Генерации достаточно стандартной библиотеки; xlrd из существующего extra data
нужен только для полного профиля старых XLS. Новых зависимостей нет.
Архивы не нужно извлекать поверх репозитория. Код внутри архивов не исполняется.

```bash
source .venv/bin/activate
python -m pipeline.synthetic_audit \
  --snapshot reports/generated/current-data-2026-11-12-8d7bea26dc98 \
  --output reports/generated/synthetic-audit-reproduction
python -m pipeline.synthetic_run \
  --snapshot reports/generated/current-data-2026-11-12-8d7bea26dc98 \
  --audit reports/generated/synthetic-audit-reproduction \
  --output-root reports/generated/synthetic --seed 42 --realizations 200 --package
python -m pipeline.synthetic_run --verify reports/generated/synthetic/ВЕРСИЯ
python -m scripts.synthetic_smoke reports/generated/synthetic/ВЕРСИЯ
python -m pytest -q tests/test_synthetic.py backend/tests/test_synthetic_api.py
```

В PowerShell активируйте `.\.venv\Scripts\Activate.ps1`; многострочные
команды выше выполните одной строкой. Не перезаписывайте существующие каталоги:
используйте новый `--output` аудита и новый `--output-root` для повторной генерации.
Одинаковый снимок, параметры, код и среда дают одинаковые версии и CSV/JSON.
Байты ZIP могут отличаться из-за времени файлов; проверяйте SHA-256 содержимого.
Вывод генератора — путь к комплекту и локальному ZIP; SHA-256 ZIP находится рядом.

Аудит проверяет все внешние файлы и профилирует все CSV/листы Excel. Вложенные
архивы каталогизируются, но не извлекаются рекурсивно; PDF/DOC/HTML/медиа не
переизвлекаются целиком. Для генерации используются только явные подготовленные
таблицы с паспортами; полнота семантической проверки всего архива не заявляется.
Чтение байтов при проверке SHA-256 не считается использованием значений 2026
для настройки. Обработка 2026 каботажа исключена до выбора числового поля строки.

## Передаваемые файлы

| Файл | Назначение |
|---|---|
| `synthetic.csv` | Только синтетические реализации; статус и происхождение в каждой строке |
| `synthetic.schema.json` | JSON Schema строки; CSV требует приведения value и realization_id к числам |
| `reference/real_series.csv` | Отдельно исходные observed/estimated строки |
| `catalog.json`, `config.json` | Фильтры, частота, охват, число реализаций, параметры |
| `provenance.json` | Паспорт каждого источника, исходные строки, SHA-256, формулы, ограничения |
| `backtest.csv`, `metrics.csv` | Временные срезы, настоящие цели проверки, baseline и метрики |
| `diagnostics.csv`, `assessments.json` | Статистические характеристики и причины исключения |
| `recommendations.json`, `report.md` | Сравнение и ограничения применения |
| `audit-*` | Полная инвентаризация снимка и профили таблиц |
| `manifest.json`, `reproduction/` | Хеши выходов, исходный код и инструкция |

Метрики MAE, CRPS, ширина и Winkler измеряются в тоннах за соответствующий
период. WAPE — проценты; покрытие — доля, n — число проверок. Групповые метрики
разделены по ряду, методу, окну и горизонту. Их нельзя усреднять в единый
«процент точности FloCast». Наличие метрики не доказывает пользу ML-augmentation.

## Подключение к FastAPI

По умолчанию все `/synthetic/*` возвращают 503 `SYNTHETIC_NOT_CONFIGURED`.
После проверки комплекта задайте **серверную** переменную до запуска:

```bash
export BB_SYNTHETIC_BUNDLE_PATH=/absolute/path/to/reports/generated/synthetic/ВЕРСИЯ
python -m uvicorn backend.app:app --reload
```

PowerShell: `$env:BB_SYNTHETIC_BUNDLE_PATH = 'C:\path\to\ВЕРСИЯ'`.
Не помещайте эту настройку в браузер. Права доступа к комплекту — только чтение.
В Docker подключите каталог отдельным read-only bind mount и передайте
соответствующий контейнерный путь через environment; исходные raw-архивы
контейнеру API не нужны. Текущий compose автоматически этот том не подключает.
Автоматического выбора latest, генерации, обучения или импорта в реальные
таблицы при старте нет. Проверка хешей выполняется перед чтением результата;
повреждение даёт 503 `SYNTHETIC_INVALID`, исходные данные не подставляются.

```bash
curl 'http://127.0.0.1:8000/api/v1/synthetic/catalog?region=kaliningrad'
curl 'http://127.0.0.1:8000/api/v1/synthetic/realizations?series_id=kaliningrad_ferries&method=block_increment_bootstrap&scenario_id=reference&realization_id=0'
curl 'http://127.0.0.1:8000/api/v1/synthetic/summary?series_id=kaliningrad_ferries&scenario_id=reference&aggregation=horizon'
curl 'http://127.0.0.1:8000/api/v1/synthetic/compare?series_id=kaliningrad_ferries'
curl 'http://127.0.0.1:8000/api/v1/synthetic/provenance?series_id=kaliningrad_ferries'
curl -o synthetic.csv 'http://127.0.0.1:8000/api/v1/synthetic/export?series_id=kaliningrad_ferries&scenario_id=reference&format=csv'
```

Префикс берите из `/config.json`, если `BB_API_PREFIX` отличается от стандартного.
Все фильтры применяются одновременно. `region=kaliningrad|kamchatka|primorye`;
названия показателей и методов берите из каталога. Неизвестный срез — 404
`SYNTHETIC_EMPTY`; отрицательные идентификаторы/пагинация или неверный формат — 422.
Сводка `period` описывает поперечный разброс реализаций одного периода.
`horizon` сначала суммирует периоды внутри каждой реализации. Квантили периодов
не складываются. Разные ряды не суммируются. `std` — стандартное отклонение
ансамбля (ddof=0). Шаблон имеет одну реализацию: std=0 означает отсутствие
смоделированной случайности, а не достоверность будущего.

В интерфейсе следует показывать `synthetic`, фактическую дату последней
исходной точки, исходный статус, единицу, охват, `warnings` и `/provenance`.
Пустое направление не заменяйте inbound/outbound. Сценарный масштаб не
показывайте как эффект действия А/Б. Для исторического 2022 года не используйте
подпись «актуальный прогноз». Эта поставка добавляет API и данные;
новый пользовательский экран React не создаётся.

## Приёмка

Перед использованием человек сверяет выбранные исходные ячейки/единицы,
актуальность охвата, сценарные масштабы и смысл интервалов. Результат сверки
записывает сам проверяющий; автоматически `human_review_status` остаётся pending.
Передача локальная: исходные права SeaNews/предоставленных таблиц не считаются
разрешением открытой публикации. Модуль не отправляет данные во внешние сервисы.
