# Новые датасеты: что добавлено и для чего подходит

Версия данных: `bb-data-a20fa503afea`. Цель остаётся `pending`.

Из документа сохранены оригиналы, из проверяемых таблиц подготовлены самостоятельные наборы. Количество файлов не равно количеству пригодных наблюдений. Ничто ниже не заменяет направленную цель линии автоматически.

| Семейство источников | Сохранено | Содержимое извлечено | Недоступно |
|---|---:|---:|---:|
| airport_freight | 6 | 6 | 1 |
| belarus_transit | 1 | 1 | 0 |
| border_reference | 2 | 1 | 0 |
| border_workbook | 12 | 5 | 0 |
| bus_schedule | 11 | 9 | 0 |
| connectivity | 2 | 1 | 0 |
| customs_history | 2 | 1 | 0 |
| historical_port_cabotage | 70 | 69 | 0 |
| investment_passport | 1 | 1 | 0 |
| osm_infrastructure | 2 | 1 | 0 |
| rail_schedule | 24 | 0 | 0 |
| reference | 21 | 2 | 8 |
| regional_archive | 7 | 6 | 0 |
| subsidised_freight | 1 | 1 | 0 |
| weather | 2 | 2 | 0 |

## Подготовленные таблицы

| Файл | Строк |
|---|---:|
| airport_freight.csv | 52 |
| border_historical_source.csv | 72 |
| border_source_checks.csv | 228 |
| border_source_observations.csv | 228 |
| connectivity_routes.csv | 2148 |
| customs_history_kaliningrad.csv | 48559 |
| documentary_observations.csv | 3 |
| extended_coverage.csv | 162 |
| lithuania_transport_counts.csv | 39934 |
| new_source_assessment.csv | 173 |
| osm_transport_ways.csv | 77570 |
| passenger_schedule_rows.csv | 1328 |
| rail_transit_cargo.csv | 84 |
| rail_transit_monthly.csv | 72 |
| rail_transit_reconciliation.csv | 4 |
| regional_source_indicators.csv | 1834 |
| schedule_image_inventory.csv | 22 |
| weather_daily.csv | 8766 |

## Пригодность для нашей задачи

- **airport_freight** — `target_candidate`. Отдельный аэропортовый показатель Храброво, все маршруты и направления. Не тоннаж коридора; груз и почта отдельно. 2017 и 2022–2026 не закрыты этим месячным рядом.
- **belarus_transit** — `target_candidate`. Узкий проверочный ряд транзита через Беларусь в/из области, неофициальный источник. Другой конец на основной территории РФ не доказан. Тара/полнота/округления требуют проверки.
- **border_reference** — `reference`. Исторические транспортные счётчики/справка границы Польши, не тоннаж и не целевой OD.
- **border_workbook** — `reference`. Число пересечений грузовиков/транспорта, не тонны, не только основная РФ ↔ область. Не суммировать с предоставленной выборкой тех же ячеек.
- **bus_schedule** — `reference`. Пассажирская сеть, план, не выполненные рейсы и не грузовые перевозки; временная актуальность требует сверки.
- **connectivity** — `reference`. Расчётная связность городов в срезе 2026; не история грузовых рейсов, не фактическая доставка с задержками. Международные ограничения требуют сверки.
- **customs_history** — `reference`. Внешняя торговля, а не внутренний грузопоток. Единицы NETTO/STOIM этой копии не подтверждены; масса не пересчитывается в тонны.
- **historical_port_cabotage** — `target_candidate`. Кандидат отдельной цели портового каботажа; не линия, не направления, не весь коридор. Сравнительные годы и версии не складывать. Месячные разности estimated.
- **investment_passport** — `reference`. Груз и почта аэропорта вместе, без OD; не смешивать с отдельным грузовым показателем Росавиации.
- **osm_infrastructure** — `reference`. Геометрия и теги, не тоннаж/рейсы/мощность. Снимок не описывает историческую доступность маршрутов и международные ограничения.
- **rail_schedule** — `reference`. Пассажирские расписания, не грузовой поток. Из изображений числовые времена автоматически не прочитаны: нужна отдельная проверка/OCR.
- **reference** — `reference`. Указатель, методика или иной охват. Сам файл/страница не становится обучающим наблюдением; конкретный разрез не подтверждён.
- **regional_archive** — `factor_candidate`. Региональный агрегат, не OD. Разные издания/специальные коды сохранены. Даты исторической доступности и лаги не проверены; в модель автоматически не включать.
- **subsidised_freight** — `reference`. Только объём по льготной комплексной ставке и выплаченная субсидия; не весь коридор, не полный тариф/стоимость цепочки.
- **weather** — `factor_candidate`. Погодный реанализ 2022–2025, координаты сетки сохранены; не наблюдения простоев. Нужны лаги, историческая доступность и будущий сценарий.

## Важные ограничения и проверки

SeaNews: накопления и месячные разности отдельно; публикации не смешиваются по версиям. В файле за февраль 2019 заголовок «за февраль» не доказывает накопление с января: такой выпуск не включается в накопительный/месячный ряд до уточнения.
БЖД: 72 календарные записи — по 36 на направление; значение `327.442` не интерпретируется молча как 327442 или 327,442. Число пустое, исходная запись и диагностика сохранены. Годовые итоги/группы не прибавляются к месячным значениям.
`rail_transit_reconciliation.csv` сверяет сумму 12 известных месяцев с опубликованным годовым итогом каждого транзитного направления. Неполная сумма не сравнивается с полной; малые расхождения сохраняются, объяснения не выдумываются.
Польская граница: первичный XLSX и предоставленная выборка сверяются в `border_source_checks.csv`. Это повторное подтверждение тех же ячеек, а не новые физические перевозки. Общие счётчики из соседних книг не подставляются вместо грузовиков.
Аэропорт: листы пассажиров исключены; данные груза 2018–2021 месячные, 2015–2016 годовые. Годовые факты не распределены по месяцам. Почта и груз+почта из инвестиционного паспорта имеют другие metric.
Погода и экономические ряды остаются кандидатами факторов: их историческая доступность/лаги не подтверждены. При подготовке нет заполнения пропусков нулями и нет автоматического переобучения модели.
`extended_coverage.csv` отличает число строк, непустых чисел и уникальных календарных периодов, показывает внутренние пропуски. Литовский CSV отфильтрован по названиям постов со стороны области; это счётчики камер, не грузовые тонны. Подписи координат сохранены с предупреждением о вероятной перестановке.
Сохранённый справочный PDF/указатель/изображение без готового ряда помечается `saved_not_extracted`, а не считается наблюдением. Все ошибки и причины указаны построчно в `new_source_assessment.csv`; человеческая приёмка не заявляется.

## Недоступные источники

- [279823](https://39.rosstat.gov.ru/statistical_news/document/279823): <urlopen error [Errno 104] Connection reset by peer>
- [И 1.1 12_25d.pdf](https://39.rosstat.gov.ru/storage/mediabank/%D0%98%201.1%2012_25d.pdf): <urlopen error [Errno 104] Connection reset by peer>
- [opendata](https://rlw.gov.ru/opendata): <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain (_ssl.c:1081)>
- [kaliningrad.pdf](https://www.rlw.gov.ru/storage/document/document_file/2026-03/16/kaliningrad.pdf): <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain (_ssl.c:1081)>
- [256339](https://39.rosstat.gov.ru/statistical_news/document/256339): <urlopen error [Errno 104] Connection reset by peer>
- [Transport_and communication](https://39.rosstat.gov.ru/Transport_and%20communication): <urlopen error [Errno 104] Connection reset by peer>
- [dejatelnost-vozdushnye-perevozki-stat-pokazateli](https://favt.gov.ru/dejatelnost-vozdushnye-perevozki-stat-pokazateli/): HTTP Error 404: Not Found
- [emiss_62996](https://repository.econdata.tech/dataset/emiss_62996): HTTP Error 502: Bad Gateway
- [transportno-ekonomicheskij-balans-rossijskoj-federacii](https://mintrans.org/ru/transportnoe-planirovanie-i-modelirovanie/transportno-ekonomicheskij-balans-rossijskoj-federacii/): <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired (_ssl.c:1081)>

## Воспроизведение и передача

`python3 -m pip install -r data/requirements-data-lock.txt`, затем `python3 -m pipeline.build --available-only` и `python3 -m pipeline.verify`. После установки зависимостей сборка не использует сеть. В пакете есть wheel-зависимости текущих Python 3.14/Linux x86_64: `python3 -m pip install --no-index --find-links data/raw/runtime_wheels -r data/requirements-data-lock.txt`. Для другой платформы нужны соответствующие wheel тех же версий. Для новых попыток получения: `python3 -m pipeline.acquire_list --retry-failed`.
PDF читает PyMuPDF, OSM — osmium; версии отражены в `extended_quality.json`. Эти зависимости нужны для повторного извлечения соответствующих оригиналов. ИИ помог написать обработку и диагностику; методику и спорные значения должен принять человек.
