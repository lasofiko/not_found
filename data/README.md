# Данные

**Локальный комплект CSV для ML-инженера, исключённый из Git:**
[data/ml_handoff/README.md](ml_handoff/README.md). Там находятся полные копии
подготовленных таблиц Калининграда, Камчатки и Приморья; большие CSV разбиты
на части до 8 МиБ без потери строк. Каталог — `ml_handoff/inventory.csv`.
Проверка без оригиналов: `python -m pipeline.ml_handoff --verify`.
`ml_handoff`, `raw`, `interim` и `processed` не добавляются в Git.
Для передачи всех обработанных данных одним ZIP с исходными именами CSV:
`python -m pipeline.ml_handoff --package`. Архив — в `reports/generated/`.

Новый отдельный Калининградский архив: `python -m pipeline.kaliningrad_additional --archive FILE.zip`.
Результаты — `processed/kaliningrad_additional/`, оригиналы — `raw/kaliningrad_additional/`.
Проверка и ZIP передачи: `python -m pipeline.kaliningrad_additional --verify --package`.
Общая таможенная таблица — `customs_long.csv`; [инструкция и ограничения](../docs/kaliningrad-additional-handoff.md).

Камчатка и Приморье обрабатываются отдельно: `python -m pipeline.far_east --archive FILE.zip`.
Результаты — `processed/far_east/`, паспорта — `processed/far_east/source_registry.json`,
оригиналы — `raw/far_east/`. Чукотка исключается по имени вложения и географии
внутри смешанных таблиц. Проверка и передача: `python -m pipeline.far_east --verify --package`.
Основной файл — `transport_long.csv`, годовая матрица — `freight_annual_panel.csv`.
Инструкция и ограничения: [передача ML-инженеру](../docs/far-east-handoff.md).

Дополнительные источники из DOCX: `python3 -m pipeline.acquire_list`, затем `python3 -m pipeline.build --available-only` и `python3 -m pipeline.verify`. Оценка пригодности каждого источника — [new-datasets-assessment.md](../docs/new-datasets-assessment.md), машинная таблица — `processed/new_source_assessment.csv`, покрытие — `processed/extended_coverage.csv`. Новые ряды БЖД, Храброво, погоды, региональной статистики и счётчиков границы хранятся отдельно от принятия цели; модель автоматически не меняется. Документ со ссылками сохранён как оригинал и связан с найденными источниками.

Для повторного извлечения PDF/OSM нужны версии из `requirements-data-lock.txt`. Установка с сетью: `python3 -m pip install -r data/requirements-data-lock.txt`. Внутренний архив включает wheel для Python 3.14/Linux x86_64; офлайн: `python3 -m pip install --no-index --find-links data/raw/runtime_wheels -r data/requirements-data-lock.txt`. Для другой платформы следует получить совместимые wheel тех же версий.

Передача паспорта цели (контракт v0.8): `docs/target-series-passport.md`, `docs/data-quality-report.md`, `docs/roma-data-summary.md`; построчная сверка — `processed/target-source-check.csv`, полная матрица пробелов — `processed/corridor-coverage.csv`. Сборка не утверждает цель. Журнал `human-review-log.csv` заполняет человек после реальной сверки; сейчас он пуст. Шаблон: `examples/observations-template.csv`, инструкция: `docs/data-input-guide.md`. Проверка своих данных: `python3 -m pipeline.input_check FILE`; воспроизведение готового комплекта: `python3 -m pipeline.reproduce`. Пакет: `python3 -m pipeline.package --output reports/generated/data-handoff.zip`.

Для текущих файлов в `data/incoming/`: `python -m pipeline.acquire --local-dir data/incoming`, затем `python -m pipeline.build --available-only` и `python -m pipeline.verify`. Оригиналы ZIP и книги сохраняются в `raw` с паспортами. Вложения связаны с архивами; одинаковые байты не размножаются. Частичный манифест перечисляет отсутствующие оригиналы; архив включает доступные входы. Повреждённые оригиналы отклоняются.

Контракт v0.4 добавляет региональные показатели/грузовой срез, раздельные ряды публикаций, накопительный портовый каботаж и оценки месячных разностей, пересечения автомобилей, карту покрытия и диагностику. `review_status: pending` требует человеческой сверки; факторы без даты доступности автоматически в модель не включаются. Подробности: `docs/supplemental-quality.md`, `docs/data-engineer-next-steps.md`.

`incoming/` — предоставленные Excel/ZIP с исходными именами, локально; `raw/` — неизменённые файлы источников; `interim/` — рабочие преобразования; `processed/` — проверенные таблицы для обучения и API; `examples/` — только малые образцы схем. Первые три каталога исключены из Git, кроме `.gitkeep`.

Крупные файлы (XLSX, PDF, ZIP, PBF, Parquet), личные данные и выгрузки не коммитить. В Git хранить манифест: `source_id`, URL/организацию, дату скачивания, SHA-256, период, географию, единицу, права использования, путь локального файла и ограничения. Файл без паспорта нельзя считать обучающим набором. Для совместной передачи большого файла используйте согласованное командой хранилище и сверяйте хеш.

`source_catalog.csv` — каталог ссылок из предоставленной Google-таблицы с приоритетами, назначением и явным статусом проверки. `source_registry.json` — паспорта полученных оригиналов и ошибки доступа (`failures`). Эти два небольших файла хранятся в Git. Пустые паспортные поля означают, что сведения пока не установлены.

`python -m pipeline.acquire` сохраняет байты ответа в `raw/<source_key>/<sha256>.<format>`, не меняя оригинал. Повторный запуск сверяет существующий файл, `--refresh` добавляет новый снимок. Реестр использует `source_id: <source_key>__<sha256[:12]>`. Обновления сайта не должны уничтожать предыдущую версию. Паспорта не подтверждают лицензию: неизвестные условия использования явно отмечаются.

`python -m pipeline.build` создаёт `processed/candidate_observations.csv` и отдельные таблицы контекста, параметров, покрытия, журнал преобразований и манифест сборки. `processed/observations.csv` команда не создаёт: выбор целевого ряда остаётся за командой. Единицы, происхождение строк и новые поля описаны в `docs/contracts.md` (v0.4).

Паспорта пользовательских XLSX/XLS описаны в `pipeline/local_sources.json`; импорт — `pipeline.acquire --local <source_key> <путь>`. Имена и периоды проверяются по каждому источнику. Экспортные файлы преобразуются в `processed/exports_clean.csv`, выборка области — `processed/exports_kaliningrad.csv`. Транзит — `processed/transit_items_clean.csv` и `processed/transit_monthly_summary.csv`. Эти дополнительные таблицы не имеют подтверждённой географии коридора и не присоединяются к целевому ряду. Отчёт — `docs/tables-quality.md`; диагностика индексов, пропусков и повторов — отдельные файлы в `processed/`.

Транзитный оригинал содержит реквизиты участников и транспорта: он остаётся вне Git, перечисленные поля исключены из очищенного CSV. Локальный архив включает оригинал, поэтому его передают только согласованным получателям. `raw/xlrd_reader/` содержит официальный wheel чтения XLS с хешем и лицензией внутри, чтобы получатель мог повторить обработку офлайн.

Все данные в `raw`, `interim`, `processed` остаются локальными, включая небольшие реальные ряды. Для воспроизводимой передачи используйте `python -m pipeline.package`: ZIP содержит именно оригиналы из реестра, результаты и код, рядом сохраняется SHA-256. Архив остаётся в `reports/generated/` и не отправляется автоматически. Описание передачи и человеческой приёмки — `docs/data-preparation.md`.
## Сводный обработанный комплект команды

`pipeline.team_bundle --from-processed FILE` создаёт независимую проверяемую
сборку из `part1-processed.zip` без первичных raw-источников, согласно решению
пользователя. Это не повторное извлечение первоисточников. Уровень проверки
`provided_tables` сохраняется в манифесте и ML-происхождении.
Дополнения: `processed/team_bundle/`; покрытие протокола: `processed/port_protocol/`.
Оригинальный ZIP: `raw/team_bundles/`; прежний манифест остаётся внутри ZIP.
Источники, конфликты, команды и ограничения: [инструкция](../docs/team-bundle-processing.md).
