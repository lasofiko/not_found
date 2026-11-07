# Контракты v0.1

## Таблица наблюдений

UTF-8 CSV, одна строка на период × OD × направление × вид × группу. Обязательные поля:

`period,period_granularity,origin,destination,direction,mode,cargo_group,value_tonnes,status,source_id`

`period`: `YYYY-MM` для месяца или `YYYY` для года; `direction`: `inbound` (в область) / `outbound` (из области); `mode`: `sea`, `rail_land`, `road`, `air`; `status`: `observed`, `estimated`, `synthetic`. `source_id` ведёт к паспорту и оригиналу. Дополнительные поля: `route_id`, `in_wagon_on_ferry`, `quality_note`, `data_version`. `value_tonnes` — неотрицательное число или пустое значение, если наблюдения нет. Ключ и правила агрегации утверждаются после выбора целевого ряда. Один физический груз нельзя дублировать между `sea` и `rail_land`.

Числа в примерах схемы отсутствуют намеренно. Отсутствующее наблюдение — пустая ячейка, не ноль. Годовой источник не превращается в двенадцать месячных фактов.

## Сценарный API

`POST /api/v1/scenarios/capacity` принимает `demand_tonnes`, `capacity_tonnes`, `capacity_delta_tonnes` (неотрицательные, один и тот же период), `period`, `assumption_source`. Возвращает `baseline_shortfall_tonnes`, `scenario_capacity_tonnes`, `scenario_shortfall_tonnes`, `shortfall_reduction_tonnes`, `status: scenario` и предупреждение. Это чувствительность к входным параметрам, не прогноз и не эффект утверждённой инвестиции.

Будущий результат прогноза должен включать `data_version`, `model_version`, `scenario_id`, `generated_at`, `geography`, `direction`, `cargo_group`, `mode`, `period`, `metric`, `value`, `unit`, `interval`, `provenance`, `warnings`. Схема будет уточнена ML Lead и Fullstack до интеграции.

## Единицы

База: т, руб., ч, км; транспортная работа — т·км. На графике видны период и единица. Тысячи тонн — только формат отображения. Вместимость в т/сутки сравнивается с т/месяц после пересчёта по конкретному числу рабочих дней. TEU, вагоны и рейсы не переводятся в тонны без документированной загрузки.
