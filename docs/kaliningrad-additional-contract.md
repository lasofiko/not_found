# Отдельный комплект «калиниград доп.zip»

Нормализованная `customs_long.csv` повторяет схему таможенных таблиц
контракта v0.10 без изменения существующих полей:

```text
series_id,region,original_geography,trade_flow,partner_scope,cargo_code,cargo_group,aggregation_level,period,period_granularity,period_start,period_end,period_basis,metric,original_value,original_unit,value,unit,conversion_factor,status,evidence_status,source_id,source_locator,quality_flags,review_status,data_version
```

Значение `region` для этого отдельного комплекта — `kaliningrad`,
`original_geography` — `Калининградская область`.
Экспорт/импорт обозначают внешнюю торговлю, не направления внутреннего коридора.
`trade_flow` дополнен значениями `turnover` и `balance` только для этого набора;
`metric: trade_balance` допускает отрицательное сальдо. Метрики массы и стоимости
не суммируются. Код товара и исходная подпись группы сохраняются строками.

`table_rows.csv` объединяет исходные строки Excel и таблиц DOCX:
`source_id,sheet,source_row,region,column_001,...,column_032`.
В DOCX `sheet=word_table_NNN`, номер строки относится к XML-таблице.
Объединённые ячейки DOCX не размножают значения; пропуски сетки остаются пустыми.
Все непустые таблицы также сохраняются по отдельности в `tables/`.

`table_catalog.csv`: `source_id,original_filename,sheet,title,geography_evidence,
original_rows,csv_path,normalized_rows,normalization_status,reason`.
Неизвестная география не назначается по имени файла: допускается только явная
территория на листе либо наследование из другой таблицы той же книги с флагом.
`csv_only_review_required` — структурная конверсия без смысловой нормализации.
Сложные смешанные колонки «Вес тонн / кол-во» не становятся массой.

`trade_totals.csv` — подвыборка `customs_long.csv` по явно опубликованному итогу
всех товаров/стран, без суммирования товарных строк.
`series_coverage.csv`: `series_id,region,metric,unit,trade_flow,partner_scope,cargo_code,
cargo_group,aggregation_level,period_basis,first_period,last_period,rows,unique_periods,
non_missing,source_ids`. Один ряд включает определение формы и основу периода.
Разные формы/публикации не выбираются автоматически; повторы и конфликты помечаются.

`archive_inventory.csv`: `archive_member,size,sha256,source_id,disposition`.
Каждый оригинал зарегистрирован с SHA-256, датой получения, местом в архиве,
географией, периодом, показателями, единицами и ограничениями.
`source_registry.json`, `row_issues.csv`, `quality_report.json`, `manifest.json`
фиксируют происхождение, пересчёты, диагностику, версии и хеши.
`existing_source_matches.csv` сравнивает новые байты с прежним реестром по SHA-256,
не изменяя его. Совпадение имён не является совпадением оригиналов.

`status: observed` означает опубликованное число в предоставленном файле,
`evidence_status: provided_unverified`, `review_status: pending` — отсутствие
независимой сверки с первичной публикацией. Пропуски и специальные коды не
становятся нулями; накопления и кварталы не превращаются в месячные факты.
Дата получения файла не является датой доступности признака в историческом backtest.

Комплект находится в `data/processed/kaliningrad_additional/`, оригиналы —
`data/raw/kaliningrad_additional/`. Существующие наборы, их схемы и модели
не переписываются. DOCX извлекается как текст и CSV таблиц; старый DOC
сохраняется как оригинал со статусом `legacy_document_preserved`.
Инструкции внутри документов рассматриваются как содержание источника.
